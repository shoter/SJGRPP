"""
Realistic braking lookahead cutoff in multiplayer: client1 builds the test track of rl_braking_lookahead_cases.py
(curves, signals, hill, 6-level descent, bridge, stations, waypoint, eight trains with varied orders) through real
commands, with chicken bit 9 on (every instance checks the cutoff against all items). The server switches the braking
settings between phases; client2 leaves and joins again mid-run, so its lookahead item bounds are rebuilt from the
downloaded map while the server's and client1's were kept up to date incrementally. With the game paused all
instances must agree on every train, and there must be no desync, mismatch or assert anywhere.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rl_braking_lookahead_cases import CONFIG, DAY_TICKS, TestTrack, Watch, setup_settings  # noqa: E402
from transport import Builder  # noqa: E402

TRAIN_FIELDS = ("tile", "x_pos", "y_pos", "z_pos", "cur_speed", "progress", "cur_real_order_index", "last_station_visited")


def paused_states(server, clients, trains) -> list[dict]:
    server.console("pause")
    for b in [server] + clients:
        b.wait_until(lambda b: b.info()["pause_mode"] != 0, timeout=20, what="paused")
    states = [{t: b.obj("vehicle", t, *TRAIN_FIELDS) for t in trains} for b in [server] + clients]
    server.console("unpause")
    return states


def run(ctx):
    cluster = ctx.cluster(clients=2, seed=4242, company="new", server_config=CONFIG)
    server = cluster.server.bridge
    c1, c2 = (c.bridge for c in cluster.clients)
    for b in (c1, c2):
        b.wait_until(lambda b: b.info()["local_company"] != 255, timeout=60, what="client in its company")
    setup_settings(server)
    server.docmd("MoneyCheatAdmin", 0, 500_000_000, as_company=c1.info()["local_company"])

    def wait(ticks: int) -> None:
        server.wait_ticks(ticks, timeout_ms=ticks * 100 + 30000)

    track = TestTrack(c1, Builder(c1, log=ctx.note), ctx.note, wait)
    track.build()
    track.add_trains()
    track.start_trains(wait)
    watch = Watch(server, track.trains)

    def phase(label: str, days: int) -> None:
        start = watch.state()
        for day in range(days):
            wait(DAY_TICKS)
            if day % 4 == 0:
                watch.sample_lookahead()
        now = watch.state()
        moved = [t for t in track.trains if now[t]["tile"] != start[t]["tile"]]
        ctx.check(len(moved) == len(track.trains), f"{label}: all {len(track.trains)} trains moved ({len(moved)})")

    phase("realistic acceleration", 20)
    server.console("setting vehicle.realistic_braking_aspect_limited 1")
    phase("aspect limited signals", 12)

    # Client2 rejoins: its lookahead item bounds start invalid and are rebuilt from the map it downloads.
    client2 = cluster.clients[1]
    client2.stop()
    cluster.start_client(client2)
    c2 = client2.bridge
    c2.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=120, what="client2 back in the game")

    server.console("setting vehicle.realistic_braking_aspect_limited 0")
    server.console("setting vehicle.train_slope_steepness 10")
    phase("steepest slopes, after client2 rejoined", 15)
    server.console("setting vehicle.train_acceleration_model 0")
    phase("original acceleration", 12)
    server.console("setting vehicle.train_acceleration_model 1")
    phase("realistic acceleration again", 10)

    states = paused_states(server, [c1, c2], track.trains)
    diffs = [f"client{i} train {t}: {s[t]} != {states[0][t]}" for i, s in enumerate(states[1:], 1) for t in track.trains
             if s[t] != states[0][t]]
    ctx.check(not diffs, "server and both clients agree on every train" + (": " + "; ".join(diffs[:3]) if diffs else ""))

    ctx.note(f"lookahead item types seen: {sorted(watch.types)}; longest list {watch.max_items} items")
    for kind in ("station", "signal", "curve_speed", "track_speed"):
        ctx.check(kind in watch.types, f"lookaheads held {kind} items")

    problems = cluster.health()
    ctx.check(not problems, "no desync/disconnect messages: " + "; ".join(problems[:5]))
    for inst in cluster.instances:
        ctx.check(not inst.crash_files(), f"{inst.name}: no crash or assert")
        stderr = inst.dir / "stderr.log"
        text = stderr.read_text(encoding="utf-8", errors="replace") if stderr.exists() else ""
        ctx.check("cutoff mismatch" not in text, f"{inst.name}: no lookahead cutoff mismatch in the log")
