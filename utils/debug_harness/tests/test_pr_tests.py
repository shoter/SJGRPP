"""Unit tests of pr_tests.py: python -m unittest discover -s utils/debug_harness/tests"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pr_tests as pt  # noqa: E402

CPP = '''
#include "../3rdparty/catch2/catch.hpp"

/* TEST_CASE("commented out") { } */
// TEST_CASE("also commented out") { }

/** Splitting fees: the remainder goes to the owner. */
TEST_CASE("Fees - split")
{
	const char *brace = "{ not a block }";
	char c = '}';
	CHECK(Split(10) == 1'000);
	SECTION("even") {
		CHECK(true);
		SECTION("nested") { CHECK(true); }
	}
	SECTION("odd") { CHECK(true); }
}

TEST_CASE("Fees - \\"quoted\\" name", "[fees]")
{
	auto s = R"x(TEST_CASE("in a raw string") { )x";
}
'''

SCENARIO = '''"""
Sharing fees: a bus pays at a foreign station.

Second paragraph.
"""

def helper(ctx, n):
    ctx.check(n > 0, f"bus loaded {n} times")


def run(ctx):
    helper(ctx, 1)
    ctx.check(True, "fees scale with inflation")
    ctx.check_eq(1, 1, "fee per tick")
    ctx.check(True, f"{x}")
'''

FULL = '''
PROFILES = {
    # a short run
    "quick": {"months": 3},
    "normal": {"months": 24},
}


class FullTest:
    def final_checks(self):
        self.check(True, "no desync")
        self.check(True, f"client{i}: company exists", "detail")
        other.check(True, "not a full test check")
'''

UNITTEST = '''
import unittest


class TestThing(unittest.TestCase):
    def test_one(self):
        """Adds one."""
        self.assertEqual(1, 1)

    def helper(self):
        pass


class NotATest:
    def test_no(self):
        pass
'''

REG_CMAKE = 'include(CreateRegression)\ncreate_regression("ai"\n    info.nut\n)\n'
REG_INFO = 'class R extends AIInfo { function GetDescription() { return "AI API regression"; } }'


def files(**overrides) -> dict[str, str]:
    base = {
        "src/tests/fees.cpp": CPP,
        "utils/debug_harness/scenarios/sharing.py": SCENARIO,
        "utils/debug_harness/full_test.py": FULL,
        "utils/debug_harness/tests/test_thing.py": UNITTEST,
        "regression/regression/CMakeLists.txt": REG_CMAKE,
        "regression/regression/info.nut": REG_INFO,
        "regression/regression/main.nut": "main",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def snap(fs: dict[str, str]) -> pt.Snapshot:
    return pt.snapshot(pt.DictTree(fs))


class TestParsing(unittest.TestCase):
    def test_cpp_cases_and_sections(self):
        tests = pt.parse_cpp("src/tests/fees.cpp", CPP)
        cases = [t for t in tests if t.category == "cpp"]
        sections = [t.id for t in tests if t.category == "cpp_section"]
        self.assertEqual([t.id for t in cases], ["Fees - split", 'Fees - "quoted" name'])
        self.assertEqual(sections, ["Fees - split / even", "Fees - split / even / nested", "Fees - split / odd"])
        self.assertEqual(cases[0].description, "Splitting fees: the remainder goes to the owner.")
        self.assertEqual(cases[0].line, 8)

    def test_cpp_comment_change_is_not_a_change(self):
        a = pt.parse_cpp("a.cpp", CPP)[0].body
        b = pt.parse_cpp("a.cpp", CPP.replace("CHECK(true);\n\t\tSECTION", "CHECK(true); // why\n\t\tSECTION"))[0].body
        c = pt.parse_cpp("a.cpp", CPP.replace("Split(10)", "Split(11)"))[0].body
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_scenario_checks(self):
        tests = pt.parse_scenario("utils/debug_harness/scenarios/sharing.py", SCENARIO)
        scenario = tests[0]
        self.assertEqual(scenario.id, "sharing")
        self.assertEqual(scenario.description, "Sharing fees: a bus pays at a foreign station.")
        self.assertEqual([c.message for c in scenario.checks],
                         ["bus loaded {n} times", "fees scale with inflation", "fee per tick", "{x}"])
        by_message = {c.message: c for c in scenario.checks}
        self.assertTrue(by_message["bus loaded {n} times"].regex.match("bus loaded 40 times"))
        self.assertFalse(by_message["bus loaded {n} times"].regex.match("bus unloaded 40 times"))
        # check_eq appends the values to the message
        self.assertTrue(by_message["fee per tick"].regex.match("fee per tick (expected 1, got 1)"))
        self.assertIsNone(by_message["{x}"].regex)   # nothing literal to match
        self.assertEqual(by_message["bus loaded {n} times"].function, "helper")
        self.assertEqual(sum(t.category == "scenario_check" for t in tests), 4)

    def test_full_test_profiles_and_checks(self):
        tests = pt.parse_full_test("utils/debug_harness/full_test.py", FULL)
        profiles = {t.id: t for t in tests if t.category == "full_profile"}
        checks = [t.check for t in tests if t.category == "full_check"]
        self.assertEqual(list(profiles), ["quick", "normal"])
        self.assertEqual(profiles["quick"].description, "a short run")
        self.assertEqual(profiles["quick"].body, "{'months': 3}")
        self.assertEqual([c.message for c in checks], ["no desync", "client{i}: company exists"])
        self.assertEqual(checks[0].function, "final_checks")

    def test_unittests(self):
        tests = pt.parse_unittest("utils/debug_harness/tests/test_thing.py", UNITTEST)
        self.assertEqual([(t.id, t.description) for t in tests], [("test_thing.TestThing.test_one", "Adds one.")])

    def test_snapshot(self):
        s = snap(files())
        self.assertEqual(len(s["cpp"]), 2)
        self.assertEqual(len(s["cpp_section"]), 3)
        self.assertEqual(list(s["scenario"]), ["sharing"])
        self.assertEqual(len(s["scenario_check"]), 4)
        self.assertEqual(list(s["full_profile"]), ["quick", "normal"])
        self.assertEqual(list(s["unittest"]), ["test_thing.TestThing.test_one"])
        self.assertEqual(list(s["regression"]), ["ai"])
        self.assertEqual(s["regression"]["ai"].description, "AI API regression")

    def test_unparsable_python_does_not_break_the_report(self):
        s = snap(files(**{"utils/debug_harness/scenarios/broken.py": "def run(ctx:\n"}))
        self.assertIn("broken", s["scenario"])


class TestCompare(unittest.TestCase):
    def changes(self, base: dict, head: dict) -> dict[str, list[pt.Change]]:
        return pt.compare(snap(base), snap(head))

    @staticmethod
    def kinds(changes: list[pt.Change]) -> list[tuple[str, str]]:
        return [(c.kind, c.test.id) for c in changes]

    def test_no_changes(self):
        self.assertFalse(any(self.changes(files(), files()).values()))

    def test_added_modified_removed(self):
        head = files(**{
            "src/tests/fees.cpp": CPP.replace("Split(10)", "Split(11)") + '\nTEST_CASE("Fees - new") { }\n',
            "utils/debug_harness/scenarios/sharing.py": SCENARIO.replace('"fee per tick"', '"fee per day"'),
            "utils/debug_harness/full_test.py": FULL.replace('"months": 24', '"months": 12'),
            "utils/debug_harness/tests/test_thing.py": None,
            "regression/regression/main.nut": "changed",
        })
        c = self.changes(files(), head)
        self.assertEqual(self.kinds(c["cpp"]), [("added", "Fees - new"), ("modified", "Fees - split")])
        self.assertEqual(self.kinds(c["scenario"]), [("modified", "sharing")])
        self.assertEqual(self.kinds(c["scenario_check"]),
                         [("added", "sharing: fee per day"), ("removed", "sharing: fee per tick")])
        self.assertEqual(self.kinds(c["full_profile"]), [("modified", "normal")])
        self.assertEqual(self.kinds(c["unittest"]), [("removed", "test_thing.TestThing.test_one")])
        self.assertEqual(self.kinds(c["regression"]), [("modified", "ai")])

    def test_renames(self):
        head = files(**{
            "src/tests/fees.cpp": CPP.replace('"Fees - split"', '"Fees - split evenly"'),
            "utils/debug_harness/scenarios/sharing.py": None,
            "utils/debug_harness/scenarios/sharing_fees.py": SCENARIO,
        })
        c = self.changes(files(), head)
        self.assertIn(("renamed", "Fees - split evenly"), self.kinds(c["cpp"]))
        self.assertEqual(self.kinds(c["scenario"]), [("renamed", "sharing_fees")])
        self.assertEqual(c["scenario"][0].old.id, "sharing")
        self.assertEqual(c["scenario_check"], [])   # the checks moved with the scenario


def write_results(root: Path) -> Path:
    """A results directory as downloaded by pr-tests.yml from a Game tests run."""
    unit = root / "pr-tests-unit"
    unit.mkdir(parents=True)
    (unit / "ctest-junit.xml").write_text(
        '<?xml version="1.0"?><testsuite tests="3">'
        '<testcase name="Fees - split" time="0.01" status="run"/>'
        '<testcase name="Fees - new" time="0.02" status="fail"><failure message="Failed"/></testcase>'
        '<testcase name="regression_ai" time="30" status="run"/>'
        '</testsuite>', encoding="utf-8")
    (unit / "harness-unittests.json").write_text(json.dumps(
        {"tests": [{"id": "test_thing.TestThing.test_one", "state": "pass", "seconds": 0.001}]}), encoding="utf-8")
    scenario = root / "game-test-scenarios" / "sharing"
    scenario.mkdir(parents=True)
    (scenario / "result.json").write_text(json.dumps({
        "scenario": "sharing", "ok": False, "seconds": 12.5, "error": "CheckFailed: fee per day (expected 1, got 2)",
        "checks": [{"ok": True, "message": "bus loaded 40 times"}, {"ok": True, "message": "fees scale with inflation"},
                   {"ok": False, "message": "fee per day (expected 1, got 2)"}]}), encoding="utf-8")
    full = root / "game-test-quick" / "quick"
    full.mkdir(parents=True)
    (full / "report.json").write_text(json.dumps({
        "profile": "quick", "ok": True, "seconds": 300,
        "checks": [{"ok": True, "name": "no desync", "severity": "fail"},
                   {"ok": True, "name": "client1: company exists", "severity": "fail"},
                   {"ok": False, "name": "client2: company exists", "severity": "warn"}]}), encoding="utf-8")
    return root


class TestResults(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.results = pt.load_results(write_results(self.tmp), {"scenarios", "quick", "normal"})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def status(self, category: str, test_id: str, fs: dict | None = None) -> pt.Status:
        test = snap(fs or files())[category][test_id]
        return pt.status_of(pt.Change("added", test), self.results)

    def test_pending_without_results(self):
        test = snap(files())["cpp"]["Fees - split"]
        self.assertEqual(pt.status_of(pt.Change("added", test), None).state, "pending")
        self.assertIsNone(pt.status_of(pt.Change("removed", test), self.results))

    def test_cpp_regression_and_unittest(self):
        fs = files(**{"src/tests/fees.cpp": CPP + '\nTEST_CASE("Fees - new") { }\nTEST_CASE("Fees - unlisted") { }\n'})
        self.assertEqual(self.status("cpp", "Fees - split").state, "pass")
        self.assertEqual(self.status("cpp", "Fees - new", fs).state, "fail")
        self.assertEqual(self.status("cpp", "Fees - unlisted", fs).state, "notrun")
        self.assertEqual(self.status("cpp_section", "Fees - split / even").state, "pass")
        self.assertEqual(self.status("regression", "ai").seconds, 30.0)
        self.assertEqual(self.status("unittest", "test_thing.TestThing.test_one").state, "pass")

    def test_scenario_and_its_checks(self):
        fs = files(**{"utils/debug_harness/scenarios/sharing.py": SCENARIO.replace('"fee per tick"', '"fee per day"')
                      + '    ctx.check(True, "after the failure")\n'})
        s = self.status("scenario", "sharing", fs)
        self.assertEqual((s.state, s.seconds), ("fail", 12.5))
        self.assertEqual(self.status("scenario_check", "sharing: bus loaded {n} times", fs).state, "pass")
        self.assertEqual(self.status("scenario_check", "sharing: fee per day", fs).state, "fail")
        self.assertEqual(self.status("scenario_check", "sharing: after the failure", fs).state, "unreached")
        self.assertEqual(self.status("scenario_check", "sharing: {x}", fs).state, "unknown")

    def test_missing_scenario_and_profile(self):
        fs = files(**{"utils/debug_harness/scenarios/other.py": SCENARIO})
        self.assertEqual(self.status("scenario", "other", fs).state, "missing")
        self.assertEqual(self.status("full_profile", "quick").state, "pass")
        self.assertEqual(self.status("full_profile", "normal").state, "missing")   # expected, no report: job died
        fs = files(**{"utils/debug_harness/full_test.py": FULL.replace('"normal"', '"max-28"')})
        self.assertEqual(self.status("full_profile", "max-28", fs).state, "notrun")   # not in this run

    def test_full_checks_by_profile(self):
        s = self.status("full_check", "client{i}: company exists")
        self.assertEqual(s.state, "warn")
        self.assertIn("quick", s.detail)
        self.assertEqual(self.status("full_check", "no desync").state, "pass")

    def test_missing_unit_results(self):
        shutil.rmtree(self.tmp / "pr-tests-unit")
        self.results = pt.load_results(self.tmp, {"scenarios"})
        self.assertEqual(self.status("cpp", "Fees - split").state, "missing")
        self.assertEqual(self.status("unittest", "test_thing.TestThing.test_one").state, "missing")


class TestReport(unittest.TestCase):
    def render(self, base: dict, head: dict, results=None, game_files=None) -> str:
        head_snap = snap(head)
        return pt.Report(pt.compare(snap(base), head_snap), head_snap, results, "abc1234567", "https://example/repo",
                         None, game_files or []).render()

    def test_new_tests_listed_with_links(self):
        head = files(**{"src/tests/fees.cpp": CPP + '\nTEST_CASE("Fees - new") { }\n'})
        text = self.render(files(), head)
        self.assertIn("**+1 C++ test case**", text)
        self.assertIn("`Fees - new`", text)
        self.assertIn("https://example/repo/blob/abc1234567/src/tests/fees.cpp#L", text)
        self.assertIn("⏳ Results of the 1 new and changed tests", text)
        self.assertNotIn("[!WARNING]", text)

    def test_warns_when_game_code_changes_without_tests(self):
        text = self.render(files(), files(), game_files=["src/economy.cpp"])
        self.assertIn("This PR adds, changes and removes no tests.", text)
        self.assertIn("[!WARNING]", text)
        self.assertIn("1 file under `src/`", text)

    def test_harness_unittests_alone_do_not_count_as_game_tests(self):
        head = files(**{"utils/debug_harness/tests/test_thing.py": UNITTEST.replace(
            "    def helper(self):", "    def test_two(self):\n        pass\n\n    def helper(self):")})
        text = self.render(files(), head, game_files=["src/economy.cpp"])
        self.assertIn("test_thing.TestThing.test_two", text)
        self.assertIn("[!WARNING]", text)

    def test_results_in_headline(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            results = pt.load_results(write_results(tmp), {"scenarios", "quick"})
            head = files(**{"src/tests/fees.cpp": CPP.replace("Split(10)", "Split(11)") + '\nTEST_CASE("Fees - new") { }\n'})
            text = self.render(files(), head, results)
            self.assertIn("❌ 1 failed (`Fees - new`)", text)
            self.assertIn("✅ 1 passed", text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pipes_in_names_are_escaped(self):
        head = files(**{"src/tests/fees.cpp": CPP + '\nTEST_CASE("a | b") { }\n'})
        self.assertIn("`a \\| b`", self.render(files(), head))


@unittest.skipIf(shutil.which("git") is None, "git not found")
class TestGit(unittest.TestCase):
    """The report command on a real repository: merge base, changed files and reading revisions."""

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, fs: dict[str, str], message: str) -> str:
        for path, text in fs.items():
            p = self.repo / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.git("init", "-q")
        self.base = self.commit(files(), "base")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_report_between_revisions(self):
        self.git("checkout", "-q", "-b", "feature")
        head = self.commit({"src/tests/fees.cpp": CPP + '\nTEST_CASE("Fees - new") { }\n', "src/fees.cpp": "x"},
                           "feature")
        self.git("checkout", "-q", self.base)
        self.commit({"README": "main moved on"}, "main")
        main = self.git("rev-parse", "HEAD")
        old_root = pt.ROOT
        pt.ROOT = self.repo
        try:
            text = pt.build_report(main, head, None, None, None)
        finally:
            pt.ROOT = old_root
        self.assertIn("**+1 C++ test case**", text)
        self.assertIn("`Fees - new`", text)
        self.assertNotIn("[!WARNING]", text)


if __name__ == "__main__":
    unittest.main()
