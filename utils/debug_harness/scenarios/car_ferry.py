"""Car ferry: a bus boards a ship refitted to carry road vehicles, crosses the water and drives off at the other dock."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ottd import BridgeError  # noqa: E402
from transport import (AXIS_X, INVALID_CARGO, INVALID_TOWN, NE, NEW_STATION, NW, ROAD_X, ROAD_Y, ROADSTOP_BUS,  # noqa: E402
                       ROADTYPE_ROAD, SE, SW, BuildError, Builder, spiral)

MOF_FERRY_ACTION = 25           # ModifyOrderFlags::MOF_FERRY_ACTION
FERRY_NONE, FERRY_BOARD, FERRY_LEAVE = 0, 1, 2
SHIP_FLAG_CAR_FERRY = 1 << 1    # VehicleShipFlag::CarFerry
VEH_HIDDEN = 1 << 0             # VehState::Hidden
INVALID_VEHICLE = 0xFFFFF
INVALID_STATION = 0xFFFF
OT_LOADING = 3
TICKS_PER_DAY = 74

WC_VEHICLE_VIEW = "VehicleView"
WID_VV_REFIT = 9
WID_VV_SHOW_ORDERS = 10
WID_VV_SHOW_DETAILS = 11


def tile_xy(b, tile: int) -> tuple[int, int]:
    mx = b.info()["map"]["x"]
    return tile % mx, tile // mx


def build_terminal(builder: Builder, station: int) -> dict:
    """A drive-through bus stop joined to a dock station, with a short straight road and a road depot behind it."""
    b = builder.b
    gmap = builder.fresh_map()
    sx, sy = tile_xy(b, b.obj("station", station, "xy")["xy"])
    for tx, ty in spiral(sx, sy, 1, 3):
        for axis in (AXIS_X, 1 - AXIS_X):
            ax, ay = (1, 0) if axis == AXIS_X else (0, 1)
            along = SW if axis == AXIS_X else SE        # direction of +axis
            back = NE if axis == AXIS_X else NW         # direction of -axis
            bits = ROAD_X if axis == AXIS_X else ROAD_Y
            stop = gmap.tile(tx, ty)
            roads = [gmap.tile(tx - ax, ty - ay), gmap.tile(tx + ax, ty + ay), gmap.tile(tx + 2 * ax, ty + 2 * ay)]
            depot = gmap.tile(tx + 3 * ax, ty + 3 * ay)
            plan = [("BuildRoadStop", stop, 1, 1, ROADSTOP_BUS, True, along, ROADTYPE_ROAD, 0, 0, station, False)]
            plan += [("BuildRoad", t, bits, ROADTYPE_ROAD, 0, INVALID_TOWN, 0) for t in roads]
            plan.append(("BuildRoadDepot", depot, ROADTYPE_ROAD, back))
            if all(r["success"] for r in builder.test_many(plan)):
                for line in plan:
                    builder.cmd(*line)
                return {"stop": stop, "roads": roads, "depot": depot}
    raise BuildError(f"no bus terminal site next to dock station {station}")


def set_ferry_action(b, vehicle: int, order: int, action: int) -> dict:
    return b.docmd("ModifyOrder", 0, vehicle, order, MOF_FERRY_ACTION, action, INVALID_CARGO, "")


def setup_car_ferry_line(b, wait, log=print) -> dict:
    """Ship route between two docks with a car ferry on it, a bus terminal at each dock and a bus with orders: board at
    dock 1, leave at dock 2, board at dock 2, leave at dock 1. The ferry is started once the bus waits at dock 1.
    wait(pred, max_ticks) lets the game run until pred() holds."""
    b.docmd("MoneyCheat", 0, 50_000_000)
    builder = Builder(b, log=log)
    towns = [b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town"))]
    route = builder.build_ship_route(tile_xy(b, towns[0]), vehicles=1, min_dist=12, max_dist=40)
    s1, s2 = route.stations
    # The route's own ship stays a passenger ship; the car ferry is a second one, refitted in the depot.
    engine = b.obj("vehicle", route.vehicles[0], "engine_type")["engine_type"]
    ship = builder.build_vehicle(route.depot_tile, engine)
    res = b.docmd("SetCarFerry", 0, ship, True)
    if not res["success"]:
        raise BuildError(f"SetCarFerry failed: {res.get('error')}")
    builder.order(ship, "station", s1)
    builder.order(ship, "station", s2)

    t1 = build_terminal(builder, s1)
    t2 = build_terminal(builder, s2)
    bus_engine = builder.pick_engine("road", roadtype=ROADTYPE_ROAD)
    bus = builder.build_vehicle(t1["depot"], bus_engine["id"])
    for station in (s1, s2, s2, s1):
        builder.order(bus, "station", station)
    for order, action in enumerate((FERRY_BOARD, FERRY_LEAVE, FERRY_BOARD, FERRY_LEAVE)):
        res = set_ferry_action(b, bus, order, action)
        if not res["success"]:
            raise BuildError(f"ModifyOrder ferry action failed: {res.get('error')}")
    builder.start_vehicle(bus)
    if not wait(lambda: b.obj("vehicle", bus, "last_station_visited")["last_station_visited"] == s1, 20 * TICKS_PER_DAY):
        raise BuildError("bus did not reach dock 1")
    builder.start_vehicle(ship)
    return {"ship": ship, "bus": bus, "s1": s1, "s2": s2, "t1": t1, "t2": t2, "ship_depot": route.depot_tile, "builder": builder}


def veh(b, vid: int, *fields: str) -> dict:
    return b.obj("vehicle", vid, *fields)


def step_until(b, pred, max_ticks: int, chunk: int = 20) -> int:
    """Step the game until pred() holds; returns ticks stepped, or -1 on timeout."""
    done = 0
    while done < max_ticks:
        if pred():
            return done
        b.step(chunk)
        done += chunk
    return done if pred() else -1


def show_vehicle(b, vid: int, *buttons: int) -> None:
    """Open the vehicle view of vid (the bridge opens the n-th primary vehicle) and click some of its buttons."""
    for n in range(256):
        before = {w["number"] for w in b.windows() if w["class_name"] == WC_VEHICLE_VIEW}
        b.call("show", "vehicle", n)
        b.wait_frames(1)
        new = [w["number"] for w in b.windows() if w["class_name"] == WC_VEHICLE_VIEW and w["number"] not in before]
        if vid in new or (not new and vid in before):
            break
        for number in new:
            b.close_window(WC_VEHICLE_VIEW, number)
    for button in buttons:
        b.click_widget(WC_VEHICLE_VIEW, vid, button)
        b.wait_frames(2)


def close_windows(b, pred) -> None:
    for w in b.windows():
        if pred(w["class_name"]):
            try:
                b.close_window(w["class"], w["number"])
            except BridgeError:
                pass  # Already closed together with its parent.


def close_news(b) -> None:
    close_windows(b, lambda name: "News" in name)


def aboard(b, bus: int) -> bool:
    return veh(b, bus, "ferry_carrier")["ferry_carrier"] != INVALID_VEHICLE


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.step(2)

    line = setup_car_ferry_line(b, lambda pred, ticks: step_until(b, pred, ticks) >= 0)
    ship, bus, s1, s2 = line["ship"], line["bus"], line["s1"], line["s2"]
    ctx.note(f"ship {ship}, bus {bus}, docks {s1} -> {s2}")

    ship_info = veh(b, ship, "flags", "cargo_cap")
    ctx.check(ship_info["flags"] & SHIP_FLAG_CAR_FERRY, f"ship refitted to a car ferry (flags {ship_info['flags']})")
    ctx.check(ship_info["cargo_cap"] > 0, f"car ferry capacity is the ship's capacity ({ship_info['cargo_cap']} road vehicles)")

    # Refit rules, on a second ship in the depot.
    builder = line["builder"]
    ship2 = builder.build_vehicle(line["ship_depot"], veh(b, ship, "engine_type")["engine_type"])
    res = b.docmd("SetCarFerry", 0, ship2, True)
    ctx.check(res["success"], f"second ship refitted to car ferry in depot ({res.get('error')})")
    cargo = veh(b, ship2, "cargo_type")["cargo_type"]
    res = b.docmd("RefitVehicle", 0, ship2, cargo, 0, False, False, 1)
    ctx.check(res["success"] and not (veh(b, ship2, "flags")["flags"] & SHIP_FLAG_CAR_FERRY),
              "refitting a car ferry to cargo makes it a cargo ship again")
    res = b.docmd("SetCarFerry", 0, ship, False, test=True)
    ctx.check(not res["success"], f"a running ship cannot be refitted ({res.get('error')})")
    show_vehicle(b, ship2, WID_VV_REFIT)
    b.console(f"scrollto instant {line['ship_depot']}")
    b.wait_frames(3)
    ctx.screenshot(b, "refit_window_car_ferry_option")
    close_windows(b, lambda name: name in ("VehicleRefit", WC_VEHICLE_VIEW))
    b.docmd("SellVehicle", line["ship_depot"], ship2, 0, 0)

    # Order texts.
    res = set_ferry_action(b, bus, 0, FERRY_BOARD)
    ctx.check(not res["success"], "setting the same ferry action again is rejected")

    # The bus drives to dock 1 and boards when the ferry is there.
    waited = step_until(b, lambda: aboard(b, bus), 200 * TICKS_PER_DAY, chunk=40)
    ctx.check(waited >= 0, f"bus boarded the car ferry ({waited} ticks)")
    if waited < 0:
        return
    info = veh(b, bus, "ferry_carrier", "ferry_dest", "vehstatus", "age", "profit_this_year", "cargo_cap")
    ctx.check_eq(info["ferry_carrier"], ship, "bus is aboard the car ferry")
    ctx.check_eq(info["ferry_dest"], s2, "bus leaves the ferry at dock 2")
    ctx.check(info["vehstatus"] & VEH_HIDDEN, "bus disappeared from the map")
    ship_pos = veh(b, ship, "last_station_visited")["last_station_visited"]
    ctx.check_eq(ship_pos, s1, "ferry is at dock 1 when the bus boards")

    # Commands that make no sense aboard a ship.
    res = b.docmd("SendVehicleToDepot", 0, bus, 0, 0, test=True)
    ctx.check(not res["success"], f"bus aboard cannot be sent to a depot ({res.get('error')})")
    res = b.docmd("TurnRoadVeh", 0, bus, test=True)
    ctx.check(not res["success"], "bus aboard cannot turn around")

    b.step(30)
    close_news(b)
    b.console(f"scrollto instant {veh(b, ship, 'tile')['tile']}")
    for target, button, label in ((ship, WID_VV_SHOW_DETAILS, "car_ferry_details"), (bus, WID_VV_SHOW_ORDERS, "bus_orders"),
                                  (bus, None, "bus_aboard_status")):
        show_vehicle(b, target, *([button] if button is not None else []))
        if button is not None:
            # Move the vehicle view out of the way of the window it opened.
            view = next(w for w in b.windows() if w["class_name"] == WC_VEHICLE_VIEW and w["number"] == target)
            print(view)
            x, y = view["left"] + 60, view["top"] + 7
            b.drag(x, y, x + 750, y + 430)
        b.wait_frames(3)
        ctx.screenshot(b, label)
        close_windows(b, lambda name: name in ("VehicleView", "VehicleDetails", "VehicleOrders"))

    # Save and load while the bus is aboard.
    b.console("save car_ferry_test")
    b.console("load car_ferry_test")
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=60, what="reload")
    after = veh(b, bus, "ferry_carrier", "ferry_dest", "vehstatus")
    ctx.check(after["ferry_carrier"] == ship and after["ferry_dest"] == s2 and after["vehstatus"] & VEH_HIDDEN,
              f"bus still aboard after save/load ({after})")

    # Crossing: the bus neither ages nor costs running costs.
    off = step_until(b, lambda: not aboard(b, bus), 200 * TICKS_PER_DAY, chunk=10)
    ctx.check(off >= 0, f"bus drove off the car ferry ({off} ticks)")
    if off < 0:
        return
    landed = veh(b, bus, "tile", "vehstatus", "age", "profit_this_year", "ferry_dest")
    ctx.check(off >= 2 * TICKS_PER_DAY, f"crossing took several days ({off} ticks)")
    ctx.check_eq(landed["age"], info["age"], "bus did not age while aboard")
    ctx.check_eq(landed["profit_this_year"], info["profit_this_year"], "no running costs while aboard")
    ctx.check(not landed["vehstatus"] & VEH_HIDDEN, "bus is visible again")
    ctx.check_eq(landed["ferry_dest"], INVALID_STATION, "ferry destination cleared")
    ctx.check(landed["tile"] in line["t2"]["roads"][:2], f"bus reappeared next to the stop at dock 2 (tile {landed['tile']})")
    ctx.check_eq(veh(b, ship, "last_station_visited")["last_station_visited"], s2, "ferry is at dock 2")

    close_news(b)
    b.console(f"scrollto instant {landed['tile']}")
    b.wait_frames(3)
    ctx.screenshot(b, "bus_drove_off")

    # It drives into the stop at dock 2 and stops there (its 'leave ship' order).
    arrived = step_until(b, lambda: veh(b, bus, "last_station_visited")["last_station_visited"] == s2, 10 * TICKS_PER_DAY, chunk=5)
    ctx.check(arrived >= 0, f"bus stopped at dock 2's bus stop ({arrived} ticks)")

    # Round trip: it boards again at dock 2 and comes back to dock 1.
    back = step_until(b, lambda: aboard(b, bus), 200 * TICKS_PER_DAY, chunk=40)
    ctx.check(back >= 0 and veh(b, bus, "ferry_dest")["ferry_dest"] == s1, f"bus boarded again for dock 1 ({back} ticks)")
    home = step_until(b, lambda: not aboard(b, bus), 200 * TICKS_PER_DAY, chunk=10)
    ctx.check(home >= 0 and veh(b, bus, "tile")["tile"] in line["t1"]["roads"][:2], f"bus is back at dock 1 ({home} ticks)")
    ctx.check(inst.alive(), "game still running")
