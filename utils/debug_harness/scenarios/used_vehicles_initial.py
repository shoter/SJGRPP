"""
Used vehicle market at the start of the game: the number of used vehicles of each vehicle type is between 0 and the
type's setting, every listing is a model on sale aged a whole number of years (at least one, at most its lifespan and
the years since the model came out), and switching the market off and on refills it using the current settings.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from used_vehicles import DAYS_IN_YEAR, TICKS_PER_DAY, listings  # noqa: E402

WID_GO_TAB_ADVANCED = 4  # GameOptionsWidgets
KINDS = {"road": "road", "rail": "train", "ship": "ship", "aircraft": "aircraft"}
FIRST = {"road": 0, "rail": 7, "ship": 100, "aircraft": 2}
SECOND = {"road": 40, "rail": 0, "ship": 0, "aircraft": 0}

CONFIG = {
    "game_creation": {"starting_year": 1990},
    "vehicle": {f"used_vehicle_initial_max_{KINDS[k]}": v for k, v in FIRST.items()},
}


def check_market(ctx, b, limits: dict[str, int], label: str) -> None:
    engines = {e["id"]: e for e in b.engines()}
    date = b.obj("globals", None, "date")["date"]
    market = listings(b)
    per_kind = {k: 0 for k in limits}
    unknown = []
    bad_age = []
    for lst in market:
        e = engines.get(lst["engine"])
        if e is None:
            unknown.append(lst)
            continue
        per_kind[e["type"]] += 1
        max_years = max(0, min(e["life_days"], date - e["intro_date"]) // DAYS_IN_YEAR)
        years, rest = divmod(lst["age"], DAYS_IN_YEAR)
        if rest != 0 or not 1 <= years <= max_years:
            bad_age.append((lst, max_years))

    print(f"{label}: {per_kind}")
    ctx.check(not unknown, f"{label}: every listing is a model on sale ({unknown[:3]})")
    for kind, limit in limits.items():
        ctx.check(per_kind[kind] <= limit, f"{label}: {kind} listings {per_kind[kind]} <= {limit}")
    ctx.check(sum(per_kind.values()) > 0, f"{label}: market is not empty ({len(market)})")
    ctx.check(not bad_age, f"{label}: ages are 1..max whole years ({bad_age[:3]})")
    ctx.check(all(lst["seller"] == 255 and lst["history"] == 0 for lst in market),
              f"{label}: generated listings have no previous owners")


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "6161"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.step(2 * TICKS_PER_DAY)

    check_market(ctx, b, FIRST, "game start")

    # Settings are only used when the market is filled: changing them leaves the market alone...
    before = listings(b)
    for kind, value in SECOND.items():
        b.console(f"setting used_vehicle_initial_max_{KINDS[kind]} {value}")
    b.step(2 * TICKS_PER_DAY)
    ctx.check(listings(b) == before, "changing the settings doesn't change the market")

    # ...switching the market off and on refills it with the new settings.
    b.console("setting used_vehicle_market false")
    b.step(2 * TICKS_PER_DAY)
    ctx.check_eq(len(listings(b)), 0, "market emptied when disabled")
    b.console("setting used_vehicle_market true")
    b.step(2 * TICKS_PER_DAY)
    check_market(ctx, b, SECOND, "refilled")

    # All maximums 0: the market stays empty.
    for kind in SECOND:
        b.console(f"setting used_vehicle_initial_max_{KINDS[kind]} 0")
    b.console("setting used_vehicle_market false")
    b.step(2 * TICKS_PER_DAY)
    b.console("setting used_vehicle_market true")
    b.step(2 * TICKS_PER_DAY)
    ctx.check_eq(len(listings(b)), 0, "no listings when every maximum is 0")

    # The settings in the Advanced settings tab (category "Expert" shows every setting).
    b.console("setting gui.settings_restriction_mode 2")
    for kind, value in {"road": 10, "rail": 5, "ship": 3, "aircraft": 5}.items():
        b.console(f"setting used_vehicle_initial_max_{KINDS[kind]} {value}")
    b.call("show", "game_options")
    b.step(5)
    win = b.find_windows("GameOptions")
    if ctx.check(len(win) == 1, "game options window opened"):
        b.click_widget("GameOptions", win[0]["number"], WID_GO_TAB_ADVANCED)
        b.step(5)
        b.text("used vehicle market")
        b.step(5)
        ctx.screenshot(b, "settings")
