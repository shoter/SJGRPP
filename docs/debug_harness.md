# Debug harness

Tooling for automated testing and debugging: run the game without a window while still rendering the GUI,
drive and inspect it over a localhost socket, orchestrate multiplayer setups, run scenario tests and collect
crash/desync diagnostics.

## Pieces

| Piece | Where | What |
|---|---|---|
| Headless video driver | `src/video/headless_v.*` | `-v headless` – full GUI rendered into an in-memory frame buffer, no OS window. `-v headless:lockstep` starts in lockstep mode. |
| Debug bridge | `src/debug_bridge.*` | Line-based TCP server on `127.0.0.1`, enabled by env `OTTD_DEBUG_BRIDGE=<port>` or console `debug_bridge <port>`. Works with any video driver (headless, dedicated, normal window). |
| Object reflection | `src/debug_bridge_reflect.cpp` | Read any *saved* field of vehicles, companies, towns, stations, industries and the date/tick globals by name, using the savegame descriptor tables. |
| Python client | `utils/debug_harness/ottd.py` | `Bridge` (client) and `Instance` (isolated game process). |
| CLI | `utils/debug_harness/ottdctl.py` | One-shot or REPL access to a bridge. |
| Multiplayer | `utils/debug_harness/mp.py` | Dedicated server + N headless clients; `up`/`status`/`health`/`down`. |
| Scenarios | `utils/debug_harness/scenario.py`, `scenarios/` | Scripted tests with checks, screenshots, auto-collection on failure. |
| Collector | `utils/debug_harness/collect.py` | Bundle logs, crash logs/dumps/saves, desync logs, bridge log, final screenshot. |
| Build helper | `utils/debug_harness/build.cmd` | MSVC + Ninja build into `build/` (set `VCPKG_INSTALLED` to reuse an existing vcpkg tree). |

Automation mode (`OTTD_DEBUG_BRIDGE` or `OTTD_AUTOMATION` set) suppresses the Windows error message box and the
crash dialog, so crashes write their crash log/dump/save/screenshot and exit instead of waiting for a click.

## Quick start

```
utils\debug_harness\build.cmd
python utils/debug_harness/scenario.py utils/debug_harness/scenarios/solo_smoke.py utils/debug_harness/scenarios/mp_join.py
```

Manual session:

```
python utils/debug_harness/mp.py up --clients 2 --session mp      # dedicated server + 2 headless clients
python utils/debug_harness/ottdctl.py --session mp client1 screenshot c1.png
python utils/debug_harness/ottdctl.py --session mp server console clients
python utils/debug_harness/mp.py health --session mp
python utils/debug_harness/collect.py --session mp
python utils/debug_harness/mp.py down --session mp
```

Single instance from Python:

```python
from ottd import Instance
with Instance("solo", mode="headless", lockstep=True) as inst:
    inst.start("-g", "-G", "1")                     # new game, seed 1
    b = inst.bridge
    b.wait_until(lambda b: b.info()["game_mode"] == "normal")
    b.step(100)                                     # exactly 100 ticks
    print(b.obj("vehicle", 12, "cur_speed", "tile"))
    b.screenshot("frame.png")
```

Instances live in `build/harness_runs/<name>/` (override with `OTTD_RUN_ROOT`): that directory is the
instance's personal dir (config, saves, autosave, screenshots, crash files, `stdout.log`, `stderr.log`,
`command_line.txt`). Content folders (`content_download`, `baseset`, `newgrf`, `ai`, `game`) are junctions to
`~/Documents/OpenTTD` (override with `OTTD_CONTENT_DIR`), so base sets and NewGRFs are found.

## Bridge protocol

One request per line, `command arg1 "arg 2" ...`; one JSON response line: `{"ok":true,"result":...}` or
`{"ok":false,"error":"..."}`. Commands (`help` lists them):

- `info` – game mode, date, tick counters, pause state, network role, client id, company, screen size, lockstep state.
- `console <line>` – run a console command and return its output lines. All existing console tooling is
  reachable this way (`dump_vehicle`, `dump_tile`, `dump_info`, `setting`, `save`, `load`, `pause`, `join`, ...).
- `log [since] [max]`, `wait_log <substring> [timeout_ms] [since]` – ring buffer (10000 lines) of console and
  `Debug()` output with sequence numbers.
- `get <name>...`, `watches` – watch expressions (see below) and probe values.
- `probes [prefix]`, `probe_clear` – values recorded with `DebugBridge::Probe()`.
- `obj_types`, `objs <type> [all]`, `obj <type> <id> [field...]`, `obj_schema <type> <id>` – reflection.
  Types: `vehicle`, `company`, `town`, `station`, `industry`, `globals`. Sub-structures are listed by name
  only (`"<structlist>"`); fields that are not saved (caches) are not available – use watches/probes for those.
