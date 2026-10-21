#!/usr/bin/env python3
"""
Benchmark of the house tile loop with many stations around large towns (temporary, for the perf-town-stations workflow).

One headless single player game on a generated map with fixed seed: every town is a city at 10x size, the local company
scatters separate drive-through bus stations over the roads of the largest towns, then the game runs in real time while
the 'fps' console command is sampled. The same seed and commands give the same game on every build, so two builds can
be compared on identical work.

  python utils/debug_harness/perf_town_stations.py --exe build/openttd --out bench.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ottd import DEFAULT_RUN_ROOT, Instance  # noqa: E402
from transport import HOUSE, Builder, GameMap  # noqa: E402

FPS_TIMES_RE = re.compile(r"^\s*(.+?) times: ([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")
KEYS = {"game loop": "game_loop_ms", "gl landscape ticks": "gl_landscape_ticks_ms", "gl station ticks": "gl_station_ticks_ms"}


def percentile(vals: list[float], p: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", help="game binary (default: build/openttd of this checkout)")
    ap.add_argument("--out", required=True, help="result JSON")
    ap.add_argument("--name", default="perf_town_stations", help="instance directory name")
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--map-bits", type=int, default=10)
    ap.add_argument("--towns", type=int, default=3, help="number of largest towns to put stations around")
    ap.add_argument("--stations", type=int, default=300, help="bus stations to build in total")
    ap.add_argument("--warmup", type=float, default=20.0, help="seconds of game before sampling")
    ap.add_argument("--seconds", type=float, default=180.0, help="seconds of sampling")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between fps samples")
    a = ap.parse_args()

    config = {
        "game_creation": {"map_x": a.map_bits, "map_y": a.map_bits, "starting_year": 1990, "landscape": "temperate",
                          "variety": 0},
        "difficulty": {"terrain_type": 0, "quantity_sea_lakes": 0, "number_towns": 2, "industry_density": 3,
                       "vehicle_breakdowns": 0, "max_no_competitors": 0},
        "economy": {"larger_towns": 1, "initial_city_size": 10, "town_growth_rate": 4},
    }
    inst = Instance(a.name, mode="headless", root=DEFAULT_RUN_ROOT / "perf", exe=a.exe, resolution=(320, 240),
                    config=config)
    result: dict = {"seed": a.seed, "map_bits": a.map_bits}
    try:
        inst.start("-g", "-G", str(a.seed), wait=300)
        b = inst.bridge
        b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=300, what="new game")
        b.docmd("MoneyCheat", 0, 500_000_000)

        gmap = GameMap.fetch(b)
        mx = gmap.sx
        houses = {i for i, t in enumerate(gmap.type) if t == HOUSE}
        towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]

        def size(xy: tuple[int, int]) -> int:
            return sum(gmap.tile(x, y) in houses for x in range(max(0, xy[0] - 12), min(gmap.sx, xy[0] + 13))
                       for y in range(max(0, xy[1] - 12), min(gmap.sy, xy[1] + 13)))

        big = sorted(towns, key=lambda xy: -size(xy))[:a.towns]
        t0 = time.monotonic()
        stations = Builder(b, log=print).build_town_stops(big, a.stations, radius=24, spacing=2, seed=a.seed)
        print(f"built {len(stations)} bus stations around {big} in {time.monotonic() - t0:.1f}s", flush=True)
        result.update({"towns": len(towns), "house_tiles": len(houses), "big_towns": big, "stations": len(stations)})

        time.sleep(a.warmup)
        samples: dict[str, list[float]] = {k: [] for k in KEYS.values()}
        tick0 = b.info()["tick_counter"]
        t0 = time.monotonic()
        while time.monotonic() - t0 < a.seconds:
            time.sleep(a.interval)
            for line in b.console("fps"):
                if (m := FPS_TIMES_RE.match(line)) and (key := KEYS.get(m.group(1).strip().lower())):
                    samples[key].append(float(m.group(2)))  # average of the last 64 ticks
        result["ticks"] = b.info()["tick_counter"] - tick0
        for key, vals in samples.items():
            if vals:
                result[f"{key}_avg"] = round(statistics.mean(vals), 4)
                result[f"{key}_p95"] = round(percentile(vals, 95), 4)
                result[f"{key}_samples"] = len(vals)
    finally:
        inst.stop()
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
