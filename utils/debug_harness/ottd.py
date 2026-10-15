"""
OpenTTD debug harness: client for the in-game debug bridge, and launcher for isolated game instances.

Library usage:
    from ottd import Instance
    with Instance("solo", mode="headless") as inst:
        inst.start()
        b = inst.bridge
        print(b.info())
        b.screenshot("out/frame.png")

See docs/debug_harness.md for the full picture.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[2]
DEFAULT_EXE = Path(os.environ.get("OTTD_EXE", REPO / "build" / "openttd.exe"))
DEFAULT_RUN_ROOT = Path(os.environ.get("OTTD_RUN_ROOT", REPO / "build" / "harness_runs"))
DEFAULT_CONTENT = Path(os.environ.get("OTTD_CONTENT_DIR", Path.home() / "Documents" / "OpenTTD"))

# Shared content folders, linked (junctions) into every instance so base sets/NewGRFs/scripts are found,
# while saves, autosaves, screenshots, crash logs and configs stay per instance.
SHARED_CONTENT_DIRS = ["content_download", "baseset", "newgrf", "ai", "game"]


class BridgeError(Exception):
    """Error reported by the game for a bridge command."""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def quote_arg(arg: Any) -> str:
    s = str(arg)
    if s and not any(c in s for c in ' \t"\\'):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


_enum_cache: dict[str, dict[str, int]] = {}


def parse_enum(header: str, enum_decl: str, scope: str = "") -> dict[str, int]:
    """Parse a C++ enum from a source header into {name: value} (implicit values and simple expressions)."""
    key = f"{header}:{enum_decl}"
    if key not in _enum_cache:
        text = (REPO / "src" / header).read_text(encoding="utf-8")
        body = text[text.index(enum_decl):]
        body = body[body.index("{") + 1:body.index("};")]
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        body = re.sub(r"//[^\n]*", "", body)
        result: dict[str, int] = {}
        value = -1
        for entry in body.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if "=" in entry:
                name, expr = (x.strip() for x in entry.split("=", 1))
                if scope:
                    expr = expr.replace(scope + "::", "")
                if expr in result:
                    value = result[expr]
                else:
                    value = int(eval(expr, {"__builtins__": {}}, dict(result)))  # trusted: our own source header
            else:
                name = entry
                value += 1
            result[name] = value
        _enum_cache[key] = result
    return _enum_cache[key]


def window_classes() -> dict[str, int]:
    """Map of WindowClass enumerator name -> value, parsed from src/window_type.h."""
    return parse_enum("window_type.h", "enum class WindowClass", "WindowClass")


def widget_types() -> dict[str, int]:
    """Map of WidgetType enumerator name -> value, parsed from src/widget_type.h."""
    return parse_enum("widget_type.h", "enum WidgetType")


def _reverse_lookup(table: dict[str, int], value: int, skip: tuple[str, ...] = ()) -> str:
    for name, v in table.items():
        if v == value and name not in skip:
            return name
    return str(value)


def window_class_name(value: int) -> str:
    return _reverse_lookup(window_classes(), value, ("None",))


def widget_type_name(value: int) -> str:
    return _reverse_lookup(widget_types(), value, ("WWT_LAST", "WPT_ATTRIBUTE_BEGIN"))


def window_class_value(cls: int | str) -> int:
    if isinstance(cls, int):
        return cls
    if cls.lstrip("-").isdigit():
        return int(cls)
    return window_classes()[cls]


class Bridge:
    """Connection to a running game's debug bridge (see src/debug_bridge.h)."""

    def __init__(self, port: int, host: str = "127.0.0.1", timeout: float = 120.0):
        self.port = port
        self.host = host
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.buf = b""

    # ---- connection ----

    def connect(self, wait: float = 0.0) -> "Bridge":
        deadline = time.monotonic() + wait
        while True:
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=5)
                self.sock.settimeout(self.timeout)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def __enter__(self) -> "Bridge":
        if self.sock is None:
            self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def raw(self, line: str, timeout: float | None = None) -> dict:
        """Send one command line, return the decoded JSON response object."""
        if self.sock is None:
            self.connect()
        assert self.sock is not None
        self.sock.settimeout(timeout if timeout is not None else self.timeout)
        self.sock.sendall(line.encode("utf-8") + b"\n")
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("debug bridge closed the connection")
            self.buf += chunk
        resp, self.buf = self.buf.split(b"\n", 1)
        return json.loads(resp.decode("utf-8"))

    def call(self, *args: Any, timeout: float | None = None) -> Any:
        """Run a bridge command; returns its result or raises BridgeError."""
        resp = self.raw(" ".join(quote_arg(a) for a in args), timeout=timeout)
        if not resp.get("ok"):
            raise BridgeError(resp.get("error"))
        return resp.get("result")

    # ---- general ----

    def ping(self) -> bool:
        return self.call("ping") == "pong"

    def info(self) -> dict:
        return self.call("info")

    def quit(self) -> None:
        try:
            self.call("quit", timeout=5)
        except (OSError, ConnectionError, BridgeError):
            pass

    def console(self, command: str, timeout: float | None = None) -> list[str]:
        """Run a console command, return its output lines."""
        resp = self.raw("console " + command, timeout=timeout)
        if not resp.get("ok"):
            raise BridgeError(resp.get("error"))
        return resp["result"]

    def log(self, since: int = 0, max_lines: int = 500) -> dict:
        return self.call("log", since, max_lines)

    def wait_log(self, needle: str, timeout_ms: int = 10000, since: int = 0) -> dict:
        return self.call("wait_log", needle, timeout_ms, since, timeout=timeout_ms / 1000 + 10)

    def last_log_seq(self) -> int:
        return self.log(since=2**62, max_lines=0)["last_seq"]

    # ---- inspection ----

    def get(self, *names: str) -> dict:
        return self.call("get", *names)

    def watches(self) -> dict:
        return self.call("watches")

    def probes(self, prefix: str = "") -> dict:
        return self.call("probes", prefix) if prefix else self.call("probes")

    def obj(self, type_: str, id_: int | None = None, *fields: str) -> dict:
        if type_ == "globals":
            return self.call("obj", "globals", *fields)
        return self.call("obj", type_, id_, *fields)

    def obj_schema(self, type_: str, id_: int = 0) -> dict:
        return self.call("obj_schema", type_, id_)

    def objs(self, type_: str, all_: bool = False) -> list[int]:
        return self.call("objs", type_, "all") if all_ else self.call("objs", type_)

    # ---- GUI ----

    def screenshot(self, path: str | Path | None = None, kind: str = "screen") -> str:
        if path is None:
            return self.call("screenshot")["path"]
        return self.call("screenshot", Path(path).resolve(), kind)["path"]

    def windows(self) -> list[dict]:
        result = self.call("windows")
        for w in result:
            w["class_name"] = window_class_name(w["class"])
        return result

    def find_windows(self, cls: int | str) -> list[dict]:
        value = window_class_value(cls)
        return [w for w in self.windows() if w["class"] == value]

    def widgets(self, cls: int | str, number: int = 0) -> dict:
        result = self.call("widgets", window_class_value(cls), number)
        result["class_name"] = window_class_name(result["class"])
        for w in result["widgets"]:
            w["type_name"] = widget_type_name(w["type"])
        return result

    def close_window(self, cls: int | str, number: int = 0) -> None:
        self.call("close_window", window_class_value(cls), number)

    def click(self, x: int, y: int, *flags: str) -> None:
        self.call("click", x, y, *flags)

    def click_widget(self, cls: int | str, number: int, widget: int, *flags: str) -> dict:
        return self.call("click_widget", window_class_value(cls), number, widget, *flags)

    def move(self, x: int, y: int) -> None:
        self.call("move", x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, steps: int = 10) -> None:
        self.call("drag", x1, y1, x2, y2, steps)

    def wheel(self, x: int, y: int, delta: int) -> None:
        self.call("wheel", x, y, delta)

    def key(self, name: str, *mods: str) -> None:
        self.call("key", name, *mods)

    def text(self, s: str) -> None:
        self.call("text", s)

    def tile_screen(self, tx: int, ty: int) -> dict:
        return self.call("tile_screen", tx, ty)

    def screen_tile(self, x: int, y: int) -> dict | None:
        return self.call("screen_tile", x, y)

    def resize(self, w: int, h: int) -> dict:
        return self.call("resize", w, h)

    # ---- time ----

    def lockstep(self, on: bool = True) -> None:
        self.call("lockstep", "on" if on else "off")

    def step(self, ticks: int = 1) -> dict:
        return self.call("step", ticks, timeout=max(self.timeout, ticks / 100))

    def wait_ticks(self, ticks: int, timeout_ms: int = 60000) -> dict:
        return self.call("wait_ticks", ticks, timeout_ms, timeout=timeout_ms / 1000 + 10)

    def wait_frames(self, frames: int = 1) -> None:
        self.call("wait_frames", frames)

    def wait_until(self, predicate, timeout: float = 30.0, interval: float = 0.1, what: str = "condition") -> Any:
        """Poll predicate(bridge) until it returns a truthy value."""
        deadline = time.monotonic() + timeout
        while True:
            value = predicate(self)
            if value:
                return value
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timeout waiting for {what}")
            time.sleep(interval)


