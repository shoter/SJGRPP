"""
Trains without a destination and a lost train in multiplayer, with the pathfinder cache check on (-d yapfdesync=1):
client 1 builds the rail grid of train_pathfinder_budget.py and runs three trains without orders, first without path
reservation (they pick random tracks; they may crash into each other, which is fine here), then with path reservation
on, when one more train with an order for a station that is not connected gets lost. Every instance must agree on the
trains (position, lost flag, reservation state) at paused checkpoints: without reservation, while the train is lost,
after client 2 left and joined again (it gets the game from a savegame), and after the station was connected and the
train found it. Nothing may desync and the cache check must find no difference.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_pathfinder_budget import CONFIG, DAY_TICKS, FULL_SEARCH_PERIOD, LOST_FLAG, RAILTYPE_RAIL, VS_CRASHED, Grid  # noqa: E402
from transport import Builder  # noqa: E402

FIELDS = ("tile", "x_pos", "y_pos", "direction", "cur_speed", "vehicle_flags", "flags", "wait_counter", "last_station_visited")


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=777, company="new", server_config=CONFIG, debug="yapfdesync=1")
    server = cluster.server.bridge
    c1 = cluster.clients[0].bridge
    for b in (c1, cluster.clients[1].bridge):
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")
    co1 = c1.info()["local_company"]
    server.docmd("MoneyCheatAdmin", 0, 500_000_000, as_company=co1)

    builder = Builder(c1, log=ctx.note)
    grid = Grid(ctx, c1, builder)
    grid.build()
    loco = builder.pick_engine("rail", wagon=False, railtypes=lambda e: e["railtypes"] & (1 << RAILTYPE_RAIL))["id"]
    station = grid.build_station()
    trains = []
    for _ in range(3):
        trains.append(builder.build_vehicle(grid.depot, loco))
        builder.start_vehicle(trains[-1])
        c1.wait_ticks(2 * DAY_TICKS)

    def checkpoint(label: str) -> list[dict]:
        server.console("pause")
        instances = [server] + [c.bridge for c in cluster.clients]
        for b in instances:
            b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=20, what="paused")
        states = [[b.obj("vehicle", t, *FIELDS) for t in trains] for b in instances]
        ticks = [b.obj("globals", None, "state_ticks")["state_ticks"] for b in instances]
        ctx.check(all(s == states[0] for s in states[1:]) and len(set(ticks)) == 1,
                  f"{label}: every instance agrees on the trains (state ticks {ticks})")
        problems = cluster.health()
        ctx.check(not problems, f"{label}: no desync, cache error or other problem ({problems[:3]})")
        server.console("unpause")
        return states[0]

    server.wait_ticks(10 * DAY_TICKS, timeout_ms=600_000)
    checkpoint("no orders, no reservation")
    # Crashed trains disappear after a while: keep checking the others only.
    alive = server.objs("vehicle")
    trains = [t for t in trains if t in alive and not server.obj("vehicle", t, "vehstatus")["vehstatus"] & VS_CRASHED]
    ctx.note(f"{len(trains)} trains without orders left after the first part")

    server.console("setting pf.reserve_paths true")
    lost = builder.build_vehicle(grid.depot, loco)
    builder.order(lost, "station", station)
    builder.start_vehicle(lost)
    trains.append(lost)
    server.wait_ticks(2 * FULL_SEARCH_PERIOD * DAY_TICKS, timeout_ms=600_000)
    states = checkpoint("lost train")
    ctx.check(states[-1]["vehicle_flags"] & LOST_FLAG, f"the train for the unconnected station is lost ({states[-1]['vehicle_flags']:#x})")
    ctx.screenshot(c1, "mp_grid")

    # Client 2 leaves and joins again: it gets the lost flag and the tick count from the savegame.
    client2 = cluster.clients[1]
    client2.stop()
    cluster.start_client(client2)
    server.wait_ticks(FULL_SEARCH_PERIOD * DAY_TICKS, timeout_ms=600_000)
    checkpoint("after client 2 joined again")

    grid.connect_station()
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if server.obj("vehicle", lost, "last_station_visited")["last_station_visited"] == station:
            break
        server.wait_ticks(DAY_TICKS, timeout_ms=60_000)
    states = checkpoint("station connected")
    ctx.check(not states[-1]["vehicle_flags"] & LOST_FLAG, "the train is no longer lost")
    ctx.check_eq(states[-1]["last_station_visited"], station, "the train that was lost reached its station")
    server.wait_ticks(4 * DAY_TICKS, timeout_ms=120_000)
    checkpoint("end")
