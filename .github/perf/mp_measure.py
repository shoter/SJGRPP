"""
TEMPORARY (perf measurement of this PR; removed before merge).

Heavy multiplayer measurement: a dedicated server loads the benchmark world (4096x4096, ~8000 vehicles), 4 headless
clients join, the game runs in real time, and the per-element tick times (`fps` console command, average of the last
512 ticks) of the server and every client are sampled at a fixed interval.

  python .github/perf/mp_measure.py --exe <openttd> --content <dir> --save world.sav --label base --out result.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "utils" / "debug_harness"))
from mp import Cluster  # noqa: E402

FPS_TIMES = re.compile(r"^\s*(.+?) times: ([\d.]+)ms\s+([\d.]+)ms\s+([\d.]+)ms")
FPS_RATE = re.compile(r"^\s*Game loop rate: ([\d.]+)fps")
ELEMENTS = ["Game loop", "GL station ticks", "GL train ticks", "GL road vehicle ticks", "GL ship ticks",
            "GL aircraft ticks", "GL landscape ticks", "GL link graph delays"]
SERVER_CONFIG = {
    "network": {"pause_on_join": "false", "max_clients": 16, "max_companies": 15, "autoclean_companies": "false",
                "min_active_clients": 0, "max_join_time": 5000, "max_download_time": 5000, "max_init_time": 5000},
    "gui": {"autosave_interval": 0},
}


def log(msg: str) -> None:
    print(f"[mp_measure {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sample(b) -> dict[str, float]:
    out = {}
    for line in b.console("fps"):
        m = FPS_TIMES.match(line)
        if m:
            out[m.group(1).strip()] = float(m.group(4))
        m = FPS_RATE.match(line)
        if m:
            out["rate"] = float(m.group(1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--content", required=True)
    ap.add_argument("--save", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--clients", type=int, default=4)
    ap.add_argument("--warmup-ticks", type=int, default=1500)
    ap.add_argument("--minutes", type=float, default=6)
    ap.add_argument("--interval", type=float, default=20)
    ap.add_argument("--run-root", default="runs")
    a = ap.parse_args()

    exe, content = Path(a.exe).resolve(), Path(a.content).resolve()
    cluster = Cluster(clients=a.clients, session=f"mp_{a.label}", root=Path(a.run_root).resolve(),
                      save=str(Path(a.save).resolve()), company="spectator", resolution=(640, 480),
                      server_config=SERVER_CONFIG)
    for inst in cluster.instances:
        inst.exe, inst.content = exe, content
    t0 = time.monotonic()
    samples: dict[str, list[dict]] = {inst.name: [] for inst in cluster.instances}
    try:
        cluster.start(timeout=900)
        server = cluster.server.bridge
        server.console("unpause")
        log(f"server + {a.clients} clients up in {time.monotonic() - t0:.0f}s; "
            f"{len(server.objs('vehicle'))} vehicles, map {server.info()['map']}")
        server.wait_ticks(a.warmup_ticks, timeout_ms=600000)
        k0, m0 = server.info()["tick_counter"], time.monotonic()
        end = m0 + a.minutes * 60
        while time.monotonic() < end:
            time.sleep(a.interval)
            for inst in cluster.instances:
                samples[inst.name].append(sample(inst.bridge))
        k1, m1 = server.info()["tick_counter"], time.monotonic()
        problems = cluster.health()
    finally:
        cluster.stop()
    result = {"label": a.label, "ticks": k1 - k0, "ticks_per_s": (k1 - k0) / (m1 - m0), "problems": problems[:20],
              "instances": {}}
    for name, rows in samples.items():
        keys = [k for k in ELEMENTS + ["rate"] if any(k in r for r in rows)]
        result["instances"][name] = {k: statistics.mean(r[k] for r in rows if k in r) for k in keys}
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    log(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
