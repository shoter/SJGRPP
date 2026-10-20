"""Multiplayer used vehicle market: clients buy and sell used vehicles, the market stays identical everywhere."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from transport import Builder  # noqa: E402
from used_vehicles import FULL_MARKET, SELL_TO_USED_MARKET, buyable_listings, listings  # noqa: E402


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=4242, company="new", server_config={"vehicle": FULL_MARKET})
    server = cluster.server.bridge
    c1, c2 = (c.bridge for c in cluster.clients)

    server.wait_ticks(150, timeout_ms=30000)
    ctx.check(len(listings(server)) > 0, "server market seeded")

    c1.docmd("MoneyCheat", 0, 50_000_000)
    builder = Builder(c1, log=print)
    mx = server.info()["map"]["x"]
    towns = [server.obj("town", t, "xy")["xy"] for t in sorted(server.objs("town"))]
    txy = [(xy % mx, xy // mx) for xy in towns]
    ox, oy = txy[0]
    txy.sort(key=lambda p: (p[0] - ox) ** 2 + (p[1] - oy) ** 2)
    route = builder.build_road_route(txy[0], txy[1], vehicles=1)
    depot = route.depot_tile

    company1 = c1.info()["local_company"]
    for _ in range(3):
        cands = buyable_listings(c1, depot)
        if not ctx.check(len(cands) > 0, "client1 can buy a road listing"):
            return
        listing = cands[0]
        res = c1.docmd("BuyUsedVehicle", depot, listing["id"], 0)
        ctx.check(res["success"], f"client1 bought listing {listing['id']}")
        res = c1.docmd("SellVehicle", depot, int(res["result_data"]), SELL_TO_USED_MARKET, 0)
        ctx.check(res["success"], "client1 sold it back to the market")

    server.wait_ticks(200, timeout_ms=30000)
    for b in (c1, c2):
        b.wait_ticks(1, timeout_ms=10000)
    market = listings(server)
    ctx.check_eq(sum(1 for lst in market if lst["seller"] == company1), 3, "three listings sold by client1's company")
    ctx.check(listings(c1) == market and listings(c2) == market, "clients see the same market as the server")

    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
