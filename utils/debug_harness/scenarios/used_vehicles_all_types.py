"""
Used vehicle market for every vehicle type: buy a used road vehicle, train, ship and aircraft into a depot of its own
type, check it keeps the listing's age, sell it back, and check a listing can't be bought into a depot of another type.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from transport import BuildError, Builder  # noqa: E402
from used_vehicles import SELL_TO_USED_MARKET, TICKS_PER_DAY, listings  # noqa: E402

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate", "variety": 0},
    "difficulty": {"terrain_type": 0, "quantity_sea_lakes": 2, "number_towns": 2, "max_no_competitors": 0,
                   "vehicle_breakdowns": 0},
    "station": {"never_expire_airports": "true"},
    "vehicle": {"plane_crashes": 0},
}


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "5151"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.step(2 * TICKS_PER_DAY)
    b.docmd("MoneyCheat", 0, 200_000_000)
    company = b.info().get("local_company", 0)

    builder = Builder(b, log=print)
    pick_engine = builder.pick_engine

    def pick_any_loco(type_, cargo="PASS", **filters):
        # Early locomotives carry no cargo themselves, which pick_engine requires.
        if type_ == "rail" and filters.get("wagon") is False:
            locos = [e for e in builder.engines() if e["type"] == "rail" and not e["wagon"] and e["railtypes"] & 1]
            return max(locos, key=lambda e: (e["intro_year"], e["max_speed"]))
        return pick_engine(type_, cargo, **filters)

    builder.pick_engine = pick_any_loco

    mx = b.info()["map"]["x"]
    towns = [b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town"))]
    txy = [(xy % mx, xy // mx) for xy in towns]
    ox, oy = txy[0]
    txy.sort(key=lambda p: (p[0] - ox) ** 2 + (p[1] - oy) ** 2)

    builders = {
        "road": lambda: builder.build_road_route(txy[0], txy[1], vehicles=1),
        "rail": lambda: builder.build_rail_route(txy[0], txy[2], wagons=1),
        "aircraft": lambda: builder.build_air_route(txy[0], txy[-1], vehicles=1),
        "ship": lambda: builder.build_ship_route(txy[0], vehicles=1),
    }
    engine_type = {e["id"]: e["type"] for e in b.engines()}
    depots: dict[str, int] = {}

    for kind, build in builders.items():
        try:
            depots[kind] = build().depot_tile
        except BuildError as e:
            ctx.check(False, f"{kind}: could not build a route with a depot: {e}")
            continue

        depot = depots[kind]
        cands = [lst for lst in listings(b) if engine_type.get(lst["engine"]) == kind]
        ctx.check(len(cands) > 0, f"{kind}: market has listings of this type ({len(cands)})")
        results = builder.test_many([("BuyUsedVehicle", depot, lst["id"], 0) for lst in cands])
        buyable = [lst for lst, r in zip(cands, results) if r["success"]]
        if not ctx.check(len(buyable) > 0, f"{kind}: a listing can be bought into the depot "
                                           f"({len(buyable)} of {len(cands)}; {results[0].get('error') if results else ''})"):
            continue

        listing = max(buyable, key=lambda lst: lst["age"])
        count_before = len(listings(b))
        res = b.docmd("BuyUsedVehicle", depot, listing["id"], 0)
        if not ctx.check(res["success"], f"{kind}: bought listing {listing['id']}: {res.get('error')}"):
            continue
        vid = int(res["result_data"])
        v = b.obj("vehicle", vid, "engine_type", "owner", "age")
        ctx.check_eq(v["engine_type"], listing["engine"], f"{kind}: vehicle has the listing's engine")
        ctx.check_eq(v["owner"], company, f"{kind}: vehicle belongs to the buyer")
        ctx.check_eq(v["age"], listing["age"], f"{kind}: vehicle keeps the listing's age")
        ctx.check_eq(len(listings(b)), count_before - 1, f"{kind}: listing removed after buying")

        res = b.docmd("SellVehicle", depot, vid, SELL_TO_USED_MARKET, 0)
        ctx.check(res["success"], f"{kind}: sold back to the market: {res.get('error')}")
        mine = [lst for lst in listings(b) if lst["seller"] == company and lst["engine"] == listing["engine"]]
        ctx.check(len(mine) == 1 and mine[0]["history"] == 1, f"{kind}: sold vehicle listed with one previous owner ({mine})")

    # A listing can only go into a depot of its own vehicle type.
    for kind, depot in depots.items():
        foreign = next((lst for lst in listings(b) if engine_type.get(lst["engine"]) not in (kind, None)), None)
        if foreign is None:
            continue
        res = b.docmd("BuyUsedVehicle", depot, foreign["id"], 0, test=True)
        ctx.check(not res["success"],
                  f"{kind} depot rejects a {engine_type[foreign['engine']]} listing: {res.get('error')}")

    # Buying a listing that no longer exists fails cleanly.
    if depots:
        kind, depot = next(iter(depots.items()))
        missing = max(lst["id"] for lst in listings(b)) + 1000
        res = b.docmd("BuyUsedVehicle", depot, missing, 0, test=True)
        ctx.check(not res["success"], f"buying a missing listing fails: {res.get('error')}")
