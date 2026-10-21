"""
Companies set their own infrastructure sharing fees: a bus of company 1 loads at company 0's road stations and a train
of company 1 runs on company 0's railway, while company 0 changes its own fees in the company settings window and with
the command. Checked exactly: the fee charged per tick (road) and per day (rail), that new games use the server fees,
that lower fees take effect at once and higher fees after the notice period (a rise can be cancelled), the server's
maximum (also when lowered later), that own fees are fixed amounts that do not rise with inflation, that unticking goes
back to the server fee, save/load with an announced rise, the news item in single player, and that the settings window
of another company is read-only.
"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sharing_fees import CHT_INFLATION_COST, CONFIG, DAY_TICKS, SHARING_COST, SHARING_REVENUE, charged_per_tick, expense, setup_shared_route  # noqa: E402
from track_toll import build_train, daily_tolls, sell_train, train_length  # noqa: E402
from transport import Builder  # noqa: E402

ROAD = 1                      # VehicleType::Road
RAIL = 0                      # VehicleType::Train
FEE = 74_000                  # server road fee per day: 1000 per tick
MAX_FEE = 148_000             # server maximum of own road fees: 2000 per tick
TOLL = 7_400                  # server track toll per tile per day
MAX_TOLL = 74_000
NOTICE_DAYS = 10
WID_GO_TAB_ADVANCED = 4       # GameOptionsWidgets

# Layout of the sharing fee panel of the company settings window at the harness' interface scale, relative to the
# panel: the rows of the fees (without announced rises above them), the tick box and the amount.
ROW_Y = (24, 38, 52, 66)
TICK_BOX_X = 12
AMOUNT_X = 195


def own_fees(b) -> dict[tuple[int, str], dict[str, int]]:
    """The own fee of every company and vehicle type, from the 'sharing_fees' console command."""
    out = {}
    for line in b.console("sharing_fees"):
        if not line.startswith("company "):
            continue
        head, rest = line.split(": ", 1)
        _, company, kind = head.split()
        out[(int(company), kind)] = {k: int(v) for k, v in (part.split(" ") for part in rest.split(", "))}
    return out


def plain(text) -> str:
    """Text without OpenTTD's control characters, which the console can't print."""
    return "".join(ch for ch in str(text) if ord(ch) < 0xE000 or ord(ch) > 0xF8FF)


def set_fee(b, type_: int, custom: bool, fee: int, as_company: int | None = None) -> dict:
    return b.docmd("SetCompanySharingFee", 0, type_, custom, fee, as_company=as_company)


def open_settings(b, company: int, nth: int) -> dict:
    """Open the company window of the nth company and click its Settings button; returns the sharing fee panel."""
    b.call("show", "company", nth)
    b.step(2)
    widgets = b.widgets("Company", company)["widgets"]
    button = next(w for w in widgets if "Settings" in (w.get("text") or ""))
    b.click_widget("Company", company, button["index"])
    b.step(2)
    return next(w for w in b.widgets("CompanySettings", company)["widgets"] if w["index"] == 1)


def click_row(b, panel: dict, row: int, x: int) -> None:
    b.click(panel["x"] + x, panel["y"] + ROW_Y[row])
    b.step(2)
    b.wait_frames(1)


def window_screenshot(ctx, b, label: str) -> None:
    """Screenshot with the mouse away from the settings window, so no tooltip covers it."""
    b.move(b.find_windows("CompanySettings")[0]["left"] - 40, 700)
    b.step(2)
    b.wait_frames(2)
    ctx.screenshot(b, label)


def old_save(ctx) -> Path | None:
    """A game with two companies saved by a build without own fees (OTTD_OLD_EXE), if set."""
    exe = os.environ.get("OTTD_OLD_EXE")
    if not exe:
        ctx.note("OTTD_OLD_EXE not set: skipping the old savegame check")
        return None
    old = ctx.solo(name="old", args=["-g", "-G", "4242"], lockstep=True, config=CONFIG, exe=exe)
    b = old.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="old build game start")
    b.console("start_ai")
    b.step(2)
    b.console(f"save {old.dir / 'old_fees'}")
    old.stop()
    return next(old.dir.glob("old_fees*.sav"), None)


def run(ctx):
    old = old_save(ctx)

    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    for kind, value in ((0, 1), (1, 1)):
        b.console(f"setting economy.infrastructure_sharing[{kind}] {value}")
    b.console(f"setting economy.sharing_fee[1] {FEE}")
    b.console(f"setting economy.track_toll {TOLL}")
    b.console(f"setting economy.sharing_fee_max[1] {MAX_FEE}")
    b.console(f"setting economy.sharing_fee_max[0] {MAX_TOLL}")
    b.console(f"setting economy.sharing_fee_notice_days {NOTICE_DAYS}")
    b.console("start_ai")
    b.step(2)
    payer = next(c for c in b.objs("company") if c != 0)

    fees = own_fees(b)
    ctx.check(all(f["custom"] == 0 and f["pending"] == 0 for f in fees.values()), "new companies charge the server fees")
    ctx.check_eq(fees[(0, "road")]["effective_fract"], FEE << 8, "company 0's road fee is the server fee")

    owner = Builder(b, log=ctx.note)
    owner.give_money(50_000_000)
    setup_shared_route(ctx, b, owner, payer, b.info()["map"]["x"])
    b.step(5 * DAY_TICKS)
    ctx.check_eq(charged_per_tick(ctx, b, payer), {1000}, "charged per tick with the server fee")

    # The settings window: every fee unticked, showing the server fee.
    panel = open_settings(b, 0, 0)
    window_screenshot(ctx, b, "settings_default")

    # Ticking the road fee starts from the server fee: what the bus pays does not change.
    for w in b.find_windows("News"):
        b.close_window("News", w["number"])
    b.step(2)
    click_row(b, panel, ROAD, TICK_BOX_X)
    fees = own_fees(b)[(0, "road")]
    ctx.check_eq((fees["custom"], fees["fee"], fees["pending"]), (1, FEE, 0), "ticking the road fee sets the server fee as own fee")
    b.step(2)
    ctx.check(len(b.find_windows("News")) == 1, "a news item tells about the change in single player")
    ctx.screenshot(b, "news")
    for w in b.find_windows("News"):
        b.close_window("News", w["number"])

    # Lower own fee typed in the window: effective at once.
    click_row(b, panel, ROAD, AMOUNT_X)
    ctx.check(len(b.find_windows("QueryString")) == 1, "clicking the amount asks for the new fee")
    for _ in range(12):
        b.key("backspace")
    b.text(str(FEE // 2))
    b.key("enter")
    b.step(2)
    fees = own_fees(b)[(0, "road")]
    ctx.check_eq((fees["custom"], fees["fee"], fees["pending"]), (1, FEE // 2, 0), "a lower fee is in effect at once")
    ctx.check_eq(charged_per_tick(ctx, b, payer), {500}, "charged per tick with the lower own fee")

    # Higher own fee: announced, in effect after the notice period.
    res = set_fee(b, ROAD, True, FEE * 3 // 2)
    ctx.check(res["success"], f"raise the road fee: {res}")
    fees = own_fees(b)[(0, "road")]
    ctx.check_eq((fees["fee"], fees["pending"], fees["pending_fee"], fees["pending_days"]), (FEE // 2, 1, FEE * 3 // 2, NOTICE_DAYS),
                 "a higher fee is announced")
    window_screenshot(ctx, b, "settings_announced_rise")
    ctx.check_eq(charged_per_tick(ctx, b, payer, wanted=20), {500}, "the old fee is charged during the notice period")
    res = set_fee(b, ROAD, True, FEE * 3 // 2)
    ctx.check_eq(own_fees(b)[(0, "road")]["pending_days"] <= NOTICE_DAYS, True, "announcing the same rise again does not postpone it")
    b.step((NOTICE_DAYS + 1) * DAY_TICKS)
    fees = own_fees(b)[(0, "road")]
    ctx.check_eq((fees["fee"], fees["pending"]), (FEE * 3 // 2, 0), "the rise is in effect after the notice period")
    ctx.check_eq(charged_per_tick(ctx, b, payer), {1500}, "charged per tick with the higher own fee")

    # Above the server's maximum: refused.
    res = set_fee(b, ROAD, True, MAX_FEE + 1)
    ctx.check(not res["success"], f"a fee above the maximum is refused: {plain(res.get('error'))}")
    ctx.check_eq(own_fees(b)[(0, "road")]["fee"], FEE * 3 // 2, "the fee did not change")

    # Announce a rise and cancel it by setting the current fee again.
    set_fee(b, ROAD, True, MAX_FEE)
    ctx.check_eq(own_fees(b)[(0, "road")]["pending"], 1, "rise to the maximum announced")
    set_fee(b, ROAD, True, FEE * 3 // 2)
    fees = own_fees(b)[(0, "road")]
    ctx.check_eq((fees["fee"], fees["pending"]), (FEE * 3 // 2, 0), "the announced rise is cancelled")

    # The server lowers the maximum below the own fee: the maximum is charged.
    b.console(f"setting economy.sharing_fee_max[1] {FEE}")
    ctx.check_eq(own_fees(b)[(0, "road")]["effective_fract"], FEE << 8, "own fee limited by the lowered maximum")
    ctx.check_eq(charged_per_tick(ctx, b, payer), {1000}, "charged per tick at the lowered maximum")
    b.console(f"setting economy.sharing_fee_max[1] {MAX_FEE}")

    # Unticking goes back to the server fee, which is lower here: at once.
    click_row(b, panel, ROAD, TICK_BOX_X)
    fees = own_fees(b)[(0, "road")]
    ctx.check_eq((fees["custom"], fees["pending"], fees["effective_fract"]), (0, 0, FEE << 8), "unticked: back to the server fee")

    # Own fees are fixed amounts: they do not rise with inflation, the server fee and the maximum do.
    set_fee(b, ROAD, True, FEE // 2)
    res = b.docmd("CheatSetting", 0, CHT_INFLATION_COST, 2 << 16)
    ctx.check(res["success"], f"set price inflation to 2x: {res}")
    fees = own_fees(b)
    ctx.check_eq(fees[(0, "road")]["effective_fract"], (FEE // 2) << 8, "own fee at 2x inflation")
    ctx.check_eq(fees[(payer, "road")]["effective_fract"], (FEE << 8) * 2, "server fee at 2x inflation")
    ctx.check_eq(charged_per_tick(ctx, b, payer), {500}, "charged per tick with the own fee at 2x inflation")
    res = set_fee(b, ROAD, True, MAX_FEE * 2)
    ctx.check(res["success"], f"the maximum rose with inflation: {res}")
    set_fee(b, ROAD, True, FEE // 2)

    # Track toll: company 1's train on company 0's railway pays company 0's own toll.
    mx = b.info()["map"]["x"]
    towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
    dist = lambda p: abs(p[0][0] - p[1][0]) + abs(p[0][1] - p[1][1])  # noqa: E731
    pair = min(((a, c) for i, a in enumerate(towns) for c in towns[i + 1:] if dist((a, c)) >= 12), key=dist)
    route = owner.build_rail_route(*pair, wagons=3)
    own_train = route.vehicles[0]
    loco = b.obj("vehicle", own_train, "engine_type")["engine_type"]
    wagon = b.obj("vehicle", b.obj("vehicle", own_train, "next")["next"]["id"], "engine_type")["engine_type"]
    sell_train(b, owner, own_train, route.depot_tile)
    payer_builder = Builder(b, log=ctx.note, as_company=payer)
    train = build_train(payer_builder, route.depot_tile, loco, wagon, 3, route.stations)
    length = train_length(b, train)
    res = set_fee(b, RAIL, True, TOLL // 2)
    ctx.check(res["success"], f"lower own track toll: {res}")
    before = expense(b, payer, SHARING_COST)
    # Only the train pays from now on: the bus is stopped.
    for v in b.objs("vehicle"):
        info = b.obj("vehicle", v, "owner", "type", "first")
        if info.get("owner") == payer and info.get("type") == ROAD:
            payer_builder.cmd("StartStopVehicle", 0, v, False)
    b.step(3 * DAY_TICKS)
    tolls = daily_tolls(ctx, b, payer, days=6)
    ctx.check_eq(set(tolls), {(TOLL // 2) * length // 16}, f"daily toll of a {length / 16} tile train with the own toll")
    ctx.check(expense(b, payer, SHARING_COST) != before, "the train paid the toll")
    ctx.check_eq(expense(b, 0, SHARING_REVENUE), -expense(b, payer, SHARING_COST), "owner's revenue equals the payer's cost")

    # Save and load with an announced rise.
    set_fee(b, ROAD, True, FEE)
    before = own_fees(b)[(0, "road")]
    ctx.check_eq(before["pending"], 1, "rise announced before saving")
    b.save_game("own_fees")
    set_fee(b, ROAD, False, 0)
    b.load_game("own_fees")
    after = own_fees(b)[(0, "road")]
    ctx.check_eq({k: v for k, v in after.items() if k != "effective_fract"}, {k: v for k, v in before.items() if k != "effective_fract"},
                 "own fee and announced rise after save/load")

    # Another company's settings are read-only, and a spectator can't set fees.
    payer_nth = sorted(b.objs("company")).index(payer)
    panel = open_settings(b, payer, payer_nth)
    click_row(b, panel, ROAD, TICK_BOX_X)
    ctx.check_eq(own_fees(b)[(payer, "road")]["custom"], 0, "clicking another company's fee does not change it")
    window_screenshot(ctx, b, "settings_other_company")
    res = set_fee(b, ROAD, True, 1, as_company=255)
    ctx.check(not res["success"], f"a spectator can't set a fee: {plain(res.get('error'))}")

    # The new server settings.
    for w in b.windows():
        if w["class_name"] in ("CompanySettings", "Company"):
            b.close_window(w["class"], w["number"])
    b.console("setting gui.settings_restriction_mode 2")
    b.call("show", "game_options")
    b.step(5)
    win = b.find_windows("GameOptions")
    if ctx.check(len(win) == 1, "game options window opened"):
        b.click_widget("GameOptions", win[0]["number"], WID_GO_TAB_ADVANCED)
        b.step(5)
        b.text("own fee")
        b.step(10)
        b.wait_frames(3)
        ctx.screenshot(b, "settings_server")

    # A game saved before this feature: every company charges the server fees.
    if old is not None:
        shutil.copy(old, inst.dir / "save" / "old_fees.sav")
        b.load_game("old_fees")
        fees = own_fees(b)
        ctx.check(len(fees) >= 8 and all(f["custom"] == 0 and f["pending"] == 0 for f in fees.values()),
                  f"old savegame loads with the server fees for all {len(fees) // 4} companies")
