"""
Variable inflation in multiplayer: the server runs a game with inflation rates that change every year. After each new
year the rates and the history must be the same on the server, two company clients and a spectator. Every client,
the spectator included, opens the Economy graph and keeps it open while the years change, and nothing may desync.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from variable_inflation import CONFIG, inflation, open_economy_graph  # noqa: E402


def run(ctx):
    cluster = ctx.cluster(clients=3, seed=777, company="new", server_config=CONFIG)
    server = cluster.server.bridge
    clients = [c.bridge for c in cluster.clients]
    for b in clients:
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")

    # The third client becomes a spectator.
    spectator = clients[2]
    server.console(f"move {spectator.info()['client_id']} 255")
    spectator.wait_until(lambda b: b.info()["local_company"] == 255, timeout=30, what="client3 spectating")

    for i, b in enumerate(clients):
        who = "spectator" if b is spectator else f"client{i + 1}"
        ctx.check(open_economy_graph(ctx, b), f"{who} opens Graphs > Economy")

    def year():
        return int(server.info()["date"][:4])

    first = year()
    for target in (first + 1,):
        server.wait_until(lambda b: int(b.info()["date"][:4]) >= target, timeout=1200, what=f"year {target}")
        server.console("pause")
        server.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=10, what="server paused")
        for b in clients:
            b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=10, what="client paused")
        states = [inflation(b) for b in [server] + clients]
        ctx.note(f"year {target}: {states[0]}")
        ctx.check(all(st == states[0] for st in states), f"same inflation on server, clients and spectator in {target}")
        for i, b in enumerate(clients):
            ctx.check(len(b.find_windows("EconomyGraph")) == 1, f"economy graph still open on client{i + 1} in {target}")
        ctx.check(len(states[0]["history"]) >= 2, f"history grows ({len(states[0]['history'])} entries)")
        server.console("unpause")

    ctx.screenshot(clients[0], "client1_economy_graph")
    ctx.screenshot(spectator, "spectator_economy_graph")
    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
