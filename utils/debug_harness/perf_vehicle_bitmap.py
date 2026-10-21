#!/usr/bin/env python3
"""
Benchmark of vehicle-on-tile lookups with many trains and road vehicles on a large map (temporary, for the
perf-vehicle-bitmap workflow).

One headless single player game on a generated map with fixed seed: the local company builds many rail shuttle routes
(one train each) and many bus routes (many buses each) between town pairs, then the game runs in real time while the
'fps' console command is sampled. To compare two builds on identical work, build and save the game once, then measure
the same savegame with each build.

  python utils/debug_harness/perf_vehicle_bitmap.py --exe build/openttd --out build.json --save bench.sav
  python utils/debug_harness/perf_vehicle_bitmap.py --exe build/openttd --out bench.json --load bench.sav
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ottd import DEFAULT_RUN_ROOT, Instance  # noqa: E402
from transport import Builder, BuildError, GameMap  # noqa: E402

FPS_TIMES_RE = re.compile(r"^\s*(.+?) times: ([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")
KEYS = {"game loop": "game_loop_ms", "gl train ticks": "gl_train_ticks_ms", "gl road vehicle ticks": "gl_road_vehicle_ticks_ms"}
ARRAYS = ("type", "height", "slope", "owner", "sub", "bits", "road_owner")


def percentile(vals: list[float], p: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def rss_mb(inst: Instance) -> tuple[float, float] | None:
    """Current and peak resident memory of the game (Linux only)."""
    try:
        with open(f"/proc/{inst.proc.pid}/status") as f:
            status = dict(line.split(":", 1) for line in f if ":" in line)
        return int(status["VmRSS"].split()[0]) / 1024, int(status["VmHWM"].split()[0]) / 1024
    except Exception:  # noqa: BLE001
        return None


class WindowBuilder(Builder):
    """
    Builder that fetches the whole map once and afterwards refreshes only a window around the route being built:
    full map fetches dominate the build time on large maps. Everything outside the window is unchanged since the last
    fetch except for earlier routes, which are kept up to date because each route refreshed its own window.
    """

    def __init__(self, bridge, **kw):
        super().__init__(bridge, **kw)
        info = bridge.info()["map"]
        self.sx, self.sy = info["x"], info["y"]
        data: dict = {k: [] for k in ARRAYS}
        stripe = 128
        for y in range(0, self.sy, stripe):
            part = bridge.tiles(0, y, self.sx, min(stripe, self.sy - y))
            for k in ARRAYS:
                data[k] += part.get(k, [255] * (self.sx * min(stripe, self.sy - y)))
        data.update({"x": 0, "y": 0, "w": self.sx, "h": self.sy})
        self.cache = GameMap(data, self.sx, self.sy)
        self.window: tuple[int, int, int, int] | None = None

    def fresh_map(self) -> GameMap:
        if self.window is not None:
            x0, y0, x1, y1 = self.window
            w, h = x1 - x0, y1 - y0
            part = self.b.tiles(x0, y0, w, h)
            for k in ARRAYS:
                arr = getattr(self.cache, k)
                src = part.get(k)
                if src is None or not arr:
                    continue
                for r in range(h):
                    start = (y0 + r) * self.sx + x0
                    arr[start:start + w] = src[r * w:(r + 1) * w]
        return self.cache

    def set_window(self, a: tuple[int, int], b: tuple[int, int], margin: int = 32) -> None:
        self.window = (max(0, min(a[0], b[0]) - margin), max(0, min(a[1], b[1]) - margin),
                       min(self.sx, max(a[0], b[0]) + margin + 1), min(self.sy, max(a[1], b[1]) + margin + 1))


def town_pairs(towns: list[tuple[int, int]], lo: int, hi: int, count: int, rng: random.Random,
               used: set[int]) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    pairs = [(i, j) for i in range(len(towns)) for j in range(i + 1, len(towns))
             if lo <= abs(towns[i][0] - towns[j][0]) + abs(towns[i][1] - towns[j][1]) <= hi]
    rng.shuffle(pairs)
    pairs.sort(key=lambda p: (p[0] in used) + (p[1] in used))
    out = []
    for i, j in pairs[:count * 3]:
        out.append((towns[i], towns[j]))
        used.update((i, j))
    return out


def build(a: argparse.Namespace, b, result: dict) -> None:
    """Build the rail and road routes of the benchmark game."""
    b.docmd("MoneyCheat", 0, 2_000_000_000)
    t0 = time.monotonic()
    builder = WindowBuilder(b, log=lambda s: None)
    print(f"map fetched in {time.monotonic() - t0:.1f}s", flush=True)
    mx = builder.sx
    towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
    rng = random.Random(a.seed)
    used: set[int] = set()
    built = {"rail": 0, "road": 0}
    errors = 0
    t0 = time.monotonic()
    for kind, lo, hi, want in (("rail", 25, 70, a.rail_routes), ("road", 14, 40, a.road_routes)):
        for ta, tb in town_pairs(towns, lo, hi, want, rng, used):
            if built[kind] >= want:
                break
            builder.set_window(ta, tb)
            try:
                if kind == "rail":
                    builder.build_rail_route(ta, tb, wagons=a.wagons)
                else:
                    builder.build_road_route(ta, tb, vehicles=a.buses)
                built[kind] += 1
            except BuildError as e:
                errors += 1
                print(f"{kind} {ta} -> {tb}: {e}", flush=True)
    print(f"built {built} routes in {time.monotonic() - t0:.1f}s ({errors} failed)", flush=True)
    result.update({"towns": len(towns), "rail_routes": built["rail"], "road_routes": built["road"],
                   "trains": built["rail"], "buses": built["road"] * a.buses, "vehicles": len(b.objs("vehicle"))})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", help="game binary (default: build/openttd of this checkout)")
    ap.add_argument("--out", required=True, help="result JSON")
    ap.add_argument("--name", default="perf_vehicle_bitmap", help="instance directory name")
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--map-bits", type=int, default=11)
    ap.add_argument("--rail-routes", type=int, default=150, help="rail shuttle routes (one train each)")
    ap.add_argument("--wagons", type=int, default=5)
    ap.add_argument("--road-routes", type=int, default=60)
    ap.add_argument("--buses", type=int, default=35, help="buses per road route")
    ap.add_argument("--warmup", type=float, default=30.0, help="seconds of game before sampling")
    ap.add_argument("--seconds", type=float, default=180.0, help="seconds of sampling")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between fps samples")
    ap.add_argument("--save", help="build the game, save it to this file and exit without measuring")
    ap.add_argument("--load", help="measure a game saved with --save instead of building one")
    a = ap.parse_args()

    config = {
        "game_creation": {"map_x": a.map_bits, "map_y": a.map_bits, "starting_year": 1990, "landscape": "temperate",
                          "variety": 0},
        "difficulty": {"terrain_type": 0, "quantity_sea_lakes": 0, "number_towns": 3, "industry_density": 0,
                       "vehicle_breakdowns": 0, "max_no_competitors": 0},
        "vehicle": {"max_trains": 5000, "max_roadveh": 5000},
    }
    inst = Instance(a.name, mode="headless", root=DEFAULT_RUN_ROOT / "perf", exe=a.exe, resolution=(320, 240),
                    config=config)
    result: dict = {"seed": a.seed, "map_bits": a.map_bits}
    try:
        if a.load:
            inst.start("-g", str(Path(a.load).resolve()), wait=300)
        else:
            inst.start("-g", "-G", str(a.seed), wait=300)
        b = inst.bridge
        b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=300, what="game")
        if a.load:
            result["vehicles"] = len(b.objs("vehicle"))
        else:
            build(a, b, result)
            if a.save:
                b.save_game("perf_vehicle_bitmap")
                sav = inst.dir / "save" / "perf_vehicle_bitmap.sav"
                size = -1
                for _ in range(120):  # saving finishes in a thread: wait until the file stops growing
                    time.sleep(1)
                    if sav.exists() and sav.stat().st_size == size and size > 0:
                        break
                    size = sav.stat().st_size if sav.exists() else -1
                Path(a.save).write_bytes(sav.read_bytes())
                return 0

        time.sleep(a.warmup)
        samples: dict[str, list[float]] = {k: [] for k in KEYS.values()}
        mem: list[float] = []
        tick0 = b.info()["tick_counter"]
        t0 = time.monotonic()
        while time.monotonic() - t0 < a.seconds:
            time.sleep(a.interval)
            for line in b.console("fps"):
                if (m := FPS_TIMES_RE.match(line)) and (key := KEYS.get(m.group(1).strip().lower())):
                    samples[key].append(float(m.group(2)))  # average of the last ticks
            if (r := rss_mb(inst)) is not None:
                mem.append(r[0])
        result["ticks"] = b.info()["tick_counter"] - tick0
        for key, vals in samples.items():
            if vals:
                result[f"{key}_avg"] = round(statistics.mean(vals), 4)
                result[f"{key}_p95"] = round(percentile(vals, 95), 4)
                result[f"{key}_samples"] = len(vals)
        if mem:
            result["mem_mb_max"] = round(max(mem), 2)
        result["cache_mismatches"] = [l for l in b.console("check_caches") if "mismatch" in l.lower()][:10]
    finally:
        inst.stop()
        Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
