"""
Multiplayer sector rights between companies: client2 can't build, demolish or terraform in a sector only client1 has
rights in, nor touch client1's track there; after buying rights (paid, since it is not its first sector) it can build.
A client joining later gets the same sectors and rights.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mp_sectors import sector_of, sectors  # noqa: E402
from ottd import Instance  # noqa: E402

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 0, "number_towns": 2, "max_no_competitors": 0, "vehicle_breakdowns": 0,
                   "money_cheat_in_multiplayer": "true"},
    "economy": {"sectors": "true", "sector_size": 2048},
}


def sync(server, *clients) -> None:
    server.wait_ticks(50, timeout_ms=30000)
    for c in clients:
        c.wait_ticks(1, timeout_ms=10000)


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=9002, company="new", server_config=CONFIG)
    server = cluster.server.bridge
    c1, c2 = (c.bridge for c in cluster.clients)
    for b in (c1, c2):
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")
    co1, co2 = c1.info()["local_company"], c2.info()["local_company"]
    mx = server.info()["map"]["x"]

    secs = sectors(server)
    inner = [i for i, s in secs.items() if mx * 20 < s[2] < mx * (mx - 20)]
    mine, home2 = inner[0], inner[1]

    def find_clear(b, sector: int) -> int:
        start = secs[sector][2]
        for d in range(0, 200):
            for t in (start + d, start - d, start + d * mx, start - d * mx):
                if 0 < t < mx * mx and sector_of(server, t) == sector and \
                        b.docmd("BuildSingleRail", t, 0, 0, 0, test=True)["success"]:
                    return t
        return -1

    res = c1.docmd("BuySectorRights", 0, mine)
    ctx.check(res["success"], f"client1 buys its first sector: {res.get('error')}")
    res = c2.docmd("BuySectorRights", 0, home2)
    ctx.check(res["success"] and res.get("cost", 0) == 0, f"client2 gets another sector free: {res}")
    sync(server, c1, c2)

    rail_tile = find_clear(c1, mine)
    ctx.check(rail_tile > 0, f"found a buildable tile in client1's sector ({rail_tile})")
    res = c1.docmd("BuildSingleRail", rail_tile, 0, 0, 0)
    ctx.check(res["success"], f"client1 builds rail in its sector: {res.get('error')}")
    free_tile = next(t for t in (rail_tile + 2, rail_tile - 2, rail_tile + 2 * mx, rail_tile - 2 * mx)
                     if sector_of(server, t) == mine and c1.docmd("BuildSingleRail", t, 0, 0, 0, test=True)["success"])
    sync(server, c1, c2)

    # client2 has no rights in client1's sector.
    for what, cmd, args in (("build rail", "BuildSingleRail", (free_tile, 0, 0, 0)),
                            ("clear land", "LandscapeClear", (free_tile,)),
                            ("terraform", "TerraformLand", (free_tile, 1, 1))):
        res = c2.docmd(cmd, *args)
        ctx.check(not res["success"] and "building rights" in str(res.get("error")),
                  f"client2 can't {what} in client1's sector: {res.get('error')}")
    res = c2.docmd("RemoveSingleRail", rail_tile, 0)
    ctx.check(not res["success"], f"client2 can't remove client1's rail: {res.get('error')}")

    # Buying in: not free any more, but then building works.
    c2.docmd("MoneyCheat", 0, 50_000_000)
    money_before = server.obj("company", co2, "money")["money"]
    res = c2.docmd("BuySectorRights", 0, mine)
    ctx.check(res["success"] and res.get("cost", 0) > 0, f"client2 pays for rights in client1's sector: {res}")
    sync(server, c1, c2)
    ctx.check(server.obj("company", co2, "money")["money"] < money_before, "client2's money went down")
    res = c2.docmd("BuildSingleRail", free_tile, 0, 0, 0)
    ctx.check(res["success"], f"client2 builds in the sector after buying rights: {res.get('error')}")
    res = c2.docmd("RemoveSingleRail", rail_tile, 0)
    ctx.check(not res["success"], f"rights don't allow removing client1's rail: {res.get('error')}")
    res = c2.docmd("BuySectorRights", 0, mine)
    ctx.check(not res["success"], f"client2 can't buy the same rights twice: {res.get('error')}")

    sync(server, c1, c2)
    s = sectors(server)
    ctx.check_eq(s[mine][3], (1 << co1) | (1 << co2), "both companies hold rights in client1's sector")
    ctx.check_eq(s[home2][3], 1 << co2, "client2's home sector is only its own")
    ctx.check(sectors(c1) == s and sectors(c2) == s, "clients agree with the server")

    late = Instance("client3", mode="headless", root=cluster.root, config=None)
    cluster.clients.append(late)
    ctx.instances.append(late)
    cluster.start_client(late)
    sync(server, c1, c2, late.bridge)
    ctx.check(sectors(late.bridge) == sectors(server), "late joiner gets the same sectors and rights")

    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
