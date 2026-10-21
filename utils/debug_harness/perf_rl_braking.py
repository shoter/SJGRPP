#!/usr/bin/env python3
"""
TEMPORARY perf workload for the "realistic braking lookahead cutoff" PR, used by
.github/workflows/perf-braking-lookahead.yml. Removed together with that workflow before the PR is merged.

  perf_rl_braking.py prepare --exe <openttd> --save <file.sav>  build the targeted network and save it
  perf_rl_braking.py run --exe <openttd> --save <file.sav> --out <file.json>   tick times on that save
  perf_rl_braking.py job --results <dir> --out <file.json>      PR - base per metric for one CI job
  perf_rl_braking.py report --jobs <dir> [--comment <file.md>]  average of the jobs' differences, as markdown

Targeted workload: a 512x512 hilly map with realistic braking; as many long rail routes as fit (unsignalled single track
between distant towns, 200+ tiles), one train with 6 wagons on each. Without signals on the line each train reserves to
the far station, so its lookahead holds every curve, slope-crossing speed change and station of the whole line: long
item lists, which is what the change shortens. The save is made once per job (with the base build) and both builds load
the same save. After a warm-up the game runs WINDOWS x 512 ticks in lockstep; the console 'fps' averages over 512 ticks
are sampled after every window.
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

ROUTES = 32
WINDOWS = 24
WINDOW_TICKS = 512
CONFIG = {
    "game_creation": {"map_x": 9, "map_y": 9, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 2, "number_towns": 3, "max_no_competitors": 0, "vehicle_breakdowns": 0},
}
FPS_RE = re.compile(r"^\s*(Game loop|GL train ticks) times:\s*([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")
KEYS = {"Game loop": "game_loop_ms", "GL train ticks": "train_ticks_ms"}

# Metrics compared between base and PR: (source, key, label). Lower is better for all of them.
METRICS = [
    ("targeted", "train_ticks_ms_avg", "targeted: train ticks avg (ms)"),
    ("targeted", "train_ticks_ms_p95", "targeted: train ticks p95 (ms)"),
    ("targeted", "game_loop_ms_avg", "targeted: game loop avg (ms)"),
    ("targeted", "game_loop_ms_p95", "targeted: game loop p95 (ms)"),
    ("targeted", "wall_ms_per_tick", "targeted: wall time per tick, incl. drawing (ms)"),
    ("quick", "game_loop_ms_avg", "quick full test: game loop avg (ms)"),
    ("quick", "game_loop_ms_p95", "quick full test: game loop p95 (ms)"),
    ("quick", "gl_train_ticks_ms_avg", "quick full test: train ticks avg (ms)"),
    ("quick", "gl_train_ticks_ms_p95", "quick full test: train ticks p95 (ms)"),
]
MARKER = "<!-- perf-braking-lookahead -->"


def start(exe: str, root: Path, name: str, args: list[str]):
    from ottd import Instance

    inst = Instance(name, mode="headless", lockstep=True, root=root, exe=Path(exe).resolve(), config=CONFIG)
    inst.start(*args)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=300, what="game start")
    return inst, b


def cmd_prepare(a) -> int:
    from transport import Builder

    inst, b = start(a.exe, Path(a.root).resolve(), "prepare", ["-g", "-G", "777"])
    try:
        b.console("setting vehicle.train_braking_model 1")
        b.step(2)
        builder = Builder(b, log=lambda s: None)
        builder.give_money(2_000_000_000)
        mx = b.info()["map"]["x"]
        towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
        dist = lambda p: abs(p[0][0] - p[1][0]) + abs(p[0][1] - p[1][1])  # noqa: E731
        pairs = sorted(((x, y) for i, x in enumerate(towns) for y in towns[i + 1:] if 120 <= dist((x, y)) <= 260),
                       key=dist, reverse=True)
        used: set = set()
        lengths = []
        for x, y in pairs:
            if len(lengths) >= ROUTES:
                break
            if x in used or y in used:
                continue
            try:
                route = builder.build_rail_route(x, y, wagons=6)
            except Exception as e:  # noqa: BLE001
                print(f"route {x} -> {y} failed: {e}", flush=True)
                continue
            used.update((x, y))
            lengths.append(route.path_length)
            print(f"route {len(lengths)}: {dist((x, y))} tiles apart, track {route.path_length}", flush=True)
        b.step(74 * 20)   # warm-up: every train has left its depot and runs on its line
        b.console(f"save {Path(a.save).resolve()}")
        print(json.dumps({"routes": len(lengths), "track_tiles": sum(lengths)}), flush=True)
    finally:
        inst.stop()
    save = Path(a.save)
    if not save.exists() and save.with_name(save.name + ".sav").exists():
        save.with_name(save.name + ".sav").rename(save)
    return 0 if save.exists() else 1


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
    result: dict = {}
    inst, b = start(a.exe, Path(a.root).resolve(), "timed", ["-g", str(Path(a.save).resolve())])
    samples: dict[str, list[float]] = {"game_loop_ms": [], "train_ticks_ms": []}
    walls = []
    try:
        b.step(WINDOW_TICKS)
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
    prep = Path(a.results) / "prepare.json"
    if prep.exists():
        out["prepare"] = json.loads(prep.read_text(encoding="utf-8"))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


def fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.4f}"


def cmd_report(a) -> int:
    jobs = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(Path(a.jobs).rglob("job_*.json"))]
    preps = [j["prepare"] for j in jobs if "prepare" in j]
    net = (f"{statistics.mean(p['routes'] for p in preps):.0f} routes, "
           f"{statistics.mean(p['track_tiles'] for p in preps):,.0f} track tiles" if preps else "see the job logs")
    lines = [MARKER, "## Perf: realistic braking lookahead cutoff (temporary workflow)", "",
             f"{len(jobs)} parallel jobs; each built the PR head and its base with the same flags and ran both on the same "
             "runner, interleaved (base, PR, base, PR). Per metric each job takes PR − base; the table shows the average of "
             "the jobs' differences and their range. Lower is better for every metric.", "",
             f"Targeted workload: 512×512 hilly map, realistic braking, long unsignalled single-track routes ({net}), one "
             f"train each reserving to its far station; one save per job loaded by both builds; {WINDOWS}×{WINDOW_TICKS} "
             "ticks timed in lockstep (`fps` 512-tick averages). Quick full test: standard `quick` profile with "
             "`--setting vehicle.train_braking_model=1`.", "",
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
    p = sub.add_parser("prepare")
    p.add_argument("--exe", required=True)
    p.add_argument("--save", required=True)
    p.add_argument("--root", default="perf_runs")
    r = sub.add_parser("run")
    r.add_argument("--exe", required=True)
    r.add_argument("--save", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--root", default="perf_runs")
    j = sub.add_parser("job")
    j.add_argument("--results", required=True)
    j.add_argument("--out", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--jobs", required=True)
    rep.add_argument("--comment")
    a = ap.parse_args()
    return {"prepare": cmd_prepare, "run": cmd_run, "job": cmd_job, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
