"""
Own/opponent vehicle colours in multiplayer: client1 and client2 each run a bus, client3 spectates. Every client
switches the colours on from the Settings menu: each company sees its own bus in the own colour and the other bus in
the opponent colour, the spectator keeps the company liveries, and the server (which never switched it on) is not
affected. The colours are local to each client, so nothing may desync.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transport import Builder  # noqa: E402
from vehicle_colour_override import (CONFIG, DARK_BLUE, DEFAULT_LIVERY, GREEN, ORANGE, RED, BROWN, YELLOW,  # noqa: E402
                                     colours, enabled, expected_offset, toggle_from_menu)

MP_CONFIG = {k: v for k, v in CONFIG.items()}
MP_CONFIG["difficulty"] = dict(CONFIG["difficulty"], max_no_competitors=0)
SPECTATOR = 255


def run(ctx):
    cluster = ctx.cluster(clients=3, seed=4242, company="new", server_config=MP_CONFIG)
    server = cluster.server.bridge
    c1, c2, c3 = (c.bridge for c in cluster.clients)
    for b in (c1, c2, c3):
        b.wait_until(lambda b: b.info()["local_company"] != SPECTATOR, timeout=60, what="client in its company")
    co1, co2 = c1.info()["local_company"], c2.info()["local_company"]
    c3.console("spectate")
    c3.wait_until(lambda b: b.info()["local_company"] == SPECTATOR, timeout=30, what="client3 spectates")
    server.console("setting economy.infrastructure_sharing[1] 1")

    for b, colour in ((c1, DARK_BLUE), (c2, GREEN)):
        for primary in (True, False):
            res = b.docmd("SetCompanyColour", 0, DEFAULT_LIVERY, primary, colour, 0)
            ctx.check(res["success"], f"company colour set to {colour} ({res.get('error')})")
    liveries = {co1: DARK_BLUE, co2: GREEN}

    # Client1 builds a bus route; client2 runs a bus on it from client1's depot.
    owner = Builder(c1, log=ctx.note)
    server.docmd("MoneyCheatAdmin", 0, 50_000_000, as_company=co1)
    mx = server.info()["map"]["x"]
    towns = [(xy % mx, xy // mx) for xy in (server.obj("town", t, "xy")["xy"] for t in sorted(server.objs("town")))]
    dist = lambda p: abs(p[0][0] - p[1][0]) + abs(p[0][1] - p[1][1])  # noqa: E731
    pair = min(((a, c) for i, a in enumerate(towns) for c in towns[i + 1:] if dist((a, c)) >= 12), key=dist)
    route = owner.build_road_route(*pair, vehicles=1)
    other = Builder(c2, log=ctx.note)
    server.docmd("MoneyCheatAdmin", 0, 10_000_000, as_company=co2)
    engine = server.obj("vehicle", route.vehicles[0], "engine_type")["engine_type"]
    bus = other.build_vehicle(route.depot_tile, engine)
    for st in route.stations:
        other.order(bus, "station", st)
    other.start_vehicle(bus)
    server.wait_ticks(30, timeout_ms=10000)

    def check(b, what: str, own: int | None, opp: int | None, local: int) -> None:
        b.wait_ticks(1, timeout_ms=10000)
        seen = colours(b)
        ctx.check(len(seen) == 2, f"{what}: two buses ({seen})")
        for vid, (vowner, twocc, offset) in seen.items():
            want = own if vowner == local else opp
            if want is None:
                ctx.check_eq(offset, expected_offset(twocc, liveries[vowner], liveries[vowner]), f"{what}: bus {vid} of company {vowner} in company colours")
            else:
                second = ORANGE if want == YELLOW else BROWN
                ctx.check_eq(offset, expected_offset(twocc, want, second), f"{what}: bus {vid} of company {vowner}")

    for b, name in ((c1, "client1"), (c2, "client2"), (c3, "client3")):
        ctx.check(not enabled(b), f"{name}: own/opponent colours are off after joining")
    check(c1, "client1 off", None, None, co1)

    for b in (c1, c2, c3):
        toggle_from_menu(ctx, b, lockstep=False)
    for b, name in ((c1, "client1"), (c2, "client2"), (c3, "client3")):
        ctx.check(enabled(b), f"{name}: menu entry switches own/opponent colours on")
    ctx.check(not enabled(server), "server: not switched on by the clients")

    check(c1, "client1 on", YELLOW, RED, co1)
    check(c2, "client2 on", YELLOW, RED, co2)
    check(c3, "client3 spectator on", None, None, SPECTATOR)
    check(server, "server", None, None, SPECTATOR)

    server.wait_ticks(300, timeout_ms=30000)
    ctx.screenshot(c1, "client1_on")
    ctx.screenshot(c2, "client2_on")
    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
