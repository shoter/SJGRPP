"""
Infrastructure sharing fees in multiplayer: client2's bus loads at client1's stations while the server changes the
price inflation and the scaling setting. Fees must be the same on the server and both clients, the bus owner must be
charged in steps of the scaled per-tick fee, and nothing may desync.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sharing_fees import CHT_INFLATION_COST, CONFIG, FEE, SHARING_COST, SHARING_REVENUE, expense, sharing_fees  # noqa: E402
from transport import Builder  # noqa: E402

MP_CONFIG = {k: v for k, v in CONFIG.items()}
MP_CONFIG["difficulty"] = dict(CONFIG["difficulty"], max_no_competitors=0)


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=4242, company="new", server_config=MP_CONFIG)
    server = cluster.server.bridge
    c1, c2 = (c.bridge for c in cluster.clients)
    for b in (c1, c2):
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")
    co1, co2 = c1.info()["local_company"], c2.info()["local_company"]
    server.console("setting economy.infrastructure_sharing[1] 1")
    server.console(f"setting economy.sharing_fee[1] {FEE}")
    server.wait_ticks(10, timeout_ms=10000)

    def all_fees():
        for b in (c1, c2):
            b.wait_ticks(1, timeout_ms=10000)
        return [sharing_fees(b) for b in (server, c1, c2)]

    fees = all_fees()
    ctx.check(fees[0] == fees[1] == fees[2] and fees[0]["road"] == FEE << 8, f"same fees everywhere at game start: {fees}")

    # Client1 builds a bus route; client2 runs a bus on it from client1's depot.
    owner = Builder(c1, log=ctx.note)
    server.docmd("MoneyCheatAdmin", 0, 50_000_000, as_company=co1)
    mx = server.info()["map"]["x"]
    towns = [(xy % mx, xy // mx) for xy in (server.obj("town", t, "xy")["xy"] for t in sorted(server.objs("town")))]
    dist = lambda p: abs(p[0][0] - p[1][0]) + abs(p[0][1] - p[1][1])  # noqa: E731
    pair = min(((a, c) for i, a in enumerate(towns) for c in towns[i + 1:] if dist((a, c)) >= 12), key=dist)
    route = owner.build_road_route(*pair, vehicles=1)
    payer = Builder(c2, log=ctx.note)
    server.docmd("MoneyCheatAdmin", 0, 10_000_000, as_company=co2)
    engine = server.obj("vehicle", route.vehicles[0], "engine_type")["engine_type"]
    bus = payer.build_vehicle(route.depot_tile, engine)
    for st in route.stations:
        payer.order(bus, "station", st)
    payer.start_vehicle(bus)

    def wait_charged(per_tick: int, what: str) -> None:
        start = expense(server, co2, SHARING_COST)
        server.wait_until(lambda b: expense(b, co2, SHARING_COST) - start >= 40 * per_tick, timeout=180,
                          what=f"bus charged ({what})")
        start = expense(server, co2, SHARING_COST)
        server.wait_until(lambda b: expense(b, co2, SHARING_COST) - start >= 20 * per_tick, timeout=180,
                          what=f"bus charged again ({what})")
        charged = expense(server, co2, SHARING_COST) - start
        ctx.check(charged % per_tick == 0, f"{what}: charged {charged} in steps of {per_tick}")

    wait_charged(1000, "no inflation")

    res = server.docmd("CheatSetting", 0, CHT_INFLATION_COST, 2 << 16)
    ctx.check(res["success"], f"server sets price inflation to 2x: {res.get('error')}")
    server.wait_ticks(5, timeout_ms=10000)
    fees = all_fees()
    ctx.check(fees[0] == fees[1] == fees[2] and fees[0]["road"] == (FEE << 8) * 2, f"same fees everywhere at 2x inflation: {fees}")
    wait_charged(2000, "2x inflation")

    server.console("setting sharing_fee_inflation false")
    server.wait_ticks(5, timeout_ms=10000)
    fees = all_fees()
    ctx.check(fees[0] == fees[1] == fees[2] and fees[0]["road"] == FEE << 8, f"same fees everywhere with scaling off: {fees}")
    wait_charged(1000, "scaling off")

    server.console("pause")
    server.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=10, what="server paused")
    ctx.check_eq(expense(server, co1, SHARING_REVENUE), -expense(server, co2, SHARING_COST), "owner's revenue equals the payer's cost")

    server.console("unpause")
    ctx.screenshot(c2, "client2")
    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
