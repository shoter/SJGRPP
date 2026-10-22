#!/usr/bin/env python3
"""
Training run for profile-guided optimisation (see cmake/PGO.cmake).

Runs an instrumented (OPTION_PGO=GENERATE) game binary through a fixed single-player workload in lockstep, so the
profile is the same on every run: it loads a busy multiplayer world (by default pgo/training.sav: the final save of a
max-28 full test, 28 companies with every transport type on 1024x1024), runs game ticks with the GUI rendered
off-screen (headless video driver), saves the game (the code that sends the map to joining clients) and runs more
ticks. The game is quit normally so the profile is written.

The workload has to look like real games: code the training does not reach is optimised for size by MSVC, so a small
or one-sided training world makes the result slower than a build without PGO.

  python utils/debug_harness/pgo_train.py --exe build/openttd.exe --pgo-dir build/pgo

MSVC: the profile (openttd!N.pgc) is written next to openttd.pgd in --pgo-dir (VCPROFILE_PATH is set to it); the
instrumented binary needs pgort140.dll from the MSVC bin directory on PATH.
GCC: the .gcda files go where -fprofile-generate pointed (OPTION_PGO_DIR).
Clang: the .profraw files in --pgo-dir are merged into default.profdata with llvm-profdata (--llvm-profdata).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ottd import Instance  # noqa: E402

DEFAULT_SAVE = HERE / "pgo" / "training.sav"


def log(msg: str) -> None:
    print(f"[pgo_train {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def step(b, ticks: int, chunk: int) -> None:
    while ticks > 0:
        n = min(chunk, ticks)
        b.step(n)
        ticks -= n


def merge_clang_profiles(pgo_dir: Path, tool: str) -> Path | None:
    """Merge the .profraw files written by a Clang-instrumented binary into default.profdata."""
    raw = sorted(pgo_dir.glob("*.profraw"))
    if not raw:
        return None
    out = pgo_dir / "default.profdata"
    log(f"merging {len(raw)} .profraw file(s) into {out}")
    subprocess.run([tool, "merge", "-o", str(out)] + [str(p) for p in raw], check=True)
    return out


def profile_files(pgo_dir: Path) -> list[str]:
    """Profile data written by the training run (MSVC .pgc, GCC .gcda, Clang .profraw/.profdata)."""
    return sorted(p.relative_to(pgo_dir).as_posix() for p in pgo_dir.rglob("*")
                  if p.suffix in (".pgc", ".gcda", ".profraw", ".profdata"))


def train(exe: str, save: Path, ticks: int, run_root: Path, content: str | None) -> int:
    # Absolute: the instance links its content folders (baseset, ...) to this directory.
    kwargs = {"content": Path(content).resolve()} if content else {}
    inst = Instance("pgo_train", mode="headless", lockstep=True, exe=exe, root=run_root, **kwargs)
    inst.prepare()
    inst.clean = False
    (inst.dir / "save").mkdir(exist_ok=True)
    shutil.copy(save, inst.dir / "save" / "pgo_train_in.sav")
    try:
        try:
            inst.start("-g", str(inst.dir / "save" / "pgo_train_in.sav"), wait=300)
        except RuntimeError:
            log("the game did not start; end of its output:\n" + inst.tail("stderr") + "\n" + inst.tail("stdout"))
            raise
        b = inst.bridge
        b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=600, what="savegame loaded")
        b.console("unpause")
        log(f"{len(b.objs('vehicle'))} vehicles, map {b.info()['map']}; running {ticks} ticks")
        # Half the ticks one per frame (vehicles and viewport drawing), half in larger steps (more game time).
        step(b, ticks // 2, 1)
        step(b, ticks - ticks // 2, 25)
        # Saving is what a server does for every joining client; loading was covered by loading the training save.
        b.save_game("pgo_train")
        step(b, 500, 5)
        log(f"date {b.info()['date']}")
    finally:
        code = inst.stop(timeout=300)
    if code != 0:
        log(f"game exited with code {code}; see {inst.dir / 'stderr.log'}")
    return code or 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", required=True, help="instrumented game binary")
    ap.add_argument("--pgo-dir", required=True, help="profile directory (OPTION_PGO_DIR)")
    ap.add_argument("--save", default=str(DEFAULT_SAVE), help="savegame to train on (default: %(default)s)")
    ap.add_argument("--ticks", type=int, default=4000, help="game ticks before saving")
    ap.add_argument("--content", help="content directory with the baseset (default: the harness default)")
    ap.add_argument("--run-root", help="directory for the game's personal dir (default: <pgo-dir>/run)")
    ap.add_argument("--llvm-profdata", default=os.environ.get("LLVM_PROFDATA", "llvm-profdata"))
    a = ap.parse_args()

    pgo_dir = Path(a.pgo_dir).resolve()
    pgo_dir.mkdir(parents=True, exist_ok=True)
    os.environ["VCPROFILE_PATH"] = str(pgo_dir)  # MSVC: where the instrumented binary writes its .pgc file
    os.environ.setdefault("LLVM_PROFILE_FILE", str(pgo_dir / "openttd-%p.profraw"))  # Clang
    run_root = Path(a.run_root).resolve() if a.run_root else pgo_dir / "run"

    t0 = time.monotonic()
    # Absolute: the game runs in its own directory and finds its data files (lang, ...) next to the binary.
    exe = str(Path(a.exe).resolve())
    if train(exe, Path(a.save), a.ticks, run_root, a.content) != 0:
        return 1
    merge_clang_profiles(pgo_dir, a.llvm_profdata)
    files = profile_files(pgo_dir)
    if not files:
        log("no profile data was written; is the binary built with OPTION_PGO=GENERATE?")
        return 1
    log(f"done in {time.monotonic() - t0:.0f}s; {len(files)} profile file(s), e.g. {files[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
