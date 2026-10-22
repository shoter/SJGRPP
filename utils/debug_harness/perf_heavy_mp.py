#!/usr/bin/env python3
"""
Heavy multiplayer benchmark (temporary, for the perf-heavy-mp workflow).

  make-save: single player on a 4096x4096 map (every town a city), one company builds road, air and rail routes with
             about 8000 vehicles and scatters bus stations over towns, then the game is saved. Infinite money, so
             nothing goes bankrupt while measuring.
  measure:   a dedicated server loads that save, 4 headless clients join the company, and the 'fps' console of the
             server and of every client is sampled while the game runs in real time.

  python utils/debug_harness/perf_heavy_mp.py make-save --out heavy.sav
  python utils/debug_harness/perf_heavy_mp.py measure --save heavy.sav --out result.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mp import Cluster  # noqa: E402
from ottd import DEFAULT_RUN_ROOT, BridgeError, Instance  # noqa: E402
from transport import BuildError, Builder  # noqa: E402

FPS_TIMES_RE = re.compile(r"^\s*(.+?) times: ([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")
METRICS = {"game loop": "game_loop_ms", "gl landscape ticks": "gl_landscape_ticks_ms",
           "gl train ticks": "gl_train_ticks_ms", "gl road vehicle ticks": "gl_road_vehicle_ticks_ms",
           "gl aircraft ticks": "gl_aircraft_ticks_ms", "gl station ticks": "gl_station_ticks_ms"}


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def percentile(vals: list[float], p: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


# ---- make-save ----

def window_around(*points: tuple[int, int], margin: int = 40) -> tuple[int, int, int, int]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs) - margin, min(ys) - margin, max(xs) - min(xs) + 2 * margin, max(ys) - min(ys) + 2 * margin


def make_save(a: argparse.Namespace) -> int:
    config = {
        "game_creation": {"map_x": a.map_bits, "map_y": a.map_bits, "starting_year": 1990, "landscape": "temperate",
                          "variety": 0},
        "difficulty": {"terrain_type": 0, "quantity_sea_lakes": 0, "number_towns": 2, "industry_density": 3,
                       "vehicle_breakdowns": 0, "max_no_competitors": 0, "infinite_money": "true"},
        "economy": {"larger_towns": 1, "initial_city_size": 4},
        "station": {"never_expire_airports": "true"},
        "pf": {"forbid_90_deg": "false"},
        "vehicle": {"plane_crashes": 0, "max_roadveh": 10000, "max_aircraft": 10000, "max_trains": 10000},
    }
    inst = Instance("heavy_gen", mode="headless", root=DEFAULT_RUN_ROOT / "perf", resolution=(320, 240), config=config)
    rng = random.Random(a.seed)
    try:
        t_start = time.monotonic()
        inst.start("-g", "-G", str(a.seed), wait=900)
        b = inst.bridge
        b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=900, what="new game")
        mx = b.info()["map"]["x"]
        towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
        log(f"map {mx}^2 generated in {time.monotonic() - t_start:.0f}s: {len(towns)} towns")
        builder = Builder(b, log=lambda s: None)

        cell = 64
        grid: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for t in towns:
            grid.setdefault((t[0] // cell, t[1] // cell), []).append(t)

        def pairs(lo: int, hi: int) -> list[tuple[tuple[int, int], tuple[int, int]]]:
            """Town pairs lo..hi tiles apart (Manhattan), in random order."""
            out = []
            reach = hi // cell + 1
            for p in towns:
                cx, cy = p[0] // cell, p[1] // cell
                near = [q for gx in range(cx - reach, cx + reach + 1) for gy in range(cy - reach, cy + reach + 1)
                        for q in grid.get((gx, gy), []) if p < q and lo <= abs(p[0] - q[0]) + abs(p[1] - q[1]) <= hi]
                if near:
                    out.append((p, rng.choice(near)))
            rng.shuffle(out)
            return out

        built = {"road": 0, "air": 0, "rail": 0}
        vehicles = {"road": 0, "air": 0, "rail": 0}
        used: set = set()
        plans = [("road", a.road_routes, a.road_vehicles, pairs(16, 40)), ("air", a.air_routes, a.air_vehicles, pairs(80, 300)),
                 ("rail", a.rail_routes, 1, pairs(25, 60))]
        for kind, routes, per_route, cands in plans:
            t0 = time.monotonic()
            for town_a, town_b in cands:
                if built[kind] >= routes:
                    break
                if (kind, town_a) in used or (kind, town_b) in used:
                    continue
                builder.window = window_around(town_a, town_b)
                try:
                    if kind == "road":
                        r = builder.build_road_route(town_a, town_b, vehicles=per_route)
                    elif kind == "air":
                        r = builder.build_air_route(town_a, town_b, vehicles=per_route)
                    else:
                        r = builder.build_rail_route(town_a, town_b)
                except (BuildError, BridgeError, TimeoutError) as e:
                    log(f"{kind} {town_a}-{town_b} failed: {e}")
                    continue
                used |= {(kind, town_a), (kind, town_b)}
                built[kind] += 1
                vehicles[kind] += len(r.vehicles)
                if built[kind] % 10 == 0:
                    log(f"{kind}: {built[kind]} routes, {vehicles[kind]} vehicles, {time.monotonic() - t0:.0f}s")
            log(f"{kind} done: {built[kind]} routes, {vehicles[kind]} vehicles in {time.monotonic() - t0:.0f}s")

        # Many stations whose catchments cover town houses (the house tile loop's nearby station lookup).
        t0 = time.monotonic()
        stop_towns = rng.sample(towns, min(a.stop_towns, len(towns)))
        stops = 0
        for town in stop_towns:
            builder.window = window_around(town, margin=20)
            try:
                stops += len(builder.build_town_stops([town], a.stops_per_town, radius=16, spacing=2, seed=a.seed))
            except (BuildError, BridgeError, TimeoutError) as e:
                log(f"town stops at {town} failed: {e}")
        log(f"{stops} town bus stations around {len(stop_towns)} towns in {time.monotonic() - t0:.0f}s")

        primary = sum(vehicles.values())
        b.save_game("heavy", timeout=600)
        src = inst.dir / "save" / "heavy.sav"
        # The save is written by a background thread: wait until the file exists and stopped growing.
        size, deadline = -1, time.monotonic() + 600
        while time.monotonic() < deadline:
            time.sleep(2)
            if src.exists() and src.stat().st_size == size and size > 0:
                break
            size = src.stat().st_size if src.exists() else -1
        shutil.copyfile(src, a.out)
        info = {"towns": len(towns), "routes": built, "vehicles": vehicles, "primary_vehicles": primary,
                "vehicle_pool": len(b.objs("vehicle")), "town_stops": stops, "seconds": round(time.monotonic() - t_start)}
        Path(a.out).with_suffix(".json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        log(f"saved {a.out} ({src.stat().st_size / 2**20:.1f} MiB): {json.dumps(info)}")
    finally:
        inst.stop()
    return 0


# ---- measure ----

def measure(a: argparse.Namespace) -> int:
    root = Path(a.root).resolve()
    server_config = {"network": {"pause_on_join": "false", "max_clients": a.clients + 4},
                     "gui": {"autosave_interval": 0}}
    cluster = Cluster(clients=a.clients, session="heavy", root=root, save=str(Path(a.save).resolve()), company="1",
                      server_config=server_config, client_config={"gui": {"autosave_interval": 0}},
                      resolution=(640, 480))
    result: dict = {"num_clients": a.clients}
    try:
        t0 = time.monotonic()
        cluster.start(timeout=1200)
        log(f"server + {a.clients} clients up in {time.monotonic() - t0:.0f}s")
        server = cluster.server.bridge
        result["vehicle_pool"] = len(server.objs("vehicle"))
        cluster.health()  # marks the logs: only problems from here on count
        time.sleep(a.warmup)

        samples: dict[str, dict[str, list[float]]] = {i.name: {k: [] for k in METRICS.values()} for i in cluster.instances}
        tick0, t_meas = server.info()["tick_counter"], time.monotonic()
        problems: list[str] = []
        while time.monotonic() - t_meas < a.seconds:
            time.sleep(a.interval)
            for inst in cluster.instances:
                try:
                    lines = inst.bridge.console("fps")
                except (BridgeError, OSError, TimeoutError) as e:
                    problems.append(f"{inst.name}: fps failed: {e}")
                    continue
                for line in lines:
                    if (m := FPS_TIMES_RE.match(line)) and (key := METRICS.get(m.group(1).strip().lower())):
                        samples[inst.name][key].append(float(m.group(2)))  # average of the last 64 ticks
        elapsed = time.monotonic() - t_meas
        ticks = server.info()["tick_counter"] - tick0
        result["ticks"] = ticks
        result["ticks_per_second"] = round(ticks / elapsed, 2)
        result["speed_ratio"] = round(ticks / elapsed / (1000 / 27), 3)
        problems += cluster.health()
        result["problems"] = problems[:20]
        result["instances"] = {}
        for name, per in samples.items():
            out = {}
            for key, vals in per.items():
                if vals:
                    out[f"{key}_avg"] = round(statistics.mean(vals), 4)
                    out[f"{key}_p95"] = round(percentile(vals, 95), 4)
            out["samples"] = len(per["game_loop_ms"])
            result["instances"][name] = out
        # Means over the clients, and over all instances.
        for group, names in (("clients", [i.name for i in cluster.clients]), ("all", [i.name for i in cluster.instances])):
            keys = {k for n in names for k in result["instances"][n] if k != "samples"}
            result[group] = {k: round(statistics.mean(result["instances"][n][k] for n in names if k in result["instances"][n]), 4)
                             for k in sorted(keys)}
    finally:
        cluster.stop()
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("make-save")
    g.add_argument("--out", required=True)
    g.add_argument("--seed", type=int, default=4242)
    g.add_argument("--map-bits", type=int, default=12)
    g.add_argument("--road-routes", type=int, default=90)
    g.add_argument("--road-vehicles", type=int, default=60, help="buses per road route")
    g.add_argument("--air-routes", type=int, default=60)
    g.add_argument("--air-vehicles", type=int, default=40, help="aircraft per air route")
    g.add_argument("--rail-routes", type=int, default=60)
    g.add_argument("--stop-towns", type=int, default=20, help="towns to scatter bus stations over")
    g.add_argument("--stops-per-town", type=int, default=60)
    m = sub.add_parser("measure")
    m.add_argument("--save", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--root", default=str(DEFAULT_RUN_ROOT / "perf"))
    m.add_argument("--clients", type=int, default=4)
    m.add_argument("--warmup", type=float, default=60.0)
    m.add_argument("--seconds", type=float, default=600.0)
    m.add_argument("--interval", type=float, default=5.0)
    a = ap.parse_args()
    return make_save(a) if a.cmd == "make-save" else measure(a)


if __name__ == "__main__":
    sys.exit(main())
