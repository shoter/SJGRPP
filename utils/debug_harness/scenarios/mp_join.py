"""Multiplayer smoke test: dedicated server + 2 headless clients, each founding a company; state stays in sync."""


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=4242, company="new")
    server = cluster.server.bridge
    c1, c2 = (c.bridge for c in cluster.clients)

    companies = server.objs("company")
    ctx.check_eq(len(companies), 2, "server has one company per client")
    ctx.check(c1.info()["local_company"] != c2.info()["local_company"], "clients play different companies")

    clients_out = "\n".join(server.console("clients"))
    ctx.check("client1" in clients_out and "client2" in clients_out, "server lists both clients by name")

    # Let the game run, then compare a slice of game state between the server and both clients.
    server.wait_ticks(150, timeout_ms=30000)
    c1.wait_ticks(1, timeout_ms=10000)
    c2.wait_ticks(1, timeout_ms=10000)
    s_money = {cid: server.obj("company", cid, "money")["money"] for cid in companies}
    for name, b in (("client1", c1), ("client2", c2)):
        ctx.check_eq({cid: b.obj("company", cid, "money")["money"] for cid in companies}, s_money,
                     f"{name} sees the same company money as the server", soft=True)

    # Something that travels client -> server -> other client: a chat message, observed in the other client's log.
    mark = c2.last_log_seq()
    c1.console('say "hello from client1"')
    line = c2.wait_log("hello from client1", timeout_ms=10000, since=mark)
    ctx.check("hello from client1" in line["text"], "chat from client1 arrives at client2")

    ctx.screenshot(c1, "client1")
    ctx.screenshot(c2, "client2")

    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
