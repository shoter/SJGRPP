#!/usr/bin/env python3
"""
Scenario test runner. A scenario is a Python file defining:

    def run(ctx):
        inst = ctx.solo(args=["-g", "-G", "1"], lockstep=True)   # or: cluster = ctx.cluster(clients=2)
        b = inst.bridge
        b.wait_until(lambda b: b.info()["game_mode"] == "normal", what="game start")
        b.step(100)
        ctx.check(b.obj("globals", None, "tick_counter")["tick_counter"] > 0, "time advances")
        ctx.screenshot(b, "after_100_ticks")

Run:  scenario.py scenarios/solo_smoke.py [more.py ...] [--keep] [--out DIR]

Every scenario gets its own output directory (screenshots, instance dirs, result.json). Instances are
stopped at the end (unless --keep); on failure the crash/desync collector runs first, so the bundle
in <out>/<scenario>/collected/ shows what happened.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import collect  # noqa: E402
from mp import Cluster  # noqa: E402
from ottd import Bridge, DEFAULT_RUN_ROOT, Instance  # noqa: E402


class CheckFailed(AssertionError):
    pass


class Context:
    def __init__(self, name: str, out: Path):
        self.name = name
        self.out = out
        self.instances: list[Instance] = []
        self.clusters: list[Cluster] = []
        self.checks: list[dict] = []
        self.screenshots: list[str] = []
        self.notes: list[str] = []

    def solo(self, name: str = "solo", mode: str = "headless", args: list[str] | None = None, **kwargs) -> Instance:
        inst = Instance(name, mode=mode, root=self.out / "instances", **kwargs)
        self.instances.append(inst)
        inst.start(*(args or []))
        return inst

    def cluster(self, clients: int = 2, **kwargs) -> Cluster:
        cluster = Cluster(clients=clients, session="cluster", root=self.out / "instances", **kwargs)
        self.clusters.append(cluster)
        self.instances += cluster.instances
        cluster.start()
        return cluster

    def check(self, condition: bool, message: str, soft: bool = False) -> bool:
        self.checks.append({"ok": bool(condition), "message": message})
        print(f"  [{'PASS' if condition else 'FAIL'}] {message}")
        if not condition and not soft:
            raise CheckFailed(message)
        return bool(condition)

    def check_eq(self, actual, expected, message: str, soft: bool = False) -> bool:
        return self.check(actual == expected, f"{message} (expected {expected!r}, got {actual!r})", soft)

    def screenshot(self, bridge: Bridge, label: str, kind: str = "screen") -> str:
        path = bridge.screenshot(self.out / "screenshots" / f"{len(self.screenshots):02}_{label}.png", kind)
        self.screenshots.append(path)
        return path

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  note: {text}")

    def health_problems(self) -> list[str]:
        problems = []
        for c in self.clusters:
            problems += c.health()
        for inst in self.instances:
            if inst.proc is not None and not inst.alive() and inst.proc.returncode not in (0, None):
                problems.append(f"{inst.name}: exited with code {inst.proc.returncode}")
        return list(dict.fromkeys(problems))


def run_scenario(path: Path, out_root: Path, keep: bool) -> dict:
    name = path.stem
    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location(f"scenario_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    ctx = Context(name, out)
    result = {"scenario": name, "file": str(path), "ok": False, "error": None}
    start = time.monotonic()
    print(f"== {name}")
    try:
        module.run(ctx)
        problems = ctx.health_problems()
        if problems:
            raise CheckFailed("health problems: " + "; ".join(problems[:10]))
        result["ok"] = True
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()
        print(result["traceback"])
    finally:
        if not result["ok"]:
            try:
                instances = {i.name: {"dir": str(i.dir), "port": i.port if i.alive() else None} for i in ctx.instances}
                collect(instances, out / "collected")
                print(f"  collected diagnostics in {out / 'collected'}")
            except Exception as e:  # noqa: BLE001
                print(f"  collector failed: {e}")
        if not keep:
            for c in ctx.clusters:
                c.stop()
            for inst in ctx.instances:
                inst.stop()
        else:
            print("  --keep: instances left running:", ", ".join(f"{i.name}:{i.port}" for i in ctx.instances if i.alive()))
    result.update({"seconds": round(time.monotonic() - start, 1), "checks": ctx.checks, "screenshots": ctx.screenshots,
                   "notes": ctx.notes})
    (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"== {name}: {'OK' if result['ok'] else 'FAILED'} ({result['seconds']}s)")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="+")
    ap.add_argument("--out", default=str(DEFAULT_RUN_ROOT / "scenarios"))
    ap.add_argument("--keep", action="store_true", help="leave instances running for manual poking")
    args = ap.parse_args()
    results = [run_scenario(Path(s), Path(args.out), args.keep) for s in args.scenarios]
    failed = [r["scenario"] for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} scenarios passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
