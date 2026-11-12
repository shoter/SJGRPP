"""
Overseas stations: a new map gets overseas stations on sea tiles at the map border; they cannot be removed.
A company pays fee * (X+1)! to take control of one. Its passenger ship sails there, disappears for the trip to the
country, comes back with passengers of the country and is paid for the trip both ways. The trip takes as long as the
ship needs to sail the paid distance there and back at its top speed, measured on the map; an aircraft does the same at
its flying speed. Vehicles of other companies cannot get orders to it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transport import Builder, BuildError  # noqa: E402

STATION_TYPE_OVERSEAS = 9     # StationType::Overseas
TILE_STATION = 5              # TileType::Station
VEH_HIDDEN = 1 << 0           # VehState::Hidden
INVALID_STATION = 0xFFFF
OWNER_NONE = 29
TICKS_PER_DAY = 74
FEE = 100_000
MIN_DAYS = 1
CARGO_AGE_PERIOD = 185        # default cargo aging period of a vehicle, in ticks
PLANE_SPEED = 4               # default plane speed factor: aircraft move at 1/4 of their speed
WID_GO_TAB_ADVANCED = 4       # GameOptionsWidgets

CONFIG = {
    "economy": {"overseas_stations": 12, "overseas_fee": FEE, "overseas_trip_min_days": MIN_DAYS,
                "overseas_max_per_company": 3, "inflation": "false"},
    "difficulty": {"max_no_competitors": 0, "vehicle_breakdowns": 0, "quantity_sea_lakes": 3, "number_towns": 2},
}


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


def money(b, company: int = 0) -> int:
    return int(b.obj("company", company, "money")["money"])


def overseas_stations(b) -> dict[int, int]:
    """Overseas station id -> tile."""
    gmap = Builder(b, log=lambda *_: None).fresh_map()
    out = {}
    for sid in b.objs("station"):
        xy = b.obj("station", sid, "xy")["xy"]
        if gmap.type[xy] == TILE_STATION and gmap.sub[xy] == STATION_TYPE_OVERSEAS:
            out[sid] = xy
    return out


def overseas_info(b) -> dict[int, dict]:
    """Overseas data from the debug bridge watch: station -> country, owner, cargo, accepted, growth, waiting, monthly."""
    raw = b.get("overseas.stations")["overseas.stations"]
    out = {}
    for item in filter(None, raw.split(";")):
        f = [int(x) for x in item.split(":")]
        out[f[0]] = {"country": f[1], "owner": f[2], "cargo": f[3], "accepted": f[4], "growth": f[5],
                     "waiting": [f[6], f[8], f[10]], "monthly": [f[7], f[9], f[11]], "link_graphs": f[12], "tiles": f[13]}
    return out


def vehicle_cargo(b, vid: int) -> tuple[int, int]:
    """(amount, sum of periods in transit) of the cargo of a ship or aircraft."""
    raw = b.get("overseas.vehicle_cargo")["overseas.vehicle_cargo"]
    for item in filter(None, raw.split(";")):
        v, count, periods = (int(x) for x in item.split(":"))
        if v == vid:
            return count, periods
    return 0, 0


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def ship_trip_ticks(tiles: int, speed: int) -> int:
    """Ticks to sail tiles there and back; speed in internal ship units (0.5 km-ish/h): 4096 / speed ticks per tile."""
    return max(MIN_DAYS * TICKS_PER_DAY, ceil_div(2 * tiles * 16 * 192 * 4, 3 * speed))


def aircraft_trip_ticks(tiles: int, speed: int) -> int:
    """Ticks to fly tiles there and back; speed in km-ish/h, two moves of speed / plane speed per tick, 16 * 256 per tile."""
    return max(MIN_DAYS * TICKS_PER_DAY, ceil_div(2 * tiles * 16 * 256 * PLANE_SPEED, 2 * speed))


def measure_axis_speed(b, vid: int, top_speed: int, stop, max_ticks: int, chunk: int = 4) -> tuple[float, int]:
    """
    Step until stop() holds and measure how fast the vehicle really moves along a map axis at its top speed.
    Returns (pixels per tick, ticks measured) over the stretches it went straight along an axis (odd direction) at top speed.
    """
    pixels = ticks = 0
    prev = None
    done = 0
    while done < max_ticks and not stop():
        cur = veh(b, vid, "x_pos", "y_pos", "direction", "cur_speed", "overseas_station")
        if (prev is not None and cur["overseas_station"] == INVALID_STATION and prev["direction"] == cur["direction"]
                and cur["direction"] % 2 == 1 and prev["cur_speed"] == cur["cur_speed"] == top_speed):
            pixels += abs(cur["x_pos"] - prev["x_pos"]) + abs(cur["y_pos"] - prev["y_pos"])
            ticks += chunk
        prev = cur
        b.step(chunk)
        done += chunk
    return (pixels / ticks if ticks else 0.0), ticks


def take_control(b, station: int, company: int | None = None) -> dict:
    return b.docmd("OverseasStationControl", 0, station, True, as_company=company)


def build_dock_near(builder: Builder, station_tile: int, towns: list[tuple[int, int]], avoid: tuple[int, int] | None = None,
                    town_range: int = 6) -> tuple[int, int]:
    """A dock next to a town on the water body of an overseas station and a ship depot; returns (dock station, depot tile)."""
    sites = builder.overseas_dock_sites(station_tile, towns=towns, town_range=town_range, avoid=avoid)
    return builder.build_overseas_dock(sites[0])


def pick_reachable(b, builder: Builder, stations: dict[int, int]) -> tuple[int, int, int]:
    """An overseas station with a dock on its water: (overseas station, dock station, depot tile)."""
    errors = []
    mx = b.info()["map"]["x"]
    towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in b.objs("town"))]
    for sid, tile in sorted(stations.items()):
        try:
            dock, depot = build_dock_near(builder, tile, towns)
            return sid, dock, depot
        except BuildError as e:
            errors.append(f"{sid}: {e}")
    raise BuildError("no reachable overseas station: " + "; ".join(errors))


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "2"], lockstep=True, config=CONFIG)   # a town next to the sea of an overseas station
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.step(2)

    stations = overseas_stations(b)
    ctx.check(0 < len(stations) <= 12, f"the map has overseas stations ({len(stations)})")
    gmap = Builder(b, log=lambda *_: None).fresh_map()
    on_border = all(min(x - 1, y - 1, gmap.sx - 2 - x, gmap.sy - 2 - y) == 0 for x, y in (gmap.xy(t) for t in stations.values()))
    ctx.check(on_border, "every overseas station is on the map border")
    ctx.check(all(b.obj("station", s, "owner")["owner"] == OWNER_NONE for s in stations), "nobody controls them at the start")

    some_tile = next(iter(stations.values()))
    res = b.docmd("LandscapeClear", some_tile)
    ctx.check(not res["success"], f"the tile of an overseas station cannot be cleared ({res.get('error')})")

    builder = Builder(b, log=ctx.note)
    builder.give_money(100_000_000)
    target, dock, depot = pick_reachable(b, builder, stations)
    ctx.note(f"overseas station {target} at tile {stations[target]}, dock {dock}, ship depot {depot}")

    ship = builder.build_vehicle(depot, builder.pick_engine("ship")["id"])
    builder.order(ship, "station", dock)
    res = b.order(ship, "station", target, test=True)
    ctx.check(not res["success"], f"an order to an uncontrolled overseas station is refused ({res.get('error')})")

    b.call("show", "station", sorted(b.objs("station")).index(target))
    b.console(f"scrollto instant {stations[target]}")
    ctx.screenshot(b, "station_uncontrolled")
    b.close_window("StationView", target)

    # Prices: fee, then 2 * fee, then 6 * fee.
    others = [s for s in sorted(stations) if s != target]
    m0 = money(b)
    res = take_control(b, target)
    ctx.check(res["success"], f"take control of the overseas station ({res.get('error')})")
    m1 = money(b)
    ctx.check_eq(m0 - m1, FEE, "the first overseas station costs the fee")
    ctx.check_eq(b.obj("station", target, "owner")["owner"], 0, "company 0 controls it")
    res = take_control(b, target)
    ctx.check(not res["success"], "a station cannot be taken twice")
    if len(others) >= 2:
        m1 = money(b)
        take_control(b, others[0])
        m2 = money(b)
        ctx.check_eq(m1 - m2, 2 * FEE, "the second overseas station costs 2! times the fee")
        take_control(b, others[1])
        m3 = money(b)
        ctx.check_eq(m2 - m3, 6 * FEE, "the third overseas station costs 3! times the fee")
        if len(others) >= 3:
            res = take_control(b, others[2])
            ctx.check(not res["success"], f"a fourth is refused with max 3 per company ({res.get('error')})")
        res = b.docmd("OverseasStationControl", 0, others[1], False)
        ctx.check(res["success"] and b.obj("station", others[1], "owner")["owner"] == OWNER_NONE, "control can be given up")

    builder.order(ship, "station", target)
    builder.start_vehicle(ship)

    # Passengers, mail and freight pile up at the overseas station.
    b.step(TICKS_PER_DAY * 5)
    info0 = overseas_info(b)[target]
    ctx.note(f"overseas station {target}: {info0}")
    ctx.check(all(w > 0 for w, m in zip(info0["waiting"], info0["monthly"]) if m > 0), "the overseas station produces its cargo")

    def sail_overseas(max_ticks: int) -> tuple[int, int, int]:
        """Run until the ship leaves the map; returns (ticks, cargo aboard just before, profit just before)."""
        seen = {"cargo": 0, "profit": 0}
        def gone():
            if veh(b, ship, "overseas_station")["overseas_station"] == target:
                return True
            seen["cargo"] = vehicle_cargo(b, ship)[0]
            seen["profit"] = int(veh(b, ship, "profit_this_year")["profit_this_year"])
            return False
        ticks = step_until(b, gone, max_ticks, chunk=10)
        return ticks, seen["cargo"], seen["profit"]

    ship_speed = 2 * builder.pick_engine("ship")["max_speed"]   # internal units, the display speed is half
    px, measured = measure_axis_speed(b, ship, ship_speed, lambda: veh(b, ship, "overseas_station")["overseas_station"] == target,
                                      200 * TICKS_PER_DAY)
    expected_px = ship_speed * 3 / 4 / 192
    ctx.note(f"ship top speed {ship_speed} (internal), {px:.3f} px/tick measured over {measured} ticks, {expected_px:.3f} expected")
    ctx.check(measured >= 40 and abs(px - expected_px) <= expected_px * 0.1,
              f"on the map the ship moves {px:.3f} px/tick at top speed, as the trip time assumes ({expected_px:.3f})")

    left, aboard, p_before = sail_overseas(200 * TICKS_PER_DAY)
    t_left = int(b.info()["tick_counter"])
    ctx.check(left >= 0, f"the ship sailed to the overseas station and left the map ({left} ticks, {aboard} passengers aboard)")
    info = veh(b, ship, "vehstatus", "overseas_return", "overseas_trip_ticks", "profit_this_year", "last_station_visited")
    ctx.check(info["vehstatus"] & VEH_HIDDEN, "the ship is hidden while overseas")
    ctx.check_eq(vehicle_cargo(b, ship)[0], 0, "cargo of the dock was delivered to the country")
    trip = info["overseas_trip_ticks"]
    tiles = overseas_info(b)[target]["tiles"]
    ctx.check_eq(trip, ship_trip_ticks(tiles, ship_speed),
                 f"the trip takes as long as sailing {tiles} tiles there and back at top speed ({trip / TICKS_PER_DAY:.1f} days)")
    res = b.docmd("StartStopVehicle", 0, ship, False)
    ctx.check(not res["success"], f"a vehicle overseas cannot be stopped ({res.get('error')})")
    res = b.docmd("SendVehicleToDepot", 0, ship, 0, 0, test=True)
    ctx.check(not res["success"], f"a vehicle overseas cannot be sent to a depot ({res.get('error')})")

    b.call("show", "station", sorted(b.objs("station")).index(target))
    b.call("show", "vehicle", 0)
    b.step(TICKS_PER_DAY * 2)
    ctx.screenshot(b, "ship_overseas")
    p_away = int(veh(b, ship, "profit_this_year")["profit_this_year"])
    ctx.check(p_away < int(info["profit_this_year"]), "running costs are paid while the ship is overseas")

    # Save and load while the ship is away; the caches must match the game state.
    fields = ("overseas_station", "overseas_return", "overseas_trip_ticks", "vehstatus")
    before_save = veh(b, ship, *fields), overseas_info(b)
    b.save_game("overseas_away")
    b.load_game("overseas_away")
    after_load = veh(b, ship, *fields), overseas_info(b)
    ctx.check(after_load == before_save, "the ship overseas and the overseas stations survive saving and loading")
    mark = b.last_log_seq()
    out = b.console("check_caches")
    b.step(2)
    lines = [str(line) for line in b.log(mark).get("lines", [])] + [str(line) for line in out]
    cache_errors = [line for line in lines if "desync" in line.lower() or "mismatch" in line.lower()]
    ctx.check(not cache_errors, f"no cache mismatches ({cache_errors[:3]})")

    elapsed = int(b.info()["tick_counter"]) - t_left
    if elapsed >= 0:
        b.step(max(0, trip - elapsed - 30))   # fast forward to just before the ship is back
    last = {"waiting": overseas_info(b)[target]["waiting"][0]}
    def returned():
        if veh(b, ship, "overseas_station")["overseas_station"] == INVALID_STATION:
            return True
        last["waiting"] = overseas_info(b)[target]["waiting"][0]
        return False
    back = step_until(b, returned, trip + 5 * TICKS_PER_DAY, chunk=1)
    ctx.check(back >= 0, f"the ship came back ({back} ticks later)")
    ret = veh(b, ship, "vehstatus", "cargo_cap")
    ctx.check(not ret["vehstatus"] & VEH_HIDDEN, "the ship is visible again")
    count, periods = vehicle_cargo(b, ship)
    ctx.check(count > 0, f"the ship came back with {count} passengers of the country")
    after = overseas_info(b)[target]["waiting"][0]
    ctx.check(abs(last["waiting"] - count - after) <= 20, f"the passengers were taken from the stockpile of the station ({last['waiting']} -> {after})")
    expected_age = trip // 2 // CARGO_AGE_PERIOD
    avg = periods // count if count else -1
    ctx.check(abs(avg - expected_age) <= 1, f"the cargo is aged half of the trip ({avg} periods, expected {expected_age})")

    p_back = int(veh(b, ship, "profit_this_year")["profit_this_year"])
    delivered = step_until(b, lambda: int(veh(b, ship, "profit_this_year")["profit_this_year"]) > p_back + 1000, 120 * TICKS_PER_DAY)
    ctx.check(delivered >= 0, "the passengers from overseas were delivered at the dock and paid for")
    ctx.screenshot(b, "after_return")

    # Second trip: the passengers picked up at the dock are paid for when the ship leaves the map.
    left, aboard, p_before = sail_overseas(200 * TICKS_PER_DAY)
    ctx.check(left >= 0 and aboard > 0, f"the ship sailed overseas again with {aboard} passengers from the dock")
    p_gone = int(veh(b, ship, "profit_this_year")["profit_this_year"])
    ctx.check(p_gone > p_before, f"passengers to the country are paid for on departure (profit {p_before} -> {p_gone})")

    # An aircraft flies to the same overseas station and is away as long as flying there and back takes.
    mx = b.info()["map"]["x"]
    towns_xy = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town"))[:2])]
    air = builder.build_air_route(towns_xy[0], towns_xy[1], vehicles=1)
    plane = air.vehicles[0]
    builder.order(plane, "station", target)
    plane_speed = builder.pick_engine("aircraft")["max_speed"]
    for e in builder.engines():
        if e["id"] == veh(b, plane, "engine_type")["engine_type"]:
            plane_speed = e["max_speed"]
    px, measured = measure_axis_speed(b, plane, plane_speed, lambda: veh(b, plane, "overseas_station")["overseas_station"] == target,
                                      300 * TICKS_PER_DAY, chunk=2)
    expected_px = 2 * plane_speed / PLANE_SPEED / 256
    ctx.note(f"aircraft top speed {plane_speed}, {px:.3f} px/tick measured over {measured} ticks, {expected_px:.3f} expected")
    ctx.check(measured >= 20 and abs(px - expected_px) <= expected_px * 0.1,
              f"on the map the aircraft moves {px:.3f} px/tick at top speed, as the trip time assumes ({expected_px:.3f})")
    flown = step_until(b, lambda: veh(b, plane, "overseas_station")["overseas_station"] == target, 300 * TICKS_PER_DAY, chunk=10)
    ctx.check(flown >= 0, f"the aircraft flew to the overseas station and left the map ({flown} ticks)")
    pinfo = veh(b, plane, "vehstatus", "overseas_trip_ticks")
    ctx.check(pinfo["vehstatus"] & VEH_HIDDEN, "the aircraft is hidden while overseas")
    plane_trip = pinfo["overseas_trip_ticks"]
    plane_days = ceil_div(plane_trip, TICKS_PER_DAY)
    ctx.check_eq(plane_trip, aircraft_trip_ticks(tiles, plane_speed),
                 f"the aircraft trip takes as long as flying {tiles} tiles there and back at top speed ({plane_trip / TICKS_PER_DAY:.1f} days)")
    b.call("show", "vehicle", 1)   # second primary vehicle: the aircraft
    ctx.screenshot(b, "aircraft_overseas")
    back = step_until(b, lambda: veh(b, plane, "overseas_station")["overseas_station"] == INVALID_STATION, (plane_days + 2) * TICKS_PER_DAY, chunk=5)
    ctx.check(back >= 0, f"the aircraft came back ({back} ticks later)")
    ctx.check(vehicle_cargo(b, plane)[0] > 0, f"the aircraft came back with {vehicle_cargo(b, plane)[0]} passengers")
    landed = step_until(b, lambda: veh(b, plane, "last_station_visited")["last_station_visited"] == air.stations[0], 120 * TICKS_PER_DAY, chunk=20)
    ctx.check(landed >= 0, "the aircraft flew on and landed at its next airport")

    # The server settings.
    for w in b.windows():
        if w["class_name"] in ("StationView", "VehicleView"):
            b.close_window(w["class"], w["number"])
    b.console("setting gui.settings_restriction_mode 2")
    b.call("show", "game_options")
    b.step(5)
    win = b.find_windows("GameOptions")
    if ctx.check(len(win) == 1, "game options window opened"):
        b.click_widget("GameOptions", win[0]["number"], WID_GO_TAB_ADVANCED)
        b.step(5)
        b.text("overseas")
        b.step(10)
        b.wait_frames(3)
        ctx.screenshot(b, "settings_server")
