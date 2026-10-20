"""
Car ferry in multiplayer: a client builds a car ferry line and a bus that crosses on it. A second client joins while
the bus is aboard (the list of road vehicles aboard each ferry is rebuilt from the savegame it downloads); every
instance must agree on where the bus is, and nothing may desync until it has driven off at the other dock.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from car_ferry import INVALID_VEHICLE, TICKS_PER_DAY, VEH_HIDDEN, setup_car_ferry_line  # noqa: E402

FIELDS = ("ferry_carrier", "ferry_dest", "vehstatus", "tile", "cur_real_order_index")


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=4242, company="new")
    server = cluster.server.bridge
    c1 = cluster.clients[0].bridge
    c1.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client1 in its company")

    def wait(pred, ticks):
        deadline = time.monotonic() + ticks * 0.04 + 30
        while time.monotonic() < deadline:
            if pred():
                return True
            time.sleep(0.2)
        return pred()

    line = setup_car_ferry_line(c1, wait)
    ship, bus, s2 = line["ship"], line["bus"], line["s2"]
    ctx.note(f"ship {ship}, bus {bus}")

    ctx.check(wait(lambda: server.obj("vehicle", bus, "ferry_carrier")["ferry_carrier"] == ship, 40 * TICKS_PER_DAY),
              "server: bus boarded the car ferry")

    # Client 2 leaves and joins again while the bus is aboard: it gets the game from a savegame.
    client2 = cluster.clients[1]
    client2.stop()
    cluster.start_client(client2)
    c2 = client2.bridge
    server.console("pause")
    for b in (server, c1, c2):
        b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=20, what="paused")
    states = [b.obj("vehicle", bus, *FIELDS) for b in (server, c1, c2)]
    ctx.check(states[0]["ferry_carrier"] == ship and states[0]["vehstatus"] & VEH_HIDDEN, f"bus aboard on the server ({states[0]})")
    ctx.check(states[1] == states[0] and states[2] == states[0], f"clients agree on the bus aboard ({states})")
    server.console("unpause")

    ctx.check(wait(lambda: server.obj("vehicle", bus, "ferry_carrier")["ferry_carrier"] == INVALID_VEHICLE, 60 * TICKS_PER_DAY),
              "server: bus drove off the car ferry")
    ctx.check(wait(lambda: server.obj("vehicle", bus, "last_station_visited")["last_station_visited"] == s2, 10 * TICKS_PER_DAY),
              "server: bus stopped at dock 2")

    server.console("pause")
    for b in (server, c1, c2):
        b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=20, what="paused")
    states = [b.obj("vehicle", bus, *FIELDS) for b in (server, c1, c2)]
    ctx.check(states[1] == states[0] and states[2] == states[0], f"clients agree on the bus after the crossing ({states})")

    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
