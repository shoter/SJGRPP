#!/usr/bin/env python3
"""
Multiplayer stress / regression test.

A dedicated server and N headless clients (each its own company) on a generated vanilla map. Every client builds road,
rail, air and ship routes through real game commands (client -> server -> all clients, like GUI actions), then the
game runs for a period of game time while the test checks:

  - stability:    no crashed/exited process, no desync, kick, lost connection or other error in any log
  - consistency:  with the game paused, server and every client agree on companies, money, stations and vehicles
  - performance:  server game speed vs real time, game loop / per-vehicle-type tick times (fps console), memory,
                  command round-trip latency, join (map download) time; optional comparison with a saved baseline
  - gameplay:     vehicles move and earn money, networks keep running
  - churn:        a client leaves and rejoins its company, a spectator downloads the map, vehicles are added while
                  running, the server saves and the save loads back in a separate instance with the same content

  python utils/debug_harness/stress_test.py                            # 3 clients, normal profile (2 game years)
  python utils/debug_harness/stress_test.py --profile quick            # 3 game months
  python utils/debug_harness/stress_test.py --clients 6 --scale 2      # more companies, twice the vehicles
  python utils/debug_harness/stress_test.py --save-baseline perf_baseline.json
  python utils/debug_harness/stress_test.py --baseline perf_baseline.json

Output: build/harness_runs/stress/<run>/ (summary.txt, report.json, perf.csv, events.log, instances/, collected/ on
failure). Exit code 0 when no check failed.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import math
import random
import re
import statistics
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import collect  # noqa: E402
from gui_exercise import GuiExerciser  # noqa: E402
from mp import Cluster  # noqa: E402
from ottd import Bridge, BridgeError, DEFAULT_RUN_ROOT, Instance  # noqa: E402
from transport import BuildError, Builder, Route  # noqa: E402

PROFILES = {
    # months of game time after the build phase, seconds between checks, state comparison every n checks
    "quick": {"months": 3, "check_interval": 20, "sync_every": 2, "churn": ["rejoin", "spectator"]},
    "normal": {"months": 24, "check_interval": 45, "sync_every": 3,
               "churn": ["save", "rejoin", "spectator", "rejoin", "save", "spectator"]},
    # 28 companies on 1024x1024; every game month each company adds a route (2 new stations, connected, with vehicles).
    "max-28": {"months": 24, "check_interval": 60, "sync_every": 3, "clients": 28, "map_bits": 10,
               "monthly_growth": True, "monthly_join": True, "resolution": (640, 480),
               "churn": ["save", "rejoin", "spectator", "rejoin", "save", "spectator"]},
}

MONEY_TOP_UP = 1_000_000_000   # given by the server to each company...
MONEY_LOW = 300_000_000        # ...whenever its money is below this (checked every game month)

KINDS = ["road", "rail", "air", "ship"]

# Lines in any log that mean something went wrong. Checked case-insensitively.
PROBLEM_RE = re.compile(r"desync|lost connection|connection lost|kicked|server closed|assert|crash|corrupt|"
                        r"\berror\b|failed to|out of sync|timeout", re.I)
# Known harmless lines that match the pattern above.
IGNORE_RE = re.compile(r"\[debug-bridge\]|Failed to execute callback|error: no such|sound driver|music driver|"
                       r"failed to open|No performance measurements", re.I)

FPS_RATE_RE = re.compile(r"^\s*(.+?) rate: ([\d.]+)fps\s+\(expected: ([\d.]+)fps\)")
FPS_TIMES_RE = re.compile(r"^\s*(.+?) times: ([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")


def months_between(d0: str, d1: str) -> float:
    y0, m0, dd0 = (int(x) for x in d0.split("-"))
    y1, m1, dd1 = (int(x) for x in d1.split("-"))
    return (y1 - y0) * 12 + (m1 - m0) + (dd1 - dd0) / 30.0


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


class ProcessStats:
    """Memory / CPU time of a child process (Windows via psapi, elsewhere via /proc)."""

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    @staticmethod
    def sample(inst: Instance) -> dict:
        if inst.proc is None or not inst.alive():
            return {}
        try:
            if sys.platform == "win32":
                handle = int(inst.proc._handle)  # type: ignore[attr-defined]
                counters = ProcessStats.PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(counters)
                ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
                ft = [ctypes.c_ulonglong() for _ in range(4)]
                ctypes.windll.kernel32.GetProcessTimes(handle, *[ctypes.byref(f) for f in ft])
                return {"mem_mb": counters.WorkingSetSize / 2**20, "peak_mem_mb": counters.PeakWorkingSetSize / 2**20,
                        "cpu_s": (ft[2].value + ft[3].value) / 1e7}
            with open(f"/proc/{inst.proc.pid}/status") as f:
                status = dict(line.split(":", 1) for line in f if ":" in line)
            with open(f"/proc/{inst.proc.pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            import os
            hz = os.sysconf("SC_CLK_TCK")
            return {"mem_mb": int(status["VmRSS"].split()[0]) / 1024, "peak_mem_mb": int(status["VmHWM"].split()[0]) / 1024,
                    "cpu_s": (int(fields[11]) + int(fields[12])) / hz}
        except Exception:  # noqa: BLE001
            return {}


class StressTest:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        profile = dict(PROFILES[args.profile])
        if args.months is not None:
            profile["months"] = args.months
        if args.check_interval is not None:
            profile["check_interval"] = args.check_interval
        if args.no_churn:
            profile["churn"] = []
        if args.clients is None:
            args.clients = profile.get("clients", 3)
        if args.map_bits is None:
            args.map_bits = profile.get("map_bits", 8)
        self.profile = profile
        self.growth_threads: list[threading.Thread] = []
        self.town_xy: list[tuple[int, int]] = []
        self.town_use: dict[str, dict[int, int]] = {}
        run_name = args.name or time.strftime(f"%Y%m%d-%H%M%S-{args.profile}")
        self.out = Path(args.out) / run_name
        self.out.mkdir(parents=True, exist_ok=True)
        self.events = open(self.out / "events.log", "w", encoding="utf-8")
        self.checks: list[dict] = []
        self.perf: list[dict] = []
        self.routes: dict[int, list[Route]] = {}
        self.build_errors: dict[int, list[str]] = {}
        self.builders: list[Builder] = []
        self.join_seconds: dict[str, float] = {}
        self.cluster: Cluster | None = None
        self.extra_instances: list[Instance] = []
        self.t_start = time.monotonic()
        self.aborted = False
        self.vehicle_history: dict[int, set] = {}
        self.gui_stats = {"rounds": 0, "windows": 0, "clicks": 0, "errors": []}
        self.growth_ok: dict[int, int] = {}
        self.growth_failed: dict[int, int] = {}
        self._monitors: dict[str, Bridge] = {}
        self.spectators_started = 0

    # ---- reporting ----

    def log(self, text: str) -> None:
        line = f"[{time.monotonic() - self.t_start:7.1f}s] {text}"
        print(line, flush=True)
        self.events.write(line + "\n")
        self.events.flush()

    def check(self, ok: bool, name: str, detail: str = "", severity: str = "fail") -> bool:
        """severity: fail (the run fails) or warn (reported only)."""
        self.checks.append({"ok": bool(ok), "name": name, "detail": detail, "severity": severity,
                            "t": round(time.monotonic() - self.t_start, 1)})
        tag = "PASS" if ok else ("FAIL" if severity == "fail" else "WARN")
        self.log(f"[{tag}] {name}" + (f": {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> bool:
        return any(not c["ok"] and c["severity"] == "fail" for c in self.checks)

    # ---- setup ----

    def server_config(self) -> dict:
        a = self.args
        return {
            "game_creation": {"map_x": a.map_bits, "map_y": a.map_bits, "starting_year": a.start_year,
                              "landscape": "temperate", "variety": 0},
            "difficulty": {"terrain_type": 0, "quantity_sea_lakes": 2, "number_towns": 2, "industry_density": 3,
                           "vehicle_breakdowns": 0, "money_cheat_in_multiplayer": "true", "max_no_competitors": 0},
            "station": {"never_expire_airports": "true"},
            "pf": {"forbid_90_deg": "false"},
            "vehicle": {"plane_crashes": 0},
            "network": {"pause_on_join": "false", "max_clients": a.clients + 8,
                        "max_companies": min(28, max(15, a.clients + 1)), "autoclean_companies": "false"},
        }

    def instances_for_collect(self) -> dict:
        insts = (self.cluster.instances if self.cluster else []) + self.extra_instances
        return {i.name: {"dir": str(i.dir), "port": i.port if i.alive() else None} for i in insts}

    def mon(self, inst: Instance) -> Bridge:
        """A bridge connection for the monitoring (main) thread; builder threads use inst.bridge."""
        b = self._monitors.get(inst.name)
        if b is None or b.sock is None:
            b = Bridge(inst.port, timeout=120).connect(wait=10)
            self._monitors[inst.name] = b
        return b

    def drop_mon(self, inst: Instance) -> None:
        b = self._monitors.pop(inst.name, None)
        if b:
            b.close()

    # ---- health ----

    def health_problems(self) -> list[str]:
        problems = []
        assert self.cluster is not None
        for inst in self.cluster.instances + [i for i in self.extra_instances if i.proc is not None]:
            if not inst.alive():
                if inst in self.extra_instances and inst.proc is not None and inst.proc.returncode == 0:
                    continue
                if getattr(inst, "_expected_down", False):
                    continue
                problems.append(f"{inst.name}: process not running (exit code {inst.proc.returncode if inst.proc else '?'})")
                continue
            try:
                log = self.mon(inst).log(self.cluster.log_marks.get(inst.name, 0), 100000)
            except (OSError, BridgeError, ConnectionError) as e:
                self.drop_mon(inst)
                problems.append(f"{inst.name}: bridge unreachable: {e}")
                continue
            self.cluster.log_marks[inst.name] = log["last_seq"]
            for line in log["lines"]:
                text = line["text"]
                if PROBLEM_RE.search(text) and not IGNORE_RE.search(text):
                    problems.append(f"{inst.name}: {text.strip()}")
        return problems

    def health_check(self, label: str) -> bool:
        problems = self.health_problems()
        return self.check(not problems, f"health ({label})", "; ".join(problems[:8]))

    # ---- state comparison ----

    def pause_synced(self, timeout: float = 60.0, extra: list[Instance] | None = None) -> int:
        """Pause the server and wait until every client (and extra instances) reached the same game tick. Returns it."""
        assert self.cluster is not None
        server = self.mon(self.cluster.server)
        server.console("pause")
        server.wait_until(lambda b: b.info()["paused"], timeout=timeout, what="server pause")
        tick = server.info()["tick_counter"]
        for c in self.cluster.clients + (extra or []):
            self.mon(c).wait_until(lambda b: (i := b.info())["paused"] and i["tick_counter"] == tick, timeout=timeout,
                                what=f"{c.name} to reach paused tick {tick}")
        return tick

    def unpause(self) -> None:
        assert self.cluster is not None
        self.mon(self.cluster.server).console("unpause")

    def state_snapshot(self, bridge) -> dict:
        companies = sorted(bridge.objs("company"))
        vehicles = sorted(bridge.objs("vehicle"))
        stations = sorted(bridge.objs("station"))
        lines = [f"obj company {c} money current_loan" for c in companies]
        lines += [f"obj vehicle {v} tile x_pos y_pos cur_speed owner profit_lifetime" for v in vehicles]
        lines += [f"obj station {s} xy owner" for s in stations]
        lines.append("obj globals tick_counter date")
        resp = bridge.batch(lines, timeout=120)
        vals = [r.get("result") if r.get("ok") else {"error": r.get("error")} for r in resp]
        nc, nv = len(companies), len(vehicles)
        return {
            "companies": dict(zip(companies, vals[:nc])),
            "vehicles": dict(zip(vehicles, vals[nc:nc + nv])),
            "stations": dict(zip(stations, vals[nc + nv:nc + nv + len(stations)])),
            "globals": vals[-1],
        }

    @staticmethod
    def diff_snapshots(a: dict, b: dict, limit: int = 6) -> list[str]:
        diffs = []
        for section in ("globals", "companies", "stations", "vehicles"):
            sa, sb = a[section], b[section]
            if section == "globals":
                if sa != sb:
                    diffs.append(f"globals {sa} != {sb}")
                continue
            if set(sa) != set(sb):
                only_a, only_b = sorted(set(sa) - set(sb)), sorted(set(sb) - set(sa))
                diffs.append(f"{section} ids differ: only server {only_a[:5]}, only client {only_b[:5]}")
            for k in sorted(set(sa) & set(sb)):
                if sa[k] != sb[k]:
                    diffs.append(f"{section} {k}: server {sa[k]} client {sb[k]}")
                    if len(diffs) >= limit:
                        return diffs
        return diffs

    def sync_check(self, label: str) -> None:
        assert self.cluster is not None
        try:
            tick = self.pause_synced()
        except TimeoutError as e:
            self.check(False, f"state match ({label})", f"clients did not reach the paused server tick: {e}")
            self.unpause()
            return
        try:
            ref = self.state_snapshot(self.mon(self.cluster.server))
            for vid, v in ref["vehicles"].items():
                if isinstance(v, dict) and "tile" in v:
                    self.vehicle_history.setdefault(vid, set()).add((v["tile"], v.get("x_pos"), v.get("y_pos")))
            # Recalculate caches from the map on every instance: what a joining client does; a mismatch means
            # that client would desync.
            cache_problems = []
            for inst in [self.cluster.server] + self.cluster.clients:
                lines = [l for l in self.mon(inst).console("check_caches") if "mismatch" in l.lower()]
                if lines:
                    cache_problems.append(f"{inst.name}: " + "; ".join(lines[:3]))
            self.check(not cache_problems, f"caches match the map ({label})", " | ".join(cache_problems[:4]))
            mismatches = []
            for c in self.cluster.clients:
                diffs = self.diff_snapshots(ref, self.state_snapshot(self.mon(c)))
                if diffs:
                    mismatches.append(f"{c.name}: " + "; ".join(diffs))
            self.check(not mismatches, f"state match ({label})",
                       f"tick {tick}, {len(ref['companies'])} companies, {len(ref['stations'])} stations, "
                       f"{len(ref['vehicles'])} vehicles" + (" | " + " | ".join(mismatches) if mismatches else ""))
        finally:
            self.unpause()

    # ---- performance ----

    def sample_perf(self, prev: dict | None) -> dict:
        assert self.cluster is not None
        server = self.mon(self.cluster.server)
        info = server.info()
        now = time.monotonic()
        sample = {"t": round(now - self.t_start, 1), "date": info["date"], "tick": info["tick_counter"],
                  "paused": info["paused"]}
        for line in server.console("fps"):
            if m := FPS_RATE_RE.match(line):
                key = m.group(1).strip().lower().replace(" ", "_")
                sample[f"{key}_fps"] = float(m.group(2))
                sample[f"{key}_fps_expected"] = float(m.group(3))
            elif m := FPS_TIMES_RE.match(line):
                key = m.group(1).strip().lower().replace(" ", "_")
                sample[f"{key}_ms"] = float(m.group(2))
        if prev and not prev.get("paused") and not info["paused"] and now > prev["_mono"]:
            expected = sample.get("game_loop_fps_expected") or 1000 / 27
            sample["speed_ratio"] = round((info["tick_counter"] - prev["tick"]) / (now - prev["_mono"]) / expected, 3)
        for inst in self.cluster.instances:
            for k, v in ProcessStats.sample(inst).items():
                sample[f"{inst.name}_{k}"] = round(v, 2)
        try:
            sample["vehicles"] = len(server.objs("vehicle"))
        except BridgeError:
            pass
        sample["_mono"] = now
        self.perf.append(sample)
        return sample

    def perf_summary(self) -> dict:
        samples = [s for s in self.perf if "speed_ratio" in s]
        out: dict = {"samples": len(self.perf)}
        if samples:
            ratios = [s["speed_ratio"] for s in samples]
            out["speed_ratio_median"] = statistics.median(ratios)
            out["speed_ratio_min"] = min(ratios)
        for key in ("game_loop_ms", "gl_train_ticks_ms", "gl_road_vehicle_ticks_ms", "gl_ship_ticks_ms",
                    "gl_aircraft_ticks_ms", "gl_station_ticks_ms", "gl_landscape_ticks_ms", "gl_link_graph_delays_ms"):
            vals = [s[key] for s in self.perf if key in s]
            if vals:
                out[f"{key}_avg"] = round(statistics.mean(vals), 3)
                out[f"{key}_p95"] = round(percentile(vals, 95), 3)
        mem = [s["server_mem_mb"] for s in self.perf if "server_mem_mb" in s]
        if mem:
            out["server_mem_mb_max"] = max(mem)
            out["server_mem_mb_growth"] = round(mem[-1] - mem[0], 1)
        lat = [ms for b in self.builders for ms in b.command_ms]
        if lat:
            out["command_ms_avg"] = round(statistics.mean(lat), 1)
            out["command_ms_p95"] = round(percentile(lat, 95), 1)
            out["commands"] = len(lat)
        out["join_seconds"] = self.join_seconds
        out["vehicles_max"] = max((s.get("vehicles", 0) for s in self.perf), default=0)
        return out

    def write_perf_csv(self) -> None:
        keys: list[str] = []
        for s in self.perf:
            keys += [k for k in s if k not in keys and not k.startswith("_")]
        with open(self.out / "perf.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.perf)

    # ---- phases ----

    def start_cluster(self) -> None:
        a = self.args
        self.log(f"starting dedicated server + {a.clients} clients (seed {a.seed}, map {2 ** a.map_bits}^2)")
        self.cluster = Cluster(clients=a.clients, session="stress", root=self.out / "instances", seed=a.seed,
                               company="new", debug=a.debug, server_config=self.server_config(),
                               # Network progress in client logs, so a stalled join or timeout can be diagnosed.
                               client_debug=a.debug or "net=2",
                               resolution=tuple(self.profile.get("resolution", (1280, 720))))
        c = self.cluster
        c.root.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        c.server.start("-G", str(a.seed), wait=300)
        c.server.bridge.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=300, what="server game")
        self.log(f"server up in {time.monotonic() - t0:.1f}s, map {c.server.bridge.info()['map']}")
        for client in c.clients:
            t0 = time.monotonic()
            c.start_client(client, timeout=300)
            client.bridge.wait_until(lambda b: b.info()["local_company"] < 64, timeout=60, what=f"{client.name} company")
            self.join_seconds[client.name] = round(time.monotonic() - t0, 1)
            self.log(f"{client.name} joined in {self.join_seconds[client.name]}s as company {client.bridge.info()['local_company']}")
        c.write_state()
        self.check(len(c.server.bridge.objs("company")) == a.clients, "every client founded a company",
                   f"{len(c.server.bridge.objs('company'))} companies")

    def top_up_money(self, force: bool = False) -> None:
        """The server gives money to every company running low (MoneyCheatAdmin is a server-only command)."""
        assert self.cluster is not None
        server = self.mon(self.cluster.server)
        given = 0
        for cid in server.objs("company"):
            if not force and server.obj("company", cid, "money")["money"] >= MONEY_LOW:
                continue
            res = server.docmd("MoneyCheatAdmin", 0, MONEY_TOP_UP, as_company=cid)
            if res["success"]:
                given += 1
            else:
                self.check(False, f"server gives money to company {cid}", str(res.get("error")))
        if given:
            self.log(f"server gave {MONEY_TOP_UP:,} to {given} companies")

    def pick_pairs(self, kind: str, count: int, rng: random.Random) -> list:
        """Town pairs (single towns for ships) for new routes, preferring towns least used for this kind."""
        txy = self.town_xy
        use = self.town_use.setdefault(kind, {})
        if kind == "ship":
            idx = sorted(range(len(txy)), key=lambda i: (use.get(i, 0), rng.random()))[:count]
            for i in idx:
                use[i] = use.get(i, 0) + 1
            return [txy[i] for i in idx]
        lo, hi = {"road": (14, 40), "rail": (25, 70), "air": (60, 400)}[kind]
        result = []
        for _ in range(count):
            best = None
            for _ in range(2000):
                i, j = rng.randrange(len(txy)), rng.randrange(len(txy))
                if i == j or not lo <= abs(txy[i][0] - txy[j][0]) + abs(txy[i][1] - txy[j][1]) <= hi:
                    continue
                score = use.get(i, 0) + use.get(j, 0)
                if best is None or score < best[0]:
                    best = (score, i, j)
                    if score == 0:
                        break
            if best is None:
                break
            _, i, j = best
            use[i] = use.get(i, 0) + 1
            use[j] = use.get(j, 0) + 1
            result.append((txy[i], txy[j]))
        return result

    def build_route(self, ci: int, builder: Builder, kind: str, target, label: str) -> Route | None:
        a = self.args
        t0 = time.monotonic()
        try:
            if kind == "road":
                r = builder.build_road_route(*target, vehicles=4 * a.scale)
            elif kind == "rail":
                r = builder.build_rail_route(*target)
            elif kind == "air":
                r = builder.build_air_route(*target, vehicles=2 * a.scale)
            else:
                r = builder.build_ship_route(target, vehicles=2 * a.scale)
        except (BuildError, BridgeError, TimeoutError, ConnectionError) as e:
            self.build_errors.setdefault(ci, []).append(f"{label} {kind} {target}: {e}")
            self.log(f"client{ci + 1}: {label} {kind} route {target} failed: {e}")
            return None
        self.routes.setdefault(ci, []).append(r)
        self.log(f"client{ci + 1}: {label} {kind} route in {time.monotonic() - t0:.1f}s: stations {r.stations}, "
                 f"{len(r.vehicles)} x {r.engine}, path {r.path_length}, {r.commands} commands")
        return r

    def grow_client(self, ci: int, builder: Builder, month_index: int) -> None:
        """Monthly growth: one new route = 2 new stations, connected (road/rail), with vehicles."""
        k = (month_index + ci) % len(KINDS)
        kinds = KINDS[k:] + KINDS[:k]
        rng = random.Random(self.args.seed * 1000 + month_index * 64 + ci)
        for kind in kinds[:2]:  # next kind as fallback when no site is found
            for target in self.pick_pairs(kind, 2, rng):
                if self.build_route(ci, builder, kind, target, f"month {month_index}") is not None:
                    self.growth_ok[ci] = self.growth_ok.get(ci, 0) + 1
                    return
        self.growth_failed[ci] = self.growth_failed.get(ci, 0) + 1

    def start_growth(self, month_index: int) -> None:
        self.growth_threads = [threading.Thread(target=self.grow_client, args=(ci, self.builders[ci], month_index), daemon=True)
                               for ci in range(len(self.builders))]
        for t in self.growth_threads:
            t.start()
        self.log(f"month {month_index}: {len(self.growth_threads)} companies each building a new route")

    def growing(self) -> bool:
        return any(t.is_alive() for t in self.growth_threads)

    def assign_towns(self) -> dict[int, dict[str, list]]:
        """Per client and route kind, the town pairs to connect, spreading clients over the map."""
        server = self.cluster.server.bridge
        mx = server.info()["map"]["x"]
        towns = [(t, server.obj("town", t, "xy")["xy"]) for t in sorted(server.objs("town"))]
        txy = [(xy % mx, xy // mx) for _, xy in towns]
        self.town_xy = txy
        rng = random.Random(self.args.seed)
        ranges = {"road": (14, 40), "rail": (25, 70), "air": (60, 400)}
        used: dict[str, set] = {k: set() for k in ranges}
        plan: dict[int, dict[str, list]] = {}
        for ci in range(self.args.clients):
            plan[ci] = {}
            for kind, (lo, hi) in ranges.items():
                pairs = [(i, j) for i in range(len(txy)) for j in range(len(txy)) if i < j
                         and lo <= abs(txy[i][0] - txy[j][0]) + abs(txy[i][1] - txy[j][1]) <= hi]
                rng.shuffle(pairs)
                pairs.sort(key=lambda p: (p[0] in used[kind]) + (p[1] in used[kind]))
                chosen = pairs[:self.args.routes * 3]  # spares in case a pair cannot be built
                for i, j in chosen[:self.args.routes]:
                    used[kind] |= {i, j}
                plan[ci][kind] = [(txy[i], txy[j]) for i, j in chosen]
            plan[ci]["ship"] = [txy[rng.randrange(len(txy))] for _ in range(self.args.routes * 3)]
        self.log(f"{len(txy)} towns; town pairs assigned per client")
        return plan

    def build_for_client(self, ci: int, builder: Builder, plan: dict[str, list]) -> None:
        a = self.args
        routes: list[Route] = []
        errors: list[str] = []
        counts = {"road": 4 * a.scale, "air": 2 * a.scale, "ship": 2 * a.scale}
        for kind in KINDS:
            built = 0
            for target in plan[kind]:
                if built >= a.routes:
                    break
                t0 = time.monotonic()
                try:
                    if kind == "road":
                        r = builder.build_road_route(*target, vehicles=counts["road"])
                    elif kind == "rail":
                        r = builder.build_rail_route(*target)
                    elif kind == "air":
                        r = builder.build_air_route(*target, vehicles=counts["air"])
                    else:
                        r = builder.build_ship_route(target, vehicles=counts["ship"])
                    routes.append(r)
                    built += 1
                    self.log(f"client{ci + 1}: built {kind} route in {time.monotonic() - t0:.1f}s: stations {r.stations}, "
                             f"{len(r.vehicles)} x {r.engine}, path {r.path_length}, {r.commands} commands")
                except (BuildError, BridgeError, TimeoutError) as e:
                    errors.append(f"{kind} {target}: {e}")
                    self.log(f"client{ci + 1}: {kind} route {target} failed: {e}")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{kind} {target}: {type(e).__name__}: {e}")
                    self.log(f"client{ci + 1}: {kind} route {target} crashed:\n{traceback.format_exc()}")
                    break
        self.routes[ci] = routes
        self.build_errors[ci] = errors

    def build_phase(self) -> None:
        assert self.cluster is not None
        plan = self.assign_towns()
        self.top_up_money(force=True)
        self.builders = [Builder(c.bridge, log=lambda s, n=c.name: self.log(f"{n}: {s}")) for c in self.cluster.clients]
        t0 = time.monotonic()
        threads = [threading.Thread(target=self.build_for_client, args=(ci, self.builders[ci], plan[ci]), daemon=True)
                   for ci in range(len(self.builders))]
        for t in threads:
            t.start()
        # Watch the cluster while the clients build in parallel.
        prev = None
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=self.profile["check_interval"] / 4)
            prev = self.sample_perf(prev)
            problems = self.health_problems()
            if problems:
                self.check(False, "health (build phase)", "; ".join(problems[:8]))
                if any("not running" in p or "desync" in p.lower() for p in problems):
                    self.aborted = True
                    return
        self.log(f"build phase done in {time.monotonic() - t0:.1f}s")
        for kind in KINDS:
            built_by = [ci for ci, rs in self.routes.items() if any(r.kind == kind for r in rs)]
            self.check(len(built_by) > 0, f"{kind} network built", f"by {len(built_by)}/{len(self.builders)} clients")
            if 0 < len(built_by) < len(self.builders):
                missing = [f"client{ci + 1}: " + "; ".join(e for e in self.build_errors[ci] if e.startswith(kind))[:300]
                           for ci in range(len(self.builders)) if ci not in built_by]
                self.check(False, f"{kind} network built by every client", " | ".join(missing), severity="warn")
        cmds = sum(b.commands_sent for b in self.builders)
        failed = sum(b.commands_failed for b in self.builders)
        self.check(failed <= max(3, cmds * 0.02), "build commands executed", f"{cmds} commands, {failed} failed after passing their test run",
                   severity="warn")

    def churn(self, event: str, index: int) -> None:
        assert self.cluster is not None
        c = self.cluster
        if event == "rejoin":
            ci = index % len(c.clients)
            client = c.clients[ci]
            company = client.bridge.info()["local_company"]
            self.log(f"churn: {client.name} leaves and rejoins company {company}")
            client._expected_down = True  # type: ignore[attr-defined]
            self.drop_mon(client)
            client.stop()
            time.sleep(3)
            client.clean = False
            t0 = time.monotonic()
            client.start("-n", f"127.0.0.1:{c.server_port}#{company + 1}", wait=300)
            client.bridge.wait_until(lambda b: (i := b.info())["networking"] and i["game_mode"] == "normal"
                                     and i["client_id"] != 0, timeout=300, what=f"{client.name} to rejoin")
            client._expected_down = False  # type: ignore[attr-defined]
            c.log_marks[client.name] = 0
            self.join_seconds[f"{client.name}_rejoin{index}"] = round(time.monotonic() - t0, 1)
            self.check(client.bridge.info()["local_company"] == company, f"{client.name} rejoined its company",
                       f"in {time.monotonic() - t0:.1f}s")
            old = self.builders[ci]
            self.builders[ci] = Builder(client.bridge, log=old.log)
            self.builders[ci].command_ms = old.command_ms
            self.builders[ci].commands_sent, self.builders[ci].commands_failed = old.commands_sent, old.commands_failed
        elif event == "spectator":
            self.spectators_started += 1
            spec = Instance(f"spectator{self.spectators_started}", mode="headless", root=c.root, debug="net=3")
            self.extra_instances.append(spec)
            self.log(f"churn: {spec.name} joins as spectator (map download)")
            t0 = time.monotonic()
            spec.start("-n", f"127.0.0.1:{c.server_port}#255", wait=300)
            try:
                spec.bridge.wait_until(lambda b: (i := b.info())["networking"] and i["game_mode"] == "normal"
                                       and i["client_id"] != 0, timeout=300, what="spectator to join")
            except TimeoutError:
                detail = self.describe_stuck(spec)
                self.drop_mon(spec)
                spec.stop()
                raise TimeoutError(f"{spec.name} did not join within 300s: {detail}") from None
            self.join_seconds[spec.name] = round(time.monotonic() - t0, 1)
            # Long enough for the join-time cache/desync checks of a client that rebuilt its caches from the map.
            spec.bridge.wait_ticks(500, timeout_ms=120000)
            problems = [p for p in self.health_problems() if "desync" in p.lower() or spec.name in p]
            self.check(not problems and spec.alive(), f"{spec.name} stays in sync after joining", "; ".join(problems[:6]))
            # Compare at the same paused tick, like the regular state comparison.
            try:
                tick = self.pause_synced(extra=[spec])
                diffs = self.diff_snapshots(self.state_snapshot(self.mon(c.server)), self.state_snapshot(spec.bridge))
                self.check(not diffs, f"{spec.name} sees the same game state",
                           f"joined in {self.join_seconds[spec.name]}s, tick {tick}" + (" | " + "; ".join(diffs) if diffs else ""))
            except TimeoutError as e:
                self.check(False, f"{spec.name} sees the same game state", f"did not reach the paused server tick: {e}")
            finally:
                self.unpause()
            self.drop_mon(spec)
            spec.stop()
        elif event == "save":
            name = f"stress_mid{index}"
            self.log(f"churn: server saves {name}")
            out = self.mon(c.server).console(f"save {name}", timeout=120)
            self.check(any("saved" in l.lower() or "saving" in l.lower() for l in out) or not out, "server save mid-game",
                       "; ".join(out)[:200], severity="warn")

    def gui_round(self, ci: int, label: str, windows: int = 8) -> None:
        """Open/draw/click many windows on one client; a crash shows up in the health check."""
        assert self.cluster is not None
        client = self.cluster.clients[ci]
        ex = GuiExerciser(self.mon(client), rng=random.Random(hash((self.args.seed, label, ci))))
        try:
            ex.round(windows)
        except (BridgeError, ConnectionError, OSError, TimeoutError) as e:
            ex.errors.append(f"{type(e).__name__}: {e}")
            self.drop_mon(client)
        st = self.gui_stats
        st["rounds"] += 1
        st["windows"] += ex.opened
        st["clicks"] += ex.clicks
        st["errors"] += [f"{client.name}: {e}" for e in ex.errors]
        if not client.alive():
            self.check(False, f"GUI exercise on {client.name} ({label})",
                       f"client process died (exit code {client.proc.returncode}) while drawing windows; crash files: "
                       f"{[p.name for p in client.crash_files()]}")

    def describe_stuck(self, inst: Instance) -> str:
        """State of a client that did not get into the game, for the report (and a screenshot in the run dir)."""
        try:
            b = inst.bridge
            info = b.info()
            windows = [w["class_name"] for w in b.windows()]
            lines = [l["text"] for l in b.log(0, 1000)["lines"] if "[net" in l["text"] or "rror" in l["text"]][-8:]
            shot = b.screenshot(self.out / f"stuck_{inst.name}.png")
            return (f"mode {info['game_mode']}, networking {info['networking']}, client_id {info['client_id']}, "
                    f"windows {windows}, screenshot {shot}, log: {' / '.join(lines) or '(no network log lines)'}")
        except Exception as e:  # noqa: BLE001
            return f"(could not inspect: {type(e).__name__}: {e})"

    def expand(self, rng: random.Random) -> None:
        """Add a vehicle to a random road/air route while the game runs: keeps client commands flowing."""
        cands = [(ci, r) for ci, rs in self.routes.items() for r in rs
                 if r.kind in ("road", "air") and len(r.vehicles) < (12 if r.kind == "road" else 6) * self.args.scale]
        if not cands:
            return
        ci, route = rng.choice(cands)
        try:
            self.builders[ci].add_vehicles(route, 1)
        except (BuildError, BridgeError) as e:
            self.log(f"expand: client{ci + 1} could not add a {route.kind} vehicle: {e}")

    def run_phase(self) -> None:
        assert self.cluster is not None
        server = self.mon(self.cluster.server)
        start_date = server.info()["date"]
        months = self.profile["months"]
        interval = self.profile["check_interval"]
        churn = list(self.profile["churn"])
        churn_at = [(i + 1) / (len(churn) + 1) for i in range(len(churn))]
        self.log(f"run phase: {months} game months from {start_date}, checks every {interval}s, churn {churn}")
        rng = random.Random(self.args.seed + 1)
        prev = self.sample_perf(None)
        n = 0
        last_month = start_date[:7]
        month_index = 0
        while True:
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline:
                time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
                if not self.cluster.server.alive():
                    break
            n += 1
            if not self.cluster.server.alive():
                self.check(False, "server process alive", f"exit code {self.cluster.server.proc.returncode}")
                self.aborted = True
                return
            prev = self.sample_perf(prev)
            date = prev["date"]
            done = months_between(start_date, date)
            self.log(f"{date} ({done:.1f}/{months} months): speed {prev.get('speed_ratio', '-')}, game loop "
                     f"{prev.get('game_loop_ms', '-')}ms, vehicles {prev.get('vehicles', '-')}, server mem {prev.get('server_mem_mb', '-')}MB")
            if not self.health_check(f"{date}"):
                problems = " ".join(c["detail"] for c in self.checks[-1:])
                if "not running" in problems or "desync" in problems.lower():
                    self.aborted = True
                    return
            if date[:7] != last_month:
                last_month = date[:7]
                month_index += 1
                self.top_up_money()
                if self.profile.get("monthly_join") or self.args.join_every_month:
                    try:
                        self.churn("spectator", 1000 + month_index)
                    except Exception as e:  # noqa: BLE001
                        self.check(False, f"monthly join {date}", f"{type(e).__name__}: {e}")
                    if any("desync" in c["detail"].lower() for c in self.checks[-3:] if not c["ok"]):
                        self.aborted = True
                        return
                if self.profile.get("monthly_growth"):
                    if self.growing():
                        self.check(False, "monthly growth keeps up", f"month {month_index - 1} routes still building",
                                   severity="warn")
                    else:
                        self.start_growth(month_index)
            if self.growing():
                # Pausing (state comparison) or replacing a builder (rejoin) would disturb the builders.
                if done < months:
                    continue
                for t in self.growth_threads:
                    t.join()
            if self.args.gui_clients:
                for ci in rng.sample(range(len(self.cluster.clients)), min(self.args.gui_clients, len(self.cluster.clients))):
                    self.gui_round(ci, date)
                if any(not c.alive() for c in self.cluster.clients):
                    self.aborted = True
                    return
            if n % self.profile["sync_every"] == 0:
                self.sync_check(date)
            if not self.args.no_expand:
                self.expand(rng)
            while churn and done / months >= churn_at[0]:
                event = churn.pop(0)
                churn_at.pop(0)
                try:
                    self.churn(event, n)
                except Exception as e:  # noqa: BLE001
                    self.check(False, f"churn {event}", f"{type(e).__name__}: {e}")
            if done >= months:
                return

    def final_checks(self) -> None:
        assert self.cluster is not None
        c = self.cluster
        if self.profile.get("monthly_growth"):
            ok, failed = sum(self.growth_ok.values()), sum(self.growth_failed.values())
            self.check(ok > 0 and failed <= ok * 0.25, "monthly growth built routes",
                       f"{ok} routes built, {failed} company-months without a new route")
        if self.args.gui_clients:
            for ci in range(len(c.clients)):
                self.gui_round(ci, "final", windows=4)
            st = self.gui_stats
            self.check(all(cl.alive() for cl in c.clients), "GUI exercise",
                       f"{st['rounds']} rounds, {st['windows']} windows, {st['clicks']} clicks on clients")
            self.check(not st["errors"], "GUI exercise without bridge errors", "; ".join(st["errors"][:6]), severity="warn")
        self.sync_check("final")
        # Gameplay sanity per route kind: vehicles move and earn money.
        server = self.mon(c.server)
        for kind in KINDS:
            vids = [v for rs in self.routes.values() for r in rs if r.kind == kind for v in r.vehicles]
            if not vids:
                continue
            resp = server.batch([f"obj vehicle {v} tile x_pos y_pos cur_speed profit_this_year profit_last_year profit_lifetime" for v in vids])
            # A route's vehicle may be gone (e.g. a bus destroyed by a train at a level crossing) and its id reused
            # by another kind of vehicle that has no profit fields: only count the route's own vehicles.
            vals = {v: r["result"] for v, r in zip(vids, resp)
                    if r.get("ok") and all(str(r["result"].get(f, "")).lstrip("-").isdigit()
                                           for f in ("profit_this_year", "profit_last_year", "profit_lifetime"))}
            for v, val in vals.items():
                self.vehicle_history.setdefault(v, set()).add((val["tile"], val["x_pos"], val["y_pos"]))
            # profit_lifetime is only updated at the end of a year.
            earning = [v for v, val in vals.items()
                       if max(int(val["profit_this_year"]), int(val["profit_last_year"]), int(val["profit_lifetime"])) > 0]
            # Positions are only sampled at sync checks: a vehicle loading or queueing at a station at each sample looks
            # parked, but one that earned money has delivered cargo, so it moved.
            moved = [v for v in vids if len(self.vehicle_history.get(v, ())) > 1 or v in earning]
            self.check(len(vals) == len(vids), f"{kind} vehicles still exist", f"{len(vals)}/{len(vids)}",
                       severity="warn")  # crashes at level crossings are normal gameplay
            self.check(len(moved) >= max(1, len(vids) // 2), f"{kind} vehicles move", f"{len(moved)}/{len(vids)} changed position or earned money")
            self.check(len(earning) > 0, f"{kind} vehicles earn money",
                       f"{len(earning)}/{len(vids)} with positive profit",
                       severity="warn" if kind == "ship" or self.profile["months"] < 2 else "fail")

        # Save on the server and load it in a separate instance: same content, keeps running.
        tick = self.pause_synced()
        ref = self.state_snapshot(server)
        out = server.console("save stress_final", timeout=180)
        self.unpause()
        save = c.server.dir / "save" / "stress_final.sav"
        # Saving runs on a background thread: wait until the file exists and stopped growing.
        deadline, last_size = time.monotonic() + 120, -1
        while time.monotonic() < deadline:
            size = save.stat().st_size if save.exists() else -1
            if size > 0 and size == last_size:
                break
            last_size = size
            time.sleep(1)
        if not self.check(save.exists(), "server saved the game", "; ".join(out)[:200]):
            return
        loader = Instance("load_check", mode="headless", root=c.root, lockstep=True)
        self.extra_instances.append(loader)
        try:
            loader.start("-g", str(save), wait=300)
            loader.bridge.wait_until(lambda b: b.info()["game_mode"] == "normal", timeout=300, what="saved game to load")
            got = self.state_snapshot(loader.bridge)
            diffs = [d for d in self.diff_snapshots(ref, got) if not d.startswith("globals")]
            self.check(not diffs, "saved game loads with the same content", f"saved at tick {tick}; " + "; ".join(diffs))
            loader.bridge.step(500)
            self.check(loader.alive(), "loaded game keeps running", "500 ticks")
        except Exception as e:  # noqa: BLE001
            self.check(False, "saved game loads", f"{type(e).__name__}: {e}")
        finally:
            loader.stop()

    def perf_checks(self) -> dict:
        a = self.args
        summary = self.perf_summary()
        # On shared CI machines timings are noisy: report them, but let only correctness checks fail the run.
        perf = "warn" if a.perf_report_only else "fail"
        if "speed_ratio_median" in summary:
            self.check(summary["speed_ratio_median"] >= a.min_speed, "server keeps up with real time",
                       f"median speed {summary['speed_ratio_median']:.2f} (min {summary['speed_ratio_min']:.2f}), limit {a.min_speed}",
                       severity=perf)
            self.check(summary["speed_ratio_min"] >= a.min_speed * 0.7, "no long server stalls",
                       f"slowest sample {summary['speed_ratio_min']:.2f}", severity="warn")
        if "game_loop_ms_p95" in summary:
            self.check(summary["game_loop_ms_p95"] <= a.max_game_loop_ms, "game loop time",
                       f"p95 {summary['game_loop_ms_p95']}ms, avg {summary.get('game_loop_ms_avg')}ms, limit {a.max_game_loop_ms}ms",
                       severity=perf)
        if "command_ms_p95" in summary:
            self.check(summary["command_ms_p95"] <= a.max_command_ms, "command round trip",
                       f"p95 {summary['command_ms_p95']}ms over {summary['commands']} commands", severity="warn")
        slow_joins = {k: v for k, v in self.join_seconds.items() if v > 60}
        self.check(not slow_joins, "join / map download time", json.dumps(self.join_seconds), severity="warn")
        if a.baseline:
            base = json.loads(Path(a.baseline).read_text(encoding="utf-8"))
            tol = a.perf_tolerance
            for key in ("game_loop_ms_avg", "gl_train_ticks_ms_avg", "gl_road_vehicle_ticks_ms_avg", "gl_ship_ticks_ms_avg",
                        "gl_aircraft_ticks_ms_avg", "command_ms_p95", "server_mem_mb_max"):
                if key in base and key in summary:
                    limit = base[key] * (1 + tol) + (0.2 if key.endswith("ms_avg") else 0)
                    self.check(summary[key] <= limit, f"perf vs baseline: {key}",
                               f"{summary[key]} (baseline {base[key]}, limit {limit:.3f})", severity=perf)
        if a.save_baseline:
            Path(a.save_baseline).write_text(json.dumps(summary, indent=2), encoding="utf-8")
            self.log(f"baseline written to {a.save_baseline}")
        return summary

    # ---- main ----

    def run(self) -> int:
        summary: dict = {}
        try:
            self.start_cluster()
            self.build_phase()
            if not self.aborted:
                self.run_phase()
            if not self.aborted:
                self.final_checks()
            self.health_check("end")
        except Exception as e:  # noqa: BLE001
            self.check(False, "test harness", f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        finally:
            try:
                summary = self.perf_checks()
            except Exception as e:  # noqa: BLE001
                self.check(False, "performance evaluation", f"{type(e).__name__}: {e}")
            if self.failed:
                try:
                    collect(self.instances_for_collect(), self.out / "collected")
                    self.log(f"diagnostics collected in {self.out / 'collected'}")
                except Exception as e:  # noqa: BLE001
                    self.log(f"collector failed: {e}")
            self.write_perf_csv()
            self.write_report(summary)
            if not self.args.keep:
                for inst in self.extra_instances:
                    inst.stop()
                if self.cluster:
                    self.cluster.stop()
            else:
                self.log("--keep: instances left running")
        return 1 if self.failed else 0

    def write_report(self, perf: dict) -> None:
        routes = {f"client{ci + 1}": [r.to_json() for r in rs] for ci, rs in self.routes.items()}
        report = {"profile": self.args.profile, "settings": self.profile, "args": vars(self.args), "ok": not self.failed,
                  "seconds": round(time.monotonic() - self.t_start, 1), "checks": self.checks, "perf": perf,
                  "routes": routes, "build_errors": {f"client{ci + 1}": e for ci, e in self.build_errors.items()}}
        (self.out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        fails = [c for c in self.checks if not c["ok"] and c["severity"] == "fail"]
        warns = [c for c in self.checks if not c["ok"] and c["severity"] == "warn"]
        lines = [f"Stress test {'PASSED' if not fails else 'FAILED'}: profile {self.args.profile}, {self.args.clients} clients, "
                 f"{report['seconds']}s", f"output: {self.out}", ""]
        lines += [f"FAIL {c['name']}: {c['detail']}" for c in fails]
        lines += [f"WARN {c['name']}: {c['detail']}" for c in warns]
        lines.append(f"\n{sum(c['ok'] for c in self.checks)}/{len(self.checks)} checks passed")
        lines.append("\nroutes:")
        for name, rs in routes.items():
            lines.append(f"  {name}: " + ", ".join(f"{r['kind']}({len(r['vehicles'])})" for r in rs))
        lines.append("\nperformance:")
        lines += [f"  {k}: {v}" for k, v in perf.items()]
        text = "\n".join(lines)
        (self.out / "summary.txt").write_text(text + "\n", encoding="utf-8")
        print("\n" + text)


def main() -> int:
    # Town/engine names may contain characters the Windows console code page cannot print.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clients", type=int, help="number of client companies (default 3; max-28 profile: 28)")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="normal",
                    help="quick = 3 game months, normal = 2 game years, max-28 = 28 companies on 1024x1024 growing monthly for 2 game years")
    ap.add_argument("--months", type=float, help="override the game months of the run phase")
    ap.add_argument("--check-interval", type=float, help="seconds between checks (default from profile)")
    ap.add_argument("--seed", type=int, default=4242, help="map generation seed")
    ap.add_argument("--map-bits", type=int, help="map size as power of two (default 8 = 256x256; max-28: 10 = 1024x1024)")
    ap.add_argument("--start-year", type=int, default=1990)
    ap.add_argument("--routes", type=int, default=1, help="routes per transport type per client")
    ap.add_argument("--scale", type=int, default=1, help="vehicle count multiplier")
    ap.add_argument("--no-churn", action="store_true", help="no rejoin/spectator/save events")
    ap.add_argument("--no-expand", action="store_true", help="do not add vehicles while running")
    ap.add_argument("--join-every-month", action="store_true",
                    help="a fresh client joins every game month and must stay in sync (always on in max-28)")
    ap.add_argument("--gui-clients", type=int, default=2,
                    help="clients that open/draw/click many windows (smallmap in every mode, lists, graphs) at each "
                         "check; 0 disables; every client does one round at the end")
    ap.add_argument("--min-speed", type=float, default=0.9, help="fail if median game speed / real time is below this")
    ap.add_argument("--max-game-loop-ms", type=float, default=20.0, help="fail if p95 game loop time exceeds this")
    ap.add_argument("--max-command-ms", type=float, default=2000.0, help="warn if p95 command round trip exceeds this")
    ap.add_argument("--baseline", help="perf summary JSON from --save-baseline to compare against")
    ap.add_argument("--perf-tolerance", type=float, default=0.5, help="allowed relative regression vs baseline")
    ap.add_argument("--perf-report-only", action="store_true",
                    help="performance limits only warn (for noisy CI machines); correctness checks still fail")
    ap.add_argument("--save-baseline", help="write this run's perf summary as a baseline")
    ap.add_argument("--debug", default="", help="-d debug levels for all instances, e.g. desync=2")
    ap.add_argument("--out", default=str(DEFAULT_RUN_ROOT / "stress"))
    ap.add_argument("--name", help="run directory name (default: timestamp)")
    ap.add_argument("--keep", action="store_true", help="leave the instances running at the end")
    args = ap.parse_args()
    return StressTest(args).run()


if __name__ == "__main__":
    sys.exit(main())
