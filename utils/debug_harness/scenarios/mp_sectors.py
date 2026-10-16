"""
Sectors in multiplayer: two clients buy building rights (also in the same sector), build only where they have rights,
and the sector state stays identical on the server and both clients; removing a company frees its rights.
"""

import re

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 0, "number_towns": 6, "max_no_competitors": 0, "vehicle_breakdowns": 0},
    "economy": {"sectors": "true", "sector_size": 2048},
}

LINE = re.compile(r"^\s*(\d+): '(.*)', tiles: (\d+), centre: (\d+), price: (-?\d+), free first: (\w+), rights: 0x([0-9A-F]+)")


def sectors(b) -> dict[int, tuple]:
    out = {}
    for line in b.console("sectors"):
        m = LINE.match(line)
        if m:
            out[int(m[1])] = (m[2], int(m[3]), int(m[4]), int(m[7], 16))
    return out


def sector_of(b, tile: int) -> int:
    return int(re.search(r"sector (-?\d+)", " ".join(b.console(f"sectors {tile}")))[1])


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=9001, company="new", server_config=CONFIG)
    server = cluster.server.bridge
    c1, c2 = (c.bridge for c in cluster.clients)
    for b in (c1, c2):
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")
    co1, co2 = c1.info()["local_company"], c2.info()["local_company"]
    mx = server.info()["map"]["x"]
    ctx.note(f"companies: client1 {co1!r}, client2 {co2!r}")

    secs = sectors(server)
    ctx.check(len(secs) > 10, f"server generated sectors ({len(secs)})")
    ctx.check(sectors(c1) == secs and sectors(c2) == secs, "clients received the same sectors")

    shared = next(i for i, s in secs.items() if mx * 20 < s[2] < mx * (mx - 20))
    centre = secs[shared][2]

    res = c1.docmd("LandscapeClear", centre, test=True)
    ctx.check(not res["success"], f"client1 cannot clear before buying: {res.get('error')}")

    for name, b in (("client1", c1), ("client2", c2)):
        res = b.docmd("BuySectorRights", 0, shared)
        ctx.check(res["success"] and res.get("cost", 0) == 0, f"{name} gets the shared sector as free first sector: {res}")

    other = next(i for i in secs if i != shared)
    res = c1.docmd("BuySectorRights", 0, other, test=True)
    ctx.check(res.get("cost", 0) > 0, f"second sector is not free ({res.get('cost')})")

    # Build a piece of rail in the shared sector, and fail to in another sector.
    def find_clear(sector: int) -> int:
        start = secs[sector][2]
        for d in range(0, 200):
            for t in (start + d, start - d, start + d * mx, start - d * mx):
                if 0 < t < mx * mx and sector_of(server, t) == sector and c1.docmd("BuildSingleRail", t, 0, 0, 0, test=True)["success"]:
                    return t
        return -1

    tile = find_clear(shared)
    ctx.check(tile > 0, f"found a buildable tile in the shared sector ({tile})")
    res = c1.docmd("BuildSingleRail", tile, 0, 0, 0)
    ctx.check(res["success"], f"client1 builds rail in the shared sector: {res.get('error')}")

    other_tile = secs[other][2]
    res = c2.docmd("BuildSingleRail", other_tile, 0, 0, 0)
    ctx.check(not res["success"] and "building rights" in str(res.get("error")),
              f"client2 cannot build in a sector without rights: {res.get('error')}")

    server.wait_ticks(100, timeout_ms=30000)
    c1.wait_ticks(1, timeout_ms=10000)
    c2.wait_ticks(1, timeout_ms=10000)
    s = sectors(server)
    ctx.check(s[shared][3] == (1 << co1) | (1 << co2), f"both companies own rights in the shared sector ({s[shared][3]:#x})")
    ctx.check(sectors(c1) == s and sectors(c2) == s, "sector rights identical on server and clients")

    # Removing company 2 frees its rights everywhere.
    client2_id = next(int(re.search(r"Client #(\d+)", l)[1]) for l in server.console("clients") if "client2" in l)
    ctx.note(server.console(f"move {client2_id} 255"))  # move client2 to spectators so the company can be reset
    server.wait_ticks(20, timeout_ms=10000)
    ctx.note(server.console(f"reset_company {co2 + 1}"))
    server.wait_ticks(50, timeout_ms=10000)
    c1.wait_ticks(1, timeout_ms=10000)
    s = sectors(server)
    ctx.check(s[shared][3] == (1 << co1), f"rights of the removed company are freed ({s[shared][3]:#x})")
    ctx.check(sectors(c1) == s, "client1 agrees after the company removal")

    ctx.screenshot(c1, "client1")
    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
