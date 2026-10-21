"""
Overseas stations in multiplayer: client 1 takes control of an overseas station, client 2 cannot take it any more and
gets a news message about its rival. Client 1's ship sails overseas; client 2 leaves and joins again while the ship is
away (it gets the game from a savegame), every instance must agree on the ship, and nothing may desync until the ship
is back on the map.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from overseas import CONFIG, INVALID_STATION, TICKS_PER_DAY, VEH_HIDDEN, overseas_info, overseas_stations, pick_reachable, take_control  # noqa: E402
from transport import Builder  # noqa: E402

FIELDS = ("overseas_station", "overseas_return", "overseas_trip_ticks", "vehstatus", "cur_real_order_index")


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=2, company="new", server_config=CONFIG)
    server = cluster.server.bridge
    c1, c2 = cluster.clients[0].bridge, cluster.clients[1].bridge
    for b in (c1, c2):
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")
    co1, co2 = c1.info()["local_company"], c2.info()["local_company"]

    def wait(pred, ticks):
        deadline = time.monotonic() + ticks * 0.04 + 30
        while time.monotonic() < deadline:
            if pred():
                return True
            time.sleep(0.2)
        return pred()

    stations = overseas_stations(server)
    builder = Builder(c1, log=ctx.note)
    server.docmd("MoneyCheatAdmin", 0, 50_000_000, as_company=co1)
    target, dock, depot = pick_reachable(c1, builder, stations)
    ctx.note(f"overseas station {target}, dock {dock}, depot {depot}; companies {co1} and {co2}")

    res = take_control(c1, target)
    ctx.check(res["success"], f"client 1 takes control ({res.get('error')})")
    ctx.check(wait(lambda: server.obj("station", target, "owner")["owner"] == co1, 5 * TICKS_PER_DAY), "server: client 1's company controls it")
    ctx.check(wait(lambda: overseas_info(c2)[target]["owner"] == co1, 5 * TICKS_PER_DAY), "client 2 sees client 1 in control")
    res = take_control(c2, target)
    ctx.check(not res["success"], f"client 2 cannot take control any more ({res.get('error')})")
    ctx.check(wait(lambda: any(str(w.get("class_name", "")) == "News" for w in c2.windows()), 3 * TICKS_PER_DAY),
              "client 2 got a news message about its rival")
    c2.wait_ticks(3 * TICKS_PER_DAY)   # the news window slides into view
    ctx.screenshot(c2, "rival_news")

    ship = builder.build_vehicle(depot, builder.pick_engine("ship")["id"])
    builder.order(ship, "station", dock)
    builder.order(ship, "station", target)
    builder.start_vehicle(ship)
    ctx.check(wait(lambda: server.obj("vehicle", ship, "overseas_station")["overseas_station"] == target, 200 * TICKS_PER_DAY),
              "server: the ship left the map for the trip overseas")

    # Client 2 leaves and joins again while the ship is away.
    client2 = cluster.clients[1]
    client2.stop()
    cluster.start_client(client2)
    c2 = client2.bridge
    server.console("pause")
    for b in (server, c1, c2):
        b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=20, what="paused")
    states = [b.obj("vehicle", ship, *FIELDS) for b in (server, c1, c2)]
    ctx.check(states[0]["overseas_station"] == target and states[0]["vehstatus"] & VEH_HIDDEN, f"ship overseas on the server ({states[0]})")
    ctx.check(states[1] == states[0] and states[2] == states[0], f"clients agree on the ship overseas ({states})")
    infos = [overseas_info(b)[target] for b in (server, c1, c2)]
    ctx.check(infos[1] == infos[0] and infos[2] == infos[0], f"clients agree on the overseas station ({infos[0]})")
    server.console("unpause")

    ctx.check(wait(lambda: server.obj("vehicle", ship, "overseas_station")["overseas_station"] == INVALID_STATION, 40 * TICKS_PER_DAY),
              "server: the ship came back")
    server.console("pause")
    for b in (server, c1, c2):
        b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=20, what="paused")
    states = [b.obj("vehicle", ship, *FIELDS) for b in (server, c1, c2)]
    ctx.check(states[1] == states[0] and states[2] == states[0], f"clients agree on the ship after its return ({states})")
    server.console("unpause")

    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
