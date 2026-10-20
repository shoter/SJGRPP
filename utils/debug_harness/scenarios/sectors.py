"""
Sectors: the map is divided into sectors and companies need building rights to build in them.

Checks random generation (every tile in a sector, sizes), the free first sector, buying further rights, that building,
demolishing and terraforming need rights, that a bridge needs rights for every tile it spans, save/load, and the
sector window and border overlay.
"""

import re

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 0, "number_towns": 4, "max_no_competitors": 0, "vehicle_breakdowns": 0},
    "economy": {"sectors": "true", "sector_size": 2048, "sector_price_per_tile": 100},
}

LINE = re.compile(r"^\s*(\d+): '(.*)', tiles: (\d+), centre: (\d+), price: (-?\d+), free first: (\w+), rights: 0x([0-9A-F]+)")


def sectors(b) -> dict[int, dict]:
    out = {}
    for line in b.console("sectors"):
        m = LINE.match(line)
        if m:
            out[int(m[1])] = {"name": m[2], "tiles": int(m[3]), "centre": int(m[4]), "price": int(m[5]), "rights": int(m[7], 16)}
    return out


def sector_of(b, tile: int) -> int:
    m = re.search(r"sector (-?\d+)", " ".join(b.console(f"sectors {tile}")))
    return int(m[1])


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "777"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    ctx.note(b.console("sectors"))

    secs = sectors(b)
    mx, my = b.info()["map"]["x"], b.info()["map"]["y"]
    ctx.check(20 <= len(secs) <= 40, f"about map size / 2048 sectors ({len(secs)})")
    ctx.check_eq(sum(s["tiles"] for s in secs.values()), mx * my, "every tile is in a sector")
    ctx.check(min(s["tiles"] for s in secs.values()) > 200, f"no tiny sectors (smallest {min(s['tiles'] for s in secs.values())})")
    ctx.check(all(sector_of(b, s["centre"]) == i for i, s in secs.items()), "sector centres are inside their sector")

    company = b.info().get("local_company", 0)
    first = next(i for i, s in secs.items() if s["centre"] % mx > 20 and s["centre"] // mx > 20)
    centre = secs[first]["centre"]

    res = b.docmd("LandscapeClear", centre, test=True)
    ctx.check(not res["success"] and "building rights" in str(res.get("error")), f"clearing without rights fails: {res.get('error')}")
    res = b.docmd("TerraformLand", centre, 1, 1, test=True)
    ctx.check(not res["success"], f"terraforming without rights fails: {res.get('error')}")

    money_before = b.obj("company", company, "money")["money"]
    res = b.docmd("BuySectorRights", 0, first)
    ctx.check(res["success"], f"buy first sector: {res}")
    ctx.check_eq(b.obj("company", company, "money")["money"], money_before, "first sector is free")
    ctx.check(sectors(b)[first]["rights"] & (1 << company), "rights recorded")
    res = b.docmd("BuySectorRights", 0, first, test=True)
    ctx.check(not res["success"], f"buying twice fails: {res.get('error')}")

    res = b.docmd("TerraformLand", centre, 1, 1, test=True)
    ctx.check(res["success"], f"terraforming with rights works: {res.get('error')}")

    second = next(i for i in secs if i != first)
    b.docmd("MoneyCheat", 0, 10_000_000)
    money_before = b.obj("company", company, "money")["money"]
    res = b.docmd("BuySectorRights", 0, second)
    ctx.check(res["success"], f"buy second sector: {res.get('error')}")
    paid = money_before - b.obj("company", company, "money")["money"]
    ctx.check(paid > 0 and abs(paid - secs[second]["tiles"] * 100) <= secs[second]["tiles"] * 10,
              f"second sector costs about tiles * price per tile ({paid} for {secs[second]['tiles']} tiles)")

    # A straight line owned - foreign - owned: a bridge over it needs rights for the middle too.
    owned = {first, second}
    bridge = None
    for y in range(2, my - 2, 2):
        row = [sector_of(b, y * mx + x) for x in range(1, mx - 1)]
        for x0 in range(len(row) - 1):
            if row[x0] in owned and row[x0 + 1] not in owned:
                x1 = next((x for x in range(x0 + 2, min(len(row), x0 + 14)) if row[x] in owned), None)
                if x1 is not None:
                    bridge = (y * mx + x0 + 1, y * mx + x1 + 1)
                    break
        if bridge:
            break
    if bridge is None:
        # Buy a neighbour of the first sector's neighbour instead: walk +x from the first centre.
        ctx.note("no owned-foreign-owned line found, skipping bridge check")
    else:
        ctx.note(f"bridge line {bridge}")
        res = b.docmd("BuildBridge", bridge[1], bridge[0], 0, 0, 0, 0, test=True)
        ctx.check(not res["success"] and "building rights" in str(res.get("error")),
                  f"bridge over a foreign sector fails on rights: {res.get('error')}")
        res = b.docmd("BuildTunnel", bridge[0], 0, 0, test=True)
        ctx.note(f"tunnel test from {bridge[0]}: {res.get('error')}")

    # Save and load keeps sectors and rights.
    before = sectors(b)
    b.save_game("sectors_test")
    b.load_game("sectors_test")
    b.step(5)
    ctx.check(sectors(b) == before, "sectors and rights survive save/load")

    # GUI: sector window and border overlay.
    b.call("show", "sectors")
    b.step(2)
    ctx.check(len(b.find_windows("Sectors")) == 1, "sector window opens")
    ctx.screenshot(b, "sector_window")
    b.console(f"scrollto {centre}")
    w = b.find_windows("Sectors")[0]
    widgets = b.widgets("Sectors", w["number"])["widgets"]
    ctx.note(str([(x.get("id"), x.get("text")) for x in widgets]))
    b.click_widget("Sectors", w["number"], 6)  # WID_SEC_OVERLAY
    b.step(2)
    # Scroll to a border of the first sector.
    x, y = centre % mx, centre // mx
    while sector_of(b, y * mx + x) == first and x < mx - 2:
        x += 1
    b.console(f"scrollto {y * mx + x}")
    b.step(2)
    ctx.screenshot(b, "overlay_on")
