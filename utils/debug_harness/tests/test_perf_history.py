"""Unit tests of perf_history.py: python -m unittest discover -s utils/debug_harness/tests"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ci_report  # noqa: E402
import perf_history as ph  # noqa: E402


def write_results(root: Path, tests: dict[str, tuple[bool, dict]]) -> Path:
    """A results directory as downloaded by the workflows: game-test-<name>/report.json per full test."""
    for name, (ok, perf) in tests.items():
        d = root / f"game-test-{name}"
        d.mkdir(parents=True)
        (d / "report.json").write_text(json.dumps({"ok": ok, "seconds": 10, "checks": [], "perf": perf, "routes": {},
                                                   "profile": name, "args": {"clients": 3}}), encoding="utf-8")
    return root


def entry(sha: str, perf: dict, ok: bool = True, test: str = "quick") -> dict:
    return {"sha": sha, "date": "2026-10-17T10:00:00+00:00", "subject": sha, "run_url": None,
            "tests": {test: {"ok": ok, "seconds": 10, "perf": perf}}}


class ResultsTest(unittest.TestCase):
    def test_full_results_keeps_full_tests_and_numeric_perf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_results(Path(tmp), {"quick": (True, {"game_loop_ms_avg": 0.5, "join_seconds": {"c1": 1.5, "c2": 4.0},
                                                              "label": "text", "commands": 10})})
            scen = root / "game-test-scenarios" / "solo"
            scen.mkdir(parents=True)
            (scen / "result.json").write_text(json.dumps({"scenario": "solo", "ok": True, "checks": []}), encoding="utf-8")
            res = ph.full_results(root)
        self.assertEqual(list(res), ["quick"])
        self.assertTrue(res["quick"]["ok"])
        self.assertEqual(res["quick"]["perf"], {"game_loop_ms_avg": 0.5, "commands": 10.0, "join_seconds_max": 4.0})


def write_run(root: Path, name: str, run: int, ok: bool, perf: dict, checks: list | None = None) -> None:
    """One parallel CI run of a full test, as downloaded: game-test-<name>-run<n>/game-test-<name>/run-<n>/<run>/."""
    d = root / f"game-test-{name}-run{run}" / f"game-test-{name}" / f"run-{run}" / f"{name}-20261021"
    d.mkdir(parents=True)
    (d / "report.json").write_text(json.dumps({"ok": ok, "seconds": 10 * run, "checks": checks or [], "perf": perf,
                                               "routes": {}, "profile": name, "args": {"clients": 3}}),
                                   encoding="utf-8")


class RepeatsTest(unittest.TestCase):
    def test_matrix_runs_full_tests_repeatedly_and_scenarios_once(self):
        jobs = ci_report.matrix_jobs("scenarios, quick,,max-28", 3)
        self.assertEqual(jobs[0], {"test": "scenarios", "run": 1, "runs": 1})
        self.assertEqual([(j["test"], j["run"], j["runs"]) for j in jobs[1:]],
                         [("quick", 1, 3), ("quick", 2, 3), ("quick", 3, 3),
                          ("max-28", 1, 3), ("max-28", 2, 3), ("max-28", 3, 3)])
        self.assertEqual(ci_report.matrix_jobs("quick", 0), [{"test": "quick", "run": 1, "runs": 1}])

    def test_mean_perf(self):
        perf = ci_report.mean_perf([
            {"game_loop_ms_avg": 1.0, "commands": 10, "vehicles_max": 5, "join_seconds": {"c1": 1.0}, "label": "a"},
            {"game_loop_ms_avg": 2.0, "commands": 20, "vehicles_max": 6, "join_seconds": {"c1": 3.0, "c2": 4.0}},
            {"game_loop_ms_avg": 3.0, "commands": 30, "vehicles_max": 6, "label": "c"},
        ])
        self.assertAlmostEqual(perf["game_loop_ms_avg"], 2.0)
        self.assertEqual(perf["commands"], 20)
        self.assertIsInstance(perf["commands"], int)
        self.assertAlmostEqual(perf["vehicles_max"], 17 / 3)
        self.assertEqual(perf["join_seconds"], {"c1": 2.0, "c2": 4.0})
        self.assertEqual(perf["label"], "a")

    def test_parallel_runs_are_averaged_and_fail_if_any_run_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_run(root, "quick", 1, True, {"game_loop_ms_avg": 1.0, "join_seconds": {"c1": 2.0}})
            write_run(root, "quick", 2, True, {"game_loop_ms_avg": 2.0, "join_seconds": {"c1": 4.0}})
            write_run(root, "quick", 3, True, {"game_loop_ms_avg": 3.0, "join_seconds": {"c1": 6.0}})
            write_run(root, "normal", 1, True, {"game_loop_ms_avg": 5.0}, [{"ok": True, "name": "desync"}])
            write_run(root, "normal", 2, False, {"game_loop_ms_avg": 7.0}, [{"ok": False, "name": "desync"}])
            loaded = ci_report.load_results(root)
            res = ph.full_results(root)
            report = ci_report.render(loaded, ["quick", "normal"], "Game tests", None)
        self.assertEqual(res["quick"], {"ok": True, "seconds": 30, "runs": 3,
                                        "perf": {"game_loop_ms_avg": 2.0, "join_seconds_max": 4.0}})
        self.assertFalse(res["normal"]["ok"])
        self.assertEqual(res["normal"]["runs"], 2)
        self.assertAlmostEqual(res["normal"]["perf"]["game_loop_ms_avg"], 6.0)
        self.assertEqual([c["name"] for c in loaded["normal"]["checks"]], ["run 1: desync", "run 2: desync"])
        self.assertIn("average of 3 parallel runs", report)
        self.assertIn("run 2: desync", report)

    def test_single_run_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_results(Path(tmp), {"quick": (True, {"game_loop_ms_avg": 0.5})})
            res = ph.full_results(root)
        self.assertEqual(res["quick"]["runs"], 1)
        self.assertEqual(res["quick"]["perf"], {"game_loop_ms_avg": 0.5})

    def test_compare_names_the_number_of_runs(self):
        h = {"entries": [entry("a" * 40, {"game_loop_ms_avg": 1.0})]}
        pr = {"quick": {"ok": True, "seconds": 10, "runs": 3, "perf": {"game_loop_ms_avg": 1.0}}}
        text = ph.render_compare(h, pr, "a" * 40, ["quick"], None, None)
        self.assertIn("averages of 3 parallel runs (PR) and 1 run (`main`)", text)


class HistoryTest(unittest.TestCase):
    def test_record_appends_and_replaces_same_commit(self):
        h = {"entries": []}
        ph.record(h, {"quick": {"ok": True, "perf": {"game_loop_ms_avg": 1.0}}}, "a" * 40, "2026-10-01", "First\nbody", None)
        ph.record(h, {"quick": {"ok": True, "perf": {"game_loop_ms_avg": 2.0}}}, "b" * 40, "2026-10-02", "Second", None)
        ph.record(h, {"quick": {"ok": True, "perf": {"game_loop_ms_avg": 3.0}}}, "a" * 40, "2026-10-03", "Rerun", None)
        self.assertEqual([e["sha"][0] for e in h["entries"]], ["b", "a"])
        self.assertEqual(h["entries"][-1]["tests"]["quick"]["perf"]["game_loop_ms_avg"], 3.0)
        self.assertEqual(h["entries"][-1]["subject"], "Rerun")
        ph.record(h, {}, "c" * 40, None, "One\ntwo", None)
        self.assertEqual(h["entries"][-1]["subject"], "One")

    def test_history_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "history.json"
            self.assertEqual(ph.load_history(path)["entries"], [])
            h = {"entries": [entry("a" * 40, {"game_loop_ms_avg": 1.0})]}
            ph.save_history(h, path)
            self.assertEqual(ph.load_history(path)["entries"], h["entries"])

    def test_base_run_prefers_base_commit_and_skips_failed_runs(self):
        h = {"entries": [entry("a" * 40, {"x": 1}), entry("b" * 40, {"x": 2}), entry("c" * 40, {"x": 3}, ok=False)]}
        self.assertEqual(ph.base_run(h, "quick", "a" * 40)["sha"], "a" * 40)
        self.assertEqual(ph.base_run(h, "quick", "f" * 40)["sha"], "b" * 40)  # unknown base: latest passing
        self.assertEqual(ph.base_run(h, "quick", "c" * 40)["sha"], "b" * 40)  # base run failed
        self.assertIsNone(ph.base_run(h, "normal", None))


class VerdictTest(unittest.TestCase):
    def test_noise_band(self):
        self.assertEqual(ph.noise_band([], 10.0, 0.05), 0.5)              # 5% floor
        self.assertEqual(ph.noise_band([], 0.01, 0.05), 0.05)             # absolute floor
        self.assertAlmostEqual(ph.noise_band([8.0, 10.0, 12.0], 10.0, 0.05), 4.0)  # 2 standard deviations

    def test_verdict_directions(self):
        self.assertEqual(ph.verdict("game_loop_ms_avg", 1.0, 1.5, 0.1), "🔴 worse")
        self.assertEqual(ph.verdict("game_loop_ms_avg", 1.0, 0.5, 0.1), "🟢 better")
        self.assertEqual(ph.verdict("game_loop_ms_avg", 1.0, 1.05, 0.1), "≈")
        self.assertEqual(ph.verdict("speed_ratio_median", 1.0, 0.8, 0.02), "🔴 worse")
        self.assertEqual(ph.verdict("vehicles_max", 100, 300, 0.0), "≈")  # workload, never judged

    def test_delta(self):
        self.assertEqual(ph.delta(2.0, 3.0), "+50.0%")
        self.assertEqual(ph.delta(0, 0), "–")


class CompareTest(unittest.TestCase):
    def history(self) -> dict:
        return {"entries": [entry(f"{i:040x}", {"game_loop_ms_avg": 1.0 + i * 0.01, "server_mem_mb_max": 100.0,
                                                 "vehicles_max": 50}) for i in range(5)]}

    def test_without_history(self):
        text = ph.render_compare({"entries": []}, {"quick": {"ok": True, "perf": {"game_loop_ms_avg": 1.0}}},
                                 None, ["quick"], None, None)
        self.assertIn("No performance history from `main` yet", text)
        self.assertIn("| Game loop avg | 1.00 ms |", text)

    def test_without_full_results(self):
        self.assertIn("No full test results", ph.render_compare(self.history(), {}, None, [], None, None))

    def test_regression_is_flagged_with_trend_chart(self):
        h = self.history()
        base = h["entries"][2]["sha"]
        pr = {"quick": {"ok": True, "perf": {"game_loop_ms_avg": 2.0, "server_mem_mb_max": 101.0, "vehicles_max": 60}}}
        text = ph.render_compare(h, pr, base, ["quick"], "https://run", "https://github.com/o/r")
        self.assertIn(f"[`{base[:7]}`](https://github.com/o/r/commit/{base})", text)
        self.assertIn("| Game loop avg | 1.02 ms | 2.00 ms | +96.1% | 🔴 worse |", text)
        self.assertIn("| Server memory max | 100.0 MB | 101.0 MB | +1.0% | ≈ |", text)
        self.assertIn("| Vehicles (max) | 50 | 60 | +20.0% | _workload_ |", text)
        self.assertIn("🔴 1 worse / 0 better", text)
        self.assertIn("```mermaid", text)
        self.assertIn('"PR"]', text)
        self.assertIn("line [1, 1.01, 1.02, 1.03, 1.04, 2]", text)

    def test_unknown_base_falls_back_to_latest(self):
        h = self.history()
        text = ph.render_compare(h, {"quick": {"ok": True, "perf": {"game_loop_ms_avg": 1.0}}}, "f" * 40, [], None, None)
        self.assertIn(f"`{h['entries'][-1]['sha'][:7]}` (latest recorded; base `fffffff` has no run)", text)

    def test_test_missing_on_main(self):
        text = ph.render_compare(self.history(), {"max-28": {"ok": True, "perf": {"game_loop_ms_avg": 3.0}}},
                                 None, ["max-28"], None, None)
        self.assertIn("No passing `main` run of `max-28`", text)


class SiteTest(unittest.TestCase):
    def test_site_writes_valid_svgs_and_readme(self):
        h = {"entries": [entry(f"{i:040x}", {k: 1.0 + i for k in ph.TREND_METRICS}) for i in range(3)]
             + [entry("e" * 40, {"game_loop_ms_avg": 9.0}, test="normal")]}
        with tempfile.TemporaryDirectory() as tmp:
            files = ph.write_site(h, Path(tmp), "https://github.com/o/r")
            self.assertEqual(len(files), len(ph.TREND_METRICS) + 1)
            for f in files[:-1]:
                root = ET.parse(f).getroot()
                self.assertTrue(root.tag.endswith("svg"))
            svg = (Path(tmp) / "charts" / "game_loop_ms_avg.svg").read_text(encoding="utf-8")
            self.assertIn("quick - latest 3.00 ms", svg)
            self.assertIn("normal - latest 9.00 ms", svg)
            readme = (Path(tmp) / "README.md").read_text(encoding="utf-8")
            self.assertIn("## quick", readme)
            self.assertIn("## normal", readme)
            self.assertIn("![Game loop avg](charts/game_loop_ms_avg.svg)", readme)
            self.assertIn("[`eeeeeee`](https://github.com/o/r/commit/" + "e" * 40 + ")", readme)

    def test_chart_without_runs(self):
        svg = ph.svg_chart({"entries": []}, ["quick"], "game_loop_ms_avg")
        self.assertIn("no runs yet", svg)
        ET.fromstring(svg)


if __name__ == "__main__":
    unittest.main()
