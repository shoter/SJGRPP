"""Used vehicle market: initial listings, buying, owner history, selling back to the market, yearly ageing, save/load."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transport import Builder  # noqa: E402

SELL_TO_USED_MARKET = 8   # SellVehicleFlags::ToUsedMarket
DAYS_IN_YEAR = 365
TICKS_PER_DAY = 74
NO_COMPANY = 255

# Enough used vehicles at the start of the game that tests can rely on finding ones of the type they need.
FULL_MARKET = {f"used_vehicle_initial_max_{kind}": 100 for kind in ("road", "train", "ship", "aircraft")}


def listings(b) -> list[dict]:
    raw = b.get("used_vehicles.listings")["used_vehicles.listings"]
    out = []
    for item in filter(None, raw.split(";")):
        lid, engine, age, seller, history = (int(x) for x in item.split(":"))
        out.append({"id": lid, "engine": engine, "age": age, "seller": seller, "history": history})
    return out


def refill_market_until(b, condition, attempts: int = 10) -> bool:
    """The number of used vehicles of a type at the start of the game can roll 0: refill the market until condition(listings) holds."""
    for _ in range(attempts):
        if condition(listings(b)):
            return True
        b.console("setting used_vehicle_market false")
        b.step(2 * TICKS_PER_DAY)
        b.console("setting used_vehicle_market true")
        b.step(2 * TICKS_PER_DAY)
    return condition(listings(b))


def buyable_listings(b, depot: int) -> list[dict]:
    """Listings that the local company can buy into the depot."""
    return [lst for lst in listings(b) if b.docmd("BuyUsedVehicle", depot, lst["id"], 0, test=True)["success"]]


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True, config={"vehicle": FULL_MARKET})
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.step(2 * TICKS_PER_DAY)

    market = listings(b)
    ctx.check(len(market) > 0, f"market seeded at game start ({len(market)} listings)")
    ctx.check(all(listing["history"] == 0 and listing["seller"] == NO_COMPANY for listing in market), "generated listings have no previous owners")

    # Build a bus route to get a road depot.
    b.docmd("MoneyCheat", 0, 50_000_000)
    builder = Builder(b, log=print)
    towns = [b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town"))]
    mx = b.info()["map"]["x"]
    txy = [(xy % mx, xy // mx) for xy in towns]
    ox, oy = txy[0]
    txy.sort(key=lambda p: (p[0] - ox) ** 2 + (p[1] - oy) ** 2)
    route = builder.build_road_route(txy[0], txy[1], vehicles=1)
    depot = route.depot_tile

    road_listings = buyable_listings(b, depot)
    if not ctx.check(len(road_listings) > 0, f"road listings can be bought into the depot ({len(road_listings)})"):
        return
    listing = max(road_listings, key=lambda lst: lst["age"])
    new_cost = b.docmd("BuildVehicle", depot, listing["engine"], False, 255, 0, test=True)["cost"]

    count_before = len(listings(b))
    res = b.docmd("BuyUsedVehicle", depot, listing["id"], 0)
    ctx.check(res["success"], f"bought used vehicle: {res}")
    vid = int(res["result_data"])
    ctx.check(0 < res["cost"] <= new_cost, f"used price {res['cost']} <= new price {new_cost}")
    age = b.obj("vehicle", vid, "age")["age"]
    ctx.check_eq(age, listing["age"], "bought vehicle keeps the listing's age")
    ctx.check_eq(len(listings(b)), count_before - 1, "listing removed after buying")
    res2 = b.docmd("BuyUsedVehicle", depot, listing["id"], 0)
    ctx.check(not res2["success"], f"same listing can't be bought twice ({res2.get('error')})")

    # Look at the bought vehicle.
    b.console(f"scrollto {depot}")
    b.step(5)
    ctx.screenshot(b, "after_buy")

    # Sell it back: it becomes a listing with one previous owner (us).
    age = b.obj("vehicle", vid, "age")["age"]
    res = b.docmd("SellVehicle", depot, vid, SELL_TO_USED_MARKET, 0)
    ctx.check(res["success"], f"sold vehicle: {res}")
    sold = [lst for lst in listings(b) if lst["seller"] == 0]
    ctx.check_eq(len(sold), 1, "sold vehicle listed on the market")
    ctx.check_eq(sold[0]["history"], 1, "sold listing records the seller in its owner history")
    ctx.check_eq(sold[0]["age"], age, "sold listing keeps the vehicle's age")

    # Buy it back and sell without the flag (e.g. an internal undo): nothing listed.
    res = b.docmd("BuyUsedVehicle", depot, sold[0]["id"], 0)
    vid = int(res["result_data"])
    before = len(listings(b))
    b.docmd("SellVehicle", depot, vid, 0, 0)
    ctx.check_eq(len(listings(b)), before, "sale without ToUsedMarket does not list the vehicle")


    # Selling a whole train lists only its locomotive, not the wagons.
    pick_engine = builder.pick_engine

    def pick_any_loco(type_, cargo="PASS", **filters):
        # Early locomotives carry no cargo themselves, which pick_engine requires.
        if type_ == "rail" and filters.get("wagon") is False:
            locos = [e for e in builder.engines() if e["type"] == "rail" and not e["wagon"] and e["railtypes"] & 1]
            return max(locos, key=lambda e: (e["intro_year"], e["max_speed"]))
        return pick_engine(type_, cargo, **filters)

    builder.pick_engine = pick_any_loco
    rail = builder.build_rail_route(txy[0], txy[2], wagons=1)
    loco_engine = b.obj("vehicle", rail.vehicles[0], "engine_type")["engine_type"]
    wagon_engine = builder.pick_engine("rail", wagon=True, railtypes=lambda e: e["railtypes"] & 1)["id"]
    train = builder.build_vehicle(rail.depot_tile, loco_engine)
    for _ in range(3):
        builder.cmd("MoveRailVehicle", 0, builder.build_vehicle(rail.depot_tile, wagon_engine), train, 0)
    before = len(listings(b))
    res = b.docmd("SellVehicle", rail.depot_tile, train, SELL_TO_USED_MARKET | 1, 0)  # | SellChain
    ctx.check(res["success"], f"sold train with wagons: {res.get('error')}")
    added = [lst for lst in listings(b)[before:] if lst["seller"] == 0]
    ctx.check(len(added) == 1 and added[0]["engine"] == loco_engine, f"only the locomotive was listed ({added})")

    # Save/load round trip.
    before = listings(b)
    b.console("save used_vehicles_test")
    b.console("load used_vehicles_test")
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=60, what="reload")
    ctx.check(listings(b) == before, f"listings survive save/load ({len(before)} listings)")

    # Yearly ageing: run past the next 1 January.
    before = {lst["id"]: lst for lst in listings(b)}
    date = b.obj("globals", None, "date")["date"]
    b.step((DAYS_IN_YEAR + 2) * TICKS_PER_DAY)
    after = listings(b)
    aged = [lst for lst in after if lst["id"] in before]
    ctx.check(len(aged) > 0 and all(lst["age"] == before[lst["id"]]["age"] + DAYS_IN_YEAR for lst in aged),
              f"surviving listings aged by exactly one year ({len(aged)} of {len(before)})")
    new = [lst for lst in after if lst["id"] not in before]
    ctx.check(len(new) > 0, f"new listings generated at the start of the year ({len(new)})")
    print(f"date before ageing {date}")
