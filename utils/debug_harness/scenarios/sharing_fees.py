"""
Infrastructure sharing fees scale with price inflation: a bus of company 1 loads at company 0's stations and pays the
road stopping fee every tick it loads. The fee charged per tick is checked exactly at 1x and 2x price inflation and with
the scaling switched off; the 'sharing_fees' console command and the sJGRPP info window show the same fees.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transport import Builder  # noqa: E402

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 0, "number_towns": 3, "max_no_competitors": 2, "vehicle_breakdowns": 0},
}

CHT_INFLATION_COST = 8
SHARING_COST = 13             # ExpensesType::SharingCost; yearly_expenses[0][13] is this year's
SHARING_REVENUE = 14
FEE = 74_000                  # per day of 74 ticks: exactly 1000 per tick without inflation
DAY_TICKS = 74
WID_TN_HELP = 29


def sharing_fees(b) -> dict[str, int]:
    """Road/rail/... fee as money fraction (1/256), from the console command."""
    out = {}
    for line in b.console("sharing_fees"):
        if "fee_fract" in line:
            out[line.split(":")[0]] = int(line.rsplit(" ", 1)[1])
    return out


def expense(b, company: int, kind: int) -> int:
    return int(b.obj("company", company, "yearly_expenses")["yearly_expenses"][kind])


def charged_per_tick(ctx, b, company: int, max_ticks: int = 60 * DAY_TICKS, wanted: int = 40) -> set[int]:
    """Step tick by tick until the fee was charged in `wanted` ticks; collect the non-zero sharing cost deltas."""
    deltas = set()
    count = 0
    ticks = 0
    last = expense(b, company, SHARING_COST)
    while count < wanted and ticks < max_ticks:
        b.step(1)
        ticks += 1
        now = expense(b, company, SHARING_COST)
        if now != last:
            deltas.add(now - last)
            count += 1
        last = now
    ctx.note(f"{count} charged ticks of {ticks}: deltas {sorted(deltas)}")
    ctx.check(count >= wanted, f"bus loaded at the foreign station for {wanted} ticks ({count})")
    return deltas


def setup_shared_route(ctx, b, builder_owner: Builder, payer: int, mx: int) -> None:
    """Owner builds a road route; the payer company runs a bus on it from the owner's depot."""
    towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
    dist = lambda p: abs(p[0][0] - p[1][0]) + abs(p[0][1] - p[1][1])  # noqa: E731
    pair = min(((a, c) for i, a in enumerate(towns) for c in towns[i + 1:] if dist((a, c)) >= 12), key=dist)
    route = builder_owner.build_road_route(*pair, vehicles=1)
    ctx.note(f"company {route.company} road route: stations {route.stations}, depot {route.depot_tile}")
    payer_builder = Builder(b, log=ctx.note, as_company=payer)
    payer_builder.give_money(10_000_000)
    engine = b.obj("vehicle", route.vehicles[0], "engine_type")["engine_type"]
    bus = payer_builder.build_vehicle(route.depot_tile, engine)
    for st in route.stations:
        payer_builder.order(bus, "station", st)
    payer_builder.start_vehicle(bus)


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.console("setting economy.infrastructure_sharing[1] 1")
    b.console(f"setting economy.sharing_fee[1] {FEE}")
    ctx.check(b.console("setting sharing_fee_inflation")[0].find("'true'") > 0, "fees scale with inflation by default in new games")
    b.console("start_ai")
    b.step(2)
    payer = next(c for c in b.objs("company") if c != 0)

    owner = Builder(b, log=ctx.note)
    owner.give_money(50_000_000)
    setup_shared_route(ctx, b, owner, payer, b.info()["map"]["x"])
    b.step(5 * DAY_TICKS)

    ctx.check_eq(sharing_fees(b)["road"], FEE << 8, "road fee without inflation")
    ctx.check_eq(charged_per_tick(ctx, b, payer), {1000}, "charged per tick without inflation")

    res = b.docmd("CheatSetting", 0, CHT_INFLATION_COST, 2 << 16)
    ctx.check(res["success"], f"set price inflation to 2x: {res}")
    ctx.check_eq(sharing_fees(b)["road"], (FEE << 8) * 2, "road fee at 2x inflation")
    ctx.check_eq(charged_per_tick(ctx, b, payer), {2000}, "charged per tick at 2x inflation")

    res = b.docmd("CheatSetting", 0, CHT_INFLATION_COST, 3 << 15)
    ctx.check_eq(sharing_fees(b)["road"], (FEE << 8) * 3 // 2, "road fee at 1.5x inflation")
    ctx.check_eq(charged_per_tick(ctx, b, payer), {1500}, "charged per tick at 1.5x inflation")

    b.console("setting sharing_fee_inflation false")
    ctx.check_eq(sharing_fees(b)["road"], FEE << 8, "road fee with scaling switched off")
    ctx.check_eq(charged_per_tick(ctx, b, payer), {1000}, "charged per tick with scaling switched off")
    b.console("setting sharing_fee_inflation true")

    ctx.check_eq(expense(b, 0, SHARING_REVENUE), -expense(b, payer, SHARING_COST), "owner's revenue equals the payer's cost")

    # Save and load: the setting and fees stay.
    b.save_game("sharing")
    b.console("setting sharing_fee_inflation false")
    ctx.check_eq(sharing_fees(b)["road"], FEE << 8, "road fee changed after saving")
    b.load_game("sharing")
    ctx.check_eq(sharing_fees(b)["road"], (FEE << 8) * 3 // 2, "road fee after save/load")

    # The info window: last entry of the Help menu.
    b.click_widget("MainToolbar", 0, WID_TN_HELP)
    b.wait_frames(2)
    menu = next(w for w in b.windows() if w["class_name"] == "DropdownMenu")
    ctx.screenshot(b, "help_menu")
    x, y = menu["left"] + menu["width"] // 2, menu["top"] + menu["height"] - 8
    b.move(x, y)
    b.wait_frames(2)
    b.click(x, y)
    b.wait_frames(2)
    b.step(2)
    b.wait_frames(2)
    info = [w for w in b.windows() if w["class_name"] == "SjgrppInfo"]
    ctx.check(len(info) == 1, "Help > sJGRPP info opens the info window")
    ctx.screenshot(b, "sjgrpp_info")
