#!/usr/bin/env python3
"""
Heavy multiplayer benchmark (temporary, for the perf-mp-heavy workflow).

build:   one headless single player game on a generated 4096x4096 map with fixed seed; the local company builds many rail
         shuttle routes (one train each) and many bus routes (many buses each), ~8000 vehicles, and saves the game.
measure: a dedicated server loads that save, N headless clients join as spectators (every client simulates the whole
         game, like in a real MP game), the game runs in real time while 'fps' and memory are sampled on every instance.

  python utils/debug_harness/perf_mp_heavy.py build --exe build/openttd --save heavy.sav --out build.json
  OTTD_EXE=build/openttd python utils/debug_harness/perf_mp_heavy.py measure --save heavy.sav --out result.json
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

from mp import Cluster  # noqa: E402
from ottd import DEFAULT_RUN_ROOT, Instance  # noqa: E402
from transport import Builder, BuildError, GameMap  # noqa: E402

FPS_TIMES_RE = re.compile(r"^\s*(.+?) times: ([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")
KEYS = {"game loop": "game_loop_ms", "gl train ticks": "gl_train_ticks_ms", "gl road vehicle ticks": "gl_road_vehicle_ticks_ms"}
ARRAYS = ("type", "height", "slope", "owner", "sub", "bits", "road_owner")


def percentile(vals: list[float], p: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def rss_mb(inst: Instance) -> float | None:
    """Current resident memory of the game (Linux only)."""
    try:
        with open(f"/proc/{inst.proc.pid}/status") as f:
            status = dict(line.split(":", 1) for line in f if ":" in line)
        return int(status["VmRSS"].split()[0]) / 1024
    except Exception:  # noqa: BLE001
        return None


class WindowBuilder(Builder):
    """
    Builder that fetches the whole map once (into byte arrays: the map is large) and afterwards refreshes only a window
    around the route being built; full map fetches would dominate the build time.
    """

    def __init__(self, bridge, **kw):
        super().__init__(bridge, **kw)
        info = bridge.info()["map"]
        self.sx, self.sy = info["x"], info["y"]
        data: dict = {k: bytearray(self.sx * self.sy) for k in ARRAYS}
        data["road_owner"][:] = b"\xff" * (self.sx * self.sy)
        stripe = 64
        for y in range(0, self.sy, stripe):
            h = min(stripe, self.sy - y)
            part = bridge.tiles(0, y, self.sx, h)
            for k in ARRAYS:
                if k in part:
                    data[k][y * self.sx:(y + h) * self.sx] = bytes(part[k])
        data.update({"x": 0, "y": 0, "w": self.sx, "h": self.sy})
        self.cache = GameMap(data, self.sx, self.sy)
        self.window: tuple[int, int, int, int] | None = None

    def fresh_map(self) -> GameMap:
        if self.window is not None:
            x0, y0, x1, y1 = self.window
            w, h = x1 - x0, y1 - y0
            part = self.b.tiles(x0, y0, w, h)
            for k in ARRAYS:
                src = part.get(k)
                if src is None:
                    continue
                arr = getattr(self.cache, k)
                for r in range(h):
                    start = (y0 + r) * self.sx + x0
                    arr[start:start + w] = bytes(src[r * w:(r + 1) * w])
        return self.cache

    def set_window(self, a: tuple[int, int], b: tuple[int, int], margin: int = 32) -> None:
        self.window = (max(0, min(a[0], b[0]) - margin), max(0, min(a[1], b[1]) - margin),
                       min(self.sx, max(a[0], b[0]) + margin + 1), min(self.sy, max(a[1], b[1]) + margin + 1))


def town_pairs(towns: list[tuple[int, int]], lo: int, hi: int, rng: random.Random, used: set[int]):
    """Town pairs at Manhattan distance lo..hi, towns not used before first (grid buckets: there are many towns)."""
    cell = hi
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(towns):
        grid.setdefault((x // cell, y // cell), []).append(i)
    order = list(range(len(towns)))
    rng.shuffle(order)
    order.sort(key=lambda i: i in used)
    for i in order:
        if i in used:
            continue
        x, y = towns[i]
        cands = [j for gx in (-1, 0, 1) for gy in (-1, 0, 1) for j in grid.get((x // cell + gx, y // cell + gy), [])
                 if j != i and j not in used and lo <= abs(towns[j][0] - x) + abs(towns[j][1] - y) <= hi]
        if cands:
            j = rng.choice(cands)
            used.update((i, j))
            yield towns[i], towns[j]


def build(a: argparse.Namespace) -> int:
    config = {
        "game_creation": {"map_x": a.map_bits, "map_y": a.map_bits, "starting_year": 1990, "landscape": "temperate",
                          "variety": 0},
        "difficulty": {"terrain_type": 0, "quantity_sea_lakes": 0, "number_towns": 3, "industry_density": 0,
                       "vehicle_breakdowns": 0, "max_no_competitors": 0},
        "vehicle": {"max_trains": 10000, "max_roadveh": 10000},
    }
    inst = Instance("perf_mp_heavy_build", mode="headless", root=DEFAULT_RUN_ROOT / "perf", exe=a.exe, resolution=(320, 240),
                    config=config)
    result: dict = {"seed": a.seed, "map_bits": a.map_bits}
    t_start = time.monotonic()
    try:
        inst.start("-g", "-G", str(a.seed), wait=600)
        b = inst.bridge
        b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=600, what="new game")
        b.docmd("MoneyCheat", 0, 2_000_000_000)
        t0 = time.monotonic()
        builder = WindowBuilder(b, log=lambda s: None)
        print(f"map fetched in {time.monotonic() - t0:.1f}s", flush=True)
        mx = builder.sx
        towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
        print(f"{len(towns)} towns", flush=True)
        rng = random.Random(a.seed)
        used: set[int] = set()
        built = {"rail": 0, "road": 0}
        errors = 0
        for kind, lo, hi, want in (("rail", 25, 70, a.rail_routes), ("road", 20, 60, a.road_routes)):
            t0 = time.monotonic()
            for ta, tb in town_pairs(towns, lo, hi, rng, used):
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
                if (built[kind] + 1) % 50 == 0:
                    print(f"{kind}: {built[kind]} routes, {time.monotonic() - t0:.0f}s", flush=True)
            print(f"built {built[kind]} {kind} routes in {time.monotonic() - t0:.1f}s", flush=True)
        result.update({"towns": len(towns), "rail_routes": built["rail"], "road_routes": built["road"], "failed": errors,
                       "vehicles": len(b.objs("vehicle"))})
        b.wait_ticks(74 * 5)  # let the vehicles leave their depots
        b.save_game("perf_mp_heavy")
        sav = inst.dir / "save" / "perf_mp_heavy.sav"
        size = -1
        for _ in range(300):  # saving finishes in a thread: wait until the file stops growing
            time.sleep(1)
            if sav.exists() and sav.stat().st_size == size and size > 0:
                break
            size = sav.stat().st_size if sav.exists() else -1
        Path(a.save).write_bytes(sav.read_bytes())
        result["save_bytes"] = sav.stat().st_size
    finally:
        inst.stop()
        result["seconds"] = round(time.monotonic() - t_start)
        Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


def measure(a: argparse.Namespace) -> int:
    cluster = Cluster(clients=a.clients, session="perf_mp_heavy", root=DEFAULT_RUN_ROOT / "perf", save=a.save,
                      company="spectator", resolution=(320, 240))
    result: dict = {"clients": a.clients}
    samples: dict[str, dict[str, list[float]]] = {}
    try:
        t0 = time.monotonic()
        cluster.start(timeout=900)
        result["start_seconds"] = round(time.monotonic() - t0, 1)
        print(f"server and {a.clients} clients up in {result['start_seconds']}s", flush=True)
        insts = cluster.instances
        samples = {i.name: {**{k: [] for k in KEYS.values()}, "mem_mb": []} for i in insts}
        time.sleep(a.warmup)
        tick0 = cluster.server.bridge.info()["tick_counter"]
        t0 = time.monotonic()
        while time.monotonic() - t0 < a.seconds:
            time.sleep(a.interval)
            for inst in insts:
                for line in inst.bridge.console("fps"):
                    if (m := FPS_TIMES_RE.match(line)) and (key := KEYS.get(m.group(1).strip().lower())):
                        samples[inst.name][key].append(float(m.group(3)))  # average of the last 128 ticks
                if (r := rss_mb(inst)) is not None:
                    samples[inst.name]["mem_mb"].append(r)
        elapsed = time.monotonic() - t0
        ticks = cluster.server.bridge.info()["tick_counter"] - tick0
        result["ticks"] = ticks
        result["speed_ratio"] = round(ticks / elapsed / (1000 / 27), 3)
        result["vehicles"] = len(cluster.server.bridge.objs("vehicle"))
        result["cache_mismatches"] = {i.name: [l for l in i.bridge.console("check_caches") if "mismatch" in l.lower()][:5]
                                      for i in insts}
        result["problems"] = cluster.health()[:20]
    finally:
        cluster.stop()
    per = {}
    for name, keys in samples.items():
        per[name] = {}
        for key, vals in keys.items():
            if vals:
                per[name][f"{key}_avg"] = round(statistics.mean(vals), 4)
                per[name][f"{key}_p95"] = round(percentile(vals, 95), 4)
                if key == "mem_mb":
                    per[name]["mem_mb_max"] = round(max(vals), 2)
    result["instances"] = per
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--exe", help="game binary")
    b.add_argument("--save", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--seed", type=int, default=4242)
    b.add_argument("--map-bits", type=int, default=12)
    b.add_argument("--rail-routes", type=int, default=500)
    b.add_argument("--wagons", type=int, default=5)
    b.add_argument("--road-routes", type=int, default=190)
    b.add_argument("--buses", type=int, default=40)
    m = sub.add_parser("measure")
    m.add_argument("--save", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--clients", type=int, default=4)
    m.add_argument("--warmup", type=float, default=60.0)
    m.add_argument("--seconds", type=float, default=480.0)
    m.add_argument("--interval", type=float, default=4.0)
    a = ap.parse_args()
    return build(a) if a.cmd == "build" else measure(a)


if __name__ == "__main__":
    sys.exit(main())
