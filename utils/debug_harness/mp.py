#!/usr/bin/env python3
"""
Multiplayer orchestrator: a dedicated server plus N headless clients on localhost, each fully isolated
(own personal dir, config, logs, debug bridge port).

CLI (processes keep running after "up"; state in <root>/<session>/session.json):
  mp.py up [--clients 2] [--session mp] [--save file.sav] [--seed 1] [--company new|spectator|N] [--debug desync=2]
  mp.py status [--session mp]
  mp.py health [--session mp]          # crashed processes / desync / disconnect messages
  mp.py down [--session mp]
  ottdctl.py --session mp client1 screenshot out.png     # drive one instance
  collect.py --session mp                                 # crash/desync bundle

Library:
  from mp import Cluster
  with Cluster(clients=2) as c:
      c.start()
      c.server.bridge.console("clients")
      c.clients[0].bridge.screenshot("c1.png")
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ottd import Bridge, BridgeError, DEFAULT_RUN_ROOT, Instance, free_port  # noqa: E402

PROBLEM_RE = re.compile(r"desync|lost connection|connection lost|kicked|server closed|error", re.I)


def company_suffix(company: str) -> str:
    if company == "new":
        return "#254"
    if company == "spectator":
        return "#255"
    return f"#{int(company)}"


class Cluster:
    def __init__(self, clients: int = 2, session: str = "mp", root: Path | str | None = None, save: str | None = None,
                 seed: int | None = None, company: str = "new", debug: str = "", client_debug: str | None = None,
                 resolution: tuple[int, int] = (1280, 720), server_config: dict | None = None,
                 client_config: dict | None = None):
        self.session = session
        self.root = (Path(root) if root else DEFAULT_RUN_ROOT) / session
        self.save = save
        self.seed = seed
        self.company = company
        self.server_port = free_port()
        self.server = Instance("server", mode="dedicated", root=self.root, server_port=self.server_port, debug=debug,
                               config=server_config)
        self.clients = [Instance(f"client{i + 1}", mode="headless", root=self.root, resolution=resolution,
                                 debug=client_debug if client_debug is not None else debug, config=client_config)
                        for i in range(clients)]
        self.log_marks: dict[str, int] = {}

    @property
    def instances(self) -> list[Instance]:
        return [self.server] + self.clients

    def start(self, timeout: float = 120.0) -> "Cluster":
        self.root.mkdir(parents=True, exist_ok=True)
        args = []
        if self.save:
            args += ["-g", str(Path(self.save).resolve())]
        if self.seed is not None:
            args += ["-G", str(self.seed)]
        self.server.start(*args, wait=timeout)
        self.server.bridge.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=timeout, what="server game")
        self.write_state()
        for client in self.clients:
            self.start_client(client, timeout)
        self.write_state()
        return self

    def start_client(self, client: Instance, timeout: float = 120.0) -> None:
        client.start("-n", f"127.0.0.1:{self.server_port}{company_suffix(self.company)}", wait=timeout)
        client.bridge.wait_until(
            lambda b: (i := b.info())["networking"] and i["game_mode"] == "normal" and i["client_id"] != 0,
            timeout=timeout, what=f"{client.name} to join")

    def write_state(self) -> None:
        state = {
            "session": self.session,
            "server_port": self.server_port,
            "instances": {i.name: {"port": i.port, "pid": i.proc.pid if i.proc else None, "dir": str(i.dir),
                                   "mode": i.mode} for i in self.instances},
        }
        (self.root / "session.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def health(self) -> list[str]:
        """Problems seen since the last call: dead processes and suspicious log lines."""
        problems = []
        for inst in self.instances:
            if not inst.alive():
                problems.append(f"{inst.name}: process not running (exit code {inst.proc.returncode if inst.proc else '?'})")
                continue
            try:
                log = inst.bridge.log(self.log_marks.get(inst.name, 0), 100000)
            except (OSError, BridgeError) as e:
                problems.append(f"{inst.name}: bridge unreachable: {e}")
                continue
            self.log_marks[inst.name] = log["last_seq"]
            problems += [f"{inst.name}: {l['text']}" for l in log["lines"]
                         if PROBLEM_RE.search(l["text"]) and "[debug-bridge]" not in l["text"]]
        return problems

    def stop(self) -> None:
        for inst in reversed(self.instances):
            inst.stop()

    def __enter__(self) -> "Cluster":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def load_state(root: Path, session: str) -> dict:
    path = root / session / "session.json"
    if not path.exists():
        sys.exit(f"no session state at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["up", "status", "health", "down"])
    ap.add_argument("--session", default="mp")
    ap.add_argument("--root", default=str(DEFAULT_RUN_ROOT))
    ap.add_argument("--clients", type=int, default=2)
    ap.add_argument("--save")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--company", default="new", help="new | spectator | <1-based company number>")
    ap.add_argument("--debug", default="", help="-d levels for all instances, e.g. desync=2,net=2")
    ap.add_argument("--resolution", default="1280x720")
    args = ap.parse_args()
    root = Path(args.root)

    if args.action == "up":
        w, h = (int(x) for x in args.resolution.lower().split("x"))
        cluster = Cluster(clients=args.clients, session=args.session, root=root, save=args.save, seed=args.seed,
                          company=args.company, debug=args.debug, resolution=(w, h))
        try:
            cluster.start()
        except Exception as e:
            print(f"startup failed: {e}", file=sys.stderr)
            for inst in cluster.instances:
                print(f"--- {inst.name} stderr ---\n{inst.tail('stderr', 20)}", file=sys.stderr)
            cluster.stop()
            return 1
        state = load_state(root, args.session)
        print(json.dumps(state, indent=2))
        return 0

    state = load_state(root, args.session)

    if args.action == "status":
        for name, inst in state["instances"].items():
            line = f"{name:10} port={inst['port']:<6} pid={inst['pid']:<7} {'alive' if pid_alive(inst['pid']) else 'DEAD'}"
            try:
                info = Bridge(inst["port"], timeout=5).connect().info()
                line += f"  mode={info['game_mode']} date={info['date']} tick={info['tick_counter']} client_id={info['client_id']} company={info['local_company']}"
            except OSError:
                line += "  (bridge unreachable)"
            print(line)
        return 0

    if args.action == "health":
        bad = False
        for name, inst in state["instances"].items():
            if not pid_alive(inst["pid"]):
                print(f"{name}: process not running")
                bad = True
                continue
            try:
                log = Bridge(inst["port"], timeout=5).connect().log(0, 100000)
                for l in log["lines"]:
                    if PROBLEM_RE.search(l["text"]) and "[debug-bridge]" not in l["text"]:
                        print(f"{name}: {l['text']}")
                        bad = True
            except OSError as e:
                print(f"{name}: bridge unreachable: {e}")
                bad = True
        if not bad:
            print("healthy")
        return 1 if bad else 0

    if args.action == "down":
        for name, inst in reversed(list(state["instances"].items())):
            try:
                Bridge(inst["port"], timeout=5).connect().quit()
            except OSError:
                pass
        deadline = time.monotonic() + 15
        for name, inst in state["instances"].items():
            while pid_alive(inst["pid"]) and time.monotonic() < deadline:
                time.sleep(0.2)
            if pid_alive(inst["pid"]):
                os.kill(inst["pid"], signal.SIGTERM)
                print(f"{name}: killed")
            else:
                print(f"{name}: stopped")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
