"""
Overseas stations with cargo distribution on for passengers: a ship line between two docks makes cargo distribution
route the passengers of dock A to dock B. A second ship from dock A to an overseas station still loads them (overseas
trips are not part of cargo distribution), the overseas station never joins a link graph, and the passengers it brings
back are delivered at dock A.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from overseas import (CONFIG, INVALID_STATION, TICKS_PER_DAY, build_dock_near, overseas_info, overseas_stations,  # noqa: E402
                      step_until, take_control, veh, vehicle_cargo)
from transport import Builder, BuildError  # noqa: E402

DISTRIBUTION_SYMMETRIC = 2


def run(ctx):
    config = dict(CONFIG)
    config["linkgraph"] = {"distribution_pax": DISTRIBUTION_SYMMETRIC, "recalc_interval": 2, "recalc_time": 4}
    inst = ctx.solo(args=["-g", "-G", "2"], lockstep=True, config=config)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.step(2)

    builder = Builder(b, log=ctx.note)
    builder.give_money(100_000_000)
    stations = overseas_stations(b)
    mx = b.info()["map"]["x"]
    towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in b.objs("town"))]

    target = dock_a = depot = dock_b = None
    for sid, tile in sorted(stations.items()):
        try:
            dock_a, depot = build_dock_near(builder, tile, towns)
            a_xy = (b.obj("station", dock_a, "xy")["xy"] % mx, b.obj("station", dock_a, "xy")["xy"] // mx)
            dock_b, _ = build_dock_near(builder, tile, towns, avoid=a_xy, town_range=10)
            target = sid
            break
        except BuildError as e:
            ctx.note(f"overseas station {sid}: {e}")
    ctx.check(target is not None, f"two docks near towns on the sea of overseas station {target}: {dock_a}, {dock_b}")
    take_control(b, target)

    engine = builder.pick_engine("ship")["id"]
    liner = builder.build_vehicle(depot, engine)
    builder.order(liner, "station", dock_a)
    builder.order(liner, "station", dock_b)
    builder.start_vehicle(liner)

    ship = builder.build_vehicle(depot, engine)
    builder.order(ship, "station", dock_a)
    builder.order(ship, "station", target)
    builder.start_vehicle(ship)

    trips = 0
    loaded_from_dock = 0
    for _ in range(3):
        seen = {"cargo": 0}
        def gone():
            if veh(b, ship, "overseas_station")["overseas_station"] == target:
                return True
            seen["cargo"] = vehicle_cargo(b, ship)[0]
            return False
        if step_until(b, gone, 200 * TICKS_PER_DAY, chunk=10) < 0:
            break
        trips += 1
        loaded_from_dock = max(loaded_from_dock, seen["cargo"])
        if step_until(b, lambda: veh(b, ship, "overseas_station")["overseas_station"] == INVALID_STATION, 40 * TICKS_PER_DAY) < 0:
            break

    ctx.check(trips >= 2, f"the overseas ship made {trips} trips")
    ctx.check(loaded_from_dock > 0, f"with cargo distribution on, the ship took {loaded_from_dock} passengers of dock A overseas")
    ctx.check_eq(overseas_info(b)[target]["link_graphs"], 0, "the overseas station is in no link graph")
    profit = int(veh(b, ship, "profit_this_year")["profit_this_year"])
    ctx.check(profit > 0, f"the overseas ship earns money ({profit >> 8})")
