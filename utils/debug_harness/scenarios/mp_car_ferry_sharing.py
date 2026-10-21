"""
Car ferry and infrastructure sharing in multiplayer: client1's company runs a car ferry line, client2's bus boards it
with ship sharing on. The boarding fee and the bus aboard must be the same on the server and both clients. Then
client1 leaves and the server removes its company with the bus aboard: the bus is sold and client2's company gets its
value back, on every instance, and nothing may desync.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from car_ferry import TICKS_PER_DAY, VEH_HIDDEN  # noqa: E402
from car_ferry_sharing import FEE, NEW_VEHICLES, SHARING_COST, SHARING_REVENUE, build_shared_line, expense  # noqa: E402
from transport import Builder  # noqa: E402

MP_CONFIG = {"difficulty": {"max_no_competitors": 0, "vehicle_breakdowns": 0}}
FIELDS = ("ferry_carrier", "ferry_dest", "vehstatus", "tile", "cur_real_order_index")


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=4242, company="new", server_config=MP_CONFIG)
    server = cluster.server.bridge
    c1, c2 = (c.bridge for c in cluster.clients)
    for b in (c1, c2):
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")
    co1, co2 = c1.info()["local_company"], c2.info()["local_company"]
    for line in ("economy.infrastructure_sharing[1] 1", "economy.infrastructure_sharing[2] 1", "economy.sharing_fee[1] 0",
                 f"economy.sharing_fee[2] {FEE}", "sharing_fee_inflation false"):
        server.console(f"setting {line}")
    server.docmd("MoneyCheatAdmin", 0, 50_000_000, as_company=co1)
    server.docmd("MoneyCheatAdmin", 0, 10_000_000, as_company=co2)
    server.wait_ticks(5, timeout_ms=10000)

    def wait(pred, ticks):
        deadline = time.monotonic() + ticks * 0.04 + 30
        while time.monotonic() < deadline:
            if pred():
                return True
            time.sleep(0.2)
        return pred()

    def pause():
        server.console("pause")
        for b in (server, c1, c2):
            b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=20, what="paused")

    line = build_shared_line(server, Builder(c1, log=ctx.note), Builder(c2, log=ctx.note), wait)
    ship, bus, s2 = line["ship"], line["bus"], line["s2"]
    ctx.note(f"company {co1}: car ferry {ship}; company {co2}: bus {bus}")

    ctx.check(wait(lambda: server.obj("vehicle", bus, "ferry_carrier")["ferry_carrier"] == ship, 60 * TICKS_PER_DAY),
              "server: client2's bus boarded client1's car ferry")
    pause()
    states = [b.obj("vehicle", bus, *FIELDS) for b in (server, c1, c2)]
    ctx.check(states[0]["ferry_carrier"] == ship and states[0]["ferry_dest"] == s2 and states[0]["vehstatus"] & VEH_HIDDEN,
              f"bus aboard on the server ({states[0]})")
    ctx.check(states[1] == states[0] and states[2] == states[0], f"clients agree on the bus aboard ({states})")
    costs = [expense(b, co2, SHARING_COST) for b in (server, c1, c2)]
    revenues = [expense(b, co1, SHARING_REVENUE) for b in (server, c1, c2)]
    ctx.check(costs == [FEE] * 3, f"bus owner paid one boarding fee everywhere: {costs}")
    ctx.check(revenues == [-FEE] * 3, f"ferry owner received it everywhere: {revenues}")

    # Client1 leaves; the server removes its company while the bus is aboard its ferry.
    value = int(server.obj("vehicle", bus, "value")["value"])
    bought = [expense(b, co2, NEW_VEHICLES) for b in (server, c2)]
    cluster.clients[0].stop()
    server.console("unpause")
    server.wait_ticks(5, timeout_ms=10000)
    out = server.console(f"reset_company {co1 + 1}")
    ctx.note(f"reset_company: {out}")
    ctx.check(wait(lambda: co1 not in server.objs("company"), 5 * TICKS_PER_DAY), "server removed client1's company")
    c2.wait_ticks(5, timeout_ms=10000)
    server.console("pause")
    for b in (server, c2):
        b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=20, what="paused")
    for name, b, before in (("server", server, bought[0]), ("client2", c2, bought[1])):
        ctx.check(bus not in b.objs("vehicle"), f"{name}: the bus aboard went with the ferry")
        ctx.check_eq(expense(b, co2, NEW_VEHICLES) - before, -value, f"{name}: bus owner got the bus's value ({value}) back")
    ctx.check(ship not in server.objs("vehicle"), "server: the ferry is gone")
    server.console("unpause")

    # Client1 joins again and gets the game without the ferry and the bus.
    client1 = cluster.clients[0]
    cluster.start_client(client1)
    c1 = client1.bridge
    pause()
    expenses = [b.obj("company", co2, "yearly_expenses")["yearly_expenses"] for b in (server, c1, c2)]
    ctx.check(expenses[1] == expenses[0] and expenses[2] == expenses[0], "all instances agree on the bus owner's expenses")
    ctx.check(bus not in c1.objs("vehicle") and ship not in c1.objs("vehicle"), "rejoined client1 has neither the ferry nor the bus")
    server.console("unpause")
    c2.wait_ticks(20, timeout_ms=10000)

    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
