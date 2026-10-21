"""
Trains without a destination and lost trains on a junction-heavy rail grid (every crossing has all six tracks).

Game "block" (no path reservation): a train without orders wanders the grid choosing random tracks and runs no
pathfinder search at junctions (yapf=3 shows every search with the unit number of its train). It is then sent away and
a train with an order for a station that is not connected to the grid gets lost; while lost it searches with an eighth
of max_search_nodes, except on its full search days ((day + vehicle index) % 8 == 0). Once a track connects the
station, the train finds the route, is no longer lost and reaches the station.

Game "pbs" (path reservation on, realistic braking): four trains without orders reserve paths to safe tiles (the
grid's dead ends) and keep moving without searching for a destination.

Set OTTD_BASELINE=1 to run against a build without this change (OTTD_EXE): the checks on searches are then only
reported, so the notes show the "before" node counts and tick times.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transport import (AXIS_X, CLEAR, NEW_STATION, RAILTYPE_RAIL, SW, TRACK_LEFT, TRACK_LOWER, TRACK_RIGHT,  # noqa: E402
                       TRACK_UPPER, TRACK_X, TRACK_Y, TREES, Builder, GameMap)

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate", "amount_of_rivers": 0},
    "difficulty": {"terrain_type": 0, "quantity_sea_lakes": 0, "number_towns": 0, "industry_density": 0,
                   "max_no_competitors": 0, "vehicle_breakdowns": 0},
    "pf": {"yapf.max_search_nodes": 10000, "path_backoff_interval": 20},
}
DAY_TICKS = 74
LINES = 16                      # grid lines in each direction
SPACING = 4                     # tiles between crossings
SIZE = (LINES - 1) * SPACING    # grid size in tiles (crossings at 0, SPACING, ..., SIZE)
MAX_NODES = 10000
LOST_NODES = MAX_NODES // 8
FULL_SEARCH_PERIOD = 8
LOST_FLAG = 1 << 7              # VehicleFlag::PathfinderLost
VS_CRASHED = 1 << 7             # VehState::Crashed
DEPOT_SELL = 1 << 4             # DepotCommandFlag::Sell
BASELINE = bool(os.environ.get("OTTD_BASELINE"))

SEARCH_RE = re.compile(r"\[YAPFt\]([-!])\s*(\d+) - (\d+) rounds - (\d+) open - (\d+) closed")


class Grid:
    """A rail grid with a depot on its west side and room for a station east of it."""

    def __init__(self, ctx, b, builder: Builder):
        self.ctx, self.b, self.builder = ctx, b, builder
        gmap = GameMap.fetch(b)
        self.map = gmap
        self.x0, self.y0 = self.find_site(gmap)
        ctx.note(f"grid {LINES}x{LINES} lines, {SIZE + 1} tiles wide, at ({self.x0}, {self.y0})")

    def tile(self, x: int, y: int) -> int:
        return self.map.tile(x, y)

    def find_site(self, gmap: GameMap) -> tuple[int, int]:
        """Grid origin with clear land (or trees) from 3 tiles west/north to 10 tiles east and 3 tiles south of it."""
        for y0 in range(8, gmap.sy - SIZE - 8, 4):
            for x0 in range(8, gmap.sx - SIZE - 16, 4):
                if all(gmap.type[gmap.tile(x, y)] in (CLEAR, TREES)
                       for y in range(y0 - 3, y0 + SIZE + 4) for x in range(x0 - 3, x0 + SIZE + 11)):
                    return x0, y0
        raise AssertionError("no free site for the rail grid")

    def build(self) -> None:
        x0, y0, cmd = self.x0, self.y0, self.builder.cmd
        # Flatten the site in chunks, so that every track piece can be built.
        for cy in range(y0 - 3, y0 + SIZE + 4, 16):
            for cx in range(x0 - 3, x0 + SIZE + 11, 16):
                ex, ey = min(cx + 15, x0 + SIZE + 10), min(cy + 15, y0 + SIZE + 3)
                cmd("ClearArea", self.tile(ex, ey), self.tile(cx, cy), False, allow_fail=True)
                cmd("LevelLand", self.tile(ex, ey), self.tile(x0, y0), False, 0, allow_fail=True)
        for i in range(LINES):
            y = y0 + i * SPACING
            cmd("BuildRailroadTrack", self.tile(x0 + SIZE, y), self.tile(x0, y), RAILTYPE_RAIL, TRACK_X, 0, True)
            x = x0 + i * SPACING
            cmd("BuildRailroadTrack", self.tile(x, y0 + SIZE), self.tile(x, y0), RAILTYPE_RAIL, TRACK_Y, 0, True)
        # Every crossing gets the four curves too: three ways on at every crossing, dead ends around the edge.
        lines = [f"docmd BuildSingleRail {self.tile(x0 + i * SPACING, y0 + j * SPACING)} {RAILTYPE_RAIL} {track} 0"
                 for i in range(LINES) for j in range(LINES) for track in (TRACK_UPPER, TRACK_LOWER, TRACK_LEFT, TRACK_RIGHT)]
        failed = [r for r in self.b.batch(lines, timeout=300) if not (r.get("ok") and r["result"]["success"])]
        self.ctx.check(not failed, f"all {len(lines)} curves at the crossings built ({len(failed)} failed: {failed[:2]})")
        # Depot west of the second line, facing the grid.
        self.depot = self.tile(x0 - 1, y0 + SPACING)
        cmd("BuildTrainDepot", self.depot, RAILTYPE_RAIL, SW)

    def build_station(self) -> int:
        """A 3 tile station east of the second line, two tiles away from the grid: not connected."""
        y = self.y0 + SPACING
        res = self.builder.cmd("BuildRailStation", self.tile(self.x0 + SIZE + 3, y), RAILTYPE_RAIL, AXIS_X, 1, 3, 0, 0,
                               NEW_STATION, False)
        return int(res["station"])

    def connect_station(self) -> None:
        y = self.y0 + SPACING
        self.builder.cmd("BuildRailroadTrack", self.tile(self.x0 + SIZE + 2, y), self.tile(self.x0 + SIZE + 1, y),
                         RAILTYPE_RAIL, TRACK_X, 0, True)

    def inside(self, tile: int) -> bool:
        x, y = self.map.xy(tile)
        return self.x0 - 1 <= x <= self.x0 + SIZE + 6 and self.y0 <= y <= self.y0 + SIZE


class Searches:
    """Rail pathfinder searches (from yapf=3 debug output) per train unit number."""

    def __init__(self, b):
        self.b = b
        self.seq = b.last_log_seq()

    def collect(self) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        while True:
            res = self.b.log(self.seq, 5000)
            for line in res["lines"]:
                self.seq = line["seq"]
                if m := SEARCH_RE.search(line["text"]):
                    out.setdefault(int(m.group(2)), []).append({"found": m.group(1) == "-", "closed": int(m.group(5))})
            if not res["lines"] or self.seq >= res["last_seq"]:
                return out


def tick_times(b) -> str:
    """Average game loop and train tick times over the last 512 ticks, from the console 'fps' output."""
    times = {}
    for line in b.console("fps"):
        if m := re.match(r"^\s*(Game loop|GL train ticks) times:\s*[\d.]+ms\s+[\d.]+ms\s+([\d.]+)ms", line):
            times[m.group(1)] = m.group(2)
    return f"game loop {times.get('Game loop')} ms, train ticks {times.get('GL train ticks')} ms"


def day(b) -> int:
    """The day number the pathfinder budget uses: state ticks / DAY_TICKS."""
    return b.obj("globals", None, "state_ticks")["state_ticks"] // DAY_TICKS


def build_train(builder: Builder, depot: int, engine: int, start: bool = True) -> int:
    train = builder.build_vehicle(depot, engine)
    if start:
        builder.start_vehicle(train)
    return train


def run_steps(b, searches: Searches, ticks: int, chunk: int = 200) -> dict[int, list[dict]]:
    """Step the game, collecting searches often enough that the bridge's log ring buffer never overflows."""
    total: dict[int, list[dict]] = {}
    for _ in range(0, ticks, chunk):
        b.step(chunk)
        for unit, s in searches.collect().items():
            total.setdefault(unit, []).extend(s)
    return total


