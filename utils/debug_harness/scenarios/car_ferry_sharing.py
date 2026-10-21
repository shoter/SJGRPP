"""
Car ferry and infrastructure sharing: a bus of company 0 waits at a dock of an AI company's car ferry line.
Without ship sharing it is left behind; with ship sharing it boards and pays the ferry owner one day of the ship
sharing fee. Ship sharing cannot be switched off while it is aboard. When the ferry owner is removed with the bus
aboard, the bus is sold and company 0 gets its value back.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from car_ferry import (INVALID_VEHICLE, TICKS_PER_DAY, VEH_HIDDEN, WC_VEHICLE_VIEW, WID_VV_SHOW_DETAILS, aboard,  # noqa: E402
                       close_news, close_windows, show_vehicle, step_until, tile_xy, veh)
from transport import FERRY_BOARD, FERRY_LEAVE, INVALID_CARGO, MOF_FERRY_ACTION, ROADTYPE_ROAD, Builder  # noqa: E402

CONFIG = {"difficulty": {"max_no_competitors": 1, "vehicle_breakdowns": 0}}

NEW_VEHICLES = 1              # ExpensesType::NewVehicles; yearly_expenses[0][1] is this year's
SHARING_COST = 13
SHARING_REVENUE = 14
FEE = 5000                    # ship sharing fee per day; one boarding costs exactly this (inflation scaling off)


def expense(b, company: int, kind: int) -> int:
    return int(b.obj("company", company, "yearly_expenses")["yearly_expenses"][kind])


def ship_sharing(b) -> bool:
    return b.console("setting economy.infrastructure_sharing[2]")[0].find("'true'") > 0


def build_shared_line(b, owner: Builder, bus_owner: Builder, wait) -> dict:
    """The ferry owner builds a car ferry line without buses; bus_owner runs a bus on it from the ferry owner's road
    depot, boarding at dock 1. The ferry is started once the bus waits at dock 1. Both need money."""
    towns = [b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town"))]
    route = owner.build_car_ferry_route(tile_xy(b, towns[0]), buses=0, start_ferry=False)
    ship = route.vehicles[0]
    s1, s2 = route.stations
    depot = route.terminals[0]["depot"]

    bus = bus_owner.build_vehicle(depot, bus_owner.pick_engine("road", roadtype=ROADTYPE_ROAD)["id"])
    for station in (s1, s2, s2, s1):
        bus_owner.order(bus, "station", station)
    for index, action in enumerate((FERRY_BOARD, FERRY_LEAVE, FERRY_BOARD, FERRY_LEAVE)):
        bus_owner.cmd("ModifyOrder", 0, bus, index, MOF_FERRY_ACTION, action, INVALID_CARGO, "")
    bus_owner.start_vehicle(bus)
    if not wait(lambda: veh(b, bus, "last_station_visited")["last_station_visited"] == s1, 20 * TICKS_PER_DAY):
        raise AssertionError("bus did not reach dock 1")
    owner.start_vehicle(ship)
    return {"ship": ship, "bus": bus, "s1": s1, "s2": s2, "t2": route.terminals[1]}


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.console("setting economy.infrastructure_sharing[1] 1")   # the bus uses the AI's road stops and depot
    b.console("setting economy.sharing_fee[1] 0")              # ... for free, so only the ferry fee is charged
    b.console(f"setting economy.sharing_fee[2] {FEE}")
    b.console("setting sharing_fee_inflation false")
    ctx.check(not ship_sharing(b), "ship sharing is off")
    b.console("start_ai")
    b.step(2)
    ai = next(c for c in b.objs("company") if c != 0)

    owner, payer = Builder(b, log=ctx.note, as_company=ai), Builder(b, log=ctx.note)
    owner.give_money(50_000_000)
    payer.give_money(10_000_000)
    line = build_shared_line(b, owner, payer, lambda pred, ticks: step_until(b, pred, ticks) >= 0)
    ship, bus, s1, s2 = line["ship"], line["bus"], line["s1"], line["s2"]
    ctx.note(f"company {ai}: car ferry {ship}, docks {s1} -> {s2}; company 0: bus {bus}")

    # Without ship sharing the ferry calls at dock 1 and leaves without the bus.
    called = step_until(b, lambda: veh(b, ship, "last_station_visited")["last_station_visited"] == s1, 60 * TICKS_PER_DAY)
    ctx.check(called >= 0, f"ferry called at dock 1 ({called} ticks)")
    left = step_until(b, lambda: veh(b, ship, "last_station_visited")["last_station_visited"] == s2, 100 * TICKS_PER_DAY)
    ctx.check(left >= 0, f"ferry crossed to dock 2 ({left} ticks)")
    ctx.check(not aboard(b, bus), "without ship sharing the bus did not board the other company's ferry")
    ctx.check_eq(veh(b, bus, "last_station_visited")["last_station_visited"], s1, "bus still waits at dock 1")
    ctx.check_eq(expense(b, 0, SHARING_COST), 0, "no sharing fee charged")

    # With ship sharing it boards when the ferry is back, paying one day of the ship fee.
    b.console("setting economy.infrastructure_sharing[2] 1")
    ctx.check(ship_sharing(b), "ship sharing switched on")
    cost0, revenue0 = expense(b, 0, SHARING_COST), expense(b, ai, SHARING_REVENUE)
    boarded = step_until(b, lambda: aboard(b, bus), 200 * TICKS_PER_DAY, chunk=1)
    ctx.check(boarded >= 0, f"with ship sharing the bus boarded the other company's ferry ({boarded} ticks)")
    if boarded < 0:
        return
    info = veh(b, bus, "ferry_carrier", "ferry_dest", "vehstatus")
    ctx.check_eq(info["ferry_carrier"], ship, "bus is aboard the AI's car ferry")
    ctx.check_eq(info["ferry_dest"], s2, "bus leaves the ferry at dock 2")
    ctx.check(info["vehstatus"] & VEH_HIDDEN, "bus disappeared from the map")
    ctx.check_eq(expense(b, 0, SHARING_COST) - cost0, FEE, "bus owner paid one day of the ship sharing fee")
    ctx.check_eq(expense(b, ai, SHARING_REVENUE) - revenue0, -FEE, "ferry owner received the fee")

    # Ship sharing cannot be switched off while the bus is aboard.
    close_news(b)
    close_windows(b, lambda name: "Debug" in name)
    b.console("setting economy.infrastructure_sharing[2] 0")
    b.wait_frames(2)
    ctx.check(ship_sharing(b), "ship sharing cannot be switched off while a bus is aboard another company's ferry")
    errors = [w for w in b.windows() if w["class_name"] == "ErrorMessage"]
    ctx.check(len(errors) == 1, "an error message explains why")
    ctx.screenshot(b, "sharing_off_refused")
    close_windows(b, lambda name: name == "ErrorMessage")

    b.step(30)
    close_news(b)
    b.console(f"scrollto instant {veh(b, ship, 'tile')['tile']}")
    show_vehicle(b, ship, WID_VV_SHOW_DETAILS)
    view = next(w for w in b.windows() if w["class_name"] == WC_VEHICLE_VIEW and w["number"] == ship)
    x, y = view["left"] + 60, view["top"] + 7
    b.drag(x, y, x + 750, y + 430)
    b.wait_frames(3)
    ctx.screenshot(b, "other_company_bus_aboard")
    close_windows(b, lambda name: name in ("VehicleView", "VehicleDetails"))

    # The bus crosses and drives off at dock 2 as on its own company's ferry.
    off = step_until(b, lambda: not aboard(b, bus), 200 * TICKS_PER_DAY, chunk=10)
    ctx.check(off >= 0, f"bus drove off the ferry ({off} ticks)")
    if off < 0:
        return
    landed = veh(b, bus, "tile", "vehstatus")
    ctx.check(not landed["vehstatus"] & VEH_HIDDEN, "bus is visible again")
    ctx.check(landed["tile"] in line["t2"]["roads"][:2], f"bus reappeared next to the stop at dock 2 (tile {landed['tile']})")
    ctx.check_eq(expense(b, 0, SHARING_COST) - cost0, FEE, "nothing more charged for the crossing")

    # It boards again at dock 2. Removing the ferry owner sells the bus aboard and refunds its value.
    back = step_until(b, lambda: aboard(b, bus), 200 * TICKS_PER_DAY, chunk=10)
    ctx.check(back >= 0 and veh(b, bus, "ferry_dest")["ferry_dest"] == s1, f"bus boarded again for dock 1 ({back} ticks)")
    ctx.check_eq(expense(b, 0, SHARING_COST) - cost0, 2 * FEE, "second boarding charged again")
    if back < 0:
        return
    value = int(veh(b, bus, "value")["value"])
    bought = expense(b, 0, NEW_VEHICLES)
    b.console(f"stop_ai {ai + 1}")
    b.step(2)
    ctx.check(ai not in b.objs("company"), "ferry owner removed")
    ctx.check(bus not in b.objs("vehicle"), "bus aboard the removed company's ferry is gone")
    ctx.check_eq(expense(b, 0, NEW_VEHICLES) - bought, -value, f"bus owner got the bus's value ({value}) back")
    ctx.check(inst.alive(), "game still running")