def _make_junction(link: Path, target: Path) -> None:
    if link.exists() or link.is_symlink():
        return
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


class Instance:
    """
    An isolated game process: own personal dir (config, saves, autosaves, screenshots, crash logs),
    shared read-mostly content dirs, its own debug bridge port, and stdout/stderr captured to files.

    mode: "headless" (full GUI rendered off-screen), "dedicated" (dedicated server), "gui" (normal window), "null".
    """

    def __init__(self, name: str, mode: str = "headless", root: Path | str | None = None, exe: Path | str | None = None,
                 port: int | None = None, resolution: tuple[int, int] = (1280, 720), lockstep: bool = False,
                 client_name: str | None = None, server_port: int | None = None, debug: str = "",
                 config: dict[str, dict[str, Any]] | None = None, content: Path | str | None = DEFAULT_CONTENT,
                 extra_args: Iterable[str] = (), clean: bool = True):
        self.name = name
        self.mode = mode
        self.root = Path(root) if root else DEFAULT_RUN_ROOT
        self.dir = self.root / name
        self.exe = Path(exe) if exe else DEFAULT_EXE
        self.port = port or free_port()
        self.resolution = resolution
        self.lockstep = lockstep
        self.client_name = client_name or name
        self.server_port = server_port
        self.debug = debug
        self.config = config or {}
        self.content = Path(content) if content else None
        self.extra_args = list(extra_args)
        self.clean = clean
        self.proc: subprocess.Popen | None = None
        self._bridge: Bridge | None = None
        self._stdout = None
        self._stderr = None

    # ---- setup ----

    def prepare(self) -> None:
        if self.clean and self.dir.exists():
            # Junctions must be removed as links, never followed.
            for d in SHARED_CONTENT_DIRS:
                link = self.dir / d
                if link.is_symlink() or (link.exists() and os.path.isjunction(link)):
                    os.rmdir(link) if sys.platform == "win32" else link.unlink()
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.content and self.content.exists():
            for d in SHARED_CONTENT_DIRS:
                if (self.content / d).is_dir():
                    _make_junction(self.dir / d, self.content / d)

        cfg: dict[str, dict[str, Any]] = {
            "misc": {"savegame_format": "lzma"},
            "network": {"server_game_type": "local", "server_advertise": "false"},
            "gui": {"autosave_on_exit": "false"},
        }
        if self.server_port:
            cfg["network"]["server_port"] = self.server_port
        private = {"client_name": self.client_name, "server_name": f"harness-{self.name}",
                   "use_relay_service": "never", "participate_survey": "no"}
        # The generated openttd.cfg has no [version] group, so the game reads it as a pre-private.cfg config
        # and takes the private settings from it; write them to both files so either interpretation works.
        cfg["network"].update(private)
        for section, values in self.config.items():
            cfg.setdefault(section, {}).update(values)
        self._write_ini(self.dir / "openttd.cfg", cfg)
        self._write_ini(self.dir / "private.cfg", {"network": private})

    @staticmethod
    def _write_ini(path: Path, data: dict[str, dict[str, Any]]) -> None:
        lines = []
        for section, values in data.items():
            lines.append(f"[{section}]")
            lines += [f"{k} = {v}" for k, v in values.items()]
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")

    def command_line(self, args: Iterable[str] = ()) -> list[str]:
        cmd = [str(self.exe), "-c", str(self.dir / "openttd.cfg"), "-x", "-s", "null", "-m", "null"]
        if self.mode == "dedicated":
            cmd += ["-D", f"127.0.0.1:{self.server_port}" if self.server_port else ""]
            cmd = [c for c in cmd if c != ""]
        elif self.mode == "headless":
            cmd += ["-v", "headless:lockstep" if self.lockstep else "headless", "-r", f"{self.resolution[0]}x{self.resolution[1]}"]
        elif self.mode == "null":
            cmd += ["-v", "null:until_exit"]
        elif self.mode == "gui":
            cmd += ["-r", f"{self.resolution[0]}x{self.resolution[1]}"]
        if self.debug:
            cmd += ["-d", self.debug]
        cmd += list(self.extra_args) + list(args)
        return cmd

    # ---- lifecycle ----

    def start(self, *args: str, wait: float = 60.0) -> "Instance":
        """Start the process (extra args are appended to the command line) and wait for the bridge."""
        self.prepare()
        env = dict(os.environ)
        env["OTTD_DEBUG_BRIDGE"] = str(self.port)
        cmd = self.command_line(args)
        (self.dir / "command_line.txt").write_text(subprocess.list2cmdline(cmd), encoding="utf-8")
        self._stdout = open(self.dir / "stdout.log", "wb")
        self._stderr = open(self.dir / "stderr.log", "wb")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(cmd, cwd=self.dir, env=env, stdout=self._stdout, stderr=self._stderr,
                                     stdin=subprocess.DEVNULL, creationflags=flags)
        self.wait_ready(wait)
        return self

    def wait_ready(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            if not self.alive():
                raise RuntimeError(f"[{self.name}] exited during startup with code {self.proc.returncode}; "
                                   f"see {self.dir / 'stderr.log'}")
            try:
                b = Bridge(self.port).connect()
                info = b.info()
                if info["game_mode"] != "bootstrap":
                    self._bridge = b
                    return
                b.close()
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"[{self.name}] debug bridge not ready on port {self.port}")
            time.sleep(0.25)

    @property
    def bridge(self) -> Bridge:
        if self._bridge is None:
            self._bridge = Bridge(self.port).connect(wait=10)
        return self._bridge

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 15.0) -> int | None:
        if self.proc is None:
            return None
        if self.alive():
            try:
                self.bridge.quit()
            except Exception:
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self._bridge:
            self._bridge.close()
            self._bridge = None
        for f in (self._stdout, self._stderr):
            if f:
                f.close()
        return self.proc.returncode

    def __enter__(self) -> "Instance":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ---- artefacts ----

    def crash_files(self) -> list[Path]:
        pats = ["crash*", "desync*", "commands-out*.log", "random-out*.log"]
        found: list[Path] = []
        for d in [self.dir, self.dir / "autosave", self.dir / "save"]:
            for pat in pats:
                found += [p for p in d.glob(pat) if p.is_file()]
        return sorted(set(found))

    def tail(self, which: str = "stderr", lines: int = 40) -> str:
        path = self.dir / f"{which}.log"
        if not path.exists():
            return ""
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
