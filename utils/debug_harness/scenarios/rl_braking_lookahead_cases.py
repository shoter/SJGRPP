"""
Realistic braking lookahead cutoff, item by item: a purpose-built loop puts every kind of lookahead item in front of
trains with different orders, and the game runs under several braking settings with chicken bit 9 on (every speed
limit calculation also applies all items and asserts the result is the same).

The loop: two parallel one-way tracks joined by 90 degree curves (curve speed items), path signals every few tiles
(signal items), a hill, a 6-level descent and a valley (items far below the train, where the downhill braking distance
can exceed the level one, so the lowest item z matters for the cutoff), a 64 km/h girder bridge over the
valley (track speed item), stations the trains stop at and pass through, and a waypoint (station items). Eight trains:
non-stop and stop-everywhere orders, a waypoint order, a conditional order (evaluated in dry-run mode by the lookahead),
an order max speed, near-end/middle stop locations, and short/long/slow/fast consists.

Phases (each with the check on): realistic acceleration; aspect-limited signals (signal items limit the speed);
steepest slopes; the original acceleration model; and a save/load (the item bounds are rebuilt after loading).
Each phase checks every train keeps moving; over the run the trains must serve their stops and the lookaheads must
have held every item type the track provides (station, signal, curve speed, track speed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transport import NEW_STATION, RAILTYPE_RAIL, Builder  # noqa: E402

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 0, "quantity_sea_lakes": 0, "number_towns": 0, "industry_density": 0, "max_no_competitors": 0,
                   "vehicle_breakdowns": 0},
    "pf": {"forbid_90_deg": "false"},
}
DAY_TICKS = 74
CHECK_BIT = 1 << 9            # DCBF_CHECK_RL_LOOKAHEAD_CUTOFF

# Track and direction enums
TRACK_X, TRACK_Y, TRACK_UPPER, TRACK_LOWER, TRACK_LEFT, TRACK_RIGHT = range(6)
DIAGDIR_SE = 1
AXIS_X = 0
SIGTYPE_PATH = 4
LEVEL, LOWER, RAISE = 0, 1, 2
MOF_STOP_LOCATION = 1
MOF_COND_VARIABLE, MOF_COND_COMPARATOR, MOF_COND_VALUE, MOF_COND_DESTINATION = 5, 6, 7, 12
COND_LOAD_PERCENTAGE, COND_LESS_THAN = 0, 2
STOP_NEAR_END, STOP_MIDDLE = 0, 1
MTF_TRAVEL_SPEED = 2
STAT_CLASS_WAYP = 1
INVALID_STATION = 0xFFFF

# Layout: track A runs towards +x on row YA, track B towards -x on row YB.
YA, YB = 100, 103
XS, XE = 40, 200
SA1, SA2, SB1, SB2 = (50, 5), (80, 3), (180, 5), (100, 3)   # (first x, platform length)
WAYPOINT_X = 175
HILL = ((110, 130), (114, 126))   # raised twice: two levels with slopes on each side
DESCENT_X, DESCENT_LEVELS = 134, 6  # from here east the land steps down 6 levels, one every 2 tiles
VALLEY = (150, 155)               # lowered once; track A crosses it on a bridge between the slopes at 149 and 155
BRIDGE = (149, 155)
BRIDGE_TYPE = 2                   # steel girder: 64 km/h, slower than every train used


def setup_settings(b) -> None:
    """Server side: realistic braking, the cutoff check, and no company limit on the landscaping the track needs."""
    for name, value in (("vehicle.train_braking_model", 1), ("debug.chicken_bits", CHECK_BIT),
                        ("construction.terraform_per_64k_frames", 1 << 30), ("construction.terraform_frame_burst", 1 << 15),
                        ("construction.clear_per_64k_frames", 1 << 30), ("construction.clear_frame_burst", 1 << 15)):
        b.console(f"setting {name} {value}")


def tile(x: int, y: int) -> int:
    return y * 256 + x


class TestTrack:
    """Builds the loop through a Builder (local company, any bridge) and keeps the ids."""

    def __init__(self, b, builder: Builder, note, wait):
        self.b = b
        self.wait = wait      # runs game ticks: the company's landscaping allowance refills over time
        self.builder = builder
        self.note = note
        self.stations: dict[str, int] = {}
        self.depot = tile(XS + 2, YA - 1)
        self.trains: list[int] = []

    def cmd(self, name, t, *fields):
        if name in ("LevelLand", "ClearArea"):
            self.wait(8)
        return self.builder.cmd(name, t, *fields)

    def build(self) -> None:
        cmd = self.cmd
        x0, x1, y0, y1 = XS - 6, XE + 6, YA - 4, YB + 5
        cmd("ClearArea", tile(x1, y1), tile(x0, y0), False)
        cmd("LevelLand", tile(x1, y1), tile(x0, y0), False, LEVEL)
        # The west raised in shrinking steps: high up to DESCENT_X, then one level down every 2 tiles.
        for k in range(DESCENT_LEVELS):
            cmd("LevelLand", tile(DESCENT_X + 2 * (DESCENT_LEVELS - 1 - k), y1), tile(x0, y0), False, RAISE)
        for xa, xb in HILL:
            cmd("LevelLand", tile(xb, y1), tile(xa, y0), False, RAISE)
        cmd("LevelLand", tile(VALLEY[1], y1), tile(VALLEY[0], y0), False, LOWER)

        for name, (x, length), y in (("SA1", SA1, YA), ("SA2", SA2, YA), ("SB1", SB1, YB), ("SB2", SB2, YB)):
            res = cmd("BuildRailStation", tile(x, y), RAILTYPE_RAIL, AXIS_X, 1, length, 0, 0, NEW_STATION, False)
            self.stations[name] = res["station"]
        cmd("BuildBridge", tile(BRIDGE[1], YA), tile(BRIDGE[0], YA), 0, BRIDGE_TYPE, RAILTYPE_RAIL, 0)

        def rail(xa, xb, y):
            cmd("BuildRailroadTrack", tile(xb, y), tile(xa, y), RAILTYPE_RAIL, TRACK_X, 0, False)

        # Track A, around stations SA1/SA2 and the bridge heads.
        rail(XS, SA1[0] - 1, YA)
        rail(SA1[0] + SA1[1], SA2[0] - 1, YA)
        rail(SA2[0] + SA2[1], BRIDGE[0] - 1, YA)
        rail(BRIDGE[1] + 1, XE, YA)
        # Track B, around stations SB2/SB1.
        rail(XS, SB2[0] - 1, YB)
        rail(SB2[0] + SB2[1], SB1[0] - 1, YB)
        rail(SB1[0] + SB1[1], XE, YB)
        # The ends: 90 degree curves joining A and B.
        for y, track in ((YA, TRACK_RIGHT), (YA + 1, TRACK_Y), (YA + 2, TRACK_Y), (YB, TRACK_UPPER)):
            cmd("BuildSingleRail", tile(XE + 1, y), RAILTYPE_RAIL, track, 0)
        for y, track in ((YB, TRACK_LEFT), (YA + 1, TRACK_Y), (YA + 2, TRACK_Y), (YA, TRACK_LOWER)):
            cmd("BuildSingleRail", tile(XS - 1, y), RAILTYPE_RAIL, track, 0)
        # Depot north of track A, joined towards +x.
        cmd("BuildTrainDepot", self.depot, RAILTYPE_RAIL, DIAGDIR_SE)
        cmd("BuildSingleRail", tile(XS + 2, YA), RAILTYPE_RAIL, TRACK_LEFT, 0)
        res = cmd("BuildRailWaypoint", tile(WAYPOINT_X, YA), AXIS_X, 1, 1, STAT_CLASS_WAYP, 0, INVALID_STATION, False)
        self.stations["WA"] = res["station"]

        # Path signals facing the direction of travel: dragged along it, between stations, bridge and junctions.
        def signals(xa, xb, y):
            cmd("BuildSignalTrack", tile(xa, y), tile(xb, y), TRACK_X, SIGTYPE_PATH, 0, 0, False, 0, 4)

        signals(XS + 4, SA1[0] - 1, YA)
        signals(SA1[0] + SA1[1], SA2[0] - 1, YA)
        signals(SA2[0] + SA2[1], BRIDGE[0] - 1, YA)
        signals(BRIDGE[1] + 1, WAYPOINT_X - 1, YA)
        signals(WAYPOINT_X + 1, XE, YA)
        signals(XE, SB1[0] + SB1[1], YB)
        signals(SB1[0] - 1, SB2[0] + SB2[1], YB)
        signals(SB2[0] - 1, XS, YB)
        self.note(f"test track built: stations {self.stations}, depot {self.depot}")

    def add_trains(self) -> None:
        bld, st = self.builder, self.stations
        engines = sorted((e for e in self.b.engines("rail") if not e.get("wagon") and e["railtypes"] & (1 << RAILTYPE_RAIL)),
                         key=lambda e: e.get("max_speed", 0))
        wagon = bld.pick_engine("rail", wagon=True, railtypes=lambda e: e["railtypes"] & (1 << RAILTYPE_RAIL))["id"]
        slow, fast = engines[0]["id"], engines[-1]["id"]
        mid = engines[len(engines) // 2]["id"]
        self.note(f"engines: slow {engines[0]['name']}, mid {engines[len(engines) // 2]['name']}, fast {engines[-1]['name']}")

        # (engine, wagons, orders, extra setup)
        plans = [
            (fast, 3, [("station", st["SA1"]), ("station", st["SB1"])]),                       # non-stop: passes SA2/SB2
            (mid, 4, [("station", st["SA1"], "stop_everywhere"), ("station", st["SB1"], "stop_everywhere")]),
            (fast, 2, [("station", st["SA1"]), ("station", st["SA2"]), ("waypoint", st["WA"]),
                       ("station", st["SB1"]), ("station", st["SB2"])]),
            (mid, 3, [("station", st["SA1"]), ("conditional", 0), ("station", st["SB2"]), ("station", st["SB1"])]),
            (fast, 5, [("station", st["SA1"]), ("station", st["SB1"])]),                       # order max speed below
            (mid, 4, [("station", st["SA1"]), ("station", st["SB1"])]),                        # stop locations below
            (slow, 2, [("station", st["SA2"]), ("station", st["SB2"])]),
            (fast, 8, [("station", st["SB1"]), ("station", st["SA1"])]),
        ]
        for i, (engine, wagons, orders) in enumerate(plans):
            train = bld.build_vehicle(self.depot, engine)
            for _ in range(wagons):
                w = bld.build_vehicle(self.depot, wagon)
                bld.cmd("MoveRailVehicle", 0, w, train, 0)
            for kind, dest, *opts in orders:
                bld.order(train, kind, dest, *opts)
            if i == 3:
                # After SA1: skip SB2 when less than half loaded (appended pointing at order 0, then retargeted).
                for mof, data in ((MOF_COND_VARIABLE, COND_LOAD_PERCENTAGE), (MOF_COND_COMPARATOR, COND_LESS_THAN),
                                  (MOF_COND_VALUE, 50), (MOF_COND_DESTINATION, 3)):
                    bld.cmd("ModifyOrder", 0, train, 1, mof, data, 0, "")
            if i == 4:
                bld.cmd("ChangeTimetable", 0, train, 1, MTF_TRAVEL_SPEED, 40, 0)
            if i == 5:
                bld.cmd("ModifyOrder", 0, train, 0, MOF_STOP_LOCATION, STOP_NEAR_END, 0, "")
                bld.cmd("ModifyOrder", 0, train, 1, MOF_STOP_LOCATION, STOP_MIDDLE, 0, "")
            self.trains.append(train)

    def start_trains(self, wait) -> None:
        for train in self.trains:
            self.builder.start_vehicle(train)
            wait(DAY_TICKS)


class Watch:
    """Samples the trains: stations served, distance moved, lookahead item types and list lengths."""

    def __init__(self, b, trains):
        self.b = b
        self.trains = trains
        self.types: set[str] = set()
        self.max_items = 0
        self.max_ahead = 0
        self.with_lookahead = 0
        self.visited: dict[int, set] = {t: set() for t in trains}

    def state(self) -> dict:
        return {t: self.b.obj("vehicle", t, "tile", "cur_speed", "last_station_visited") for t in self.trains}

    def sample_lookahead(self) -> None:
        for t in self.trains:
            la = self.b.lookahead(t)
            if not la:
                continue
            self.with_lookahead += 1
            self.types.update(i["type"] for i in la["items"])
            self.max_items = max(self.max_items, len(la["items"]))
            self.max_ahead = max(self.max_ahead, la["reservation_end_position"] - la["current_position"])


def run_phase(ctx, b, step, watch: Watch, label: str, days: int) -> None:
    start = watch.state()
    visited = watch.visited
    moved = set()
    for day in range(days):
        step(DAY_TICKS)
        now = watch.state()
        for t, s in now.items():
            visited[t].add(s["last_station_visited"])
            if s["tile"] != start[t]["tile"]:
                moved.add(t)
        if day % 3 == 0:
            watch.sample_lookahead()
    ctx.check(len(moved) == len(watch.trains), f"{label}: all {len(watch.trains)} trains moved ({len(moved)})")


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    setup_settings(b)
    builder = Builder(b, log=ctx.note)
    builder.give_money(500_000_000)

    track = TestTrack(b, builder, ctx.note, b.step)
    track.build()
    track.add_trains()
    track.start_trains(b.step)
    watch = Watch(b, track.trains)

    run_phase(ctx, b, b.step, watch, "realistic acceleration", 40)
    b.console("setting vehicle.realistic_braking_aspect_limited 1")
    run_phase(ctx, b, b.step, watch, "aspect limited signals", 25)
    b.console("setting vehicle.realistic_braking_aspect_limited 0")
    b.console("setting vehicle.train_slope_steepness 10")
    run_phase(ctx, b, b.step, watch, "steepest slopes", 25)
    b.console("setting vehicle.train_acceleration_model 0")
    run_phase(ctx, b, b.step, watch, "original acceleration", 25)
    b.console("setting vehicle.train_acceleration_model 1")
    b.save_game("rl_cases")
    b.load_game("rl_cases")
    ctx.check(b.console("setting debug.chicken_bits")[0].split("'")[3] == str(CHECK_BIT), "check still on after load")
    run_phase(ctx, b, b.step, watch, "after save/load", 40)

    served = {t: sorted(v - {65535}) for t, v in watch.visited.items()}
    ctx.note(f"stations served: {served}")
    # One busy loop: slow trains hold up the ones behind them, so the last ones out reach their first stop late.
    ctx.check(all(len(v) >= 1 for v in served.values()), "every train served a stop")
    ctx.check(sum(len(v) >= 2 for v in served.values()) >= 6, "most trains served at least two stops")
    ctx.note(f"lookahead item types seen: {sorted(watch.types)}; longest list {watch.max_items} items, "
             f"reservation up to {watch.max_ahead} units ahead")
    for kind in ("station", "signal", "curve_speed", "track_speed"):
        ctx.check(kind in watch.types, f"lookaheads held {kind} items")
    # Signals every few tiles and a busy loop keep these lists short; rl_braking_lookahead.py covers long ones.
    ctx.check(watch.max_items >= 5, f"lookaheads held several items at once ({watch.max_items})")

    ctx.check(inst.alive() and not inst.crash_files(), "game did not crash or assert")
    stderr = inst.dir / "stderr.log"
    text = stderr.read_text(encoding="utf-8", errors="replace") if stderr.exists() else ""
    ctx.check("cutoff mismatch" not in text, "no realistic braking lookahead cutoff mismatch in the log")

    b.console("setting gui.show_track_reservation 1")
    b.console(f"scrollto instant {tile(BRIDGE[0] - 4, YA + 1)}")
    b.step(1)
    ctx.screenshot(b, "test_track")