def summary(searches: list[dict]) -> str:
    if not searches:
        return "0 searches"
    closed = [s["closed"] for s in searches]
    return f"{len(closed)} searches, {sum(closed)} nodes, avg {sum(closed) // len(closed)}, max {max(closed)}"


def start_game(ctx, name: str, extra: dict | None = None):
    config = {k: dict(v) for k, v in CONFIG.items()}
    for k, v in (extra or {}).items():
        config.setdefault(k, {}).update(v)
    inst = ctx.solo(name=name, args=["-g", "-G", "777"], lockstep=True, config=config, debug="yapf=3")
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what=f"{name} game start")
    builder = Builder(b, log=ctx.note)
    builder.give_money(500_000_000)
    grid = Grid(ctx, b, builder)
    grid.build()
    loco = builder.pick_engine("rail", wagon=False, railtypes=lambda e: e["railtypes"] & (1 << RAILTYPE_RAIL))
    return b, builder, grid, loco["id"]


def show_grid(ctx, b, grid: Grid, label: str) -> None:
    for w in b.windows():
        if w["class_name"] in ("News", "Message", "Error"):
            b.close_window(w["class"], w["number"])
    b.console(f"scrollto instant {grid.x0 + SIZE // 2} {grid.y0 + SIZE // 2}")
    b.console("zoomto 2")
    b.step(2)
    ctx.screenshot(b, label)


