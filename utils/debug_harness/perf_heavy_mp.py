#!/usr/bin/env python3
"""
TEMPORARY heavy multiplayer benchmark for PR #24 (realistic braking lookahead cutoff), used by
.github/workflows/perf-heavy-mp.yml on a draft PR that is never merged.

  perf_heavy_mp.py prepare --exe <openttd> --save <file.sav>   build the heavy save (single player, lockstep)
  perf_heavy_mp.py bench --exe <openttd> --save <file.sav> --out <file.json>   dedicated server + 4 clients on it
  perf_heavy_mp.py report --results <dir> [--comment <file.md>]              PR vs main over all bench jobs

The save: a 2048x2048 map with LINES straight single-track lines of LINE_LENGTH tiles, without signals, between two
terminus stations, with a waypoint every WAYPOINT_STEP tiles and one realistic-braking shuttle train each: every train
reserves to its far station, so its lookahead holds the waypoints (station items) of the line ahead. It is made once
(with the main build) and every bench job loads it.

A bench job starts a dedicated server on the save and 4 headless clients (spectators: every instance simulates the
whole game) and from the moment all clients joined samples the console 'fps' of every instance at fixed intervals:
game loop, train ticks and the achieved game speed. The report averages each instance over the jobs of a variant and
compares the PR with main.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MAP_BITS = 11
LINES = 2000
LINE_LENGTH = 360
LINE_STEP, ROW_STEP = LINE_LENGTH + 40, 4
WAYPOINT_STEP = 24
MARKER = "<!-- perf-heavy-mp -->"

TRACK_X, TRACK_Y, TRACK_UPPER, TRACK_LOWER, TRACK_LEFT, TRACK_RIGHT = range(6)
DIAGDIR_SE, AXIS_X, SIGTYPE_PATH_ONEWAY = 1, 0, 5
RAILTYPE_RAIL, NEW_STATION, INVALID_CARGO = 0, 0xFFFD, 0xFF
STAT_CLASS_WAYP, INVALID_STATION = 1, 0xFFFF

CONFIG = {
    "game_creation": {"map_x": MAP_BITS, "map_y": MAP_BITS, "starting_year": 1990, "landscape": "temperate",
                      "water_borders": 0, "custom_town_number": 1},
    "difficulty": {"terrain_type": 0, "quantity_sea_lakes": 0, "number_towns": 4, "industry_density": 0,
                   "max_no_competitors": 0, "vehicle_breakdowns": 0},
    "pf": {"forbid_90_deg": "false"},
    "network": {"max_clients": 16, "pause_on_join": "false"},
}
FPS_TIMES_RE = re.compile(r"^\s*(Game loop|GL train ticks) times:\s*([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")
FPS_RATE_RE = re.compile(r"^\s*Game loop rate:\s*([\d.]+)fps")
KEYS = {"Game loop": "game_loop_ms", "GL train ticks": "train_ticks_ms"}


def quote(a) -> str:
    from ottd import quote_arg
    return quote_arg(int(a) if isinstance(a, bool) else a)


def run_batch(b, lines: list[str], what: str, check: bool = True) -> list:
    out = []
    for i in range(0, len(lines), 300):
        for line, resp in zip(lines[i:i + 300], b.batch(lines[i:i + 300], timeout=600)):
            if not check:
                out.append(resp.get("result") if resp.get("ok") else {"success": False, "error": resp.get("error")})
                continue
            if not resp.get("ok"):
                raise RuntimeError(f"{what}: {line}: {resp.get('error')}")
            res = resp["result"]
            if isinstance(res, dict) and res.get("success") is False and "already" not in str(res.get("error")):
                raise RuntimeError(f"{what}: {line}: {res.get('error')}")
            out.append(res)
    return out


def docmd(name: str, t: int, *fields) -> str:
    return " ".join(["docmd", name, quote(t), *(quote(f) for f in fields)])


def cmd_prepare(a) -> int:
    from ottd import Instance

    size = 1 << a.map_bits
    config = {k: dict(v) for k, v in CONFIG.items()}
    config["game_creation"].update(map_x=a.map_bits, map_y=a.map_bits)
    tile = lambda x, y: y * size + x  # noqa: E731
    root = Path(a.root).resolve()
    t_start = time.monotonic()
    with Instance("prepare", mode="headless", lockstep=True, root=root, exe=Path(a.exe).resolve(), config=config,
                  resolution=(640, 480)) as inst:
        inst.start("-g", "-G", "777", wait=600)
        b = inst.bridge
        b.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=600, what="game start")
        for name, value in (("vehicle.train_braking_model", 1), ("vehicle.max_trains", 10000),
                            ("difficulty.town_council_tolerance", 0),
                            ("construction.terraform_per_64k_frames", 1 << 30), ("construction.terraform_frame_burst", 1 << 15),
                            ("construction.clear_per_64k_frames", 1 << 30), ("construction.clear_frame_burst", 1 << 15)):
            b.console(f"setting {name} {value}")
        b.docmd("MoneyCheat", 0, 2_000_000_000)
        b.step(10)
        engines = [e for e in b.engines("rail") if e["railtypes"] & (1 << RAILTYPE_RAIL)]
        locos = sorted((e for e in engines if not e.get("wagon")), key=lambda e: e["max_speed"])
        wagon = next(e for e in engines if e.get("wagon") and e["cargo_label"] == "PASS")["id"]
        loco_choices = [locos[-1]["id"], locos[len(locos) // 2]["id"], locos[-2]["id"]]
        towns = [(xy % size, xy // size) for xy in (b.obj("town", t, "xy")["xy"] for t in b.objs("town"))]
        cols = (size - 16) // LINE_STEP

        def clear_of_towns(x0: int, y: int) -> bool:
            # Town authorities refuse stations once clearing the land has spoilt their rating.
            return all(not (x0 - 10 <= tx <= x0 + LINE_LENGTH + 10 and y - 10 <= ty <= y + 10) for tx, ty in towns)

        trains = waypoints = 0
        n = skipped = 0
        row = 0
        while n < a.lines:
            y = 8 + row * ROW_STEP
            if y + 4 >= size:
                raise RuntimeError(f"room for only {n} lines ({skipped} sites skipped)")
            row += 1
            # Land for the whole row: clear, level, then one level up so nothing sits at sea level and floods.
            b.step(4)
            run_batch(b, [docmd("ClearArea", tile(size - 8, y + 1), tile(8, y - 2), False),
                          docmd("LevelLand", tile(size - 8, y + 1), tile(8, y - 2), False, 0)], "row land", check=False)
            b.step(4)
            run_batch(b, [docmd("LevelLand", tile(size - 8, y + 1), tile(8, y - 2), False, 2)], "row raise", check=False)
            for c in range(cols):
                if n >= a.lines:
                    break
                x0 = 8 + c * LINE_STEP
                x1 = x0 + LINE_LENGTH - 1
                if not clear_of_towns(x0, y):
                    skipped += 1
                    continue
                depot = tile(x0 + 6, y - 1)
                # Terminus stations (4 tiles) at both ends, one straight track between, a depot off its west end.
                track = [docmd("BuildRailStation", tile(x0, y), RAILTYPE_RAIL, AXIS_X, 1, 4, 0, 0, NEW_STATION, False),
                         docmd("BuildRailStation", tile(x1 - 3, y), RAILTYPE_RAIL, AXIS_X, 1, 4, 0, 0, NEW_STATION, False),
                         docmd("BuildRailroadTrack", tile(x1 - 4, y), tile(x0 + 4, y), RAILTYPE_RAIL, TRACK_X, 0, True),
                         docmd("BuildTrainDepot", depot, RAILTYPE_RAIL, DIAGDIR_SE)]
                tests = run_batch(b, [line.replace("docmd ", "docmd test ", 1) for line in track], "test", check=False)
                if not all(isinstance(t, dict) and t.get("success") for t in tests):
                    if skipped < 400:
                        bad = next(t for t in tests if not (isinstance(t, dict) and t.get("success")))
                        print(f"site ({x0}, {y}) test failed: {bad.get('command')}: {bad.get('error')}", flush=True)
                    skipped += 1
                    continue
                # Waypoints along the line: station items in every train's lookahead, passed without stopping.
                track += [docmd("BuildSingleRail", tile(x0 + 6, y), RAILTYPE_RAIL, TRACK_LEFT, 0)]
                track += [docmd("BuildRailWaypoint", tile(x, y), AXIS_X, 1, 1, STAT_CLASS_WAYP, 0, INVALID_STATION, False)
                          for x in range(x0 + 4 + WAYPOINT_STEP, x1 - 4 - 8, WAYPOINT_STEP)]
                res = run_batch(b, track, "line", check=False)
                failed = [(line, r) for line, r in zip(track, res) if not (isinstance(r, dict) and r.get("success"))]
                if failed:
                    print(f"line at ({x0}, {y}) skipped: {failed[:1]}", flush=True)
                    skipped += 1
                    continue
                waypoints += len(track) - 5
                st_a, st_b = res[0]["station"], res[1]["station"]
                loco = loco_choices[n % len(loco_choices)]
                first = int(run_batch(b, [docmd("BuildVehicle", depot, loco, False, INVALID_CARGO, 0)], "loco")[0]["result_data"])
                wagons = [int(r["result_data"]) for r in run_batch(
                    b, [docmd("BuildVehicle", depot, wagon, False, INVALID_CARGO, 0)] * 3, "wagons")]
                run_batch(b, [docmd("MoveRailVehicle", 0, w, first, 0) for w in wagons]
                          + [f"order {first} station {st_a}", f"order {first} station {st_b}",
                             docmd("StartStopVehicle", 0, first, False)], "train")
                trains += 1
                n += 1
                if n % 200 == 0:
                    print(f"{n}/{a.lines} lines ({skipped} sites skipped), {time.monotonic() - t_start:.0f}s", flush=True)
        b.step(74 * 20)      # twenty days: every train out of its depot and running
        vehicles = len(b.objs("vehicle"))
        if vehicles < trains:
            raise RuntimeError(f"only {vehicles} of {trains} trains left before saving")
        sample = [b.lookahead(t) for t in b.objs("vehicle")[:200]]
        lists = [len(la["items"]) for la in sample if la]
        b.console(f"save {Path(a.save).resolve()}")
    save = Path(a.save)
    if not save.exists() and save.with_name(save.name + ".sav").exists():
        save.with_name(save.name + ".sav").rename(save)
    info = {"layout": f"{n} straight single-track lines of {LINE_LENGTH} tiles without signals between two terminus "
                      f"stations, a waypoint every {WAYPOINT_STEP} tiles ({waypoints} waypoints), one shuttle train each",
            "lines": n, "skipped_sites": skipped, "trains": trains, "primary_vehicles": vehicles, "map": f"{size}x{size}",
            "lookahead_items_avg": round(statistics.mean(lists), 1) if lists else None,
            "lookahead_items_max": max(lists) if lists else None,
            "prepare_seconds": round(time.monotonic() - t_start), "save_mb": round(save.stat().st_size / 1e6, 1)}
    print(json.dumps(info), flush=True)
    Path(a.info).write_text(json.dumps(info), encoding="utf-8")
    return 0


def fps_sample(b) -> dict:
    out = {}
    for line in b.console("fps"):
        if m := FPS_TIMES_RE.match(line):
            out[KEYS[m.group(1)]] = float(m.group(4))   # the 512 data points average
        elif m := FPS_RATE_RE.match(line):
            out["game_speed_fps"] = float(m.group(1))
    return out


def cmd_bench(a) -> int:
    from mp import Cluster

    cluster = Cluster(clients=a.clients, session="bench", root=Path(a.root).resolve(), save=str(Path(a.save).resolve()),
                      company="spectator", resolution=(320, 240), server_config=CONFIG)
    result: dict = {"exe": a.exe, "instances": {}}
    t0 = time.monotonic()
    try:
        cluster.start(timeout=900)
        result["start_seconds"] = round(time.monotonic() - t0)
        insts = cluster.instances
        print(f"cluster up in {result['start_seconds']}s", flush=True)
        time.sleep(a.warmup)
        samples: dict[str, list[dict]] = {i.name: [] for i in insts}
        ticks0 = cluster.server.bridge.info()["tick_counter"]
        t_measure = time.monotonic()
        while time.monotonic() - t_measure < a.measure:
            time.sleep(a.interval)
            for inst in insts:
                samples[inst.name].append(fps_sample(inst.bridge))
        elapsed = time.monotonic() - t_measure
        result["server_ticks_per_second"] = round((cluster.server.bridge.info()["tick_counter"] - ticks0) / elapsed, 2)
        for name, rows in samples.items():
            agg = {}
            for key in ("game_loop_ms", "train_ticks_ms", "game_speed_fps"):
                vals = [r[key] for r in rows if key in r]
                if vals:
                    agg[key] = round(statistics.mean(vals), 3)
            result["instances"][name] = agg
        result["vehicles"] = len(cluster.server.bridge.objs("vehicle"))
        result["problems"] = cluster.health()
    finally:
        cluster.stop()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return 0


def cmd_report(a) -> int:
    jobs: dict[str, list[dict]] = {"main": [], "pr": []}
    for f in sorted(Path(a.results).rglob("bench_*.json")):
        variant = f.stem.split("_")[1]
        jobs.setdefault(variant, []).append(json.loads(f.read_text(encoding="utf-8")))
    info = next((json.loads(f.read_text(encoding="utf-8")) for f in Path(a.results).rglob("save_info.json")), {})
    names = sorted({n for js in jobs.values() for j in js for n in j["instances"]}, key=lambda n: (n != "server", n))

    def per_job(variant: str, name: str, key: str) -> list[float]:
        return [j["instances"][name][key] for j in jobs[variant] if key in j["instances"].get(name, {})]

    def fmt(vals: list[float]) -> str:
        if not vals:
            return "-"
        m = statistics.mean(vals)
        return f"{m:.3f} ({min(vals):.3f}…{max(vals):.3f})"

    lines = [MARKER, "## Heavy multiplayer benchmark: realistic braking lookahead cutoff (temporary)", "",
             f"Save (artifact `heavy-save` of the run): {info.get('map', '?')} map, {info.get('layout', '?')}: "
             f"{info.get('trains', '?')} realistic-braking trains, made once with the main build. Lookahead items per "
             f"train when saved: {info.get('lookahead_items_avg', '?')} on average, up to {info.get('lookahead_items_max', '?')}. "
             "Each job loads the save on a dedicated server with 4 spectator clients on one runner and, "
             + (f"after a {a.warmup}s warm-up, " if a.warmup else "as soon as every client has joined, ")
             + f"samples `fps` every {a.interval}s for {a.measure}s on every instance (512-tick averages). "
             f"{len(jobs['main'])} jobs ran main, {len(jobs['pr'])} jobs ran the PR, each on its own runner; values are the "
             "mean over the jobs of a variant with the per-job range in brackets. Lower is better for ms, higher for fps.",
             "", "| Instance | Metric | main | PR | PR − main | % |", "|---|---|---:|---:|---:|---:|"]
    for name in names:
        for key, label in (("train_ticks_ms", "train ticks (ms)"), ("game_loop_ms", "game loop (ms)"),
                           ("game_speed_fps", "game speed (fps)")):
            m, p = per_job("main", name, key), per_job("pr", name, key)
            if not m or not p:
                continue
            d = statistics.mean(p) - statistics.mean(m)
            pct = f"{d / statistics.mean(m) * 100:+.1f}%" if statistics.mean(m) else "-"
            lines.append(f"| {name} | {label} | {fmt(m)} | {fmt(p)} | {d:+.3f} | {pct} |")
    for key, label in (("train_ticks_ms", "train ticks (ms)"), ("game_loop_ms", "game loop (ms)")):
        m = [statistics.mean(j["instances"][n][key] for n in j["instances"] if key in j["instances"][n]) for j in jobs["main"]]
        p = [statistics.mean(j["instances"][n][key] for n in j["instances"] if key in j["instances"][n]) for j in jobs["pr"]]
        if m and p:
            d = statistics.mean(p) - statistics.mean(m)
            lines.append(f"| **all instances** | {label} | {fmt(m)} | {fmt(p)} | {d:+.3f} | {d / statistics.mean(m) * 100:+.1f}% |")
    tps = {v: [j.get("server_ticks_per_second") for j in jobs[v] if j.get("server_ticks_per_second")] for v in jobs}
    start = {v: [j.get("start_seconds") for j in jobs[v] if j.get("start_seconds") is not None] for v in jobs}
    lines += ["", f"Server ticks per second: main {fmt(tps['main'])}, PR {fmt(tps['pr'])}.",
              f"Server load + 4 client joins before measuring (s): main {fmt(start['main'])}, PR {fmt(start['pr'])}.",
              f"Problems (desync/disconnect): main {sum(len(j.get('problems', [])) for j in jobs['main'])}, "
              f"PR {sum(len(j.get('problems', [])) for j in jobs['pr'])}."]
    text = "\n".join(lines) + "\n"
    if a.comment:
        Path(a.comment).write_text(text, encoding="utf-8")
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--exe", required=True)
    p.add_argument("--save", required=True)
    p.add_argument("--info", default="save_info.json")
    p.add_argument("--root", default="perf_runs")
    p.add_argument("--lines", type=int, default=LINES)
    p.add_argument("--map-bits", type=int, default=MAP_BITS)
    bn = sub.add_parser("bench")
    bn.add_argument("--exe", required=True)
    bn.add_argument("--save", required=True)
    bn.add_argument("--out", required=True)
    bn.add_argument("--root", default="perf_runs")
    bn.add_argument("--clients", type=int, default=4)
    bn.add_argument("--warmup", type=int, default=120)
    bn.add_argument("--measure", type=int, default=600)
    bn.add_argument("--interval", type=int, default=20)
    r = sub.add_parser("report")
    r.add_argument("--results", required=True)
    r.add_argument("--comment")
    r.add_argument("--warmup", type=int, default=120)
    r.add_argument("--measure", type=int, default=600)
    r.add_argument("--interval", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "bench":
        os.environ["OTTD_EXE"] = str(Path(a.exe).resolve())   # the cluster's instances use it
    return {"prepare": cmd_prepare, "bench": cmd_bench, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
