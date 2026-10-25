"""
TEMPORARY (perf measurement of this PR; removed before merge).

  mp_report.py <dir with mp-<base|head>-<n>.json files> <out.md>

Each job ran one side (base = main, head = this PR) on its own runner. Per side, the values are averaged over the
jobs; the change is head vs base, with the smallest..largest job value of each side to show the runner spread.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

MARKER = "<!-- perf-mp-heavy -->"
ELEMENTS = ["Game loop", "GL train ticks", "GL road vehicle ticks", "GL aircraft ticks", "GL ship ticks",
            "GL station ticks", "GL landscape ticks"]


def role_values(result: dict) -> dict[str, dict[str, float]]:
    """server, mean of the clients, and every client by name."""
    inst = result["instances"]
    out = {"server": inst["server"]}
    clients = [v for k, v in inst.items() if k != "server"]
    out["clients (mean)"] = {k: statistics.mean(c[k] for c in clients if k in c)
                             for k in ELEMENTS if any(k in c for c in clients)}
    for k, v in inst.items():
        if k != "server":
            out[k] = v
    return out


def main() -> int:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    runs = {"base": [], "head": []}
    for p in sorted(src.rglob("mp-*.json")):
        r = json.loads(p.read_text())
        runs[r["label"]].append(r)
    lines = [MARKER, "## Heavy multiplayer test (Windows, MSVC): this PR vs main", "",
             f"Dedicated server + 4 headless clients on one `windows-latest` runner, real time, on a 4096x4096 map with "
             f"~8000 vehicles (road, rail, air). {len(runs['base'])} jobs ran main (base) and {len(runs['head'])} jobs "
             f"ran this PR (head), each on its own runner; values are ms per tick (average of the `fps` samples), "
             f"averaged over the jobs of each side. Lower is better.", ""]
    if not runs["base"] or not runs["head"]:
        lines.append(f"Incomplete: {len(runs['base'])} base and {len(runs['head'])} head results.")
    else:
        roles = ["server", "clients (mean)"] + sorted(k for k in role_values(runs["head"][0]) if k.startswith("client") and k != "clients (mean)")
        lines += ["| instance | element | main | this PR | change | main jobs (min..max) | PR jobs (min..max) |",
                  "|---|---|---|---|---|---|---|"]
        for role in roles:
            for el in ELEMENTS:
                b = [role_values(r)[role][el] for r in runs["base"] if el in role_values(r).get(role, {})]
                h = [role_values(r)[role][el] for r in runs["head"] if el in role_values(r).get(role, {})]
                if not b or not h or statistics.mean(b) == 0:
                    continue
                mb, mh = statistics.mean(b), statistics.mean(h)
                bold = "**" if el == "Game loop" else ""
                lines.append(f"| {role} | {bold}{el}{bold} | {mb:.3f} | {mh:.3f} | {bold}{(mh / mb - 1) * 100:+.1f}%{bold} "
                             f"({mh - mb:+.3f}) | {min(b):.3f}..{max(b):.3f} | {min(h):.3f}..{max(h):.3f} |")
        tps = {s: statistics.mean(r["ticks_per_s"] for r in runs[s]) for s in runs}
        lines += ["", f"Game speed (server ticks per second, 1x speed is about 37): main {tps['base']:.1f}, this PR {tps['head']:.1f}."]
        problems = [f"{r['label']}: {p}" for s in runs for r in runs[s] for p in r.get("problems", [])]
        if problems:
            lines += ["", "Problems reported by the harness:", ""] + [f"- {p}" for p in problems[:10]]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
