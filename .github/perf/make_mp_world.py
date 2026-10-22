"""
TEMPORARY (perf measurement of this PR; removed before merge).

Builds the heavy multiplayer benchmark world: a new 4096x4096 game, then road, rail and air routes built through the
debug bridge (harness route builder) until the target number of vehicles runs, then some ticks so vehicles spread out.

  python .github/perf/make_mp_world.py --exe <openttd> --content <dir> --out world.sav [--vehicles 8000]

The harness builder reads the map with the 'tiles' bridge command; on 4096x4096 the whole map per route is too much, so
the map is fetched lazily in 128x128 chunks around where a route is planned. Ship routes scan the whole map for water
bodies, so they are not used.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "utils" / "debug_harness"))
from ottd import BridgeError, Instance  # noqa: E402
import transport  # noqa: E402
from transport import Builder, BuildError  # noqa: E402

CHUNK = 128
CONFIG = {
    "game_creation": {"map_x": 12, "map_y": 12, "starting_year": 1990, "landscape": "temperate", "variety": 0},
    "difficulty": {"terrain_type": 1, "quantity_sea_lakes": 2, "number_towns": 3, "industry_density": 4,
                   "vehicle_breakdowns": 0, "max_no_competitors": 0},
    "station": {"never_expire_airports": "true"},
    "pf": {"forbid_90_deg": "false"},
    "vehicle": {"plane_crashes": 0, "max_roadveh": 5000, "max_trains": 5000, "max_aircraft": 5000},
}
DISTANCES = {"road": (14, 40), "rail": (25, 70), "air": (80, 600)}
# Per route: vehicles at build time (road, air) or extra trains cloned onto a rail line.
PER_ROUTE = {"road": 16, "rail": 5, "air": 10}
# Route kinds in build order, repeated: mostly road (most vehicles in real games), then rail and air.
CYCLE = ["road", "road", "rail", "road", "air", "road", "rail"]


def log(msg: str) -> None:
    print(f"[make_mp_world {time.strftime('%H:%M:%S')}] {msg}", flush=True)


class _LazyArray:
    def __init__(self, owner: "LazyGameMap", key: str):
        self.owner, self.key = owner, key

    def __getitem__(self, i: int) -> int:
        return self.owner.value(self.key, i)

    def __len__(self) -> int:
        return self.owner.sx * self.owner.sy


class LazyGameMap(transport.GameMap):
    """GameMap whose per-tile arrays are fetched in CHUNK x CHUNK pieces on first access."""

    def __init__(self, bridge):  # noqa: D107 (does not call the base constructor on purpose)
        m = bridge.info()["map"]
        self.b, self.sx, self.sy = bridge, m["x"], m["y"]
        self.chunks: dict[tuple[int, int], dict] = {}
        for key in ("type", "height", "slope", "owner", "sub", "bits", "road_owner"):
            setattr(self, key, _LazyArray(self, key))

    def value(self, key: str, i: int) -> int:
        x, y = i % self.sx, i // self.sx
        cx, cy = x // CHUNK, y // CHUNK
        chunk = self.chunks.get((cx, cy))
        if chunk is None:
            w, h = min(CHUNK, self.sx - cx * CHUNK), min(CHUNK, self.sy - cy * CHUNK)
            chunk = self.b.tiles(cx * CHUNK, cy * CHUNK, w, h)
            self.chunks[(cx, cy)] = chunk
        return chunk[key][(y - chunk["y"]) * chunk["w"] + (x - chunk["x"])]


def town_pairs(txy: list[tuple[int, int]], kind: str, rng: random.Random) -> list:
    lo, hi = DISTANCES[kind]
    out = []
    for i, a in enumerate(txy):
        near = [b for b in txy[i + 1:] if lo <= abs(a[0] - b[0]) + abs(a[1] - b[1]) <= hi]
        if near:
            out.append((a, rng.choice(near)))
    rng.shuffle(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--content", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--vehicles", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--run-root", default="runs")
    ap.add_argument("--settle-ticks", type=int, default=3000)
    ap.add_argument("--map-bits", type=int, default=12)
    a = ap.parse_args()

    CONFIG["game_creation"].update(map_x=a.map_bits, map_y=a.map_bits)
    t0 = time.monotonic()
    inst = Instance("make_mp_world", mode="headless", lockstep=True, exe=str(Path(a.exe).resolve()),
                    root=Path(a.run_root).resolve(), content=Path(a.content).resolve(), resolution=(640, 480),
                    config=CONFIG)
    try:
        inst.start("-g", "-G", str(a.seed), wait=1800)
        b = inst.bridge
        b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=1800, what="map generation")
        log(f"map {b.info()['map']} generated in {time.monotonic() - t0:.0f}s")
        mx = b.info()["map"]["x"]
        txy = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
        log(f"{len(txy)} towns")
        rng = random.Random(a.seed)
        pairs = {kind: town_pairs(txy, kind, rng) for kind in DISTANCES}
        builder = Builder(b, log=lambda m: None)
        builder.fresh_map = lambda: LazyGameMap(b)
        built = {k: 0 for k in DISTANCES}
        failed = {k: 0 for k in DISTANCES}
        vehicles, step = 0, 0
        while vehicles < a.vehicles and any(pairs.values()):
            kind = CYCLE[step % len(CYCLE)]
            step += 1
            if not pairs[kind]:
                continue
            if step % 20 == 1:
                b.docmd("MoneyCheatAdmin", 0, 2_000_000_000)
            target = pairs[kind].pop()
            try:
                if kind == "road":
                    builder.build_road_route(*target, vehicles=PER_ROUTE["road"])
                elif kind == "rail":
                    route = builder.build_rail_route(*target)
                    builder.add_vehicles(route, PER_ROUTE["rail"] - 1)
                else:
                    builder.build_air_route(*target, vehicles=PER_ROUTE["air"])
                built[kind] += 1
            except (BuildError, BridgeError, TimeoutError) as e:
                failed[kind] += 1
                if failed[kind] % 25 == 1:
                    log(f"{kind} route {target} not built: {e}")
            if step % 10 == 0:
                vehicles = len(b.objs("vehicle"))
                log(f"{vehicles} vehicles; routes {built}, failed {failed}, {time.monotonic() - t0:.0f}s")
        vehicles = len(b.objs("vehicle"))
        log(f"done building: {vehicles} vehicles, routes {built}, failed {failed}")
        b.console("unpause")
        for _ in range(a.settle_ticks // 100):
            b.step(100)
        b.save_game("mp_world")
        log(f"saved after {a.settle_ticks} ticks, date {b.info()['date']}, {len(b.objs('vehicle'))} vehicles")
    finally:
        inst.stop(timeout=300)
    shutil.copy(inst.dir / "save" / "mp_world.sav", a.out)
    log(f"wrote {a.out} ({Path(a.out).stat().st_size / 1e6:.1f} MB) in {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
