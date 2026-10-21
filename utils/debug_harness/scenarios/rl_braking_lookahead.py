"""
Realistic braking lookahead cutoff: the speed limit calculation stops at the first lookahead item from which on no item
can lower the speed any more. With chicken bit 9 (debug.chicken_bits 512) every speed limit calculation also applies
all items and fails (log line + assert) if the result differs. Trains with long reservations (unsignalled single
track between distant towns, so the reservation runs to the far station with many curve and station items) run for
four months with the check on, across a save/load (the item bounds are not saved and are rebuilt after loading).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transport import Builder  # noqa: E402

CONFIG = {
    "game_creation": {"map_x": 8, "map_y": 8, "starting_year": 1990, "landscape": "temperate"},
    "difficulty": {"terrain_type": 1, "number_towns": 2, "max_no_competitors": 0, "vehicle_breakdowns": 0},
}
DAY_TICKS = 74
DAYS = 120
CHECK_BIT = 1 << 9            # DCBF_CHECK_RL_LOOKAHEAD_CUTOFF


def setting(b, name: str) -> str:
    line = b.console(f"setting {name}")[0]
    return line.split("'")[3]


def train_state(b, train: int) -> dict:
    return b.obj("vehicle", train, "tile", "cur_speed", "last_station_visited")


def run(ctx):
    inst = ctx.solo(args=["-g", "-G", "4242"], lockstep=True, config=CONFIG)
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="game start")
    b.console("setting vehicle.train_braking_model 1")
    b.console(f"setting debug.chicken_bits {CHECK_BIT}")
    ctx.check_eq(setting(b, "vehicle.train_braking_model"), "1", "realistic braking on")
    ctx.check_eq(setting(b, "debug.chicken_bits"), str(CHECK_BIT), "lookahead cutoff check on")

    builder = Builder(b, log=ctx.note)
    builder.give_money(100_000_000)
    mx = b.info()["map"]["x"]
    towns = [(xy % mx, xy // mx) for xy in (b.obj("town", t, "xy")["xy"] for t in sorted(b.objs("town")))]
    dist = lambda p: abs(p[0][0] - p[1][0]) + abs(p[0][1] - p[1][1])  # noqa: E731
    pairs = sorted(((a, c) for i, a in enumerate(towns) for c in towns[i + 1:] if 40 <= dist((a, c)) <= 160),
                   key=dist, reverse=True)
    trains = []
    for pair in pairs:
        if len(trains) == 2:
            break
        try:
            route = builder.build_rail_route(*pair, wagons=4)
        except Exception as e:  # noqa: BLE001
            ctx.note(f"rail route {pair} failed: {e}")
            continue
        ctx.note(f"rail route {pair}: {dist(pair)} tiles apart, path {route.path_length}, stations {route.stations}")
        trains.append((route.vehicles[0], route.stations))
    if not ctx.check(len(trains) >= 1, f"built {len(trains)} long rail routes"):
        return

    visited: dict[int, set] = {t: set() for t, _ in trains}
    max_speed: dict[int, int] = {t: 0 for t, _ in trains}
    for day in range(DAYS):
        if day == DAYS // 2:
            b.save_game("rl_lookahead")
            b.load_game("rl_lookahead")
            ctx.check_eq(setting(b, "debug.chicken_bits"), str(CHECK_BIT), "check still on after load")
        b.step(DAY_TICKS)
        for t, _ in trains:
            s = train_state(b, t)
            visited[t].add(s["last_station_visited"])
            max_speed[t] = max(max_speed[t], s["cur_speed"])

    for t, stations in trains:
        ctx.note(f"train {t}: visited {sorted(visited[t])}, top speed {max_speed[t]}")
        ctx.check(set(stations) <= visited[t], f"train {t} served both stations")
        ctx.check(max_speed[t] > 40, f"train {t} reached speed ({max_speed[t]})")

    ctx.check(inst.alive() and not inst.crash_files(), "game did not crash or assert")
    stderr = inst.dir / "stderr.log"
    text = stderr.read_text(encoding="utf-8", errors="replace") if stderr.exists() else ""
    ctx.check("cutoff mismatch" not in text, "no realistic braking lookahead cutoff mismatch in the log")

    # Picture: the first train on its long reservation.
    b.console("setting gui.show_track_reservation 1")
    t = trains[0][0]
    for _ in range(40 * DAY_TICKS // 8):
        if train_state(b, t)["cur_speed"] >= 100:
            break
        b.step(8)
    b.console(f"scrollto instant {b.obj('vehicle', t, 'tile')['tile']}")
    b.call("show", "vehicle", 0)  # the first primary vehicle: the first train
    b.step(1)
    ctx.screenshot(b, "long_reservation")