def block_game(ctx) -> None:
    b, builder, grid, loco = start_game(ctx, "block")
    searches = Searches(b)
    check = lambda cond, msg: ctx.check(cond, msg, soft=BASELINE)  # noqa: E731

    # A train without orders wanders the grid.
    wanderer = build_train(builder, grid.depot, loco)
    unit = b.obj("vehicle", wanderer, "unitnumber")["unitnumber"]
    tiles = set()
    found: dict[int, list[dict]] = {}
    for _ in range(20):
        for u, s in run_steps(b, searches, 100, 100).items():
            found.setdefault(u, []).extend(s)
        tiles.add(b.obj("vehicle", wanderer, "tile")["tile"])
    ctx.note(f"no orders, no reservation: train {unit} over 2000 ticks: {summary(found.get(unit, []))}; {tick_times(b)}")
    ctx.check(wanderer in b.objs("vehicle"), "the train without orders is not crashed")
    # Before this change it drove towards tile 0, into the dead ends of the grid's north corner, and stayed there.
    check(len(tiles) >= 10 and all(grid.inside(t) for t in tiles),
          f"the train without orders keeps moving all over the grid ({len(tiles)} different tiles in 20 samples)")
    check(not found.get(unit), f"the train without orders ran no pathfinder searches ({summary(found.get(unit, []))})")
    show_grid(ctx, b, grid, "no_orders_wandering")

    # Send it away to be sold, so it does not run into the next train (there are no signals).
    builder.cmd("SendVehicleToDepot", 0, wanderer, DEPOT_SELL, 0)
    for _ in range(100):
        if wanderer not in b.objs("vehicle"):
            break
        b.step(DAY_TICKS)
    ctx.check(wanderer not in b.objs("vehicle"), "the train without orders went to the depot and was sold")

    # A train for a station that is not connected gets lost.
    station = grid.build_station()
    lost = builder.build_vehicle(grid.depot, loco)
    builder.order(lost, "station", station)
    unit = b.obj("vehicle", lost, "unitnumber")["unitnumber"]
    searches.collect()
    builder.start_vehicle(lost)
    days = 3 * FULL_SEARCH_PERIOD
    by_day: list[tuple[int, list[dict]]] = []
    for _ in range(days):
        by_day.append((day(b), run_steps(b, searches, DAY_TICKS, DAY_TICKS).get(unit, [])))
    flags = b.obj("vehicle", lost, "vehicle_flags")["vehicle_flags"]
    ctx.check(flags & LOST_FLAG, f"the train for the unconnected station is lost (flags {flags:#x})")
    lost_searches = [s for _, d in by_day[1:] for s in d]
    ctx.note(f"lost train {unit} (vehicle {lost}) over {days - 1} days: {summary(lost_searches)}; {tick_times(b)}")
    ctx.check(lost_searches, "the lost train still searches at junctions")
    # Each step starts at a day boundary only if the game started on one, so leave out the days next to full search days.
    small = [s["closed"] for n, d in by_day[1:] for s in d if (n + lost) % FULL_SEARCH_PERIOD not in (0, 1, FULL_SEARCH_PERIOD - 1)]
    big = [s["closed"] for n, d in by_day[1:] for s in d if (n + lost) % FULL_SEARCH_PERIOD == 0]
    check(small and max(small) <= LOST_NODES, f"lost train searches outside its full search days stop at {LOST_NODES} nodes "
          f"(max {max(small, default=None)})")
    check(not big or max(big) > LOST_NODES, f"on its full search days the lost train searches more nodes (max {max(big, default=None)})")
    show_grid(ctx, b, grid, "lost_train")

    # Connect the station: the train finds it (at the latest on its next full search day) and is no longer lost.
    grid.connect_station()
    for _ in range(4 * FULL_SEARCH_PERIOD):
        b.step(DAY_TICKS)
        if not b.obj("vehicle", lost, "vehicle_flags")["vehicle_flags"] & LOST_FLAG:
            break
    flags = b.obj("vehicle", lost, "vehicle_flags")["vehicle_flags"]
    ctx.check(not flags & LOST_FLAG, f"the train is no longer lost once the station is connected (flags {flags:#x})")
    for _ in range(60):
        if b.obj("vehicle", lost, "last_station_visited")["last_station_visited"] == station:
            break
        b.step(DAY_TICKS)
    ctx.check_eq(b.obj("vehicle", lost, "last_station_visited")["last_station_visited"], station,
                 "the train that was lost reaches its station")


