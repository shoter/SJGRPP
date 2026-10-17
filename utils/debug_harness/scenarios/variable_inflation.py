"""
Variable inflation: with inflation on, the cost and income inflation rates change by 0.5% at the start of each year
and stay within their limits. The game runs 18 years; every year's history entry must hold the rates of that year and
the cumulative inflation at its start, the history keeps only the last 16 years, the income rate with equal limits never
moves, and save/load keeps rates and history. The Economy graph is opened from the Graphs menu.
"""

WID_TN_GRAPHS = 12
WID_GRAPH_MATRIX = 10

CONFIG = {
    "game_creation": {"map_x": 6, "map_y": 6, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 0, "number_towns": 2, "max_no_competitors": 0, "vehicle_breakdowns": 0,
                   "initial_interest": 2},
    "economy": {"inflation": "true", "inflation_change_chance": 100,
                "inflation_cost_min": 3, "inflation_cost_max": 8,
                "inflation_income_min": 4, "inflation_income_max": 4},
}


def inflation(b) -> dict:
    """Parse the 'dump_inflation' console command."""
    out = {"history": []}
    for line in b.console("dump_inflation"):
        key, _, value = line.partition(": ")
        if key == "history":
            year, cost, income, prices, payment = (int(v) for v in value.split())
            out["history"].append({"year": year, "cost": cost, "income": income, "prices": prices, "payment": payment})
        elif key in ("cost_rate", "income_rate"):
            out[key] = int(value)
    return out


def next_year(b) -> None:
    """Step until the calendar year changes."""
    year = int(b.info()["date"][:4])
    while int(b.info()["date"][:4]) == year:
        b.step(200)


def open_economy_graph(ctx, b, lockstep: bool = False, menu_shot: str | None = None) -> bool:
    """Close other windows, open Graphs > Economy (last entry of the Graphs menu); True if the graph window is open."""
    for w in b.windows():
        if w["class_name"] not in ("MainWindow", "MainToolbar", "Statusbar"):
            b.close_window(w["class"], w.get("number", 0))
    b.wait_frames(2)
    b.click_widget("MainToolbar", 0, WID_TN_GRAPHS)
    b.wait_frames(2)
    menu = next(w for w in b.windows() if w["class_name"] == "DropdownMenu")
    if menu_shot:
        ctx.screenshot(b, menu_shot)
    x, y = menu["left"] + menu["width"] // 2, menu["top"] + menu["height"] - 8
    b.move(x, y)
    b.wait_frames(2)
    b.click(x, y)
    b.wait_frames(2)
    if lockstep:
        b.step(2)
    b.wait_frames(4)
    return len(b.find_windows("EconomyGraph")) == 1


def check_history(ctx, state: dict, what: str) -> None:
    hist = state["history"]
    years = [h["year"] for h in hist]
    ctx.check(years == list(range(years[0], years[0] + len(years))), f"{what}: consecutive years {years}")
    for prev, cur in zip(hist, hist[1:]):
        ctx.check(abs(cur["cost"] - prev["cost"]) == 5, f"{what}: cost rate moved by 0.5% {prev['cost']} -> {cur['cost']} in {cur['year']}")
        ctx.check(cur["prices"] > prev["prices"] and cur["payment"] > prev["payment"], f"{what}: cumulative inflation rises in {cur['year']}")
    for h in hist:
        ctx.check(30 <= h["cost"] <= 80, f"{what}: cost rate {h['cost']} within 3%..8% in {h['year']}")
        ctx.check(h["income"] == 40, f"{what}: income rate fixed at 4% in {h['year']}")


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "777"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")

    start = inflation(b)
    ctx.note(f"at start: {start}")
    ctx.check_eq(len(start["history"]), 1, "one history entry at game start")
    ctx.check_eq(start["cost_rate"], 30, "initial interest 2% clamped up to the 3% minimum cost inflation")
    ctx.check_eq(start["income_rate"], 40, "income rate set to its fixed 4%")

    for _ in range(18):
        next_year(b)
    state = inflation(b)
    ctx.note(f"after 18 years: cost {state['cost_rate']} income {state['income_rate']}, years {[h['year'] for h in state['history']]}")
    ctx.check_eq(len(state["history"]), 16, "history keeps the last 16 years")
    ctx.check_eq(state["history"][-1]["cost"], state["cost_rate"], "last entry holds the current cost rate")
    check_history(ctx, state, "18 years")

    # Save and load keep rates and history.
    save = str(inst.dir / "inflation.sav")
    b.console(f"save {save}")
    b.console(f"load {save}")
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="reload")
    ctx.check_eq(inflation(b), state, "rates and history after save/load")

    ctx.check(open_economy_graph(ctx, b, lockstep=True, menu_shot="graphs_menu"), "Graphs > Economy opens the economy graph")
    ctx.screenshot(b, "economy_graph")

    # Toggle off the two cumulative lines (key rows 0 and 2) to see the current rates on their own scale.
    matrix = next(w for w in b.widgets("EconomyGraph", 0)["widgets"] if w["index"] == WID_GRAPH_MATRIX)
    row_h = matrix["height"] // 4
    for row in (0, 2):
        b.click(matrix["x"] + matrix["width"] // 2, matrix["y"] + row * row_h + row_h // 2)
        b.wait_frames(2)
    b.step(2)
    ctx.screenshot(b, "economy_graph_current_rates")

    # Inflation rates that never change keep the old behaviour.
    b.console("setting inflation_change_chance 0")
    before = inflation(b)
    next_year(b)
    after = inflation(b)
    ctx.check_eq(after["cost_rate"], before["cost_rate"], "chance 0: cost rate unchanged after a year")
