"""
Own infrastructure sharing fees in multiplayer: client2's bus loads at client1's stations while client1 lowers and
raises its own road fee. Every change is announced in the chat of the server and both clients; the own fees and
announced rises must be the same on every instance, the bus owner is charged in steps of the own per-tick fee, the
settings window of client1's company is read-only for client2, and nothing may desync.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from company_sharing_fees import FEE, MAX_FEE, ROAD, ROW_Y, TICK_BOX_X, own_fees, set_fee  # noqa: E402
from mp_sharing_fees import MP_CONFIG  # noqa: E402
from sharing_fees import SHARING_COST, SHARING_REVENUE, expense  # noqa: E402
from transport import Builder  # noqa: E402

NOTICE_DAYS = 5


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=4242, company="new", server_config=MP_CONFIG)
    server = cluster.server.bridge
    c1, c2 = (c.bridge for c in cluster.clients)
    everyone = (server, c1, c2)
    for b in (c1, c2):
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")
    co1, co2 = c1.info()["local_company"], c2.info()["local_company"]
    server.console("setting economy.infrastructure_sharing[1] 1")
    server.console(f"setting economy.sharing_fee[1] {FEE}")
    server.console(f"setting economy.sharing_fee_max[1] {MAX_FEE}")
    server.console(f"setting economy.sharing_fee_notice_days {NOTICE_DAYS}")
    server.wait_ticks(10, timeout_ms=10000)

    def fees_everywhere(what: str) -> dict:
        """Pause the game, compare the own fees on all instances, unpause; returns the server's."""
        server.console("pause")
        for b in everyone:
            b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=20, what="paused")
        fees = [own_fees(b) for b in everyone]
        server.console("unpause")
        ctx.check(fees[0] == fees[1] == fees[2], f"same own fees on the server and both clients: {what}")
        return fees[0]

    def wait_message(text: str, since: list[int]) -> None:
        for b, seq, name in zip(everyone, since, ("server", "client1", "client2")):
            res = b.wait_log(text, 20000, seq)
            line = res.get("text", "").encode("ascii", "replace").decode()
            ctx.check(text in line, f"{name} shows the change in the chat: {line}")

    def wait_charged(per_tick: int, what: str) -> None:
        start = expense(server, co2, SHARING_COST)
        server.wait_until(lambda b: expense(b, co2, SHARING_COST) - start >= 40 * per_tick, timeout=180,
                          what=f"bus charged ({what})")
        start = expense(server, co2, SHARING_COST)
        server.wait_until(lambda b: expense(b, co2, SHARING_COST) - start >= 20 * per_tick, timeout=180,
                          what=f"bus charged again ({what})")
        charged = expense(server, co2, SHARING_COST) - start
        ctx.check(charged % per_tick == 0, f"{what}: charged {charged} in steps of {per_tick}")

    fees = fees_everywhere("game start")
    ctx.check(all(f["custom"] == 0 for f in fees.values()), "new companies charge the server fees")

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
    wait_charged(1000, "server fee")

    # Client1 lowers its own road fee: in effect at once, announced in every chat.
    since = [b.last_log_seq() for b in everyone]
    res = set_fee(c1, ROAD, True, FEE // 2)
    ctx.check(res["success"], f"client1 lowers its road fee: {res.get('success')}")
    wait_message("changed its stopping fee for road vehicles", since)
    ctx.screenshot(c2, "client2_chat_changed")
    fees = fees_everywhere("lower fee")
    ctx.check_eq((fees[(co1, "road")]["custom"], fees[(co1, "road")]["fee"], fees[(co1, "road")]["pending"]), (1, FEE // 2, 0),
                 "client1's lower fee is in effect")
    wait_charged(500, "client1's lower fee")

    # Client1 raises it: announced with the notice period, in effect after it.
    since = [b.last_log_seq() for b in everyone]
    set_fee(c1, ROAD, True, FEE * 3 // 2)
    wait_message("will raise its stopping fee for road vehicles", since)
    ctx.screenshot(c2, "client2_chat_announced")
    fees = fees_everywhere("announced rise")
    ctx.check_eq((fees[(co1, "road")]["fee"], fees[(co1, "road")]["pending"], fees[(co1, "road")]["pending_fee"]), (FEE // 2, 1, FEE * 3 // 2),
                 "client1's rise is announced")
    server.wait_until(lambda b: own_fees(b)[(co1, "road")]["pending"] == 0, timeout=120, what="notice period over")
    fees = fees_everywhere("rise in effect")
    ctx.check_eq((fees[(co1, "road")]["fee"], fees[(co1, "road")]["pending"]), (FEE * 3 // 2, 0), "client1's rise is in effect")
    wait_charged(1500, "client1's higher fee")

    # Client2 sets its own fee: only its own company changes.
    since = [b.last_log_seq() for b in everyone]
    set_fee(c2, ROAD, True, 1000)
    wait_message("changed its stopping fee for road vehicles from", since)
    fees = fees_everywhere("client2's own fee")
    ctx.check_eq((fees[(co2, "road")]["fee"], fees[(co1, "road")]["fee"]), (1000, FEE * 3 // 2), "client2 changed only its own fee")

    # Client1's settings are read-only for client2.
    nth = sorted(server.objs("company")).index(co1)
    c2.call("show", "company", nth)
    c2.wait_frames(3)
    widgets = c2.widgets("Company", co1)["widgets"]
    button = next(w for w in widgets if "Settings" in (w.get("text") or ""))
    c2.click_widget("Company", co1, button["index"])
    c2.wait_frames(3)
    panel = next(w for w in c2.widgets("CompanySettings", co1)["widgets"] if w["index"] == 1)
    c2.click(panel["x"] + TICK_BOX_X, panel["y"] + ROW_Y[ROAD])
    c2.wait_frames(3)
    server.wait_ticks(5, timeout_ms=10000)
    fees = fees_everywhere("after client2 clicked client1's settings")
    ctx.check_eq(fees[(co1, "road")]["custom"], 1, "client2 can't change client1's fee")
    c2.move(0, 0)
    c2.wait_frames(2)
    ctx.screenshot(c2, "client2_reads_client1_settings")

    server.console("pause")
    server.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=10, what="server paused")
    ctx.check_eq(expense(server, co1, SHARING_REVENUE), -expense(server, co2, SHARING_COST), "owner's revenue equals the payer's cost")
    server.console("unpause")
    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
