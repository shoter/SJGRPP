"""
TEMPORARY (perf measurement of this PR; removed before merge).

CPU-heavy tick benchmark: load .github/perf/heavy.sav (1024x1024, 28 companies, 3,752 vehicles) with the null video
driver, let the game run as fast as it can (no frame limiter), and measure a fixed number of ticks after a warm-up:
CPU time per tick, ticks per second, and the per-element averages of the `fps` console command.

  python .github/perf/heavy_bench.py <label> <exe> <out.json> [--ticks 6000] [--warmup 1000]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import re
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "utils" / "debug_harness"))
from ottd import Instance  # noqa: E402

FPS_LINE = re.compile(r"^\s*(.+?) times: ([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")


def cpu_seconds(pid: int) -> float:
    """User + kernel CPU time of a Windows process."""
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32")
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    ft = [wintypes.FILETIME() for _ in range(4)]
    k32.GetProcessTimes(h, *[ctypes.byref(f) for f in ft])
    k32.CloseHandle(h)
    value = lambda f: (f.dwHighDateTime << 32 | f.dwLowDateTime) / 1e7  # noqa: E731
    return value(ft[2]) + value(ft[3])


def fps(b) -> dict[str, float]:
    """Average over all 512 data points of each element in the `fps` console output."""
    out = {}
    for line in b.console("fps"):
        m = FPS_LINE.match(line)
        if m:
            out[m.group(1).strip()] = float(m.group(4))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("exe")
    ap.add_argument("out")
    ap.add_argument("--ticks", type=int, default=6000)
    ap.add_argument("--warmup", type=int, default=1000)
    ap.add_argument("--content", required=True)
    ap.add_argument("--run-root", required=True)
    a = ap.parse_args()

    inst = Instance(f"heavy_{a.label}", mode="null", exe=str(Path(a.exe).resolve()), root=Path(a.run_root).resolve(),
                    content=Path(a.content).resolve())
    inst.prepare()
    inst.clean = False
    (inst.dir / "save").mkdir(exist_ok=True)
    shutil.copy(Path(__file__).parent / "heavy.sav", inst.dir / "save" / "heavy.sav")
    try:
        inst.start("-g", str(inst.dir / "save" / "heavy.sav"), wait=300)
        b = inst.bridge
        b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=300, what="save loaded")
        b.console("unpause")
        w0 = b.info()["tick_counter"]
        while b.info()["tick_counter"] < w0 + a.warmup:
            time.sleep(0.02)
        t0, k0, c0 = time.monotonic(), b.info()["tick_counter"], cpu_seconds(inst.proc.pid)
        samples, last = [], t0
        while True:
            k1 = b.info()["tick_counter"]
            if k1 >= k0 + a.ticks:
                t1, c1 = time.monotonic(), cpu_seconds(inst.proc.pid)
                break
            if time.monotonic() - last >= 3:
                samples.append(fps(b))
                last = time.monotonic()
            time.sleep(0.02)
        samples.append(fps(b))
    finally:
        inst.stop()
    ticks = k1 - k0
    result = {
        "label": a.label,
        "ticks": ticks,
        "cpu_ms_per_tick": (c1 - c0) * 1000 / ticks,
        "ticks_per_s": ticks / (t1 - t0),
        "fps_ms": {k: statistics.mean(s[k] for s in samples if k in s) for k in samples[-1]},
    }
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
