"""
GUI exerciser: opens many game windows on a (headless) client and pokes at them, to catch crashes in drawing code
that only show up with a particular game state (e.g. the smallmap owner view on a 28-company map).

    from gui_exercise import GuiExerciser
    ex = GuiExerciser(bridge, rng=random.Random(1))
    ex.smallmap()          # every map mode, fully zoomed out (draws every tile), scrolled, toggles
    ex.windows(12)         # 12 random windows from the 'show' list, safe buttons clicked
    ex.close_all()

Only windows whose buttons don't change the game are clicked; others are just opened, drawn and closed. A crash
shows as a dead process / bridge error, which the caller's health check reports.
"""

from __future__ import annotations

import random

from ottd import Bridge, BridgeError, widget_types

# SmallMapWidgets
SM_ZOOM_IN, SM_ZOOM_OUT = 5, 6
SM_MODES = {"contour": 7, "vehicles": 8, "industries": 9, "linkstats": 10, "routes": 11, "vegetation": 12, "owners": 13}
SM_TOGGLES = [14, 15, 17, 18, 19, 20, 23, 24]  # centre, town names, enable/disable all, height, industry names, hide served, heat
SM_MAP = 2
SM_CENTERMAP = 14

# Windows whose buttons are pure views (sorting, tabs, graph toggles, scrolling): safe to click everything.
CLICK_SAFE = {"towns", "industries", "industry_cargoes", "subsidies", "messages", "framerate", "linkgraph_legend",
              "cargo_payment_rates", "company_value_graph", "income_graph", "operating_profit_graph",
              "delivered_cargo_graph", "performance_graph", "performance_detail", "performance_league", "league",
              "finances", "town", "station_cargo", "departures"}
KEEP_OPEN = {"MainWindow", "MainToolbar", "Statusbar", "NewsWindow", "SendNetworkMsg", "NetworkStatusWindow"}
CLICK_TYPES = {"WWT_PUSHTXTBTN", "WWT_PUSHIMGBTN", "WWT_TEXTBTN", "WWT_TEXTBTN_2", "WWT_IMGBTN", "WWT_IMGBTN_2",
               "WWT_DROPDOWN", "NWID_BUTTON_DROPDOWN", "WWT_TEXT", "WWT_LABEL"}


class GuiExerciser:
    def __init__(self, bridge: Bridge, rng: random.Random | None = None, log=None):
        self.b = bridge
        self.rng = rng or random.Random()
        self.log = log or (lambda s: None)
        self.opened = 0
        self.clicks = 0
        self.errors: list[str] = []
        self._names: list[str] | None = None

    def names(self) -> list[str]:
        if self._names is None:
            self._names = self.b.call("show")
        return self._names

    def _frames(self, n: int = 1) -> None:
        self.b.wait_frames(n)

    def show(self, name: str, ident: int = -1) -> dict | None:
        """Open a window and return its description (the newest window), or None if there was nothing to show."""
        before = {(w["class"], w["number"]) for w in self.b.windows()}
        try:
            self.b.call("show", name, ident)
        except BridgeError as e:
            if "nothing to show" not in str(e):
                self.errors.append(f"show {name}: {e}")
            return None
        self._frames(2)
        self.opened += 1
        new = [w for w in self.b.windows() if (w["class"], w["number"]) not in before]
        return new[-1] if new else None

    def click_buttons(self, win: dict, limit: int = 12) -> None:
        types = widget_types()
        wanted = {types[t] for t in CLICK_TYPES if t in types}
        try:
            widgets = self.b.widgets(win["class"], win["number"])["widgets"]
        except BridgeError:
            return  # closed in the meantime
        cands = [w for w in widgets if w["type"] in wanted and w.get("role") is None and w["width"] > 0 and w["height"] > 0
                 and not w.get("disabled")]
        for w in self.rng.sample(cands, min(limit, len(cands))):
            try:
                self.b.click_widget(win["class"], win["number"], w["index"])
                self.clicks += 1
            except BridgeError:
                break  # window closed by the click
            self._frames(1)
            self.b.key("esc")  # dismiss a dropdown or query the click may have opened

    def smallmap(self, focus_tiles: list[int] | None = None) -> None:
        """
        Every smallmap mode, zoomed fully out, plus scrolling and the toggles. Zoomed out, a pixel shows only the most
        important tile of its block, so focus_tiles (e.g. depots, stations) are also drawn at full zoom.
        """
        win = self.show("smallmap")
        if win is None:
            win = next((w for w in self.b.windows() if w["class_name"] == "SmallMap"), None)
        if win is None:
            self.errors.append("smallmap did not open")
            return
        cls, num = win["class"], win["number"]
        modes = list(SM_MODES.items())
        self.rng.shuffle(modes)
        for name, mode in modes:
            self.b.click_widget(cls, num, mode)
            for _ in range(6):
                self.b.click_widget(cls, num, SM_ZOOM_OUT)
                self._frames(1)
            self.b.screenshot()  # forces a full redraw of the whole map in this mode
            for _ in range(2):
                self.b.click_widget(cls, num, SM_ZOOM_IN)
                self._frames(1)
            self._scroll_map(cls, num)
            for tile in (focus_tiles or [])[:8]:
                self.b.console(f"scrollto {tile}")
                self.b.click_widget(cls, num, SM_CENTERMAP)
                for _ in range(6):
                    self.b.click_widget(cls, num, SM_ZOOM_IN)
                self.b.screenshot()
            for t in self.rng.sample(SM_TOGGLES, 3):
                try:
                    self.b.click_widget(cls, num, t)
                except BridgeError:
                    pass  # toggle not present in this mode
                self._frames(1)
            self.clicks += 12
        self.log(f"smallmap: {len(modes)} modes drawn")

    def _scroll_map(self, cls: int, num: int) -> None:
        try:
            m = next(w for w in self.b.widgets(cls, num)["widgets"] if w["index"] == SM_MAP)
        except (StopIteration, BridgeError):
            return
        cx, cy = m["x"] + m["width"] // 2, m["y"] + m["height"] // 2
        dx, dy = m["width"] // 3, m["height"] // 3
        self.b.drag(cx, cy, cx + dx, cy + dy, 5)
        self.b.drag(cx, cy, cx - dx, cy - dy, 5)
        self.b.wheel(cx, cy, -1)
        self._frames(1)

    def windows(self, count: int) -> None:
        names = [n for n in self.names() if n != "smallmap"]
        for _ in range(count):
            name = self.rng.choice(names)
            win = self.show(name, self.rng.randrange(0, 1000))
            if win is not None and name in CLICK_SAFE:
                self.click_buttons(win)
            self.b.screenshot()

    def close_all(self) -> None:
        self.b.key("esc")
        for w in self.b.windows():
            if w["class_name"] not in KEEP_OPEN:
                try:
                    self.b.close_window(w["class"], w["number"])
                except BridgeError:
                    pass
        self._frames(1)

    def round(self, windows: int = 10) -> None:
        """One exercise round: smallmap in all modes, some random windows, then close everything."""
        try:
            self.smallmap()
            self.windows(windows)
        finally:
            self.close_all()
