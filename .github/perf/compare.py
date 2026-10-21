"""
TEMPORARY (perf measurement of this PR; removed before merge).

  compare.py job <results dir> <out.json>    one runner: base vs head per metric (means over the runs of each)
  compare.py report <dir with job json files> <out.md> [--title T]
                                              all runners: mean change, absolute change and spread across runners

Results dir layout (written by the workflow): heavy-<base|head>-<n>.json from heavy_bench.py and
quick-<base|head>-<n>/report.json from full_test.py.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

MARKER = "<!-- perf-build-flags -->"
HEAVY_FPS = ["Game loop", "GL train ticks", "GL road vehicle ticks", "GL ship ticks", "GL aircraft ticks",
             "GL station ticks", "GL landscape ticks"]
QUICK = ["game_loop_ms_avg", "game_loop_ms_p95", "gl_train_ticks_ms_avg", "gl_road_vehicle_ticks_ms_avg",
         "gl_ship_ticks_ms_avg", "gl_aircraft_ticks_ms_avg", "gl_station_ticks_ms_avg", "gl_landscape_ticks_ms_avg"]
HIGHER_IS_BETTER = {"heavy: ticks/s"}


def heavy_metrics(r: dict) -> dict[str, float]:
    out = {"heavy: CPU ms/tick": r["cpu_ms_per_tick"], "heavy: ticks/s": r["ticks_per_s"]}
    for k in HEAVY_FPS:
        if k in r["fps_ms"]:
            out[f"heavy: {k} ms"] = r["fps_ms"][k]
    return out


def quick_metrics(report: dict) -> dict[str, float]:
    perf = report.get("perf", {})
    return {f"quick: {k}": perf[k] for k in QUICK if k in perf}


def job(results: Path, out: Path) -> None:
    values: dict[str, dict[str, list[float]]] = {}
    for side in ("base", "head"):
        runs = [heavy_metrics(json.loads(p.read_text())) for p in sorted(results.glob(f"heavy-{side}-*.json"))]
        runs += [quick_metrics(json.loads(p.read_text())) for p in sorted(results.glob(f"quick-{side}-*/report.json"))]
        for run in runs:
            for k, v in run.items():
                values.setdefault(k, {}).setdefault(side, []).append(v)
    metrics = {}
    for k, sides in values.items():
        if "base" not in sides or "head" not in sides:
            continue
        base, head = statistics.mean(sides["base"]), statistics.mean(sides["head"])
        metrics[k] = {"base": base, "head": head, "diff": head - base,
                      "pct": (head / base - 1) * 100 if base else 0.0, "runs": [len(sides["base"]), len(sides["head"])]}
    out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))


def report(jobs_dir: Path, out: Path, title: str) -> None:
    jobs = [json.loads(p.read_text()) for p in sorted(jobs_dir.rglob("job-*.json"))]
    names = list(dict.fromkeys(k for j in jobs for k in j))
    lines = [MARKER, f"## {title}", "",
             f"Base = this PR's base commit, head = this PR; both built with MSVC (RelWithDebInfo, x64-windows-static) "
             f"and run on the same `windows-latest` runner, alternating. {len(jobs)} runners; the change is head vs base, "
             f"averaged over the runners (spread = smallest..largest runner).", "",
             "| metric | base | head | change | spread | runners better |", "|---|---|---|---|---|---|"]
    for k in names:
        per = [j[k] for j in jobs if k in j]
        pcts = [m["pct"] for m in per]
        better = sum(1 for p in pcts if (p > 0 if k in HIGHER_IS_BETTER else p < 0))
        mean = lambda key: statistics.mean(m[key] for m in per)  # noqa: E731
        lines.append(f"| {k} | {mean('base'):.3f} | {mean('head'):.3f} | **{statistics.mean(pcts):+.1f}%** "
                     f"({mean('diff'):+.3f}) | {min(pcts):+.1f}%..{max(pcts):+.1f}% | {better}/{len(per)} |")
    lines += ["", "heavy = null video driver, free-running ticks on a 1024x1024 save with 28 companies and 3,752 vehicles; "
              "quick = the `quick` full test (dedicated server + 3 clients, real time). Lower is better except ticks/s.", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    if sys.argv[1] == "job":
        job(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        title = sys.argv[sys.argv.index("--title") + 1] if "--title" in sys.argv else "Windows performance: this PR vs base"
        report(Path(sys.argv[2]), Path(sys.argv[3]), title)