- `screenshot [path] [screen|viewport|world|minimap]` – `screen` is the raw frame buffer (needs a GUI driver).
  Taken after the next redraw, without the in-game "screenshot saved" message.
- `windows`, `widgets <class> <number>`, `close_window <class> <number>` – window list (with source file:line of
  the window definition), widget rectangles/strings/state; frame widgets without an ID come with a `role`
  (`closebox`, `caption`, ...). The Python client maps class/type numbers to enum names.
- `move`, `click <x> <y> [right] [ctrl] [shift]`, `click_widget <class> <number> <widget> [...]`,
  `drag x1 y1 x2 y2 [steps]`, `wheel x y delta`, `key <name|char> [ctrl] [shift] [alt]`, `text <string>`.
- `tile_screen <tx> <ty>`, `screen_tile <x> <y>` – map/screen coordinate conversion via the main viewport.
- `resize <w> <h>`.
- `lockstep on|off`, `step <n>` (lockstep only; replies after the ticks ran and were drawn),
  `wait_ticks <n> [timeout_ms]`, `wait_frames <n>`.
- `quit`, `crash yes-really [segv]` (to test crash tooling).

Commands run on the game thread from `InputLoop()`, i.e. at the same point GUI input is processed, so they
are safe to use with game state; in multiplayer, commands issued from a client go through the normal command
queue exactly like GUI actions.

## Lockstep vs real time

The headless driver runs in real time by default. In lockstep (`-v headless:lockstep` or `lockstep on`) a
running game only advances when asked (`step <n>`), which makes single player tests deterministic. Mode
switches, world generation and the intro game keep ticking. Lockstep is not available in network games; use
`wait_ticks` and `wait_log` there.

## Exposing values from game code

```cpp
#include "debug_bridge.h"

/* At namespace scope in any .cpp: evaluated on every "get"/"watches". */
DEBUG_BRIDGE_WATCH("pf.cache_size", _pf_cache.size());

/* Anywhere (any thread): latest value + hit count + tick; a single branch when the bridge is off. */
DebugBridge::Probe("yapf.last_cost", cost);
```

## Notes

- `-x` is passed so harness runs never write the config back; every start regenerates `openttd.cfg` and
  `private.cfg` in the instance dir.
- Desync investigation: start the cluster with `--debug desync=2` (writes `commands-out.log` in the instance
  dirs), `mp.py health` reports desync/disconnect lines, and `collect.py` bundles everything including
  `dump_desync_msgs` output from still-running instances.

## Game commands from Python

`docmd [test] [as <company>] <command> <tile> <field>...` issues any game command whose payload is a simple tuple
(`cmds [filter]` lists commands and their field types) as the local company, through the same network-safe path as the
GUI; it replies once the command was executed (in multiplayer after the server round trip) with success, cost, error,
result data (e.g. the new vehicle id) and the station on the command tile. `test` only runs the command's test.
`as <company>` is server-only (e.g. `MoneyCheatAdmin` to give a company money). `docmd_hex` takes a raw serialised
payload, `order <vehicle> station|waypoint|depot <dest> [options]` appends orders. `engines [type]` and
`tiles [x y w h]` provide what route planning needs. `utils/debug_harness/transport.py` builds road, rail, air and
ship routes with these (A* over the tile dump, every piece test-run before building).

## Stress test

`python utils/debug_harness/stress_test.py --profile quick|normal|max-28 [--clients N]`: dedicated server + N headless
clients, each building all transport types, then the game runs (quick: 3 game months, normal: 2 years, max-28: 28
companies on 1024x1024 for 2 years, each adding a route every month). The server tops up company money monthly.
Checks: crashes/desyncs/disconnects, paused server/client state comparison, game speed and tick times (`fps`),
memory, command latency, join times, vehicles moving and earning, rejoin/spectator/save churn, save reload. Results in
`build/harness_runs/stress/<run>/` (summary.txt, report.json, perf.csv); `--save-baseline` / `--baseline` compare
performance between runs. Exit code 1 on any failed check.

## CI (GitHub Actions)

`.github/workflows/game-tests.yml` builds the game once on Linux, then runs each test as a separate parallel job:
pull requests to `main` run `scenarios` (all `scenarios/*.py`), `quick`, `normal` and `max-28`; pushes to `main` run
`quick`; "Run workflow" accepts any comma-separated subset. Stress tests run with `--perf-report-only`: performance is
reported but only correctness (crashes, desyncs, state/cache mismatches, gameplay) fails a job. Each test job writes its
own summary; the `Report` job combines all results with `utils/debug_harness/ci_report.py` into the run summary and a
PR comment (updated in place). Artifacts `game-test-<name>` hold report.json, perf.csv, events.log, screenshots and the
crash/desync bundle (`collected/`) of a failed test.

Run the same locally on Linux: build `openttd`, put a base set into `$OTTD_CONTENT_DIR/baseset`, then
`python3 utils/debug_harness/stress_test.py --profile quick --perf-report-only`.
