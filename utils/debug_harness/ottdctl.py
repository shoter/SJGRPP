#!/usr/bin/env python3
"""
Command line client for the OpenTTD debug bridge.

  ottdctl.py --port 7001 info
  ottdctl.py --port 7001 console "dump_vehicle 12"
  ottdctl.py --port 7001 obj vehicle 12 cur_speed tile
  ottdctl.py --port 7001 screenshot out.png
  ottdctl.py --port 7001 windows          (window classes shown by name)
  ottdctl.py --port 7001 --repl           (interactive)
  ottdctl.py --session mp server info     (use the ports of a running mp.py session)

Any bridge command works (send "help" for the list); arguments are passed through as-is.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ottd import Bridge, BridgeError, DEFAULT_RUN_ROOT, quote_arg, window_class_name  # noqa: E402


def resolve_port(args: argparse.Namespace, rest: list[str]) -> tuple[int, list[str]]:
    if args.session:
        state_path = Path(args.root) / args.session / "session.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not rest:
            sys.exit(f"--session needs an instance name first, one of: {', '.join(state['instances'])}")
        name, rest = rest[0], rest[1:]
        return state["instances"][name]["port"], rest
    if args.port is None:
        sys.exit("need --port or --session")
    return args.port, rest


def show(result, raw: bool) -> None:
    if raw:
        print(json.dumps(result))
        return
    if isinstance(result, list) and result and all(isinstance(x, str) for x in result):
        print("\n".join(result))
    elif isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def run_one(bridge: Bridge, words: list[str], raw: bool) -> int:
    if not words:
        return 0
    line = words[0] + (" " + " ".join(words[1:]) if words[0] == "console" else
                       "".join(" " + quote_arg(w) for w in words[1:]))
    resp = bridge.raw(line)
    if not resp.get("ok"):
        print(f"error: {resp.get('error')}", file=sys.stderr)
        return 1
    result = resp.get("result")
    if words[0] == "windows" and isinstance(result, list):
        for w in result:
            w["class_name"] = window_class_name(w["class"])
    show(result, raw)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int)
    ap.add_argument("--session", help="mp.py session name (reads <root>/<session>/session.json)")
    ap.add_argument("--root", default=str(DEFAULT_RUN_ROOT))
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--raw", action="store_true", help="print compact JSON")
    ap.add_argument("--repl", action="store_true")
    args, rest = ap.parse_known_args()

    port, rest = resolve_port(args, rest)
    bridge = Bridge(port, timeout=args.timeout).connect()

    if args.repl:
        import shlex
        while True:
            try:
                line = input(f"ottd:{port}> ")
            except (EOFError, KeyboardInterrupt):
                return 0
            if line.strip() in ("exit", "q"):
                return 0
            try:
                run_one(bridge, shlex.split(line, posix=True) if not line.startswith("console ") else ["console", line[8:]], args.raw)
            except (BridgeError, OSError) as e:
                print(f"error: {e}", file=sys.stderr)

    try:
        return run_one(bridge, rest, args.raw)
    except (BridgeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
