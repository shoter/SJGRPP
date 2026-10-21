"""
Track toll per tile of train length: a train of company 1 runs on company 0's railway and pays the track toll once a
day. The toll charged per day is checked exactly for two trains half a tile apart in length, at 2x price inflation, and does not
change while the train loads (weight does not matter). New games default to the new toll, also when the config file
still has the old per 1000 tonnes toll (economy.sharing_fee[0]); a save keeps the set toll, and a game saved before this
feature (made with the build in OTTD_OLD_EXE, if set) loads with the default toll.
"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sharing_fees import (CHT_INFLATION_COST, CONFIG, DAY_TICKS, SHARING_COST, SHARING_REVENUE, WID_TN_HELP, expense,  # noqa: E402
                          sharing_fees)
from transport import Builder  # noqa: E402

FEE = 7_400                   # per tile of train length per day: 100 per tick and tile
DEF_TRACK_TOLL = 20
VEHICLE_LENGTH = 8            # a default vehicle is half a tile (TILE_SIZE = 16 length units)
SELL_CHAIN = 1                # SellVehicleFlags::SellChain
WID_GO_TAB_ADVANCED = 4       # GameOptionsWidgets


# The config file of a player who ran an older build: the old rail fee, and a road fee to see the file was read.
OLD_CFG = {**CONFIG, "economy": {"sharing_fee[0]": 100, "sharing_fee[1]": 300}}


def setting(b, name: str) -> str:
    """Value of a setting, from the console: "Current value for 'x' is 'y' ..."."""
    line = b.console(f"setting {name}")[0]
    return line.split("'")[3]


def train_length(b, train: int) -> int:
    """Length of a train in vehicle length units: every part of a default train is half a tile long."""
    parts = 0
    v = train
    while v is not None:
        parts += 1
        nxt = b.obj("vehicle", v, "next")["next"]
        v = nxt["id"] if isinstance(nxt, dict) else None
    return parts * VEHICLE_LENGTH


def daily_tolls(ctx, b, company: int, days: int) -> list[int]:
    """Step tick by tick and collect the sharing cost charged each time; the first (partial) day is dropped."""
    tolls = []
    last = expense(b, company, SHARING_COST)
    ticks = 0
    while len(tolls) < days + 1 and ticks < (days + 3) * DAY_TICKS:
        b.step(1)
        ticks += 1
        now = expense(b, company, SHARING_COST)
        if now != last:
            tolls.append(now - last)
        last = now
    ctx.note(f"tolls charged over {ticks} ticks: {tolls}")
    ctx.check(len(tolls) == days + 1, f"train paid the toll once a day for {days} days ({len(tolls)} charges in {ticks} ticks)")
    return tolls[1:]


def sell_train(b, builder: Builder, train: int, depot: int) -> None:
    """Stop a train that is still in its depot and sell it with its wagons."""
    builder.cmd("StartStopVehicle", 0, train, False)
    builder.cmd("SellVehicle", depot, train, SELL_CHAIN, 0)


def build_train(builder: Builder, depot: int, loco: int, wagon: int, wagons: int, stations: list[int]) -> int:
    train = builder.build_vehicle(depot, loco)
    for _ in range(wagons):
        w = builder.build_vehicle(depot, wagon)
        builder.cmd("MoveRailVehicle", 0, w, train, 0)
    for st in stations:
        builder.order(train, "station", st)
    builder.start_vehicle(train)
    return train


def old_save(ctx) -> Path | None:
    """A game saved by a build without this feature, with a track toll of 100 (per 1000 tonnes there)."""
    exe = os.environ.get("OTTD_OLD_EXE")
    if not exe:
        ctx.note("OTTD_OLD_EXE not set: skipping the old savegame check")
        return None
    old = ctx.solo(name="old", args=["-g", "-G", "4242"], lockstep=True, config=CONFIG, exe=exe)
    b = old.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="old build game start")
    b.console("setting economy.sharing_fee[0] 100")
    b.console(f"save {old.dir / 'old_toll'}")
    old.stop()
    # Older builds may or may not add the extension themselves.
    return next(old.dir.glob("old_toll*.sav"), None)


def run(ctx):
    old = old_save(ctx)

    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True, config=OLD_CFG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    ctx.check_eq(setting(b, "economy.sharing_fee[1]"), "300", "fees from the config file are used")
    ctx.check_eq(setting(b, "economy.track_toll"), str(DEF_TRACK_TOLL),
                 "new games start with the default track toll, not the old per 1000 tonnes one of the config file")
    b.console("setting economy.infrastructure_sharing[0] 1")
    b.console(f"setting economy.track_toll {FEE}")
    b.console("start_ai")
    b.step(2)
    payer = next(c for c in b.objs("company") if c != 0)

    # Company 0 builds a railway; its own shuttle train is sold before it leaves the depot.
    owner = Builder(b, log=ctx.note)
    owner.give_money(50_000_000)
    mx = b.info()["map"]["x"]
    towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
    dist = lambda p: abs(p[0][0] - p[1][0]) + abs(p[0][1] - p[1][1])  # noqa: E731
    pair = min(((a, c) for i, a in enumerate(towns) for c in towns[i + 1:] if dist((a, c)) >= 12), key=dist)
    route = owner.build_rail_route(*pair, wagons=3)
    ctx.note(f"company 0 rail route: stations {route.stations}, depot {route.depot_tile}")
    own_train = route.vehicles[0]
    loco = b.obj("vehicle", own_train, "engine_type")["engine_type"]
    wagon_veh = b.obj("vehicle", own_train, "next")["next"]["id"]
    wagon = b.obj("vehicle", wagon_veh, "engine_type")["engine_type"]
    sell_train(b, owner, own_train, route.depot_tile)
    ctx.check(own_train not in b.objs("vehicle"), "company 0's own train is sold")

    # Company 1 runs a locomotive with three wagons on it.
    payer_builder = Builder(b, log=ctx.note, as_company=payer)
    payer_builder.give_money(10_000_000)
    train = build_train(payer_builder, route.depot_tile, loco, wagon, 3, route.stations)
    length = train_length(b, train)
    ctx.note(f"train {train}: {length} length units")
    tolls = daily_tolls(ctx, b, payer, days=12)
    ctx.check_eq(set(tolls), {FEE * length // 16}, f"daily toll of a {length / 16} tile train")
    ctx.check_eq(expense(b, 0, SHARING_REVENUE), -expense(b, payer, SHARING_COST), "owner's revenue equals the payer's cost")

    # The train loaded at a station during these days, and the toll stayed the same: the weight does not matter.
    visited = b.obj("vehicle", train, "last_station_visited")["last_station_visited"]
    ctx.check(visited in route.stations, f"train loaded at a station while paying the toll (last visited {visited})")

    # One more wagon: half a tile longer.
    payer_builder.cmd("SendVehicleToDepot", 0, train, 1 << 4, 0)  # DepotCommandFlag::Sell
    for _ in range(200 * DAY_TICKS):
        if train not in b.objs("vehicle"):
            break
        b.step(DAY_TICKS // 2)
    ctx.check(train not in b.objs("vehicle"), "first train sold in the depot")
    train = build_train(payer_builder, route.depot_tile, loco, wagon, 4, route.stations)
    ctx.check_eq(train_length(b, train), length + VEHICLE_LENGTH, "second train is one wagon longer")
    length = train_length(b, train)
    tolls = daily_tolls(ctx, b, payer, days=8)
    ctx.check_eq(set(tolls), {FEE * length // 16}, f"daily toll of a {length / 16} tile train")

    # Price inflation doubles the toll.
    res = b.docmd("CheatSetting", 0, CHT_INFLATION_COST, 2 << 16)
    ctx.check(res["success"], f"set price inflation to 2x: {res}")
    ctx.check_eq(sharing_fees(b)["rail"], (FEE << 8) * 2, "rail fee at 2x inflation")
    tolls = daily_tolls(ctx, b, payer, days=6)
    ctx.check_eq(set(tolls), {2 * FEE * length // 16}, f"daily toll of a {length / 16} tile train at 2x inflation")

    # The setting with its help text, and the info window.
    for w in b.windows():
        if "Debug" in w["class_name"]:
            b.close_window(w["class"], w["number"])
    b.console("setting gui.settings_restriction_mode 2")
    b.call("show", "game_options")
    b.step(5)
    win = b.find_windows("GameOptions")
    if ctx.check(len(win) == 1, "game options window opened"):
        b.click_widget("GameOptions", win[0]["number"], WID_GO_TAB_ADVANCED)
        b.step(5)
        b.text("track toll")
        b.step(5)
        w = b.find_windows("GameOptions")[0]
        b.click(w["left"] + w["width"] // 2, w["top"] + 131)  # the setting row: shows its help text below
        b.step(5)
        ctx.screenshot(b, "settings")
        b.close_window("GameOptions", w["number"])

    b.click_widget("MainToolbar", 0, WID_TN_HELP)
    b.wait_frames(2)
    menu = next(w for w in b.windows() if w["class_name"] == "DropdownMenu")
    x, y = menu["left"] + menu["width"] // 2, menu["top"] + menu["height"] - 8
    b.move(x, y)
    b.wait_frames(2)
    b.click(x, y)
    b.wait_frames(2)
    b.step(2)
    b.wait_frames(2)
    info = [w for w in b.windows() if w["class_name"] == "SjgrppInfo"]
    if ctx.check(len(info) == 1, "Help > sJGRPP info opens the info window"):
        ctx.screenshot(b, "sjgrpp_info")

    # A save keeps the toll; a game from before this feature gets the default toll.
    b.save_game("toll")
    b.console("setting economy.track_toll 1")
    b.load_game("toll")
    ctx.check_eq(setting(b, "economy.track_toll"), str(FEE), "track toll after save/load")
    if old is not None:
        shutil.copy(old, inst.dir / "save" / "old_toll.sav")
        b.load_game("old_toll")
        ctx.check_eq(setting(b, "economy.track_toll"), str(DEF_TRACK_TOLL), "old savegame loads with the default track toll")
