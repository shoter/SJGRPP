#!/usr/bin/env python3
"""
TEMPORARY perf workload for the "trains without a destination / lost trains" PR, used by
.github/workflows/perf-lost-trains.yml. Removed together with that workflow before the PR is merged.

  perf_lost_trains.py run --exe <openttd> --out <file.json>     targeted workload: tick times (and YAPF node counts)
  perf_lost_trains.py job --results <dir> --out <file.json>     PR - base per metric for one CI job
  perf_lost_trains.py report --jobs <dir> [--comment <file.md>] average of the jobs' differences, as markdown

Targeted workload: the 16x16 rail grid of scenarios/train_pathfinder_budget.py (all six tracks at every crossing), path
reservation and realistic braking on, ORDERLESS trains without orders and LOST trains with an order for a station that is
not connected. After a warm-up the game runs WINDOWS x 512 ticks in lockstep; the console 'fps' averages over 512 ticks
are sampled after every window. A second, shorter run with -d yapf=3 counts the pathfinder searches and nodes (counts are
deterministic; that run is not timed).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scenarios"))

ORDERLESS = 16
LOST = 6
WINDOWS = 12
WINDOW_TICKS = 512
COUNT_TICKS = 3000
FPS_RE = re.compile(r"^\s*(Game loop|GL train ticks) times:\s*([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")
KEYS = {"Game loop": "game_loop_ms", "GL train ticks": "train_ticks_ms"}

# Metrics compared between base and PR: (source, key, label). Lower is better for all of them.
METRICS = [
    ("targeted", "game_loop_ms_avg", "targeted: game loop avg (ms)"),
    ("targeted", "game_loop_ms_p95", "targeted: game loop p95 (ms)"),
    ("targeted", "train_ticks_ms_avg", "targeted: train ticks avg (ms)"),
    ("targeted", "train_ticks_ms_p95", "targeted: train ticks p95 (ms)"),
    ("targeted", "wall_ms_per_tick", "targeted: wall time per tick, incl. drawing (ms)"),
    ("targeted", "yapf_nodes", "targeted: rail pathfinder nodes in 3000 ticks"),
    ("targeted", "yapf_searches", "targeted: rail pathfinder searches in 3000 ticks"),
    ("quick", "game_loop_ms_avg", "quick full test: game loop avg (ms)"),
    ("quick", "game_loop_ms_p95", "quick full test: game loop p95 (ms)"),
    ("quick", "gl_train_ticks_ms_avg", "quick full test: train ticks avg (ms)"),
    ("quick", "gl_train_ticks_ms_p95", "quick full test: train ticks p95 (ms)"),
]
MARKER = "<!-- perf-lost-trains -->"


class Ctx:
    """The bits of the scenario context Grid uses."""

    def note(self, text: str) -> None:
        print(f"  note: {text}", flush=True)

    def check(self, cond, message: str, soft: bool = False) -> bool:
        if not cond:
            raise AssertionError(message)
        return True


def start(exe: str, root: Path, name: str, debug: str = ""):
    from ottd import Instance
    from train_pathfinder_budget import CONFIG, DAY_TICKS, RAILTYPE_RAIL, Grid
    from transport import Builder

    config = {k: dict(v) for k, v in CONFIG.items()}
    config["pf"]["reserve_paths"] = "true"
    inst = Instance(name, mode="headless", lockstep=True, root=root, exe=Path(exe).resolve(), config=config, debug=debug)
    inst.start("-g", "-G", "777")
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=180, what="game start")
    b.console("setting vehicle.train_braking_model 1")
    b.step(2)
    builder = Builder(b, log=lambda s: None)
    builder.give_money(500_000_000)
    grid = Grid(Ctx(), b, builder)
    grid.build()
    station = grid.build_station()
    loco = builder.pick_engine("rail", wagon=False, railtypes=lambda e: e["railtypes"] & (1 << RAILTYPE_RAIL))["id"]
    for i in range(ORDERLESS + LOST):
        train = builder.build_vehicle(grid.depot, loco)
        if i >= ORDERLESS:
            builder.order(train, "station", station)
        builder.start_vehicle(train)
        b.step(DAY_TICKS // 2)
    b.step(20 * DAY_TICKS)   # warm-up: everything left the depot, the lost trains are flagged
    return inst, b


def fps(b) -> dict[str, float]:
    out = {}
    for line in b.console("fps"):
        if m := FPS_RE.match(line):
            out[KEYS[m.group(1)]] = float(m.group(4))   # the 512 data points average
    return out


def p95(vals: list[float]) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]


def cmd_run(a) -> int:
    from train_pathfinder_budget import Searches

    root = Path(a.root).resolve()
    result: dict = {}
    inst, b = start(a.exe, root, "timed")
    samples: dict[str, list[float]] = {"game_loop_ms": [], "train_ticks_ms": []}
    walls = []
    try:
        for _ in range(WINDOWS):
            t0 = time.perf_counter()
            b.step(WINDOW_TICKS)
            walls.append((time.perf_counter() - t0) * 1000 / WINDOW_TICKS)
            for k, v in fps(b).items():
                samples[k].append(v)
        result["vehicles"] = len(b.objs("vehicle"))
    finally:
        inst.stop()
    for k, vals in samples.items():
        result[f"{k}_avg"] = round(statistics.mean(vals), 4)
        result[f"{k}_p95"] = round(p95(vals), 4)
        result[f"{k}_samples"] = vals
    result["wall_ms_per_tick"] = round(statistics.mean(walls), 4)

    inst, b = start(a.exe, root, "counted", debug="yapf=3")
    try:
        searches = Searches(b)
        nodes = count = 0
        for _ in range(0, COUNT_TICKS, 200):
            b.step(200)
            for found in searches.collect().values():
                count += len(found)
                nodes += sum(s["closed"] for s in found)
    finally:
        inst.stop()
    result["yapf_nodes"] = nodes
    result["yapf_searches"] = count
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if not k.endswith("_samples")}), flush=True)
    return 0


def load_runs(results: Path) -> dict[str, dict[str, list[float]]]:
    """{"base"|"pr": {"source:key": [value per run]}} from <label>_<n>_targeted.json and <label>_<n>_quick/report.json."""
    runs: dict[str, dict[str, list[float]]] = {"base": {}, "pr": {}}
    for label in runs:
        for f in sorted(results.glob(f"{label}_*_targeted.json")):
            for k, v in json.loads(f.read_text(encoding="utf-8")).items():
                if isinstance(v, (int, float)):
                    runs[label].setdefault(f"targeted:{k}", []).append(v)
        for f in sorted(results.glob(f"{label}_*_quick/report.json")):
            report = json.loads(f.read_text(encoding="utf-8"))
            for k, v in report.get("perf", {}).items():
                if isinstance(v, (int, float)):
                    runs[label].setdefault(f"quick:{k}", []).append(v)
            runs[label].setdefault("quick:ok", []).append(1 if report.get("ok") else 0)
    return runs


def cmd_job(a) -> int:
    runs = load_runs(Path(a.results))
    out = {"base": {}, "pr": {}, "diff": {}}
    for source, key, _ in METRICS:
        k = f"{source}:{key}"
        base, pr = runs["base"].get(k), runs["pr"].get(k)
        if base and pr:
            out["base"][k] = statistics.mean(base)
            out["pr"][k] = statistics.mean(pr)
            out["diff"][k] = out["pr"][k] - out["base"][k]
    out["quick_ok"] = {label: runs[label].get("quick:ok", []) for label in runs}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


def fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.3f}"


def cmd_report(a) -> int:
    jobs = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(Path(a.jobs).rglob("job_*.json"))]
    lines = [MARKER, "## Perf: trains without a destination / lost trains (temporary workflow)", "",
             f"{len(jobs)} parallel jobs; each built the PR head and its base with the same flags and ran both on the same "
             "runner, interleaved (base, PR, base, PR). Per metric each job takes PR − base; the table shows the average of "
             "the jobs' differences and their range. Lower is better for every metric.", "",
             f"Targeted workload: 16×16-line rail grid (all six tracks at every crossing), path reservation + realistic braking, "
             f"{ORDERLESS} trains without orders and {LOST} lost trains; {WINDOWS}×{WINDOW_TICKS} ticks timed in lockstep "
             f"(`fps` 512-tick averages), node counts from a separate `-d yapf=3` run of {COUNT_TICKS} ticks.", "",
             "| Metric | base | PR | PR − base | % | range of PR − base over jobs |", "|---|---:|---:|---:|---:|---|"]
    for source, key, label in METRICS:
        k = f"{source}:{key}"
        diffs = [j["diff"][k] for j in jobs if k in j["diff"]]
        if not diffs:
            continue
        base = statistics.mean(j["base"][k] for j in jobs if k in j["base"])
        pr = statistics.mean(j["pr"][k] for j in jobs if k in j["pr"])
        d = statistics.mean(diffs)
        pct = f"{d / base * 100:+.1f}%" if base else "-"
        lines.append(f"| {label} | {fmt(base)} | {fmt(pr)} | {'+' if d >= 0 else ''}{fmt(d)} | {pct} | "
                     f"{fmt(min(diffs))} … {fmt(max(diffs))} |")
    oks = [ok for j in jobs for label in ("base", "pr") for ok in j.get("quick_ok", {}).get(label, [])]
    lines += ["", f"Quick full tests passed: {sum(oks)}/{len(oks)}."]
    text = "\n".join(lines) + "\n"
    if a.comment:
        Path(a.comment).write_text(text, encoding="utf-8")
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--exe", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--root", default="perf_runs")
    j = sub.add_parser("job")
    j.add_argument("--results", required=True)
    j.add_argument("--out", required=True)
    p = sub.add_parser("report")
    p.add_argument("--jobs", required=True)
    p.add_argument("--comment")
    a = ap.parse_args()
    return {"run": cmd_run, "job": cmd_job, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
