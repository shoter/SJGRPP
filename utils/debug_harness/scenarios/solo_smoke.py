"""Single player smoke test: new game in lockstep, time control, reflection, GUI interaction, screenshots."""


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "12345"], lockstep=True)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")

    t0 = b.info()["tick_counter"]
    b.step(100)
    ctx.check_eq(b.info()["tick_counter"] - t0, 100, "lockstep step advances exactly 100 ticks")

    ctx.check_eq(b.probes("bridge.")["bridge.last_step"]["value"], "100", "DebugBridge::Probe value recorded")
    ctx.check_eq(b.get("bridge.connections")["bridge.connections"], "1", "DEBUG_BRIDGE_WATCH registered watch")

    globals_ = b.obj("globals", None, "tick_counter", "date")
    ctx.check(globals_["tick_counter"] == b.info()["tick_counter"], "reflected tick_counter matches info")

    towns = b.objs("town")
    ctx.check(len(towns) > 0, f"map has towns ({len(towns)})")
    town = b.obj("town", towns[0], "xy", "statues")
    ctx.check(isinstance(town["xy"], int), "town fields readable by name")

    # Open a town window through the console and check it is there, then close it with a click on its close box.
    b.console(f"show_town_window {towns[0]}")
    wins = b.find_windows("TownView")
    ctx.check(len(wins) == 1, "town window opened")
    widgets = b.widgets("TownView", wins[0]["number"])["widgets"]
    ctx.screenshot(b, "town_window")
    close_box = next(w for w in widgets if w.get("role") == "closebox")
    b.click(close_box["x"] + close_box["width"] // 2, close_box["y"] + close_box["height"] // 2)
    b.wait_frames(2)
    ctx.check(len(b.find_windows("TownView")) == 0, "town window closed by clicking its close box")

    # Real-time mode works too.
    b.lockstep(False)
    before = b.info()["tick_counter"]
    b.wait_ticks(20, timeout_ms=10000)
    ctx.check(b.info()["tick_counter"] >= before + 20, "real-time ticks advance")
    ctx.screenshot(b, "final")