def pbs_game(ctx) -> None:
    b, builder, grid, loco = start_game(ctx, "pbs", {"pf": {"reserve_paths": "true"}})
    b.console("setting vehicle.train_braking_model 1")
    b.step(2)
    out = b.console("setting vehicle.train_braking_model")
    ctx.check("is: '1'" in " ".join(out), f"realistic braking is on ({out})")
    station = grid.build_station()
    searches = Searches(b)
    wanderers, lost = [], []
    for i in range(12):
        train = builder.build_vehicle(grid.depot, loco)
        if i % 3 == 2:
            builder.order(train, "station", station)
            lost.append(train)
        else:
            wanderers.append(train)
        builder.start_vehicle(train)
        b.step(DAY_TICKS)
    trains = wanderers + lost
    unit = {t: b.obj("vehicle", t, "unitnumber")["unitnumber"] for t in trains}
    b.step(10 * DAY_TICKS)
    searches.collect()
    positions: dict[int, set] = {t: set() for t in trains}
    found: dict[int, list[dict]] = {}
    for _ in range(30):
        for u, s in run_steps(b, searches, 100, 100).items():
            found.setdefault(u, []).extend(s)
        for t in trains:
            positions[t].add(b.obj("vehicle", t, "tile")["tile"])
    times = tick_times(b)
    no_orders = [s for t in wanderers for s in found.get(unit[t], [])]
    lost_searches = [s for t in lost for s in found.get(unit[t], [])]
    ctx.note(f"path reservation + realistic braking, {len(wanderers)} trains without orders and {len(lost)} lost trains "
             f"over 3000 ticks: no orders {summary(no_orders)}; lost {summary(lost_searches)}; {times}")
    alive = b.objs("vehicle")
    ctx.check(all(t in alive and not b.obj("vehicle", t, "vehstatus")["vehstatus"] & VS_CRASHED for t in trains),
              "no train crashed")
    ctx.check(all(len(p) >= 2 for p in positions.values()),
              f"every train keeps moving (tiles seen in 30 samples: {[len(p) for p in positions.values()]})")
    ctx.check(all(b.obj("vehicle", t, "vehicle_flags")["vehicle_flags"] & LOST_FLAG for t in lost), "the trains for the "
              "unconnected station are lost")
    # What is left for trains without orders are the searches for the nearest safe tile (the grid's dead ends).
    biggest = max((s["closed"] for s in no_orders), default=0)
    ctx.check(biggest < LOST_NODES, f"no full-network searches for trains without orders (biggest {biggest} nodes)",
              soft=BASELINE)
    show_grid(ctx, b, grid, "no_orders_pbs")


def run(ctx):
    if BASELINE:
        ctx.note("OTTD_BASELINE set: search checks are only reported")
    block_game(ctx)
    pbs_game(ctx)
