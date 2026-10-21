#!/usr/bin/env python3
"""
Render game test results (full_test.py report.json files and scenario.py result.json files) as one Markdown report,
for the GitHub Actions job summary and the PR comment.

    ci_report.py <results dir> [--expect quick,normal,...] [--title ...] [--run-url URL] [--out report.md]
    ci_report.py --matrix scenarios,quick,... --repeats 3

<results dir> contains one sub directory per test job (e.g. downloaded artifacts 'game-test-quick/...'). Tests named
in --expect without results are reported as missing (job crashed, timed out or was cancelled). A full test that ran
several times in parallel (game-test-<name>/run-<n>/...) is reported once: it passes only if every run passed, and its
performance is the average of the runs.
Exit code is 0; the test jobs themselves carry pass/fail.

--matrix prints the game test job matrix as JSON: scenarios once, each full test --repeats times.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PERF_ROWS = [
    ("speed_ratio_median", "Game speed vs real time (median)", "{:.2f}x"),
    ("speed_ratio_min", "Game speed vs real time (slowest sample)", "{:.2f}x"),
    ("game_loop_ms_avg", "Game loop avg", "{:.2f} ms"),
    ("game_loop_ms_p95", "Game loop p95", "{:.2f} ms"),
    ("gl_train_ticks_ms_avg", "Train ticks avg", "{:.3f} ms"),
    ("gl_road_vehicle_ticks_ms_avg", "Road vehicle ticks avg", "{:.3f} ms"),
    ("gl_ship_ticks_ms_avg", "Ship ticks avg", "{:.3f} ms"),
    ("gl_aircraft_ticks_ms_avg", "Aircraft ticks avg", "{:.3f} ms"),
    ("gl_landscape_ticks_ms_avg", "Landscape ticks avg", "{:.3f} ms"),
    ("server_mem_mb_max", "Server memory max", "{:.1f} MB"),
    ("server_mem_mb_growth", "Server memory growth", "{:+.1f} MB"),
    ("command_ms_avg", "Command round trip avg", "{:.0f} ms"),
    ("command_ms_p95", "Command round trip p95", "{:.0f} ms"),
    ("commands", "Commands issued by clients", "{:,}"),
    ("vehicles_max", "Vehicles (max)", "{:,}"),
]


def esc(text: str, limit: int = 400) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return text.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def matrix_jobs(tests: str, repeats: int) -> list[dict]:
    """The game test job matrix: scenarios run once, each full test `repeats` times (its perf is their average)."""
    jobs = []
    for test in (t.strip() for t in tests.split(",")):
        if not test:
            continue
        runs = 1 if test == "scenarios" else max(1, repeats)
        jobs += [{"test": test, "run": run, "runs": runs} for run in range(1, runs + 1)]
    return jobs


def mean_perf(perfs: list[dict]) -> dict:
    """Average of the perf summaries of several runs of one full test.

    Numbers are averaged over the runs that have them (kept as int when all are ints and the mean is whole), dicts of
    numbers (join_seconds) per key; anything else is taken from the first run that has it."""
    keys = list(dict.fromkeys(k for p in perfs for k in p))
    out: dict = {}
    for key in keys:
        values = [p[key] for p in perfs if key in p]
        nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums and len(nums) == len(values):
            avg = sum(nums) / len(nums)
            out[key] = int(avg) if all(isinstance(v, int) for v in nums) and avg == int(avg) else avg
        elif values and all(isinstance(v, dict) for v in values):
            out[key] = mean_perf(values)
        else:
            out[key] = values[0]
    return out


def merge_runs(runs: list[dict]) -> dict:
    """One normalised full test result from its parallel runs: passes only if every run passed, perf is the average."""
    if len(runs) == 1:
        return {**runs[0], "runs": 1}
    merged = dict(runs[0])
    merged["ok"] = all(r["ok"] for r in runs)
    merged["seconds"] = max((r["seconds"] for r in runs if r["seconds"] is not None), default=None)
    merged["checks"] = [{**c, "name": f"run {i}: {c.get('name', '')}"} for i, r in enumerate(runs, 1) for c in r["checks"]]
    merged["perf"] = mean_perf([r["perf"] for r in runs])
    merged["build_errors"] = {k: v for r in runs for k, v in (r.get("build_errors") or {}).items()}
    merged["runs"] = len(runs)
    return merged


def load_results(root: Path) -> dict[str, dict]:
    """test name -> normalised result: ok, seconds, checks, perf, routes, kind, runs.

    A full test with several reports (parallel CI runs, run-<n> directories) becomes one result: see merge_runs."""
    results: dict[str, dict] = {}
    full_runs: dict[str, list[dict]] = {}
    for report in sorted(root.rglob("report.json")):
        data = json.loads(report.read_text(encoding="utf-8"))
        group = next((p.name for p in report.parents if p.name.startswith("game-test-")), None)
        name = group.removeprefix("game-test-") if group else (data.get("args", {}).get("name") or report.parent.name)
        full_runs.setdefault(name, []).append(
            {"kind": "full", "ok": data.get("ok", False), "seconds": data.get("seconds"),
             "checks": data.get("checks", []), "perf": data.get("perf", {}), "routes": data.get("routes", {}),
             "profile": data.get("profile"), "clients": data.get("args", {}).get("clients"),
             "build_errors": data.get("build_errors", {})})
    for name, runs in full_runs.items():
        results[name] = merge_runs(runs)
    scenario_files = sorted(root.rglob("result.json"))
    by_group: dict[str, list[dict]] = {}
    for f in scenario_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if "scenario" not in data:
            continue
        group = next((p.name for p in f.parents if p.name.startswith("game-test-")), "scenarios")
        by_group.setdefault(group.removeprefix("game-test-"), []).append(data)
    for group, items in by_group.items():
        checks = []
        for item in items:
            checks += [{**c, "name": f"{item['scenario']}: {c['message']}", "severity": "fail", "detail": ""}
                       for c in item.get("checks", [])]
            if item.get("error") and not any(not c["ok"] for c in item.get("checks", [])):
                checks.append({"ok": False, "name": f"{item['scenario']}: {item['error']}", "severity": "fail", "detail": ""})
        results[group] = {"kind": "scenarios", "ok": all(i.get("ok") for i in items),
                          "seconds": sum(i.get("seconds", 0) for i in items), "checks": checks,
                          "scenarios": [{"name": i["scenario"], "ok": i.get("ok"), "seconds": i.get("seconds"),
                                         "error": i.get("error")} for i in items]}
    return results


def render(results: dict[str, dict], expect: list[str], title: str, run_url: str | None) -> str:
    names = list(dict.fromkeys(expect + sorted(results)))
    failed = [n for n in names if n not in results or not results[n]["ok"]]
    head = "✅ all passed" if not failed else f"❌ {len(failed)} of {len(names)} failed"
    out = [f"## {title} — {head}", ""]
    out += ["| Test | Result | Duration | Checks | Warnings | Notes |", "|---|---|---|---|---|---|"]
    for n in names:
        r = results.get(n)
        if r is None:
            out.append(f"| `{n}` | ⚠️ no result | – | – | – | job crashed, timed out or was cancelled |")
            continue
        checks = r["checks"]
        fails = [c for c in checks if not c["ok"] and c.get("severity", "fail") == "fail"]
        warns = [c for c in checks if not c["ok"] and c.get("severity") == "warn"]
        passed = sum(c["ok"] for c in checks)
        if r["kind"] == "full":
            p = r["perf"]
            notes = f"{r.get('clients')} clients, {p.get('vehicles_max', 0):,.0f} vehicles"
            if r.get("runs", 1) > 1:
                notes += f", {r['runs']} runs"
            if "game_loop_ms_p95" in p:
                notes += f", game loop p95 {p['game_loop_ms_p95']:.2f} ms"
        else:
            notes = f"{len(r['scenarios'])} scenarios"
        status = "✅ pass" if r["ok"] else "❌ fail"
        out.append(f"| `{n}` | {status} | {duration(r['seconds'])} | {passed}/{len(checks)} | {len(warns)} | {esc(notes)} |")
    out.append("")

    for n in names:
        r = results.get(n)
        if r is None:
            continue
        fails = [c for c in r["checks"] if not c["ok"] and c.get("severity", "fail") == "fail"]
        warns = [c for c in r["checks"] if not c["ok"] and c.get("severity") == "warn"]
        icon = "✅" if r["ok"] else "❌"
        out.append(f"<details{' open' if fails else ''}><summary><b>{icon} {n}</b></summary>")
        out.append("")
        if fails:
            out += ["**Failures**", "", "| Check | Detail |", "|---|---|"]
            out += [f"| {esc(c['name'], 120)} | {esc(c.get('detail', ''))} |" for c in fails]
            out.append("")
        if warns:
            out += ["**Warnings**", "", "| Check | Detail |", "|---|---|"]
            out += [f"| {esc(c['name'], 120)} | {esc(c.get('detail', ''))} |" for c in warns]
            out.append("")
        if r["kind"] == "scenarios":
            out += ["| Scenario | Result | Duration |", "|---|---|---|"]
            out += [f"| `{s['name']}` | {'✅' if s['ok'] else '❌ ' + esc(s.get('error') or '', 150)} | {duration(s['seconds'])} |"
                    for s in r["scenarios"]]
            out.append("")
        else:
            p = r["perf"]
            rows = [(label, fmt.format(p[key])) for key, label, fmt in PERF_ROWS if isinstance(p.get(key), (int, float))]
            joins = p.get("join_seconds") or {}
            if joins:
                rows.append(("Join / map download (max)", f"{max(joins.values()):.1f} s over {len(joins)} joins"))
            if rows:
                avg = f", average of {r['runs']} parallel runs" if r.get("runs", 1) > 1 else ""
                out += [f"**Performance** (reported only{avg})", "", "| Metric | Value |", "|---|---|"]
                out += [f"| {label} | {value} |" for label, value in rows]
                out.append("")
            kinds: dict[str, list[int]] = {}
            for routes in r["routes"].values():
                for route in routes:
                    kinds.setdefault(route["kind"], []).append(len(route["vehicles"]))
            if kinds:
                out += ["**Networks built**", "", "| Type | Routes | Vehicles |", "|---|---|---|"]
                out += [f"| {k} | {len(v)} | {sum(v)} |" for k, v in sorted(kinds.items())]
                out.append("")
            passed = [c for c in r["checks"] if c["ok"]]
            if passed:
                out.append(f"<details><summary>{len(passed)} passed checks</summary>")
                out.append("")
                out += [f"- ✅ {esc(c['name'], 150)}" + (f" — {esc(c.get('detail', ''), 200)}" if c.get("detail") else "") for c in passed]
                out += ["", "</details>", ""]
        out += ["</details>", ""]
    if run_url:
        out.append(f"Logs, perf CSVs and crash/desync bundles: [workflow run artifacts]({run_url})")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="?")
    ap.add_argument("--expect", default="", help="comma separated test names that should have results")
    ap.add_argument("--title", default="🚂 Game tests")
    ap.add_argument("--run-url")
    ap.add_argument("--out")
    ap.add_argument("--matrix", metavar="TESTS", help="print the CI job matrix (JSON) for these comma separated tests")
    ap.add_argument("--repeats", type=int, default=1, help="with --matrix: parallel runs of each full test")
    args = ap.parse_args()
    if args.matrix is not None:
        print(json.dumps(matrix_jobs(args.matrix, args.repeats), separators=(",", ":")))
        return 0
    if args.results is None:
        ap.error("the results directory is required")
    results = load_results(Path(args.results))
    text = render(results, [e for e in args.expect.split(",") if e], args.title, args.run_url)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
