# Full test performance on main

Written by the *Performance history* workflow after every full test run on `main` (`utils/debug_harness/perf_history.py`). Raw data: [history.json](history.json).

![Game loop avg](charts/game_loop_ms_avg.svg)

![Game loop p95](charts/game_loop_ms_p95.svg)

![Server memory max](charts/server_mem_mb_max.svg)

![Command round trip p95](charts/command_ms_p95.svg)

## quick

| Commit | Date | Result | Game loop avg | Game loop p95 | Server memory max | Command round trip p95 | Vehicles (max) |
|---|---|---|---|---|---|---|---|
| [`967acf9`](https://github.com/shoter/SJGRPP/commit/967acf9f722fe74c84a7a550aef3b276acef8032) | 2026-10-17 | ✅ [run](https://github.com/shoter/SJGRPP/actions/runs/38993951271) | 0.05 ms | 0.09 ms | 55.2 MB | 100 ms | 31 |

## normal

| Commit | Date | Result | Game loop avg | Game loop p95 | Server memory max | Command round trip p95 | Vehicles (max) |
|---|---|---|---|---|---|---|---|
| [`967acf9`](https://github.com/shoter/SJGRPP/commit/967acf9f722fe74c84a7a550aef3b276acef8032) | 2026-10-17 | ✅ [run](https://github.com/shoter/SJGRPP/actions/runs/38993951271) | 0.05 ms | 0.06 ms | 55.0 MB | 83 ms | 49 |

## max-28

| Commit | Date | Result | Game loop avg | Game loop p95 | Server memory max | Command round trip p95 | Vehicles (max) |
|---|---|---|---|---|---|---|---|
| [`967acf9`](https://github.com/shoter/SJGRPP/commit/967acf9f722fe74c84a7a550aef3b276acef8032) | 2026-10-17 | ✅ [run](https://github.com/shoter/SJGRPP/actions/runs/38993951271) | 0.96 ms | 1.32 ms | 100.1 MB | 950 ms | 880 |

## sharing

| Commit | Date | Result | Game loop avg | Game loop p95 | Server memory max | Command round trip p95 | Vehicles (max) |
|---|---|---|---|---|---|---|---|
| [`967acf9`](https://github.com/shoter/SJGRPP/commit/967acf9f722fe74c84a7a550aef3b276acef8032) | 2026-10-17 | ✅ [run](https://github.com/shoter/SJGRPP/actions/runs/38993951271) | 0.06 ms | 0.07 ms | 55.0 MB | 83 ms | 41 |
