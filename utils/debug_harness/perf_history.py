#!/usr/bin/env python3
"""
Performance history of the full tests on main, and the comparison of a pull request against it.

    perf_history.py record  <results dir> --history history.json --sha SHA [--date ISO] [--subject TEXT] [--run-url URL]
    perf_history.py compare <results dir> --history history.json [--base-sha SHA] [--expect quick,...] [--out perf.md]
    perf_history.py site    --history history.json --out-dir <dir> [--repo-url URL]

<results dir> holds full_test.py report.json files (downloaded 'game-test-<name>' artifacts), as for ci_report.py.

record   appends (or replaces) the entry of one main commit: per test whether it passed and its perf summary.
compare  renders the PR comment: every metric of the PR run against the main run of the PR's base commit (else the
         latest main run), flagged only when the change is larger than the run-to-run noise seen on main, plus Mermaid
         trend charts of the last main runs with the PR as the last point.
site     writes the README.md and SVG trend charts of the perf-history branch (the main README embeds the charts).

Timings come from shared CI machines: each full test runs several times in parallel and its numbers are the average of
those runs (ci_report.merge_runs), and a change is only flagged when it exceeds max(relative floor, absolute floor,
2 standard deviations of the recent main runs).
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_report import load_results  # noqa: E402

HISTORY_VERSION = 1
MAX_ENTRIES = 1000
TREND_RUNS = 20       # main runs in the PR comment charts
CHART_RUNS = 40       # main runs in the perf-history SVG charts
NOISE_RUNS = 10       # main runs used to estimate the noise band
REL_FLOOR = 0.05      # changes below 5% are never flagged

# key, label, unit, number format, better ("lower" / "higher" / None = context only), absolute noise floor
METRICS = [
    ("speed_ratio_median", "Game speed vs real time (median)", "x", "{:.2f}", "higher", 0.02),
    ("game_loop_ms_avg", "Game loop avg", "ms", "{:.2f}", "lower", 0.05),
    ("game_loop_ms_p95", "Game loop p95", "ms", "{:.2f}", "lower", 0.1),
    ("gl_train_ticks_ms_avg", "Train ticks avg", "ms", "{:.3f}", "lower", 0.02),
    ("gl_road_vehicle_ticks_ms_avg", "Road vehicle ticks avg", "ms", "{:.3f}", "lower", 0.02),
    ("gl_ship_ticks_ms_avg", "Ship ticks avg", "ms", "{:.3f}", "lower", 0.02),
    ("gl_aircraft_ticks_ms_avg", "Aircraft ticks avg", "ms", "{:.3f}", "lower", 0.02),
    ("gl_station_ticks_ms_avg", "Station ticks avg", "ms", "{:.3f}", "lower", 0.02),
    ("gl_landscape_ticks_ms_avg", "Landscape ticks avg", "ms", "{:.3f}", "lower", 0.02),
    ("server_mem_mb_max", "Server memory max", "MB", "{:.1f}", "lower", 5.0),
    ("server_mem_mb_growth", "Server memory growth", "MB", "{:+.1f}", "lower", 5.0),
    ("command_ms_avg", "Command round trip avg", "ms", "{:.0f}", "lower", 20.0),
    ("command_ms_p95", "Command round trip p95", "ms", "{:.0f}", "lower", 30.0),
    ("join_seconds_max", "Join / map download (max)", "s", "{:.1f}", "lower", 2.0),
    ("vehicles_max", "Vehicles (max)", "", "{:,.0f}", None, 0.0),
    ("commands", "Commands issued by clients", "", "{:,.0f}", None, 0.0),
]
METRIC = {m[0]: m for m in METRICS}
# Metrics charted in the PR comment and on the perf-history branch.
TREND_METRICS = ["game_loop_ms_avg", "game_loop_ms_p95", "server_mem_mb_max", "command_ms_p95"]


# ---- data ----

def perf_of(result: dict) -> dict[str, float]:
    """The numeric perf metrics of one full test result (ci_report normalised form)."""
    p = result.get("perf") or {}
    out = {k: float(v) for k, v in p.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    joins = p.get("join_seconds") or {}
    if isinstance(joins, dict) and joins:
        out["join_seconds_max"] = float(max(joins.values()))
    return out


def full_results(results_dir: Path) -> dict[str, dict]:
    """test name -> {"ok", "seconds", "perf", "runs"} for the full tests (not scenarios) in a results directory.

    perf is the average of the test's parallel runs (ci_report.merge_runs)."""
    return {name: {"ok": bool(r["ok"]), "seconds": r.get("seconds"), "perf": perf_of(r), "runs": r.get("runs", 1)}
            for name, r in load_results(results_dir).items() if r["kind"] == "full"}


