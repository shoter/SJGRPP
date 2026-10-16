"""
Sectors in the scenario editor: a new map is divided into sectors, sectors can be created, painted, renamed, priced,
marked as not available as free first sector and merged; a game started from the scenario keeps them.
"""

import re
import shutil
import struct

CONFIG = {
    "game_creation": {"map_x": 7, "map_y": 7, "starting_year": 1990, "landscape": "temperate"},
    "economy": {"sectors": "true", "sector_size": 1024},
}

LINE = re.compile(r"^\s*(\d+): '(.*)', tiles: (\d+), centre: (\d+), price: (-?\d+), free first: (\w+), rights: 0x([0-9A-F]+)")


def sectors(b) -> dict[int, dict]:
    out = {}
    for line in b.console("sectors"):
        m = LINE.match(line)
        if m:
            out[int(m[1])] = {"name": m[2], "tiles": int(m[3]), "centre": int(m[4]), "price": int(m[5]), "free_first": m[6] == "yes"}
    return out


def sector_of(b, tile: int) -> int:
    return int(re.search(r"sector (-?\d+)", " ".join(b.console(f"sectors {tile}")))[1])


def run(ctx):
    inst = ctx.solo(args=["-e", "-G", "31337"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "editor", timeout=120, what="scenario editor")
    mx = b.info()["map"]["x"]

    secs = sectors(b)
    ctx.check(len(secs) >= 8, f"new scenario map is divided into sectors ({len(secs)})")

    res = b.docmd("SectorGenerate", 0, 2048)
    ctx.check(res["success"], f"regenerate with larger sectors: {res.get('error')}")
    secs = sectors(b)
    ctx.check(4 <= len(secs) <= 12, f"fewer sectors after regenerating ({len(secs)})")

    # New sector in a corner, then paint a 10x10 block into it.
    corner = 5 * mx + 5
    res = b.docmd("SectorCreate", corner)
    ctx.check(res["success"], f"create sector: {res}")
    new = sector_of(b, corner)
    ctx.check(new not in secs, f"corner tile is in the new sector {new}")
    res = b.docmd("SectorPaint", 14 * mx + 14, corner, new, False)
    ctx.check(res["success"], f"paint: {res.get('error')}")
    ctx.check_eq(sectors(b)[new]["tiles"], 100, "painted 10x10 tiles into the new sector")

    ctx.check(b.docmd("SectorRename", 0, new, "Mazowieckie")["success"], "rename")
    ctx.check(b.docmd("SectorSetProperties", 0, new, 12345, True)["success"], "set price and forbid free first pick")
    s = sectors(b)[new]
    ctx.check(s["name"] == "Mazowieckie" and s["price"] == 12345 and not s["free_first"], f"properties applied: {s}")

    victim = next(i for i in sectors(b) if i != new)
    tiles_before = sectors(b)[new]["tiles"] + sectors(b)[victim]["tiles"]
    ctx.check(b.docmd("SectorMerge", 0, victim, new)["success"], "merge")
    after = sectors(b)
    ctx.check(victim not in after and after[new]["tiles"] == tiles_before, "merged sector removed, tiles moved")

    # Lasso: the inside of a triangle, brush: single tiles.
    def paint_tiles(sector: int, polygon: bool, tiles: list[int]) -> dict:
        payload = struct.pack("<BBI", sector, polygon, len(tiles)) + b"".join(struct.pack("<I", t) for t in tiles)
        return b.docmd_hex("SectorPaintTiles", 0, payload)

    before = sectors(b)[new]["tiles"]
    res = paint_tiles(new, True, [20 * mx + 20, 20 * mx + 40, 40 * mx + 20])
    ctx.check(res["success"], f"lasso: {res.get('error')}")
    ctx.check(sector_of(b, 25 * mx + 25) == new and sector_of(b, 38 * mx + 38) != new, "lasso fills the inside of the outline only")
    ctx.check(sectors(b)[new]["tiles"] - before >= 200, f"lasso painted about 231 tiles ({sectors(b)[new]['tiles'] - before})")
    res = paint_tiles(new, False, [50 * mx + 50, 50 * mx + 51])
    ctx.check(res["success"] and sector_of(b, 50 * mx + 51) == new, f"brush: {res.get('error')}")
    count = len(sectors(b))
    ctx.check(b.docmd("SectorRemoveEmpty", 0)["success"] and len(sectors(b)) == count, "remove empty keeps sectors with tiles")

    # A scenario needs a town to be playable.
    res = b.docmd("FoundTown", 0, 1, False, 0, True, 0, "")
    ctx.check(res["success"], f"found a random town: {res.get('error')}")

    b.call("show", "sectors")
    b.step(2)
    ctx.screenshot(b, "editor_window")

    # Save, then start a game from it as a scenario.
    b.console("save sectors_scn")
    saved = inst.dir / "save" / "sectors_scn.sav"
    b.wait_until(lambda b: saved.exists() and saved.stat().st_size > 0, timeout=30, what="scenario saved")
    b.step(2)
    scn = ctx.out / "sectors_scn.scn"
    shutil.copy(saved, scn)
    inst.stop()

    game_inst = ctx.solo(name="game", args=["-g", str(scn.resolve())], lockstep=True, config=CONFIG)
    b = game_inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game from scenario")
    b.step(5)

    game = sectors(b)
    ctx.check(game[new]["name"] == "Mazowieckie" and not game[new]["free_first"], f"scenario sector kept in game: {game[new]}")
    ctx.check(any(not re.fullmatch(r"Sector \d+", s["name"]) and s["name"] != "Mazowieckie" for s in game.values()),
              f"the sector of the town is named after it: {[s['name'] for s in game.values()]}")
    res = b.docmd("BuySectorRights", 0, new, test=True)
    ctx.check(res.get("cost", 0) > 0, f"no free first pick for the marked sector (cost {res.get('cost')})")
    res = b.docmd("SectorRename", 0, new, "x", test=True)
    ctx.check(not res["success"], "sectors cannot be edited in a game")
    free = next(i for i, s in game.items() if s["free_first"])
    res = b.docmd("BuySectorRights", 0, free)
    ctx.check(res["success"] and res.get("cost") == 0, f"other sector is free as first ({res})")
