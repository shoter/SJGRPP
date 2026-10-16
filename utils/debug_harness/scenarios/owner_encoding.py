"""
Regression: road owners of companies with high ids (> 15 companies).

Owners are stored in 5-bit map fields in an encoded ("legacy") form. Road depots and level crossings used to store the
road owner un-encoded, so the depot of company 18 read back as OWNER_DEITY (crashing the smallmap owner view, which
indexes per-company tables with it) and level crossings over town roads read back as owned by company 24.

Company 18 builds a road route (with depot) and a level crossing over a town road; then the road owners are checked
through the 'tiles' dump and the GUI is exercised (smallmap in every mode fully zoomed out, plus many windows).
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui_exercise import GuiExerciser  # noqa: E402
from transport import AXIS_X, ROAD, ROAD_NORMAL, ROAD_X, ROAD_Y, SLOPE_FLAT, TRACK_X, TRACK_Y, Builder, GameMap  # noqa: E402

COMPANY = 18      # raw id 18 decodes as OWNER_DEITY
COMPANY_B = 17    # raw id 17 decodes as OWNER_WATER
COMPANIES = 25    # so that company 24 (raw OWNER_TOWN decodes to it) exists
OWNER_TOWN = 0x1C

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 0, "number_towns": 2, "max_no_competitors": 27, "vehicle_breakdowns": 0},
    "pf": {"forbid_90_deg": "false"},
}


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")

    # Companies 1..COMPANIES-1 as (idle) AI companies.
    for _ in range(COMPANIES - 1):
        b.console("start_ai")
        b.step(2)
    ctx.check(COMPANY in b.objs("company"), f"company {COMPANY} exists ({len(b.objs('company'))} companies)")

    builder = Builder(b, log=ctx.note, as_company=COMPANY)
    builder.cmd("MoneyCheat", 0, 100_000_000)

    mx = b.info()["map"]["x"]
    towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
    pair = next((a, c) for i, a in enumerate(towns) for c in towns[i + 1:] if 14 <= abs(a[0] - c[0]) + abs(a[1] - c[1]) <= 40)
    route = builder.build_road_route(*pair, vehicles=1)
    ctx.note(f"company {COMPANY} road route: stations {route.stations}, depot tile {route.depot_tile}")
    builder_b = Builder(b, log=ctx.note, as_company=COMPANY_B)
    builder_b.cmd("MoneyCheat", 0, 100_000_000)
    pair_b = next((a, c) for i, a in enumerate(towns) for c in towns[i + 1:]
                  if 14 <= abs(a[0] - c[0]) + abs(a[1] - c[1]) <= 40 and {a, c}.isdisjoint(pair))
    route_b = builder_b.build_road_route(*pair_b, vehicles=1)

    # Level crossing: rail across a straight, flat town road.
    gmap = GameMap.fetch(b)
    cands = [i for i in range(len(gmap.type)) if gmap.type[i] == ROAD and gmap.sub[i] == ROAD_NORMAL and gmap.slope[i] == SLOPE_FLAT
             and gmap.bits[i] in (ROAD_X, ROAD_Y) and gmap.road_owner[i] == OWNER_TOWN]
    tests = builder.test_many([("BuildSingleRail", i, 0, TRACK_Y if gmap.bits[i] == ROAD_X else TRACK_X, 0) for i in cands[:300]])
    crossing = next((i for i, r in zip(cands, tests) if r["success"]), None)
    ctx.check(crossing is not None, "found a town road tile for a level crossing")
    builder.cmd("BuildSingleRail", crossing, 0, TRACK_Y if gmap.bits[crossing] == ROAD_X else TRACK_X, 0)

    tiles = b.tiles()
    depot_owner = tiles["road_owner"][route.depot_tile]
    crossing_owner = tiles["road_owner"][crossing]
    ok_depot = ctx.check_eq(depot_owner, COMPANY, f"road owner of company {COMPANY}'s road depot", soft=True)
    ok_depot = ctx.check_eq(tiles["road_owner"][route_b.depot_tile], COMPANY_B,
                            f"road owner of company {COMPANY_B}'s road depot", soft=True) and ok_depot
    ok_crossing = ctx.check_eq(crossing_owner, OWNER_TOWN, "road owner of a level crossing over a town road", soft=True)
    ctx.check(tiles["owner"][crossing] == COMPANY, "rail owner of the level crossing", soft=True)

    # Caches recalculated from the map must match the incrementally kept ones (a joining client does exactly this;
    # a mismatch there is a desync).
    mismatches = [l for l in b.console("check_caches") if "mismatch" in l.lower()]
    ok_caches = ctx.check(not mismatches, "caches match the map (check_caches) " + "; ".join(mismatches[:3]), soft=True)

    # Draw every tile in every smallmap mode, then many other windows: must not crash.
    ex = GuiExerciser(b, rng=random.Random(4242), log=ctx.note)
    ex.smallmap(focus_tiles=[route.depot_tile, crossing, route_b.depot_tile])
    ex.windows(40)
    ex.close_all()
    ctx.screenshot(b, "after_gui")
    ctx.check(inst.alive(), f"game survived GUI exercise ({ex.opened} windows, {ex.clicks} clicks)")
    ctx.check(ok_depot and ok_crossing and ok_caches, "road owners are stored correctly for high company ids")
