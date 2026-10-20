"""
Multiplayer used vehicle handover: a vehicle client1 sells to the market shows up for client2, who buys it into its own
depot; client1 can't buy it again or sell it, client2's resale records both owners, and a client joining later gets
the same market.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ottd import Instance  # noqa: E402
from transport import Builder  # noqa: E402
from used_vehicles import FULL_MARKET, SELL_TO_USED_MARKET, buyable_listings, listings  # noqa: E402

CONFIG = {
    "difficulty": {"max_no_competitors": 0, "vehicle_breakdowns": 0, "money_cheat_in_multiplayer": "true"},
    "vehicle": FULL_MARKET,
}


def sync(server, *clients) -> None:
    server.wait_ticks(50, timeout_ms=30000)
    for c in clients:
        c.wait_ticks(1, timeout_ms=10000)


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=4243, company="new", server_config=CONFIG)
    server = cluster.server.bridge
    c1, c2 = (c.bridge for c in cluster.clients)
    for b in (c1, c2):
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")
    co1, co2 = c1.info()["local_company"], c2.info()["local_company"]
    server.wait_ticks(150, timeout_ms=30000)

    mx = server.info()["map"]["x"]
    towns = [server.obj("town", t, "xy")["xy"] for t in sorted(server.objs("town"))]
    txy = [(xy % mx, xy // mx) for xy in towns]
    ox, oy = txy[0]
    txy.sort(key=lambda p: (p[0] - ox) ** 2 + (p[1] - oy) ** 2)

    routes = []
    for b, (a, z) in ((c1, (txy[0], txy[1])), (c2, (txy[-1], txy[-2]))):
        b.docmd("MoneyCheat", 0, 50_000_000)
        routes.append(Builder(b, log=print).build_road_route(a, z, vehicles=1))
    depot1, depot2 = (r.depot_tile for r in routes)

    # client1 buys a generated listing and sells it back: now it is a listing with client1 as seller.
    cands = buyable_listings(c1, depot1)
    if not ctx.check(len(cands) > 0, "client1 can buy a generated road listing"):
        return
    listing = cands[0]
    res = c1.docmd("BuyUsedVehicle", depot1, listing["id"], 0)
    ctx.check(res["success"], f"client1 bought generated listing {listing['id']}: {res.get('error')}")
    res = c1.docmd("SellVehicle", depot1, int(res["result_data"]), SELL_TO_USED_MARKET, 0)
    ctx.check(res["success"], f"client1 sold it to the market: {res.get('error')}")
    sync(server, c1, c2)

    sold = [lst for lst in listings(server) if lst["seller"] == co1]
    if not ctx.check(len(sold) == 1, f"one listing sold by client1's company ({sold})"):
        return
    sold = sold[0]
    ctx.check(sold in listings(c2), "client2 sees client1's listing")

    # client2 buys it into its own depot.
    res = c2.docmd("BuyUsedVehicle", depot2, sold["id"], 0)
    if not ctx.check(res["success"], f"client2 bought client1's vehicle: {res.get('error')}"):
        return
    vid = int(res["result_data"])
    sync(server, c1, c2)
    v = server.obj("vehicle", vid, "owner", "age", "tile")
    ctx.check_eq(v["owner"], co2, "bought vehicle belongs to client2's company")
    # Real-time multiplayer: a few days may pass between buying and reading the vehicle.
    ctx.check(0 <= v["age"] - sold["age"] <= 5, f"bought vehicle keeps its age ({v['age']} vs listing {sold['age']})")
    ctx.check(all(lst["id"] != sold["id"] for lst in listings(server)), "listing gone from the market")
    ctx.check(listings(c1) == listings(server) and listings(c2) == listings(server), "clients see the same market")

    res = c1.docmd("BuyUsedVehicle", depot1, sold["id"], 0)
    ctx.check(not res["success"], f"client1 can't buy the sold listing: {res.get('error')}")
    res = c1.docmd("SellVehicle", v["tile"], vid, SELL_TO_USED_MARKET, 0)
    ctx.check(not res["success"], f"client1 can't sell client2's vehicle: {res.get('error')}")

    # client2 resells: the listing now remembers both previous owners.
    res = c2.docmd("SellVehicle", v["tile"], vid, SELL_TO_USED_MARKET, 0)
    ctx.check(res["success"], f"client2 resold the vehicle: {res.get('error')}")
    sync(server, c1, c2)
    resold = [lst for lst in listings(server) if lst["seller"] == co2]
    ctx.check(len(resold) == 1 and resold[0]["history"] == 2, f"resold listing has two previous owners ({resold})")

    # A client joining now receives the market in the savegame.
    late = Instance("client3", mode="headless", root=cluster.root, config=None)
    cluster.clients.append(late)
    ctx.instances.append(late)
    cluster.start_client(late)
    c3 = late.bridge
    sync(server, c1, c2, c3)
    ctx.check(listings(c3) == listings(server), f"late joiner gets the same market ({len(listings(server))} listings)")

    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
