"""
Own/opponent vehicle colours: Settings menu > "Own/opponent vehicle colours" draws all vehicles of the local company
in one colour (yellow by default) and all vehicles of other companies in another (red by default). Company 0 runs a
bus, an AI company runs a bus from company 0's depot. Checks the palette of every vehicle with the toggle off, on,
after changing the colour settings, and off again.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transport import Builder  # noqa: E402

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 0, "number_towns": 3, "max_no_competitors": 2, "vehicle_breakdowns": 0},
}

WID_TN_SETTINGS = 2
DARK_BLUE, YELLOW, RED, GREEN, ORANGE, BROWN, WHITE = 0, 3, 4, 6, 12, 13, 15
DEFAULT_LIVERY = 0  # LiveryScheme::Default
VEH_HIDDEN = 1 << 0  # VehState::Hidden


def colours(b) -> dict[int, tuple[int, int, int]]:
    """Vehicle id -> (owner, 2CC, recolour offset) from the debug bridge watch."""
    out = {}
    for entry in b.get("vehicle_colour_override.colours")["vehicle_colour_override.colours"].split(";"):
        if entry:
            vid, owner, twocc, offset = (int(x) for x in entry.split(":"))
            out[vid] = (owner, twocc, offset)
    return out


def expected_offset(twocc: int, primary: int, secondary: int) -> int:
    return primary + secondary * 16 if twocc else primary


def enabled(b) -> bool:
    return b.get("vehicle_colour_override.enabled")["vehicle_colour_override.enabled"] == "true"


def toggle_from_menu(ctx, b, menu_shot: str | None = None, lockstep: bool = True) -> None:
    """Click Settings in the toolbar and pick the last entry, "Own/opponent vehicle colours"."""
    b.click_widget("MainToolbar", 0, WID_TN_SETTINGS)
    b.wait_frames(2)
    menu = next(w for w in b.windows() if w["class_name"] == "DropdownMenu")
    x, y = menu["left"] + menu["width"] // 2, menu["top"] + menu["height"] - 8
    b.move(x, y)
    b.wait_frames(2)
    if menu_shot:
        ctx.screenshot(b, menu_shot)
    b.click(x, y)
    b.wait_frames(2)
    if lockstep:
        b.step(2)
    b.wait_frames(4)


def frame_buses(ctx, b, buses: list[int], depot_tile: int) -> bool:
    """Close other windows, run the game until the buses drive close to each other and zoom the main view in on them."""
    b.console("setting smooth_scroll off")
    for w in b.windows():
        if w["class_name"] not in ("MainWindow", "MainToolbar", "Statusbar"):
            b.close_window(w["class"], w.get("number", 0))
    mx = b.info()["map"]["x"]

    def positions():
        info = [b.obj("vehicle", v, "tile", "vehstatus") for v in buses]
        if any(i["vehstatus"] & VEH_HIDDEN or i["tile"] == depot_tile for i in info) or info[0]["tile"] == info[1]["tile"]:
            return None
        return [(i["tile"] % mx, i["tile"] // mx) for i in info]

    for _ in range(1000):
        pos = positions()
        if pos and 1 <= abs(pos[0][0] - pos[1][0]) + abs(pos[0][1] - pos[1][1]) <= 3:
            break
        b.step(5)
    else:
        ctx.note("buses never came close to each other; screenshots show the first bus only")
        pos = positions() or [(0, 0)]
    cx, cy = sum(p[0] for p in pos) // len(pos), sum(p[1] for p in pos) // len(pos)
    b.console(f"scrollto {cy * mx + cx}")
    b.wait_frames(2)
    width, height = b.info()["screen"]["width"], b.info()["screen"]["height"]
    for _ in range(2):
        b.wheel(width // 2, height // 2, -1)
        b.wait_frames(2)
    b.console(f"scrollto {cy * mx + cx}")
    b.wait_frames(2)
    return bool(pos)


def check_colours(ctx, b, what: str, local: int, own: tuple[int, int] | None, other: tuple[int, int] | None, liveries: dict) -> None:
    """own/other: (primary, secondary) expected for own/other vehicles, or None for the company's own livery."""
    seen = colours(b)
    ctx.check(len(seen) >= 2, f"{what}: at least two vehicles ({seen})")
    for vid, (owner, twocc, offset) in seen.items():
        want = own if owner == local else other
        if want is None:
            want = liveries[owner]
        ctx.check_eq(offset, expected_offset(twocc, *want), f"{what}: vehicle {vid} of company {owner} (2CC {twocc})")


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.console("setting economy.infrastructure_sharing[1] 1")
    b.console("start_ai")
    b.step(2)
    other = next(c for c in b.objs("company") if c != 0)

    # Fixed company colours that differ from the override colours.
    for company, colour in ((0, DARK_BLUE), (other, GREEN)):
        for primary in (True, False):
            res = b.docmd("SetCompanyColour", 0, DEFAULT_LIVERY, primary, colour, 0, as_company=company)
            ctx.check(res["success"], f"company {company} colour set to {colour} ({res.get('error')})")
    liveries = {0: (DARK_BLUE, DARK_BLUE), other: (GREEN, GREEN)}

    owner = Builder(b, log=ctx.note)
    owner.give_money(50_000_000)
    mx = b.info()["map"]["x"]
    towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
    dist = lambda p: abs(p[0][0] - p[1][0]) + abs(p[0][1] - p[1][1])  # noqa: E731
    pair = min(((a, c) for i, a in enumerate(towns) for c in towns[i + 1:] if dist((a, c)) >= 12), key=dist)
    route = owner.build_road_route(*pair, vehicles=1)
    opponent = Builder(b, log=ctx.note, as_company=other)
    opponent.give_money(10_000_000)
    engine = b.obj("vehicle", route.vehicles[0], "engine_type")["engine_type"]
    bus = opponent.build_vehicle(route.depot_tile, engine)
    for st in route.stations:
        opponent.order(bus, "station", st)
    opponent.start_vehicle(bus)
    b.step(30)
    # The AI may have built vehicles of its own; they count as opponents too.
    ctx.note(f"vehicles: {colours(b)}")

    ctx.check(not enabled(b), "own/opponent colours are off at game start")
    check_colours(ctx, b, "off", 0, None, None, liveries)

    frame_buses(ctx, b, [route.vehicles[0], bus], route.depot_tile)
    ctx.screenshot(b, "off_company_colours")
    toggle_from_menu(ctx, b, menu_shot="settings_menu")
    ctx.check(enabled(b), "menu entry switches own/opponent colours on")
    check_colours(ctx, b, "on, defaults", 0, (YELLOW, ORANGE), (RED, BROWN), liveries)
    ctx.screenshot(b, "on_default_colours")

    b.console(f"setting vehicle_colour_override_own {WHITE}")
    b.console(f"setting vehicle_colour_override_other {DARK_BLUE}")
    b.console(f"setting vehicle_colour_override_other_secondary {WHITE}")
    b.step(1)
    check_colours(ctx, b, "on, changed settings", 0, (WHITE, ORANGE), (DARK_BLUE, WHITE), liveries)

    # A livery change while the override is on must not show through.
    res = b.docmd("SetCompanyColour", 0, DEFAULT_LIVERY, True, BROWN, 0)
    ctx.check(res["success"], f"company 0 colour changed while override is on ({res.get('error')})")
    liveries[0] = (BROWN, DARK_BLUE)
    b.step(1)
    check_colours(ctx, b, "on, after livery change", 0, (WHITE, ORANGE), (DARK_BLUE, WHITE), liveries)

    toggle_from_menu(ctx, b)
    ctx.check(not enabled(b), "menu entry switches own/opponent colours off again")
    check_colours(ctx, b, "off again", 0, None, None, liveries)
