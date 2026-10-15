#!/usr/bin/env python3
"""
Crash/desync collector: bundles everything useful for post-mortem analysis of one or more game instances
into a single folder (and a summary), so a failure can be analysed after the processes are gone.

Collected per instance: stdout/stderr logs, command line, configs, crash*.log/.dmp/.sav/.png,
desync/commands-out/random-out logs; and, if the instance is still alive, the bridge log ring buffer,
info, a final screenshot and a few diagnostic console dumps.

  collect.py --session mp --out build/harness_runs/mp/_collected
  collect.py --dirs build/harness_runs/smoke2 --out somewhere
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ottd import Bridge, DEFAULT_RUN_ROOT  # noqa: E402

COPY_PATTERNS = ["stdout.log", "stderr.log", "command_line.txt", "openttd.cfg", "crash*", "desync*",
                 "commands-out*.log", "random-out*.log", "autosave/crash*", "autosave/desync*",
                 "autosave/commands-out*.log", "autosave/random-out*.log", "save/crash*", "save/desync*"]

PROBLEM_RE = re.compile(r"desync|assertion|crash|error|lost connection|connection lost|kicked", re.I)

LIVE_CONSOLE_DUMPS = ["dump_desync_msgs", "dump_command_log", "dump_special_events_log", "clients"]


def collect_instance(name: str, inst_dir: Path, out: Path, port: int | None) -> dict:
    dest = out / name
    dest.mkdir(parents=True, exist_ok=True)
    summary: dict = {"name": name, "dir": str(inst_dir), "files": [], "problems": [], "alive": False}

    for pat in COPY_PATTERNS:
        for src in inst_dir.glob(pat):
            if src.is_file():
                target = dest / src.relative_to(inst_dir).as_posix().replace("/", "__")
                shutil.copy2(src, target)
                summary["files"].append(target.name)

    for log_name in ("stdout.log", "stderr.log"):
        path = inst_dir / log_name
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if PROBLEM_RE.search(line) and "[debug-bridge]" not in line:
                    summary["problems"].append(f"{log_name}: {line.strip()}")

    crash_logs = sorted(inst_dir.glob("crash*.log")) + sorted(inst_dir.glob("crash*.json.log"))
    for crash in crash_logs:
        head = crash.read_text(encoding="utf-8", errors="replace").splitlines()[:30]
        reason = [line for line in head if "Reason" in line or "Message" in line or "assert" in line.lower()]
        summary["problems"].append(f"crash log {crash.name}: {' | '.join(reason) or head[:3]}")

    if port:
        try:
            b = Bridge(port, timeout=20).connect()
            summary["alive"] = True
            info = b.info()
            (dest / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
            log = b.log(0, 100000)
            (dest / "bridge_log.txt").write_text(
                "\n".join(f"{l['seq']:>7} [{l['source']}] {l['text']}" for l in log["lines"]), encoding="utf-8")
            for line in log["lines"]:
                if PROBLEM_RE.search(line["text"]) and "[debug-bridge]" not in line["text"]:
                    summary["problems"].append(f"bridge log: {line['text']}")
            for cmd in LIVE_CONSOLE_DUMPS:
                try:
                    (dest / f"console_{cmd}.txt").write_text("\n".join(b.console(cmd)), encoding="utf-8")
                except Exception as e:  # noqa: BLE001
                    (dest / f"console_{cmd}.txt").write_text(f"error: {e}", encoding="utf-8")
            if info.get("video_driver") == "headless":
                try:
                    b.screenshot(dest / "final_screen.png")
                except Exception as e:  # noqa: BLE001
                    summary["problems"].append(f"final screenshot failed: {e}")
            b.close()
        except OSError:
            pass

    summary["problems"] = list(dict.fromkeys(summary["problems"]))[:200]
    return summary


def collect(instances: dict[str, dict], out: Path) -> dict:
    """instances: name -> {"dir": path, "port": int|None}"""
    out.mkdir(parents=True, exist_ok=True)
    result = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "instances": []}
    for name, data in instances.items():
        result["instances"].append(collect_instance(name, Path(data["dir"]), out, data.get("port")))
    (out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    lines = [f"Collected {result['time']} into {out}"]
    for s in result["instances"]:
        lines.append(f"\n== {s['name']} ({'alive' if s['alive'] else 'not running'}) {s['dir']}")
        lines.append(f"   files: {', '.join(s['files']) or '-'}")
        for p in s["problems"][:40]:
            lines.append(f"   ! {p}")
    (out / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="mp.py session name")
    ap.add_argument("--root", default=str(DEFAULT_RUN_ROOT))
    ap.add_argument("--dirs", nargs="*", default=[], help="instance directories")
    ap.add_argument("--out", help="output directory (default: <session or first dir>/_collected_<time>)")
    args = ap.parse_args()

    instances: dict[str, dict] = {}
    base: Path | None = None
    if args.session:
        base = Path(args.root) / args.session
        state = json.loads((base / "session.json").read_text(encoding="utf-8"))
        instances = {n: {"dir": d["dir"], "port": d["port"]} for n, d in state["instances"].items()}
    for d in args.dirs:
        p = Path(d)
        instances[p.name] = {"dir": str(p), "port": None}
        base = base or p.parent
    if not instances:
        ap.error("need --session or --dirs")

    out = Path(args.out) if args.out else (base or Path(".")) / f"_collected_{time.strftime('%Y%m%d_%H%M%S')}"
    collect(instances, out)
    print((out / "summary.txt").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
