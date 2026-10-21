"""
Building transport networks from Python through the debug bridge: every action is a real game command issued by the
client (``docmd`` / ``order`` bridge commands), so in multiplayer it travels client -> server -> all clients exactly
like a GUI action.

    from transport import Builder
    builder = Builder(client.bridge, log=print)
    builder.give_money(100_000_000)
    route = builder.build_road_route(town_a_xy, town_b_xy, vehicles=4)

Each build_* method plans sites/paths from a tile snapshot (``tiles`` bridge command), checks every piece with test
runs first (``docmd test``: nothing is sent), then builds. Failures raise BuildError; the caller decides what that means.
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field
from typing import Callable, Iterable

from ottd import Bridge, BridgeError, quote_arg

# TileType
CLEAR, RAILWAY, ROAD, HOUSE, TREES, STATION, WATER, VOID, INDUSTRY, TUNNELBRIDGE, OBJECT = range(11)
WATER_CLEAR = 0  # WaterTileType::Clear (sub value of WATER tiles)
WATER_CLASS_SEA = 0  # WaterClass::Sea (bits value of WATER tiles)
ROAD_NORMAL = 0  # RoadTileType::Normal

# DiagDirection: NE = -x, SE = +y, SW = +x, NW = -y
NE, SE, SW, NW = 0, 1, 2, 3
DIRS = (NE, SE, SW, NW)
DELTA = {NE: (-1, 0), SE: (0, 1), SW: (1, 0), NW: (0, -1)}
AXIS_X, AXIS_Y = 0, 1

# Track
TRACK_X, TRACK_Y, TRACK_UPPER, TRACK_LOWER, TRACK_LEFT, TRACK_RIGHT = range(6)
TRACK_FOR_EDGES = {
    frozenset((NE, SW)): TRACK_X, frozenset((NW, SE)): TRACK_Y,
    frozenset((NE, NW)): TRACK_UPPER, frozenset((SE, SW)): TRACK_LOWER,
    frozenset((SW, NW)): TRACK_LEFT, frozenset((NE, SE)): TRACK_RIGHT,
}
# RoadBit values
ROAD_BIT = {NW: 1, SW: 2, SE: 4, NE: 8}
ROAD_X = ROAD_BIT[SW] | ROAD_BIT[NE]
ROAD_Y = ROAD_BIT[NW] | ROAD_BIT[SE]

SLOPE_FLAT = 0
SLOPE_STEEP = 16

NEW_STATION = 0xFFFD
INVALID_TOWN = 0xFFFF
INVALID_CARGO = 0xFF
ROADSTOP_BUS = 0
MOF_FERRY_ACTION = 25           # ModifyOrderFlags::MOF_FERRY_ACTION
FERRY_NONE, FERRY_BOARD, FERRY_LEAVE = 0, 1, 2
# Airport types tried in order (type, size): small, commuter, city. Small planes can use all of them.
AIRPORT_TYPES = ((0, (4, 3)), (5, (5, 4)), (1, (6, 6)))
RAILTYPE_RAIL = 0
ROADTYPE_ROAD = 0
OWNER_NONE_VALUES = (255,)  # tiles without an owner in the "tiles" dump


def reverse(d: int) -> int:
    return (d + 2) % 4


def axis_of(d: int) -> int:
    return AXIS_X if d in (NE, SW) else AXIS_Y


def turn_of(d_in: int, d_out: int) -> int:
    """0 straight, +1 / -1 for the two turn senses."""
    if d_in == d_out:
        return 0
    return 1 if (d_out - d_in) % 4 == 1 else -1


def direction_towards(x0: int, y0: int, x1: int, y1: int) -> int:
    dx, dy = x1 - x0, y1 - y0
    if abs(dx) >= abs(dy):
        return SW if dx > 0 else NE
    return SE if dy > 0 else NW


class BuildError(Exception):
    pass


class GameMap:
    """Snapshot of the per-tile arrays returned by the 'tiles' bridge command (whole map)."""

    def __init__(self, data: dict, map_x: int, map_y: int):
        assert data["x"] == 0 and data["y"] == 0 and data["w"] == map_x and data["h"] == map_y
        self.sx, self.sy = map_x, map_y
        self.type = data["type"]
        self.height = data["height"]
        self.slope = data["slope"]
        self.owner = data["owner"]
        self.sub = data["sub"]
        self.bits = data["bits"]
        self.road_owner = data.get("road_owner", [])

    @classmethod
    def fetch(cls, bridge: Bridge) -> "GameMap":
        m = bridge.info()["map"]
        return cls(bridge.tiles(), m["x"], m["y"])

    def tile(self, x: int, y: int) -> int:
        return y * self.sx + x

    def xy(self, tile: int) -> tuple[int, int]:
        return tile % self.sx, tile // self.sx

    def inside(self, x: int, y: int, margin: int = 2) -> bool:
        return margin <= x < self.sx - margin and margin <= y < self.sy - margin

    def t(self, x: int, y: int) -> int:
        return self.type[self.tile(x, y)]

    def is_free(self, x: int, y: int, flat: bool = False) -> bool:
        """Unowned clear land or trees, i.e. something we can build on without demolishing."""
        if not self.inside(x, y):
            return False
        i = self.tile(x, y)
        if self.type[i] not in (CLEAR, TREES):
            return False
        if flat and self.slope[i] != SLOPE_FLAT:
            return False
        return not (self.slope[i] & SLOPE_STEEP)

    def is_sea(self, x: int, y: int, sea_only: bool = False) -> bool:
        """Open water; sea_only: not on rivers or canals."""
        if not (0 < x < self.sx - 1 and 0 < y < self.sy - 1):
            return False
        i = self.tile(x, y)
        return self.type[i] == WATER and self.sub[i] == WATER_CLEAR and (not sea_only or self.bits[i] == WATER_CLASS_SEA)

    def water_bodies(self, sea_only: bool = False) -> dict[int, int]:
        """Label connected navigable water: tile -> body id."""
        label: dict[int, int] = {}
        next_id = 0
        for y in range(1, self.sy - 1):
            for x in range(1, self.sx - 1):
                start = self.tile(x, y)
                if start in label or not self.is_sea(x, y, sea_only):
                    continue
                stack = [(x, y)]
                label[start] = next_id
                while stack:
                    cx, cy = stack.pop()
                    for dx, dy in DELTA.values():
                        nx, ny = cx + dx, cy + dy
                        n = self.tile(nx, ny)
                        if n not in label and self.is_sea(nx, ny, sea_only):
                            label[n] = next_id
                            stack.append((nx, ny))
                next_id += 1
        return label


def spiral(cx: int, cy: int, r_min: int, r_max: int) -> Iterable[tuple[int, int]]:
    """Tiles ordered by distance from (cx, cy), within [r_min, r_max] (Chebyshev rings)."""
    for r in range(r_min, r_max + 1):
        ring = [(cx + dx, cy + dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if max(abs(dx), abs(dy)) == r]
        ring.sort(key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        yield from ring


# ---------------------------------------------------------------------------------------------------------------------
# Path planning


@dataclass(frozen=True)
class PathStep:
    x: int
    y: int
    d_in: int   # travel direction when entering this tile
    d_out: int  # travel direction when leaving this tile


def plan_path(gmap: GameMap, start: tuple[int, int], start_dir: int, goal: tuple[int, int], goal_dir: int,
              step_cost: Callable[[int, int, int, int], float | None], no_double_turn: bool,
              blocked: set[int], max_nodes: int = 400_000) -> list[PathStep] | None:
    """
    A* from 'start' (entered travelling start_dir) to 'goal' (left travelling goal_dir).
    step_cost(x, y, d_in, d_out) gives the cost of crossing a tile in that way, or None if impossible.
    no_double_turn forbids two consecutive turns in the same sense (a 90 degree turn for trains).
    """
    gx, gy = goal

    def h(x: int, y: int) -> float:
        return abs(x - gx) + abs(y - gy)

    counter = itertools.count()
    # state: (x, y, d_in, last_turn)
    start_state = (start[0], start[1], start_dir, 0)
    open_heap = [(h(*start), next(counter), 0.0, start_state)]
    best: dict[tuple, float] = {start_state: 0.0}
    parent: dict[tuple, tuple | None] = {start_state: None}
    expanded = 0
    while open_heap:
        _, _, g, state = heapq.heappop(open_heap)
        if g > best.get(state, math.inf):
            continue
        expanded += 1
        if expanded > max_nodes:
            return None
        x, y, d_in, last_turn = state
        candidates = [goal_dir] if (x, y) == goal else [d for d in DIRS if d != reverse(d_in)]
        for d_out in candidates:
            turn = turn_of(d_in, d_out)
            if no_double_turn and turn != 0 and turn == last_turn:
                continue
            if gmap.tile(x, y) in blocked:
                continue
            c = step_cost(x, y, d_in, d_out)
            if c is None:
                continue
            if (x, y) == goal:
                # Done: walk back.
                steps = [PathStep(x, y, d_in, d_out)]
                s = state
                while parent[s] is not None:
                    prev, prev_out = parent[s]
                    steps.append(PathStep(prev[0], prev[1], prev[2], prev_out))
                    s = prev
                return list(reversed(steps))
            dx, dy = DELTA[d_out]
            nstate = (x + dx, y + dy, d_out, turn)
            ng = g + c
            if ng < best.get(nstate, math.inf):
                best[nstate] = ng
                parent[nstate] = (state, d_out)
                heapq.heappush(open_heap, (ng + h(x + dx, y + dy), next(counter), ng, nstate))
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Results


@dataclass
class Route:
    kind: str                      # rail | road | air | ship
    company: int
    stations: list[int] = field(default_factory=list)
    depot_tile: int | None = None
    vehicles: list[int] = field(default_factory=list)
    template_vehicle: int | None = None
    engine: str = ""
    path_length: int = 0
    commands: int = 0
    buses: list[int] = field(default_factory=list)       # ferry: road vehicles crossing on the car ferry
    terminals: list[dict] = field(default_factory=list)  # ferry: bus stop, roads and depot at each dock

    def to_json(self) -> dict:
        return dict(self.__dict__)


class Builder:
    """Builds networks for the local company of one client (one bridge connection, one thread)."""

    def __init__(self, bridge: Bridge, log: Callable[[str], None] = print, as_company: int | None = None):
        """as_company: act for that company instead of the local one (server or single player only)."""
        self.b = bridge
        self.log = log
        self.as_company = as_company
        self.company = bridge.info()["local_company"] if as_company is None else as_company
        self.commands_sent = 0
        self.commands_failed = 0
        self.command_ms: list[float] = []
        self._engines: list[dict] | None = None

    # ---- command helpers ----

    def cmd(self, name: str, tile: int, *fields, allow_fail: bool = False) -> dict:
        import time
        t0 = time.monotonic()
        res = self.b.docmd(name, tile, *fields, as_company=self.as_company)
        self.command_ms.append((time.monotonic() - t0) * 1000)
        self.commands_sent += 1
        if not res["success"]:
            self.commands_failed += 1
            if not allow_fail:
                raise BuildError(f"{name} at {tile} failed: {res.get('error')}")
        return res

    def test_many(self, lines: list[tuple]) -> list[dict]:
        """Test-run many commands in one round trip: each entry is (name, tile, *fields)."""
        out = []
        for chunk_start in range(0, len(lines), 400):
            chunk = lines[chunk_start:chunk_start + 400]
            prefix = "docmd test " + (f"as {self.as_company} " if self.as_company is not None else "")
            reqs = [prefix + " ".join(quote_arg(int(a) if isinstance(a, bool) else a) for a in line) for line in chunk]
            for resp in self.b.batch(reqs):
                out.append(resp["result"] if resp.get("ok") else {"success": False, "error": resp.get("error")})
        return out

    def order(self, vehicle: int, kind: str, dest: int, *options: str) -> None:
        if self.as_company is not None:
            res = self.b.call("order", "as", self.as_company, vehicle, kind, dest, *options, timeout=70)
        else:
            res = self.b.order(vehicle, kind, dest, *options)
        self.commands_sent += 1
        if not res["success"]:
            self.commands_failed += 1
            raise BuildError(f"order {kind} {dest} for vehicle {vehicle} failed: {res.get('error')}")

    def give_money(self, amount: int) -> None:
        self.cmd("MoneyCheat", 0, amount)

    def engines(self) -> list[dict]:
        if self._engines is None:
            self._engines = self.b.engines()
        return self._engines

    def pick_engine(self, type_: str, cargo: str = "PASS", **filters) -> dict:
        cands = [e for e in self.engines() if e["type"] == type_ and e["cargo_label"] == cargo and e["capacity"] > 0]
        for k, v in filters.items():
            if callable(v):
                cands = [e for e in cands if v(e)]
            else:
                cands = [e for e in cands if e.get(k) == v]
        if not cands:
            raise BuildError(f"no buildable {type_} engine for {cargo} with {filters}")
        # Newest, then fastest.
        return max(cands, key=lambda e: (e["intro_year"], e["max_speed"]))

    def pick_car_ferry_engine(self) -> dict:
        """Newest ship that can be refitted to a car ferry (one that can carry goods)."""
        cands = [e for e in self.engines() if e["type"] == "ship" and e.get("car_ferry") and e["capacity"] > 0]
        if not cands:
            raise BuildError("no buildable ship that can carry goods")
        return max(cands, key=lambda e: (e["intro_year"], e["max_speed"]))

    def fresh_map(self) -> GameMap:
        return GameMap.fetch(self.b)

    # ---- vehicles ----

    def build_vehicle(self, depot_tile: int, engine: int) -> int:
        res = self.cmd("BuildVehicle", depot_tile, engine, False, INVALID_CARGO, 0)
        if "result_data" not in res:
            raise BuildError(f"BuildVehicle returned no vehicle id: {res}")
        return int(res["result_data"])

    def start_vehicle(self, vehicle: int) -> None:
        self.cmd("StartStopVehicle", 0, vehicle, False)

    def clone_vehicle(self, depot_tile: int, vehicle: int, start: bool = True) -> int:
        res = self.cmd("CloneVehicle", depot_tile, vehicle, True)
        if "result_data" not in res:
            raise BuildError(f"CloneVehicle returned no vehicle id: {res}")
        vid = int(res["result_data"])
        if start:
            self.start_vehicle(vid)
        return vid

    def add_vehicles(self, route: Route, count: int) -> list[int]:
        """Clone the route's first vehicle (shared orders) count times."""
        new = []
        for _ in range(count):
            new.append(self.clone_vehicle(route.depot_tile, route.template_vehicle))
        route.vehicles += new
        return new

    def _station_of(self, res: dict) -> int:
        if "station" not in res:
            raise BuildError(f"no station on the tile after {res.get('command')}: {res}")
        return int(res["station"])

    # ---- rail ----

    def build_rail_route(self, town_a: tuple[int, int], town_b: tuple[int, int], wagons: int = 3,
                         platform: int = 4) -> Route:
        """Two terminus stations (depot behind platform A) joined by a single track; one shuttle train."""
        start_cmds = self.commands_sent
        gmap = self.fresh_map()
        site_a = self._rail_station_site(gmap, town_a, town_b, platform)
        site_b = self._rail_station_site(gmap, town_b, town_a, platform, avoid={site_a["exit"]})
        self.log(f"rail: stations at {site_a['origin']} -> {site_b['origin']}")
        route = Route("rail", self.company)
        for site in (site_a, site_b):
            x, y = site["origin"]
            res = self.cmd("BuildRailStation", gmap.tile(x, y), RAILTYPE_RAIL, site["axis"], 1, platform, 0, 0, NEW_STATION, False)
            route.stations.append(self._station_of(res))
        dx, dy = site_a["depot"]
        self.cmd("BuildTrainDepot", gmap.tile(dx, dy), RAILTYPE_RAIL, site_a["depot_dir"])
        route.depot_tile = gmap.tile(dx, dy)

        steps = self._build_track(site_a["exit"], site_a["exit_dir"], site_b["exit"], reverse(site_b["exit_dir"]))
        route.path_length = len(steps)

        loco = self.pick_engine("rail", wagon=False, railtypes=lambda e: e["railtypes"] & (1 << RAILTYPE_RAIL))
        wagon = self.pick_engine("rail", wagon=True, railtypes=lambda e: e["railtypes"] & (1 << RAILTYPE_RAIL))
        train = self.build_vehicle(route.depot_tile, loco["id"])
        for _ in range(wagons):
            w = self.build_vehicle(route.depot_tile, wagon["id"])
            self.cmd("MoveRailVehicle", 0, w, train, 0)
        self.order(train, "station", route.stations[0])
        self.order(train, "station", route.stations[1])
        self.start_vehicle(train)
        route.vehicles.append(train)
        route.template_vehicle = train
        route.engine = loco["name"]
        route.commands = self.commands_sent - start_cmds
        return route

    def _rail_station_site(self, gmap: GameMap, town: tuple[int, int], towards: tuple[int, int], platform: int,
                           avoid: set = frozenset()) -> dict:
        main_dir = direction_towards(*town, *towards)
        dir_order = [main_dir] + [d for d in DIRS if d != main_dir and d != reverse(main_dir)] + [reverse(main_dir)]
        tests, sites = [], []
        for x, y in spiral(*town, 3, 12):
            for exit_dir in dir_order:
                ex, ey = DELTA[exit_dir]
                # Platform tiles p0..p(L-1) along exit_dir, depot behind p0, exit tile after p(L-1).
                tiles = [(x + ex * i, y + ey * i) for i in range(-1, platform + 2)]
                if not all(gmap.is_free(tx, ty) for tx, ty in tiles) or any(t in avoid for t in tiles):
                    continue
                plat = tiles[1:platform + 1]
                origin = min(plat)  # north-most tile of the platform
                site = {"origin": origin, "axis": axis_of(exit_dir), "depot": tiles[0], "depot_dir": exit_dir,
                        "exit": tiles[-2], "exit_dir": exit_dir, "probe_exit": tiles[-1]}
                sites.append(site)
                tests.append(("BuildRailStation", gmap.tile(*origin), RAILTYPE_RAIL, site["axis"], 1, platform, 0, 0, NEW_STATION, False))
                tests.append(("BuildTrainDepot", gmap.tile(*tiles[0]), RAILTYPE_RAIL, exit_dir))
            if len(sites) >= 60:
                break
        results = self.test_many(tests)
        for i, site in enumerate(sites):
            if results[2 * i]["success"] and results[2 * i + 1]["success"]:
                return site
        raise BuildError(f"no rail station site near {town}")

    def _rail_step_cost(self, gmap: GameMap) -> Callable[[int, int, int, int], float | None]:
        def cost(x: int, y: int, d_in: int, d_out: int) -> float | None:
            if not gmap.inside(x, y):
                return None
            i = gmap.tile(x, y)
            t = gmap.type[i]
            s = gmap.slope[i]
            if s & SLOPE_STEEP:
                return None
            c = 1.0 if d_in == d_out else 1.4
            if t in (CLEAR, TREES):
                pass
            elif t == ROAD and gmap.sub[i] == ROAD_NORMAL and d_in == d_out:
                # Level crossing: straight across a straight road.
                road_axis = AXIS_X if gmap.bits[i] == ROAD_X else AXIS_Y if gmap.bits[i] == ROAD_Y else None
                if road_axis is None or road_axis == axis_of(d_in) or s != SLOPE_FLAT:
                    return None
                c += 4
            else:
                return None
            if s != SLOPE_FLAT:
                c += 2.5 if d_in == d_out else 6
            return c
        return cost

    def _build_track(self, start: tuple[int, int], start_dir: int, goal: tuple[int, int], goal_dir: int,
                     attempts: int = 12) -> list[PathStep]:
        blocked: set[int] = set()
        for attempt in range(attempts):
            gmap = self.fresh_map()
            steps = plan_path(gmap, start, start_dir, goal, goal_dir, self._rail_step_cost(gmap), True, blocked)
            if steps is None:
                raise BuildError(f"no rail path {start} -> {goal}")
            pieces = [(gmap.tile(s.x, s.y), TRACK_FOR_EDGES[frozenset((reverse(s.d_in), s.d_out))]) for s in steps]
            results = self.test_many([("BuildSingleRail", t, RAILTYPE_RAIL, track, 0) for t, track in pieces])
            bad = [pieces[i][0] for i, r in enumerate(results) if not r["success"]]
            if bad:
                blocked.update(bad)
                continue
            for i, (t, track) in enumerate(pieces):
                res = self.cmd("BuildSingleRail", t, RAILTYPE_RAIL, track, 0, allow_fail=True)
                if not res["success"]:
                    # Something changed under us (town growth, another company): undo and re-plan around it.
                    self.log(f"rail: piece at {t} failed during build ({res.get('error')}), re-planning")
                    for t2, track2 in pieces[:i]:
                        self.cmd("RemoveSingleRail", t2, track2, allow_fail=True)
                    blocked.add(t)
                    break
            else:
                return steps
        raise BuildError(f"rail path {start} -> {goal} failed after {attempts} attempts")

    # ---- road ----

    def build_road_route(self, town_a: tuple[int, int], town_b: tuple[int, int], vehicles: int = 4) -> Route:
        start_cmds = self.commands_sent
        steps = self._build_road(town_a, town_b)
        route = Route("road", self.company, path_length=len(steps))
        gmap = self.fresh_map()

        stop_a = self._road_stop(gmap, steps, from_end=False)
        stop_b = self._road_stop(gmap, steps, from_end=True, avoid_near=stop_a[0])
        for idx, step in (stop_a, stop_b):
            res = self.cmd("BuildRoadStop", gmap.tile(step.x, step.y), 1, 1, ROADSTOP_BUS, True, step.d_out,
                           ROADTYPE_ROAD, 0, 0, NEW_STATION, False)
            route.stations.append(self._station_of(res))
        route.depot_tile = self._road_depot(gmap, steps, stop_a[0])

        bus = self.pick_engine("road", roadtype=ROADTYPE_ROAD)
        first = self.build_vehicle(route.depot_tile, bus["id"])
        self.order(first, "station", route.stations[0])
        self.order(first, "station", route.stations[1])
        self.start_vehicle(first)
        route.vehicles.append(first)
        route.template_vehicle = first
        route.engine = bus["name"]
        self.add_vehicles(route, vehicles - 1)
        route.commands = self.commands_sent - start_cmds
        return route

    def _road_step_cost(self, gmap: GameMap) -> Callable[[int, int, int, int], float | None]:
        def cost(x: int, y: int, d_in: int, d_out: int) -> float | None:
            if not gmap.inside(x, y, 1):
                return None
            i = gmap.tile(x, y)
            t = gmap.type[i]
            s = gmap.slope[i]
            if s & SLOPE_STEEP:
                return None
            if t == ROAD and gmap.sub[i] == ROAD_NORMAL:
                c = 0.4
            elif t in (CLEAR, TREES):
                c = 1.0
            else:
                return None
            if d_in != d_out:
                c += 0.3
            if s != SLOPE_FLAT:
                c += 1.0 if d_in == d_out else 3.0
            return c
        return cost

    def _road_bits(self, steps: list[PathStep], i: int) -> int:
        s = steps[i]
        bits = ROAD_BIT[s.d_out]
        if i > 0:
            bits |= ROAD_BIT[reverse(s.d_in)]
        return bits

    def _build_road(self, a: tuple[int, int], b: tuple[int, int], attempts: int = 12) -> list[PathStep]:
        blocked: set[int] = set()
        for attempt in range(attempts):
            gmap = self.fresh_map()
            best = None
            for d0 in DIRS:
                steps = plan_path(gmap, a, d0, b, direction_towards(*a, *b), self._road_step_cost(gmap), False, blocked)
                if steps and (best is None or len(steps) < len(best)):
                    best = steps
            if best is None:
                raise BuildError(f"no road path {a} -> {b}")
            steps = best
            # The last tile only needs the bit towards the previous tile.
            pieces = []
            for i, s in enumerate(steps):
                bits = self._road_bits(steps, i) if i < len(steps) - 1 else ROAD_BIT[reverse(s.d_in)]
                tile = gmap.tile(s.x, s.y)
                missing = bits & ~gmap.bits[tile] if gmap.type[tile] == ROAD else bits
                if missing:
                    pieces.append((tile, missing))
            results = self.test_many([("BuildRoad", t, bits, ROADTYPE_ROAD, 0, INVALID_TOWN, 0) for t, bits in pieces])
            bad = [pieces[i][0] for i, r in enumerate(results) if not r["success"] and "lready built" not in str(r.get("error"))]
            if bad:
                blocked.update(bad)
                continue
            failed = None
            for t, bits in pieces:
                res = self.cmd("BuildRoad", t, bits, ROADTYPE_ROAD, 0, INVALID_TOWN, 0, allow_fail=True)
                if not res["success"] and "lready built" not in str(res.get("error")):
                    failed = t
                    break
            if failed is None:
                return steps
            self.log(f"road: piece at {failed} failed during build, re-planning")
            blocked.add(failed)
        raise BuildError(f"road path {a} -> {b} failed after {attempts} attempts")

    def _road_stop(self, gmap: GameMap, steps: list[PathStep], from_end: bool, avoid_near: int | None = None) -> tuple[int, PathStep]:
        order = list(range(len(steps) - 2, 0, -1)) if from_end else list(range(1, len(steps) - 1))
        cands = []
        for i in order[:25]:
            s = steps[i]
            if s.d_in != s.d_out:
                continue
            if avoid_near is not None and abs(i - avoid_near) < 6:
                continue
            cands.append((i, s))
        results = self.test_many([("BuildRoadStop", gmap.tile(s.x, s.y), 1, 1, ROADSTOP_BUS, True, s.d_out, ROADTYPE_ROAD,
                                   0, 0, NEW_STATION, False) for _, s in cands])
        for (i, s), r in zip(cands, results):
            if r["success"]:
                return i, s
        raise BuildError("no drive-through road stop site on the route")

    def _road_depot(self, gmap: GameMap, steps: list[PathStep], near: int) -> int:
        cands = []
        for i in sorted(range(1, len(steps) - 1), key=lambda i: abs(i - near)):
            s = steps[i]
            if s.d_in != s.d_out or abs(i - near) < 2:
                continue
            for side in DIRS:
                if axis_of(side) == axis_of(s.d_in):
                    continue
                dx, dy = DELTA[side]
                if gmap.is_free(s.x + dx, s.y + dy, flat=True):
                    cands.append((s, side))
            if len(cands) >= 20:
                break
        tests = []
        for s, side in cands:
            dx, dy = DELTA[side]
            tests.append(("BuildRoadDepot", gmap.tile(s.x + dx, s.y + dy), ROADTYPE_ROAD, reverse(side)))
            tests.append(("BuildRoad", gmap.tile(s.x, s.y), ROAD_BIT[side], ROADTYPE_ROAD, 0, INVALID_TOWN, 0))
        results = self.test_many(tests)
        for k, (s, side) in enumerate(cands):
            if results[2 * k]["success"] and results[2 * k + 1]["success"]:
                dx, dy = DELTA[side]
                depot = gmap.tile(s.x + dx, s.y + dy)
                self.cmd("BuildRoadDepot", depot, ROADTYPE_ROAD, reverse(side))
                self.cmd("BuildRoad", gmap.tile(s.x, s.y), ROAD_BIT[side], ROADTYPE_ROAD, 0, INVALID_TOWN, 0)
                return depot
        raise BuildError("no road depot site next to the route")

    # ---- air ----

    def build_air_route(self, town_a: tuple[int, int], town_b: tuple[int, int], vehicles: int = 2) -> Route:
        start_cmds = self.commands_sent
        route = Route("air", self.company)
        gmap = self.fresh_map()
        airports = []
        for town in (town_a, town_b):
            origin, airport_type, size = self._airport_site(gmap, town)
            res = self.cmd("BuildAirport", gmap.tile(*origin), airport_type, 0, NEW_STATION, False)
            route.stations.append(self._station_of(res))
            airports.append((origin, size))
        plane = self.pick_engine("aircraft", fast=False, helicopter=False)
        # The hangar is wherever building an aircraft works.
        (ox, oy), (w, h) = airports[0]
        hangar_tiles = [gmap.tile(ox + dx, oy + dy) for dy in range(h) for dx in range(w)]
        results = self.test_many([("BuildVehicle", t, plane["id"], False, INVALID_CARGO, 0) for t in hangar_tiles])
        hangar = next((t for t, r in zip(hangar_tiles, results) if r["success"]), None)
        if hangar is None:
            raise BuildError(f"no hangar found in airport at {airports[0]}: {results[0].get('error')}")
        route.depot_tile = hangar
        first = self.build_vehicle(hangar, plane["id"])
        self.order(first, "station", route.stations[0])
        self.order(first, "station", route.stations[1])
        self.start_vehicle(first)
        route.vehicles.append(first)
        route.template_vehicle = first
        route.engine = plane["name"]
        self.add_vehicles(route, vehicles - 1)
        route.commands = self.commands_sent - start_cmds
        return route

    def _airport_site(self, gmap: GameMap, town: tuple[int, int]) -> tuple[tuple[int, int], int, tuple[int, int]]:
        errors: set[str] = set()
        for airport_type, (w, h) in AIRPORT_TYPES:
            cands = []
            for x, y in spiral(*town, 3, 16):
                if all(gmap.is_free(x + dx, y + dy) for dx in range(-1, w + 1) for dy in range(-1, h + 1)):
                    cands.append((x, y))
                if len(cands) >= 80:
                    break
            results = self.test_many([("BuildAirport", gmap.tile(x, y), airport_type, 0, NEW_STATION, False) for x, y in cands])
            for c, r in zip(cands, results):
                if r["success"]:
                    return c, airport_type, (w, h)
            errors |= {str(r.get("error")) for r in results}
        raise BuildError(f"no airport site near {town} ({sorted(errors)[:3]})")

    # ---- ships ----

    def build_ship_route(self, near: tuple[int, int], vehicles: int = 2, min_dist: int = 16, max_dist: int = 60,
                         car_ferry: bool = False, start: bool = True) -> Route:
        """car_ferry: refit the ships to carry road vehicles, with both docks on the open sea (river mouths can be
        impassable). start: start the first ship."""
        start_cmds = self.commands_sent
        gmap = self.fresh_map()
        bodies = gmap.water_bodies(sea_only=car_ferry)
        sites = self._dock_sites(gmap, bodies, sea_only=car_ferry)
        if not sites:
            raise BuildError("no dock sites on the map")
        sites.sort(key=lambda s: (s[0] - near[0]) ** 2 + (s[1] - near[1]) ** 2)
        results = self.test_many([("BuildDock", gmap.tile(x, y), NEW_STATION, False) for x, y, _, _ in sites[:300]])
        ok = [s for s, r in zip(sites, results) if r["success"]]
        pair = None
        for a in ok[:60]:
            for b in ok:
                d = abs(a[0] - b[0]) + abs(a[1] - b[1])
                if a[3] == b[3] and min_dist <= d <= max_dist:
                    pair = (a, b)
                    break
            if pair:
                break
        if pair is None:
            raise BuildError(f"no dock pair {min_dist}-{max_dist} tiles apart on one water body ({len(ok)} dock sites)")
        route = Route("ship", self.company)
        for x, y, _, _ in pair:
            res = self.cmd("BuildDock", gmap.tile(x, y), NEW_STATION, False)
            route.stations.append(self._station_of(res))
        route.depot_tile = self._ship_depot(gmap, pair[0])
        ship = self.pick_car_ferry_engine() if car_ferry else self.pick_engine("ship")
        first = self.build_vehicle(route.depot_tile, ship["id"])
        if car_ferry:
            self.cmd("SetCarFerry", 0, first, True)
        self.order(first, "station", route.stations[0])
        self.order(first, "station", route.stations[1])
        if start:
            self.start_vehicle(first)
        route.vehicles.append(first)
        route.template_vehicle = first
        route.engine = ship["name"]
        self.add_vehicles(route, vehicles - 1)
        route.path_length = abs(pair[0][0] - pair[1][0]) + abs(pair[0][1] - pair[1][1])
        route.commands = self.commands_sent - start_cmds
        return route

    # ---- car ferries ----

    def build_car_ferry_route(self, near: tuple[int, int], buses: int = 2, start_ferry: bool = True) -> Route:
        """
        A car ferry between two docks, a bus terminal at each dock and buses with orders: board at dock 1, leave at
        dock 2, board at dock 2, leave at dock 1. Route.vehicles is the ferry followed by the buses (none if buses=0).
        """
        start_cmds = self.commands_sent
        route = self.build_ship_route(near, vehicles=1, min_dist=12, max_dist=40, car_ferry=True, start=start_ferry)
        route.kind = "ferry"
        s1, s2 = route.stations
        route.terminals = [self._ferry_terminal(s1), self._ferry_terminal(s2)]
        if buses == 0:
            route.commands = self.commands_sent - start_cmds
            return route
        bus_engine = self.pick_engine("road", roadtype=ROADTYPE_ROAD)
        depot = route.terminals[0]["depot"]
        bus = self.build_vehicle(depot, bus_engine["id"])
        for station in (s1, s2, s2, s1):
            self.order(bus, "station", station)
        for index, action in enumerate((FERRY_BOARD, FERRY_LEAVE, FERRY_BOARD, FERRY_LEAVE)):
            self.cmd("ModifyOrder", 0, bus, index, MOF_FERRY_ACTION, action, INVALID_CARGO, "")
        self.start_vehicle(bus)
        route.buses = [bus] + [self.clone_vehicle(depot, bus) for _ in range(buses - 1)]
        route.vehicles += route.buses
        route.engine += f" + {buses} x {bus_engine['name']}"
        route.commands = self.commands_sent - start_cmds
        return route

    def _ferry_terminal(self, station: int) -> dict:
        """A drive-through bus stop joined to a dock station, a short straight road and a road depot behind it."""
        gmap = self.fresh_map()
        sx, sy = gmap.xy(self.b.obj("station", station, "xy")["xy"])
        for tx, ty in spiral(sx, sy, 1, 3):
            for axis in (AXIS_X, AXIS_Y):
                ax, ay = (1, 0) if axis == AXIS_X else (0, 1)
                along = SW if axis == AXIS_X else SE        # direction of +axis
                back = NE if axis == AXIS_X else NW         # direction of -axis
                bits = ROAD_X if axis == AXIS_X else ROAD_Y
                if not gmap.inside(tx - ax, ty - ay) or not gmap.inside(tx + 3 * ax, ty + 3 * ay):
                    continue
                stop = gmap.tile(tx, ty)
                roads = [gmap.tile(tx - ax, ty - ay), gmap.tile(tx + ax, ty + ay), gmap.tile(tx + 2 * ax, ty + 2 * ay)]
                depot = gmap.tile(tx + 3 * ax, ty + 3 * ay)
                plan = [("BuildRoadStop", stop, 1, 1, ROADSTOP_BUS, True, along, ROADTYPE_ROAD, 0, 0, station, False)]
                plan += [("BuildRoad", t, bits, ROADTYPE_ROAD, 0, INVALID_TOWN, 0) for t in roads]
                plan.append(("BuildRoadDepot", depot, ROADTYPE_ROAD, back))
                if all(r["success"] for r in self.test_many(plan)):
                    for line in plan:
                        self.cmd(*line)
                    return {"station": station, "stop": stop, "roads": roads, "depot": depot}
        raise BuildError(f"no bus terminal site next to dock station {station}")

    def _dock_sites(self, gmap: GameMap, bodies: dict[int, int], sea_only: bool = False) -> list[tuple[int, int, int, int]]:
        """(x, y, water_dir, water_body) of sloped free land tiles with open water in front."""
        sites = []
        for y in range(2, gmap.sy - 2):
            for x in range(2, gmap.sx - 2):
                i = gmap.tile(x, y)
                if gmap.type[i] not in (CLEAR, TREES) or gmap.slope[i] not in (3, 6, 9, 12):
                    continue
                for d in DIRS:
                    dx, dy = DELTA[d]
                    w1 = (x + dx, y + dy)
                    w2 = (x + 2 * dx, y + 2 * dy)
                    if gmap.is_sea(*w1, sea_only) and gmap.is_sea(*w2, sea_only) and gmap.is_sea(x + 3 * dx, y + 3 * dy, sea_only):
                        sites.append((x, y, d, bodies[gmap.tile(*w2)]))
                        break
        return sites

    def _ship_depot(self, gmap: GameMap, dock: tuple[int, int, int, int]) -> int:
        x, y, d, body = dock
        dx, dy = DELTA[d]
        cx, cy = x + 4 * dx, y + 4 * dy
        cands = []
        for tx, ty in spiral(cx, cy, 0, 8):
            for axis in (AXIS_X, AXIS_Y):
                ox, oy = (1, 0) if axis == AXIS_X else (0, 1)
                if gmap.is_sea(tx, ty) and gmap.is_sea(tx + ox, ty + oy):
                    cands.append((gmap.tile(tx, ty), axis))
            if len(cands) >= 60:
                break
        results = self.test_many([("BuildShipDepot", t, axis) for t, axis in cands])
        for (t, axis), r in zip(cands, results):
            if r["success"]:
                self.cmd("BuildShipDepot", t, axis)
                return t
        raise BuildError(f"no ship depot site near dock at {(x, y)}")