def load_history(path: Path) -> dict:
    if path.exists() and path.stat().st_size:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("entries", [])
        return data
    return {"version": HISTORY_VERSION, "entries": []}


def save_history(history: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def record(history: dict, results: dict[str, dict], sha: str, date: str | None, subject: str,
           run_url: str | None) -> dict:
    """Add the entry of one main commit (replacing an older entry of the same commit), oldest first."""
    entry = {"sha": sha, "date": date or datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "subject": subject.splitlines()[0] if subject else "", "run_url": run_url, "tests": results}
    entries = [e for e in history["entries"] if e["sha"] != sha] + [entry]
    history["entries"] = entries[-MAX_ENTRIES:]
    history["version"] = HISTORY_VERSION
    return entry


def runs_of(history: dict, test: str) -> list[dict]:
    """Main entries in which `test` passed, oldest first."""
    return [e for e in history["entries"] if e.get("tests", {}).get(test, {}).get("ok")]


def series(runs: list[dict], test: str, key: str) -> list[tuple[dict, float]]:
    return [(e, e["tests"][test]["perf"][key]) for e in runs if key in e["tests"][test].get("perf", {})]


def base_run(history: dict, test: str, base_sha: str | None) -> dict | None:
    runs = runs_of(history, test)
    if base_sha:
        for e in reversed(runs):
            if e["sha"] == base_sha:
                return e
    return runs[-1] if runs else None


def noise_band(values: list[float], base: float, abs_floor: float) -> float:
    """Smallest absolute change that is flagged."""
    spread = 2 * statistics.stdev(values) if len(values) >= 3 else 0.0
    return max(REL_FLOOR * abs(base), abs_floor, spread)


def verdict(key: str, base: float, value: float, band: float) -> str:
    better = METRIC[key][4]
    diff = value - base
    if better is None or abs(diff) <= band:
        return "≈"
    improved = diff < 0 if better == "lower" else diff > 0
    return "🟢 better" if improved else "🔴 worse"


def fmt(key: str, value: float) -> str:
    _, _, unit, number, _, _ = METRIC[key]
    text = number.format(value)
    return f"{text} {unit}".strip() if unit != "x" else f"{text}x"


def delta(base: float, value: float) -> str:
    if base == 0:
        return "–" if value == 0 else f"{value:+.3g}"
    return f"{(value - base) / abs(base) * 100:+.1f}%"


def short(sha: str) -> str:
    return sha[:7]


def runs_label(test: dict) -> str:
    """'3 parallel runs' / '1 run' for a test result or history entry (entries before repeats have no 'runs')."""
    runs = test.get("runs", 1)
    return f"{runs} parallel runs" if runs > 1 else "1 run"


# ---- PR comment ----

def mermaid_chart(title: str, labels: list[str], values: list[float], unit: str) -> list[str]:
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.15 or abs(hi) * 0.1 or 1.0
    y_lo = max(0.0, lo - pad) if lo >= 0 else lo - pad
    return ["```mermaid", "xychart-beta", f'    title "{title}"',
            "    x-axis [" + ", ".join(f'"{l}"' for l in labels) + "]",
            f'    y-axis "{unit}" {y_lo:.4g} --> {hi + pad:.4g}',
            "    line [" + ", ".join(f"{v:.4g}" for v in values) + "]", "```"]


def render_compare(history: dict, pr: dict[str, dict], base_sha: str | None, expect: list[str],
                   run_url: str | None, repo_url: str | None) -> str:
    names = [n for n in expect if n in pr] + sorted(n for n in pr if n not in expect)
    out = ["## 📈 Full test performance vs `main`", ""]
    if not names:
        out += ["No full test results in this run (the test jobs crashed, timed out or were not selected).", ""]
    elif not history["entries"]:
        out += ["No performance history from `main` yet: it is recorded after each merge to `main` "
                "(workflow *Performance history*). PR values only:", ""]

    def commit_link(e: dict) -> str:
        return f"[`{short(e['sha'])}`]({repo_url}/commit/{e['sha']})" if repo_url else f"`{short(e['sha'])}`"

    summary_rows = []
    sections = []
    for name in names:
        r = pr[name]
        base = base_run(history, name, base_sha)
        runs = runs_of(history, name)
        sec = [f"<details{' open' if base else ''}><summary><b>{name}</b></summary>", ""]
        if not r["ok"]:
            sec += ["⚠️ This test failed in the PR, so its timings may not be comparable.", ""]
        if base is None:
            sec += [f"No passing `main` run of `{name}` to compare with yet.", ""]
            sec += ["| Metric | PR |", "|---|---|"]
            sec += [f"| {METRIC[k][1]} | {fmt(k, r['perf'][k])} |" for k, *_ in METRICS if k in r["perf"]]
            sections += sec + ["", "</details>", ""]
            summary_rows.append(f"| `{name}` | – | no `main` baseline | – | – |")
            continue
        exact = base["sha"] == base_sha
        sec += [f"Compared with `main` at {commit_link(base)}"
                + ("" if exact or not base_sha else f" (latest recorded; base `{short(base_sha)}` has no run)")
                + (f", [run]({base['run_url']})" if base.get("run_url") else "") + "."
                + f" Values are averages of {runs_label(r)} (PR) and {runs_label(base['tests'][name])} (`main`).", ""]
        sec += ["| Metric | main | PR | Δ | |", "|---|---|---|---|---|"]
        worse = better = 0
        for key, label, _, _, direction, abs_floor in METRICS:
            bperf, pperf = base["tests"][name].get("perf", {}), r["perf"]
            if key not in bperf or key not in pperf:
                continue
            recent = [v for _, v in series(runs, name, key)][-NOISE_RUNS:]
            band = noise_band(recent, bperf[key], abs_floor)
            v = verdict(key, bperf[key], pperf[key], band)
            worse += v.startswith("🔴")
            better += v.startswith("🟢")
            sec.append(f"| {label} | {fmt(key, bperf[key])} | {fmt(key, pperf[key])} | {delta(bperf[key], pperf[key])} "
                       f"| {v if direction else '_workload_'} |")
        sec.append("")
        trend = [key for key in TREND_METRICS if key in r["perf"] and series(runs, name, key)]
        for i, key in enumerate(trend):
            points = series(runs, name, key)[-TREND_RUNS:]
            labels = [short(e["sha"]) for e, _ in points] + ["PR"]
            values = [v for _, v in points] + [r["perf"][key]]
            chart = mermaid_chart(f"{name}: {METRIC[key][1]}", labels, values, METRIC[key][2] or "value")
            if i == 0:
                sec += chart + [""]
            else:
                if i == 1:
                    sec += [f"<details><summary>More trends ({len(trend) - 1})</summary>", ""]
                sec += chart + [""]
        if len(trend) > 1:
            sec += ["</details>", ""]
        sections += sec + ["</details>", ""]
        gl = (fmt("game_loop_ms_avg", base["tests"][name]["perf"]["game_loop_ms_avg"]) + " → "
              + fmt("game_loop_ms_avg", r["perf"]["game_loop_ms_avg"])
              if "game_loop_ms_avg" in r["perf"] and "game_loop_ms_avg" in base["tests"][name].get("perf", {}) else "–")
        status = "🔴" if worse else ("🟢" if better else "≈")
        summary_rows.append(f"| `{name}` | {commit_link(base)} | {gl} | {status} {worse} worse / {better} better | "
                            f"{len(runs)} |")
    if summary_rows:
        out += ["| Test | main | Game loop avg | Changes beyond noise | main runs |", "|---|---|---|---|---|"]
        out += summary_rows + [""]
    out += sections
    out.append("_Timings come from shared CI runners. A change is flagged only when it is larger than "
               f"{REL_FLOOR:.0%}, a per-metric floor and twice the standard deviation of the last {NOISE_RUNS} "
               "`main` runs. Workload rows are for context._")
    if run_url:
        out += ["", f"PR run: [workflow run]({run_url})"]
    return "\n".join(out) + "\n"


# ---- perf-history branch ----

def svg_chart(history: dict, tests: list[str], key: str) -> str:
    """Small multiples: one panel per test with the trend of `key` over the last CHART_RUNS passing main runs."""
    label, unit = METRIC[key][1], METRIC[key][2]
    cols = 2
    pw, ph, gap, top = 420, 170, 20, 40
    rows = max(1, (len(tests) + cols - 1) // cols)
    width, height = cols * pw + (cols + 1) * gap, top + rows * (ph + gap)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
             f'font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="12">',
             "<style>.bg{fill:#ffffff}.fg{fill:#1f2328}.mute{fill:#59636e}.grid{stroke:#d1d9e0}.line{stroke:#0969da}"
             ".dot{fill:#0969da}.panel{fill:#f6f8fa;stroke:#d1d9e0}"
             "@media (prefers-color-scheme: dark){.bg{fill:#0d1117}.fg{fill:#f0f6fc}.mute{fill:#9198a1}"
             ".grid{stroke:#3d444d}.line{stroke:#4493f8}.dot{fill:#4493f8}.panel{fill:#151b23;stroke:#3d444d}}</style>",
             f'<rect class="bg" width="{width}" height="{height}" rx="6"/>',
             f'<text class="fg" x="{gap}" y="26" font-size="15" font-weight="600">{html.escape(label)}'
             f'{f" ({unit})" if unit else ""} on main</text>']
    for i, test in enumerate(tests):
        x0 = gap + (i % cols) * (pw + gap)
        y0 = top + (i // cols) * (ph + gap)
        parts.append(f'<rect class="panel" x="{x0}" y="{y0}" width="{pw}" height="{ph}" rx="4"/>')
        points = series(runs_of(history, test), test, key)[-CHART_RUNS:]
        title = html.escape(test)
        if points:
            title += f" - latest {html.escape(fmt(key, points[-1][1]))}"
        parts.append(f'<text class="fg" x="{x0 + 10}" y="{y0 + 18}" font-weight="600">{title}</text>')
        if not points:
            parts.append(f'<text class="mute" x="{x0 + pw / 2}" y="{y0 + ph / 2}" text-anchor="middle">no runs yet</text>')
            continue
        values = [v for _, v in points]
        lo, hi = min(values), max(values)
        pad = (hi - lo) * 0.1 or abs(hi) * 0.1 or 1.0
        lo, hi = (max(0.0, lo - pad) if lo >= 0 else lo - pad), hi + pad
        cx0, cx1, cy0, cy1 = x0 + 52, x0 + pw - 14, y0 + 30, y0 + ph - 26

        def px(j: int) -> float:
            return cx0 + (cx1 - cx0) * (j / (len(values) - 1) if len(values) > 1 else 0.5)

        def py(v: float) -> float:
            return cy1 - (cy1 - cy0) * (v - lo) / (hi - lo)

        for v in (lo, (lo + hi) / 2, hi):
            parts.append(f'<line class="grid" x1="{cx0}" x2="{cx1}" y1="{py(v):.1f}" y2="{py(v):.1f}"/>')
            parts.append(f'<text class="mute" x="{cx0 - 6}" y="{py(v) + 4:.1f}" text-anchor="end">{v:.3g}</text>')
        if len(values) > 1:
            path = " ".join(f"{px(j):.1f},{py(v):.1f}" for j, v in enumerate(values))
            parts.append(f'<polyline class="line" fill="none" stroke-width="2" points="{path}"/>')
        for j, (e, v) in enumerate(points):
            parts.append(f'<circle class="dot" cx="{px(j):.1f}" cy="{py(v):.1f}" r="2.5">'
                         f'<title>{short(e["sha"])} {html.escape(e["date"][:10])}: {html.escape(fmt(key, v))}</title></circle>')
        first, last = points[0][0], points[-1][0]
        parts.append(f'<text class="mute" x="{cx0}" y="{y0 + ph - 8}">{short(first["sha"])} {first["date"][:10]}</text>')
        parts.append(f'<text class="mute" x="{cx1}" y="{y0 + ph - 8}" text-anchor="end">'
                     f'{short(last["sha"])} {last["date"][:10]}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def all_tests(history: dict) -> list[str]:
    order = ["quick", "normal", "max-28", "sharing"]
    seen = {t for e in history["entries"] for t in e.get("tests", {})}
    return [t for t in order if t in seen] + sorted(seen - set(order))


def write_site(history: dict, out_dir: Path, repo_url: str | None) -> list[Path]:
    tests = all_tests(history)
    charts = out_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    written = []
    for key in TREND_METRICS:
        path = charts / f"{key}.svg"
        path.write_text(svg_chart(history, tests, key), encoding="utf-8")
        written.append(path)
    lines = ["# Full test performance on main", "",
             "Written by the *Performance history* workflow after every full test run on `main` "
             "(`utils/debug_harness/perf_history.py`). Raw data: [history.json](history.json).", ""]
    for key in TREND_METRICS:
        lines += [f"![{METRIC[key][1]}](charts/{key}.svg)", ""]
    table_keys = ["game_loop_ms_avg", "game_loop_ms_p95", "server_mem_mb_max", "command_ms_p95", "vehicles_max"]
    for test in tests:
        lines += [f"## {test}", "", "| Commit | Date | Result | " + " | ".join(METRIC[k][1] for k in table_keys) + " |",
                  "|---|---|---|" + "---|" * len(table_keys)]
        for e in reversed(history["entries"][-30:]):
            t = e.get("tests", {}).get(test)
            if t is None:
                continue
            sha = f"[`{short(e['sha'])}`]({repo_url}/commit/{e['sha']})" if repo_url else f"`{short(e['sha'])}`"
            res = ("✅" if t["ok"] else "❌") + (f" [run]({e['run_url']})" if e.get("run_url") else "")
            cells = [fmt(k, t["perf"][k]) if k in t.get("perf", {}) else "–" for k in table_keys]
            lines.append(f"| {sha} | {e['date'][:10]} | {res} | " + " | ".join(cells) + " |")
        lines.append("")
    readme = out_dir / "README.md"
    readme.write_text("\n".join(lines), encoding="utf-8")
    return written + [readme]


# ---- main ----

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("results")
    rec.add_argument("--history", required=True)
    rec.add_argument("--sha", required=True)
    rec.add_argument("--date")
    rec.add_argument("--subject", default="")
    rec.add_argument("--run-url")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("results")
    cmp_.add_argument("--history", required=True)
    cmp_.add_argument("--base-sha")
    cmp_.add_argument("--expect", default="")
    cmp_.add_argument("--run-url")
    cmp_.add_argument("--repo-url")
    cmp_.add_argument("--out")
    site = sub.add_parser("site")
    site.add_argument("--history", required=True)
    site.add_argument("--out-dir", required=True)
    site.add_argument("--repo-url")
    a = ap.parse_args()

    history = load_history(Path(a.history))
    if a.cmd == "record":
        results = full_results(Path(a.results))
        if not results:
            print("no full test results found, nothing recorded")
            return 0
        record(history, results, a.sha, a.date, a.subject, a.run_url)
        save_history(history, Path(a.history))
        print(f"recorded {short(a.sha)}: " + ", ".join(f"{n} {'ok' if r['ok'] else 'FAILED'}" for n, r in results.items()))
    elif a.cmd == "compare":
        text = render_compare(history, full_results(Path(a.results)), a.base_sha,
                              [e for e in a.expect.split(",") if e], a.run_url, a.repo_url)
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(text)
    else:
        for path in write_site(history, Path(a.out_dir), a.repo_url):
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
