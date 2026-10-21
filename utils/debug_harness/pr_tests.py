#!/usr/bin/env python3
"""
The tests a pull request adds, changes and removes, with their results once they ran, as one Markdown report for the
PR comment (.github/workflows/pr-tests.yml).

    pr_tests.py report --base <rev> --head <rev> [--results DIR] [--expect scenarios,quick,...]
                       [--repo-url URL] [--run-url URL] [--out pr-tests.md]
    pr_tests.py unittest [--out harness-unittests.json]

report compares the merge base of <base> and <head> with <head> and lists:
  - C++ unit tests: Catch2 TEST_CASEs and their SECTIONs in src/tests/
  - harness scenarios (utils/debug_harness/scenarios/*.py) and the ctx.check() messages in each
  - full test profiles (PROFILES in full_test.py) and its self.check() names
  - harness unit tests (utils/debug_harness/tests/test_*.py)
  - AI/GS regression tests (regression/*/ with create_regression)
Without --results every result is pending. With --results (the artifacts of a Game tests run) each test gets its result:
ctest JUnit XML (Catch2 test cases and regression tests), harness-unittests.json (written by the unittest command),
scenario.py result.json and full_test.py report.json files. Tests named in --expect without results are reported as
missing (job crashed or was cancelled). Exit code is 0; the test jobs themselves carry pass/fail.

unittest runs the harness unit tests like `python -m unittest discover -s utils/debug_harness/tests -v` and also writes
each test's result to a JSON file. Exit code 1 when a test failed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import time
import unittest
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = "utils/debug_harness"
SCENARIO_DIR = f"{HARNESS}/scenarios/"
FULL_TEST = f"{HARNESS}/full_test.py"
UNITTEST_DIR = f"{HARNESS}/tests/"
CPP_DIR = "src/tests/"
REGRESSION_DIR = "regression/"
TEST_PATHS = [CPP_DIR, SCENARIO_DIR, FULL_TEST, UNITTEST_DIR, REGRESSION_DIR]

MARKER = "<!-- pr-tests-report -->"
MAX_COMMENT = 60_000   # GitHub comments are limited to 65536 characters

# Category key -> label, in report order.
CATEGORIES = {
    "cpp": "C++ unit tests (Catch2 test cases)",
    "cpp_section": "C++ test sections",
    "scenario": "Harness scenarios",
    "scenario_check": "Scenario checks",
    "full_profile": "Full test profiles",
    "full_check": "Full test checks",
    "unittest": "Harness unit tests",
    "regression": "AI/GS regression tests",
}
# Categories whose changes count as "this PR tests the game" (harness unit tests only test the harness).
GAME_TEST_CATEGORIES = {"cpp", "cpp_section", "scenario", "scenario_check", "full_profile", "full_check", "regression"}

CPP_CASE_RE = re.compile(r"\b(TEST_CASE|TEST_CASE_METHOD|TEMPLATE_TEST_CASE|TEMPLATE_TEST_CASE_SIG|SCENARIO)\s*\(")
CPP_SECTION_RE = re.compile(r"\b(SECTION|DYNAMIC_SECTION|GIVEN|WHEN|THEN|AND_GIVEN|AND_WHEN|AND_THEN)\s*\(")
CPP_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
REGRESSION_RE = re.compile(r'create_regression\(\s*"([^"]+)"')
NUT_DESCRIPTION_RE = re.compile(r'GetDescription\s*\(\s*\)\s*\{\s*return\s*"((?:[^"\\]|\\.)*)"')


# ---------------------------------------------------------------------------------------------------------------------
# Tests found in the sources

@dataclass
class Check:
    """One check call: its message as written in the source ({expr} for interpolated parts)."""
    message: str
    line: int
    pattern: str | None   # regex matched against the start of the run time message, None when too generic
    function: str = ""

    @property
    def regex(self) -> re.Pattern | None:
        return re.compile(self.pattern, re.S) if self.pattern else None


@dataclass
class Test:
    category: str
    id: str
    path: str
    line: int
    body: str                # normalised source, for change detection
    description: str = ""
    parent: str = ""         # scenario_check: the scenario, cpp_section: the test case
    checks: list[Check] = field(default_factory=list)
    check: Check | None = None


def blank(chars: list[str], start: int, end: int) -> None:
    for i in range(start, min(end, len(chars))):
        if chars[i] != "\n":
            chars[i] = " "


def mask_cpp(text: str) -> tuple[str, str]:
    """(text without comments, text without comments and string/char literal contents), both of the same length."""
    nocomment = list(text)
    code = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(nocomment, i, j)
            blank(code, i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(nocomment, i, j)
            blank(code, i, j)
            i = j
        elif c == "R" and text.startswith('"', i + 1) and not (i and (text[i - 1].isalnum() or text[i - 1] == "_")):
            m = re.match(r'R"([^()\\\s]{0,16})\(', text[i:])
            if not m:
                i += 1
                continue
            close = ")" + m.group(1) + '"'
            j = text.find(close, i + m.end())
            j = n if j < 0 else j
            blank(code, i + m.end(), j)
            i = j + len(close)
        elif c in "\"'":
            if c == "'" and i and text[i - 1].isalnum():   # digit separator: 1'000
                i += 1
                continue
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            blank(code, i + 1, j)
            i = j + 1
        else:
            i += 1
    return "".join(nocomment), "".join(code)


def match_close(code: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing the one at `start`, or -1."""
    depth = 0
    for i in range(start, len(code)):
        if code[i] == open_ch:
            depth += 1
        elif code[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


def unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def preceding_comment(lines: list[str], line: int) -> str:
    """The comment block directly above 1-based `line`, without comment markers."""
    out = []
    i = line - 2
    while i >= 0:
        s = lines[i].strip()
        if not s.startswith(("//", "/*", "*")):
            break
        out.append(re.sub(r"^(/\*\*?|\*/|\*|//+!?<?)\s?", "", s).removesuffix("*/").strip())
        i -= 1
    return " ".join(x for x in reversed(out) if x)


def cpp_macro(text: str, code: str, m: re.Match) -> tuple[str, int, int] | None:
    """(name, body start, body end) of a Catch2 macro: the first string literal in its arguments and its { } block."""
    open_paren = m.end() - 1
    close_paren = match_close(code, open_paren, "(", ")")
    if close_paren < 0:
        return None
    args = text[open_paren + 1:close_paren]
    sm = CPP_STRING_RE.search(args)
    name = unescape(sm.group(1)) if sm else " ".join(args.split())
    brace = close_paren + 1
    while brace < len(code) and code[brace].isspace():
        brace += 1
    if brace >= len(code) or code[brace] != "{":
        return None
    end = match_close(code, brace, "{", "}")
    return (name, brace, end) if end > 0 else None


def parse_cpp(path: str, text: str) -> list[Test]:
    nocomment, code = mask_cpp(text)
    lines = text.splitlines()
    tests: list[Test] = []

    def line_of(pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    def sections(case: str, prefix: str, start: int, end: int) -> None:
        pos = start
        while (m := CPP_SECTION_RE.search(code, pos, end)) is not None:
            found = cpp_macro(text, code, m)
            if found is None:
                pos = m.end()
                continue
            name, b0, b1 = found
            sid = f"{prefix} / {name}"
            tests.append(Test("cpp_section", sid, path, line_of(m.start()), " ".join(nocomment[b0:b1 + 1].split()),
                              parent=case))
            sections(case, sid, b0 + 1, b1)
            pos = b1 + 1

    pos = 0
    while (m := CPP_CASE_RE.search(code, pos)) is not None:
        found = cpp_macro(text, code, m)
        if found is None:
            pos = m.end()
            continue
        name, b0, b1 = found
        line = line_of(m.start())
        tests.append(Test("cpp", name, path, line, " ".join(nocomment[b0:b1 + 1].split()),
                          preceding_comment(lines, line)))
        sections(name, name, b0 + 1, b1)
        pos = b1 + 1
    return tests


def message_parts(node: ast.AST) -> list[tuple[bool, str]]:
    """A check message expression as (is literal, text) parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [(True, node.value)]
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            parts += message_parts(v) if isinstance(v, ast.Constant) else [(False, ast.unparse(v.value))]
        return parts
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return message_parts(node.left) + message_parts(node.right)
    return [(False, ast.unparse(node))]


def make_check(node: ast.AST, line: int, function: str) -> Check:
    parts = message_parts(node)
    message = "".join(t if lit else "{" + t + "}" for lit, t in parts)
    literal = sum(len(t.strip()) for lit, t in parts if lit)
    pattern = "".join(re.escape(t) if lit else ".*?" for lit, t in parts) if literal >= 4 else None
    return Check(message, line, pattern, function)


def find_checks(tree: ast.AST, receivers: set[str] | None = None) -> list[Check]:
    """Calls of <x>.check(cond, message) and <x>.check_eq(actual, expected, message), unique by message."""
    found: dict[str, Check] = {}

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            fn = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr in ("check", "check_eq") \
                    and (receivers is None or (isinstance(child.func.value, ast.Name) and child.func.value.id in receivers)):
                index = 1 if child.func.attr == "check" else 2
                arg = child.args[index] if len(child.args) > index else \
                    next((k.value for k in child.keywords if k.arg in ("message", "name")), None)
                if arg is not None:
                    check = make_check(arg, child.lineno, function)
                    found.setdefault(check.message, check)
            visit(child, fn)

    visit(tree, "")
    return list(found.values())


def strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def first_paragraph(doc: str | None, limit: int = 400) -> str:
    text = " ".join((doc or "").strip().split("\n\n")[0].split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def parse_python(path: str, text: str) -> ast.Module | None:
    try:
        return ast.parse(text, filename=path)
    except SyntaxError:
        return None


def parse_scenario(path: str, text: str) -> list[Test]:
    name = Path(path).stem
    tree = parse_python(path, text)
    if tree is None:
        return [Test("scenario", name, path, 1, text, "(could not be parsed)")]
    body = ast.unparse(ast.Module(body=strip_docstring(tree.body), type_ignores=[]))
    checks = find_checks(tree)
    tests = [Test("scenario", name, path, 1, body, first_paragraph(ast.get_docstring(tree)), checks=checks)]
    tests += [Test("scenario_check", f"{name}: {c.message}", path, c.line, c.message, parent=name, check=c)
              for c in checks]
    return tests


def parse_full_test(path: str, text: str) -> list[Test]:
    tree = parse_python(path, text)
    if tree is None:
        return []
    lines = text.splitlines()
    tests = []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "PROFILES" for t in targets) and isinstance(node.value, ast.Dict):
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    comment = []
                    i = key.lineno - 2
                    while i >= 0 and lines[i].strip().startswith("#"):
                        comment.append(lines[i].strip().lstrip("#").strip())
                        i -= 1
                    tests.append(Test("full_profile", key.value, path, key.lineno, ast.unparse(value),
                                      " ".join(reversed(comment))))
    for c in find_checks(tree, {"self"}):
        tests.append(Test("full_check", c.message, path, c.line, c.message, check=c))
    return tests


def parse_unittest(path: str, text: str) -> list[Test]:
    tree = parse_python(path, text)
    if tree is None:
        return []
    module = Path(path).stem
    tests = []
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or not any(ast.unparse(b).endswith("TestCase") for b in cls.bases):
            continue
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name.startswith("test"):
                body = ast.unparse(strip_docstring(fn.body)) if strip_docstring(fn.body) else ""
                tests.append(Test("unittest", f"{module}.{cls.name}.{fn.name}", path, fn.lineno, body,
                                  first_paragraph(ast.get_docstring(fn), 200)))
    return tests


def parse_file(path: str, text: str) -> list[Test]:
    if path.startswith(CPP_DIR) and path.endswith((".cpp", ".h", ".hpp")):
        return parse_cpp(path, text)
    if path.startswith(SCENARIO_DIR) and path.endswith(".py") and "/" not in path[len(SCENARIO_DIR):]:
        return parse_scenario(path, text)
    if path == FULL_TEST:
        return parse_full_test(path, text)
    if path.startswith(UNITTEST_DIR) and Path(path).name.startswith("test") and path.endswith(".py"):
        return parse_unittest(path, text)
    return []


# ---------------------------------------------------------------------------------------------------------------------
# Reading revisions

class Tree:
    """Files of one revision: paths, text and a content id of a directory."""

    def files(self) -> list[str]:
        raise NotImplementedError

    def read(self, path: str) -> str:
        raise NotImplementedError

    def dir_id(self, path: str) -> str:
        raise NotImplementedError


class GitTree(Tree):
    def __init__(self, rev: str, repo: Path | None = None):
        self.rev = rev
        self.repo = repo or ROOT
        self._files: list[str] | None = None

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True).stdout.decode(
            "utf-8", "replace")

    def files(self) -> list[str]:
        if self._files is None:
            self._files = self.git("ls-tree", "-r", "--name-only", self.rev, "--", *TEST_PATHS).splitlines()
        return self._files

    def read(self, path: str) -> str:
        return self.git("show", f"{self.rev}:{path}")

    def dir_id(self, path: str) -> str:
        return self.git("rev-parse", f"{self.rev}:{path}").strip()


class DictTree(Tree):
    """In memory files, for the unit tests."""

    def __init__(self, files: dict[str, str]):
        self.contents = files

    def files(self) -> list[str]:
        return sorted(self.contents)

    def read(self, path: str) -> str:
        return self.contents[path]

    def dir_id(self, path: str) -> str:
        h = hashlib.sha1()
        for p in self.files():
            if p.startswith(path.rstrip("/") + "/"):
                h.update(p.encode() + b"\0" + self.contents[p].encode() + b"\0")
        return h.hexdigest()


def regression_tests(tree: Tree, files: list[str]) -> list[Test]:
    tests = []
    for cmake in files:
        parts = cmake.split("/")
        if len(parts) != 3 or parts[0] != "regression" or parts[2] != "CMakeLists.txt":
            continue
        m = REGRESSION_RE.search(tree.read(cmake))
        if not m:
            continue
        info = f"regression/{parts[1]}/info.nut"
        dm = NUT_DESCRIPTION_RE.search(tree.read(info)) if info in files else None
        tests.append(Test("regression", m.group(1), f"regression/{parts[1]}/main.nut", 1,
                          tree.dir_id(f"regression/{parts[1]}"), unescape(dm.group(1)) if dm else ""))
    return tests


Snapshot = dict[str, dict[str, Test]]


def snapshot(tree: Tree, reuse: dict[str, list[Test]] | None = None, changed: set[str] | None = None,
             parsed: dict[str, list[Test]] | None = None) -> Snapshot:
    """All tests of a tree. Files not in `changed` are taken from `reuse` (the other revision's parse) when present."""
    snap: Snapshot = {c: {} for c in CATEGORIES}
    files = tree.files()
    for path in files:
        if reuse is not None and changed is not None and path not in changed and path in reuse:
            tests = reuse[path]
        else:
            tests = parse_file(path, tree.read(path))
        if parsed is not None:
            parsed[path] = tests
        for t in tests:
            snap[t.category].setdefault(t.id, t)
    for t in regression_tests(tree, files):
        snap["regression"][t.id] = t
    return snap


@dataclass
class Change:
    kind: str                # added, modified, removed, renamed
    test: Test               # the head version (base version for removed)
    old: Test | None = None  # the base version for modified and renamed


def compare(base: Snapshot, head: Snapshot) -> dict[str, list[Change]]:
    changes: dict[str, list[Change]] = {}
    for cat in CATEGORIES:
        b, h = base.get(cat, {}), head.get(cat, {})
        added = [h[i] for i in h if i not in b]
        removed = [b[i] for i in b if i not in h]
        out = [Change("modified", h[i], b[i]) for i in h if i in b and h[i].body != b[i].body]
        # Same body under a new name: renamed. Checks have no body beyond their message.
        if cat not in ("scenario_check", "full_check"):
            by_body = {}
            for t in removed:
                by_body.setdefault(t.body, t)
            for t in list(added):
                old = by_body.pop(t.body, None)
                if old is not None:
                    added.remove(t)
                    removed.remove(old)
                    out.append(Change("renamed", t, old))
        out += [Change("added", t) for t in added] + [Change("removed", t) for t in removed]
        order = {"added": 0, "renamed": 1, "modified": 2, "removed": 3}
        changes[cat] = sorted(out, key=lambda c: (order[c.kind], c.test.path, c.test.line))
    # A renamed scenario has the same checks: they moved with it.
    renamed = {n for c in changes["scenario"] if c.kind == "renamed" and c.old for n in (c.test.id, c.old.id)}
    changes["scenario_check"] = [c for c in changes["scenario_check"] if c.test.parent not in renamed]
    return changes


# ---------------------------------------------------------------------------------------------------------------------
# Results

@dataclass
class Status:
    state: str               # pass, fail, warn, pending, notrun, missing, unreached, unknown
    seconds: float | None = None
    detail: str = ""


ICONS = {"pass": "✅", "fail": "❌", "warn": "🟡", "pending": "⏳", "notrun": "⚪", "missing": "⚠️",
         "unreached": "➖", "unknown": "❔"}
WORDS = {"pass": "passed", "fail": "failed", "warn": "warning", "pending": "pending", "notrun": "not run",
         "missing": "no result", "unreached": "not reached", "unknown": "not matched"}


@dataclass
class Results:
    junit: dict[str, Status] = field(default_factory=dict)       # ctest test name -> status
    unittests: dict[str, Status] = field(default_factory=dict)   # unittest id -> status
    scenarios: dict[str, dict] = field(default_factory=dict)     # scenario name -> result.json
    full: dict[str, dict] = field(default_factory=dict)          # test name (profile) -> report.json
    expect: set[str] = field(default_factory=set)
    have_unit: bool = False
    have_unittests: bool = False


def load_results(root: Path, expect: set[str]) -> Results:
    r = Results(expect=expect)
    for f in sorted(root.rglob("*.xml")):
        try:
            xml = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        for tc in xml.iter("testcase"):
            r.have_unit = True
            fail = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
            if fail is not None:
                state, detail = "fail", fail.get("message") or (fail.text or "").strip()
            elif tc.find("skipped") is not None or tc.get("status") in ("notrun", "disabled"):
                state, detail = "notrun", ""
            else:
                state, detail = "pass", ""
            try:
                seconds = float(tc.get("time", ""))
            except ValueError:
                seconds = None
            r.junit[tc.get("name", "")] = Status(state, seconds, detail)
    for f in sorted(root.rglob("harness-unittests.json")):
        r.have_unittests = True
        for t in json.loads(f.read_text(encoding="utf-8")).get("tests", []):
            state = {"pass": "pass", "skip": "notrun"}.get(t["state"], "fail")
            r.unittests[t["id"]] = Status(state, t.get("seconds"), t.get("message", ""))
    for f in sorted(root.rglob("result.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if "scenario" in data:
            r.scenarios[data["scenario"]] = data
    for f in sorted(root.rglob("report.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        group = next((p.name for p in f.parents if p.name.startswith("game-test-")), None)
        name = data.get("profile") or (group.removeprefix("game-test-") if group else f.parent.name)
        prev = r.full.get(name)
        if prev is not None:
            # Parallel runs of the same full test: it passes only if every run passed.
            data = {**data, "ok": bool(prev.get("ok")) and bool(data.get("ok")),
                    "checks": prev.get("checks", []) + data.get("checks", []),
                    "seconds": max(s for s in (prev.get("seconds"), data.get("seconds"), 0) if s is not None)}
        r.full[name] = data
    return r


def first_line(text: str, limit: int = 200) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def status_of(change: Change, results: Results | None) -> Status | None:
    """The result of an added, modified or renamed test. None for removed tests."""
    if change.kind == "removed":
        return None
    if results is None:
        return Status("pending")
    t = change.test
    cat = t.category
    if cat in ("cpp", "cpp_section", "regression"):
        name = t.parent if cat == "cpp_section" else f"regression_{t.id}" if cat == "regression" else t.id
        if name in results.junit:
            return results.junit[name]
        return Status("missing" if not results.have_unit else "notrun")
    if cat == "unittest":
        if t.id in results.unittests:
            return results.unittests[t.id]
        return Status("missing" if not results.have_unittests else "notrun")
    if cat in ("scenario", "scenario_check"):
        name = t.parent if cat == "scenario_check" else t.id
        data = results.scenarios.get(name)
        if data is None:
            return Status("missing" if "scenarios" in results.expect else "notrun")
        if cat == "scenario":
            return Status("pass" if data.get("ok") else "fail", data.get("seconds"), first_line(data.get("error") or ""))
        regex = t.check.regex if t.check else None
        if regex is None:
            return Status("unknown")
        hits = [c for c in data.get("checks", []) if regex.match(c.get("message", ""))]
        if not hits:
            return Status("unreached", detail="scenario failed before this check" if not data.get("ok") else "")
        bad = [c for c in hits if not c.get("ok")]
        return Status("fail", detail=first_line(bad[0]["message"])) if bad else Status("pass")
    if cat == "full_profile":
        data = results.full.get(t.id)
        if data is None:
            return Status("missing" if t.id in results.expect else "notrun",
                          detail="" if t.id in results.expect else "not in this run's test list")
        fails = [c for c in data.get("checks", []) if not c.get("ok") and c.get("severity", "fail") == "fail"]
        detail = f"{sum(bool(c.get('ok')) for c in data.get('checks', []))}/{len(data.get('checks', []))} checks"
        if fails:
            detail = first_line(fails[0].get("name", "")) + ("" if len(fails) == 1 else f" (+{len(fails) - 1} more)")
        return Status("pass" if data.get("ok") else "fail", data.get("seconds"), detail)
    if cat == "full_check":
        if not results.full:
            return Status("missing" if results.expect - {"scenarios"} else "notrun")
        regex = t.check.regex if t.check else None
        if regex is None:
            return Status("unknown")
        passed, failed, warned = [], [], []
        for profile, data in sorted(results.full.items()):
            hits = [c for c in data.get("checks", []) if regex.match(c.get("name", ""))]
            if any(not c.get("ok") and c.get("severity", "fail") == "fail" for c in hits):
                failed.append(profile)
            elif any(not c.get("ok") for c in hits):
                warned.append(profile)
            elif hits:
                passed.append(profile)
        detail = " · ".join(f"{ICONS[s]} {', '.join(p)}" for s, p in (("pass", passed), ("warn", warned),
                                                                          ("fail", failed)) if p)
        if failed:
            return Status("fail", detail=detail)
        if warned:
            return Status("warn", detail=detail)
        if passed:
            return Status("pass", detail=detail)
        return Status("unreached", detail="in none of the full tests of this run")
    return Status("unknown")


# ---------------------------------------------------------------------------------------------------------------------
# Report

KIND_ICONS = {"added": "🆕", "modified": "✏️", "renamed": "🔀", "removed": "🗑️"}


def esc(text: str, limit: int = 300) -> str:
    text = first_line(text, limit)
    return text.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def code(text: str, limit: int = 200) -> str:
    text = first_line(text, limit).replace("|", "\\|")
    ticks = "``" if "`" in text else "`"
    return f"{ticks}{text}{ticks}" if ticks == "`" else f"{ticks} {text} {ticks}"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 1:
        return f"{seconds:.2f}s"
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def status_text(s: Status | None, with_detail: bool = True) -> str:
    if s is None:
        return "–"
    out = f"{ICONS[s.state]} {WORDS[s.state]}"
    if s.seconds is not None:
        out += f" in {duration(s.seconds)}"
    if with_detail and s.detail:
        out += f": {esc(s.detail, 160)}"
    return out


class Report:
    def __init__(self, changes: dict[str, list[Change]], head: Snapshot, results: Results | None, head_sha: str,
                 repo_url: str | None, run_url: str | None, game_files: list[str], limit: int = 60):
        self.changes = changes
        self.head = head
        self.results = results
        self.head_sha = head_sha
        self.repo_url = repo_url
        self.run_url = run_url
        self.game_files = game_files
        self.limit = limit

    def link(self, t: Test, text: str) -> str:
        if not self.repo_url or not self.head_sha:
            return text
        return f"[{text}]({self.repo_url}/blob/{self.head_sha}/{t.path}#L{t.line})"

    def where(self, t: Test, removed: bool = False) -> str:
        loc = f"{Path(t.path).name}:{t.line}"
        return loc if removed else self.link(t, loc)

    def count(self, cat: str, kind: str) -> int:
        return sum(c.kind == kind for c in self.changes.get(cat, []))

    def tested(self) -> list[tuple[Change, Status]]:
        """Added/modified/renamed tests (not checks or sections, those are part of their test) with their status."""
        out = []
        for cat in ("cpp", "scenario", "full_profile", "unittest", "regression"):
            for c in self.changes.get(cat, []):
                if c.kind != "removed":
                    out.append((c, status_of(c, self.results)))
        return out

    def headline(self) -> list[str]:
        parts = []
        for cat, label in (("cpp", "C++ test case"), ("scenario", "scenario"), ("scenario_check", "scenario check"),
                           ("full_profile", "full test profile"), ("full_check", "full test check"),
                           ("unittest", "harness unit test"), ("regression", "regression test")):
            n = self.count(cat, "added")
            m = self.count(cat, "modified") + self.count(cat, "renamed")
            r = self.count(cat, "removed")
            bits = [f"+{n}" if n else "", f"~{m}" if m else "", f"−{r}" if r else ""]
            bits = [b for b in bits if b]
            if bits:
                parts.append(f"{' '.join(bits)} {label}{'s' if n + m + r != 1 else ''}")
        lines = ["## 🧪 Tests in this PR", ""]
        lines.append("**" + " · ".join(parts) + "**" if parts else "This PR adds, changes and removes no tests.")
        lines.append("")
        tested = self.tested()
        if tested:
            if self.results is None:
                lines.append(f"⏳ Results of the {len(tested)} new and changed tests appear here when *Game tests* "
                             "finishes.")
            else:
                failed = [c for c, s in tested if s.state == "fail"]
                passed = [c for c, s in tested if s.state == "pass"]
                other = len(tested) - len(failed) - len(passed)
                if not failed and not other:
                    lines.append(f"✅ All {len(tested)} new and changed tests passed.")
                else:
                    text = f"**{len(tested)} new and changed tests:** ✅ {len(passed)} passed"
                    if failed:
                        text = f"❌ {text} · ❌ {len(failed)} failed (" + ", ".join(
                            code(c.test.id, 80) for c in failed[:10]) + (" …" if len(failed) > 10 else "") + ")"
                    if other:
                        text += f" · ⚠️ {other} without a result"
                    lines.append(text)
            lines.append("")
        game_changes = sum(c.kind != "removed" for cat in GAME_TEST_CATEGORIES for c in self.changes.get(cat, []))
        if self.game_files and not game_changes:
            lines.append(f"> [!WARNING]\n> This PR changes game code ({len(self.game_files)} file"
                         f"{'s' if len(self.game_files) != 1 else ''} under `src/`) but adds or changes no C++ test, "
                         "harness scenario, full test or regression test.")
            lines.append("")
        return lines

    def totals(self) -> list[str]:
        lines = ["| | Added | Changed | Removed | In this PR's head |", "|---|---:|---:|---:|---:|"]
        for cat, label in CATEGORIES.items():
            a, m, r = self.count(cat, "added"), self.count(cat, "modified") + self.count(cat, "renamed"), \
                self.count(cat, "removed")
            cell = lambda n, s: f"{s}{n}" if n else "·"  # noqa: E731
            lines.append(f"| {label} | {cell(a, '+')} | {cell(m, '~')} | {cell(r, '−')} | {len(self.head.get(cat, {}))} |")
        return lines

    def capped(self, items: list, render) -> list[str]:
        out = []
        for item in items[:self.limit]:
            out += render(item)
        if len(items) > self.limit:
            out.append(f"| | … and {len(items) - self.limit} more | | |" if out and out[-1].startswith("|")
                       else f"- … and {len(items) - self.limit} more")
        return out

    def name_cell(self, c: Change, name: str) -> str:
        t = c.test
        cell = code(name)
        if c.kind == "removed":
            cell = f"~~{cell}~~"
        if c.kind == "renamed" and c.old:
            cell += f"<br><sub>renamed from {code(c.old.id)}</sub>"
        if t.description:
            cell += f"<br><sub>{esc(t.description, 240)}</sub>"
        return cell

    def section_cpp(self) -> list[str]:
        cases = self.changes.get("cpp", [])
        sections = self.changes.get("cpp_section", [])
        if not cases and not sections:
            return []
        lines = ["### C++ unit tests (Catch2)", "", "| | Test case | Where | Result |", "|---|---|---|---|"]
        shown_cases = {c.test.id for c in cases}
        by_case: dict[str, list[Change]] = {}
        for s in sections:
            by_case.setdefault(s.test.parent, []).append(s)
        # Section changes in otherwise unchanged test cases can't happen (the case body contains them), but a moved
        # section of a renamed case can; show those under the case.
        rows = list(cases) + [Change("modified", self.head["cpp"][p]) for p in by_case
                              if p not in shown_cases and p in self.head.get("cpp", {})]

        def render(c: Change) -> list[str]:
            out = [f"| {KIND_ICONS[c.kind]} | {self.name_cell(c, c.test.id)} | {self.where(c.test, c.kind == 'removed')} "
                   f"| {status_text(status_of(c, self.results))} |"]
            for s in by_case.get(c.test.id, []) if c.kind != "removed" else []:
                depth = s.test.id.count(" / ")
                label = s.test.id.rsplit(" / ", 1)[-1]
                out.append(f"| | {'&nbsp;' * 4 * depth}↳ {KIND_ICONS[s.kind]} {code(label)} "
                           f"| {self.where(s.test, s.kind == 'removed')} | |")
            return out

        return lines + self.capped(rows, render) + [""]

    def check_line(self, c: Change, results: bool = True) -> str:
        t = c.test
        text = f"- {KIND_ICONS[c.kind]} "
        text += f"~~{code(t.check.message if t.check else t.id)}~~" if c.kind == "removed" else \
            code(t.check.message if t.check else t.id)
        if t.check and t.check.function and t.category == "full_check":
            text += f" <sub>in {self.where(t, c.kind == 'removed')} `{t.check.function}()`</sub>"
        if results and c.kind != "removed":
            s = status_of(c, self.results)
            if s and s.state != "pending":
                if t.category == "full_check" and s.state in ("pass", "warn", "fail"):
                    text += f" — {s.detail}"   # the profiles, by result
                else:
                    shown = s.detail and t.check and s.detail != t.check.message
                    text += f" — {ICONS[s.state]} {WORDS[s.state]}" + (f": {esc(s.detail, 200)}" if shown else "")
        return text

    def section_scenarios(self) -> list[str]:
        scenarios = self.changes.get("scenario", [])
        checks = self.changes.get("scenario_check", [])
        if not scenarios and not checks:
            return []
        by_scenario: dict[str, list[Change]] = {}
        for c in checks:
            by_scenario.setdefault(c.test.parent, []).append(c)
        names = [c.test.id for c in scenarios] + [p for p in by_scenario if p not in {c.test.id for c in scenarios}]
        lines = ["### Harness scenarios", ""]

        def render(name: str) -> list[str]:
            c = next((x for x in scenarios if x.test.id == name), None)
            if c is None:
                t = self.head["scenario"].get(name)
                if t is None:
                    return []
                c = Change("modified", t)
            t = c.test
            title = f"`{name}`" if c.kind == "removed" else self.link(t, f"`{name}`")
            head = f"**{KIND_ICONS[c.kind]} {title}**"
            if c.kind == "renamed" and c.old:
                head += f" (renamed from `{c.old.id}`)"
            s = status_of(c, self.results)
            if s is not None:
                head += f" — {status_text(s)}"
            out = [head]
            if t.description:
                out.append(f"<sub>{esc(t.description, 400)}</sub>")
            out.append("")
            if c.kind == "removed":
                out.append(f"- {len(t.checks)} check{'s' if len(t.checks) != 1 else ''} removed")
            else:
                changed = by_scenario.get(name, [])
                for x in changed[:self.limit]:
                    out.append(self.check_line(x))
                if len(changed) > self.limit:
                    out.append(f"- … and {len(changed) - self.limit} more")
                unchanged = len(t.checks) - sum(x.kind == "added" for x in changed)
                if c.kind != "added" and unchanged > 0:
                    out.append(f"- {unchanged} unchanged check{'s' if unchanged != 1 else ''}")
                if not t.checks and c.kind == "added":
                    out.append("- no `ctx.check()` calls found in this file")
            out.append("")
            return out

        for name in names[:self.limit]:
            lines += render(name)
        if len(names) > self.limit:
            lines += [f"… and {len(names) - self.limit} more scenarios", ""]
        return lines

    def section_full(self) -> list[str]:
        profiles = self.changes.get("full_profile", [])
        checks = self.changes.get("full_check", [])
        if not profiles and not checks:
            return []
        lines = ["### Full tests", ""]
        if profiles:
            lines += ["| | Profile | Settings | Result |", "|---|---|---|---|"]

            def render(c: Change) -> list[str]:
                return [f"| {KIND_ICONS[c.kind]} | {self.name_cell(c, c.test.id)} | {code(c.test.body, 160)} "
                        f"| {status_text(status_of(c, self.results))} |"]

            lines += self.capped(profiles, render) + [""]
        if checks:
            lines += [f"**Checks** in {FULL_TEST.rsplit('/', 1)[-1]}", ""]
            lines += self.capped(checks, lambda c: [self.check_line(c)]) + [""]
        return lines

    def section_simple(self, cat: str, title: str, column: str) -> list[str]:
        items = self.changes.get(cat, [])
        if not items:
            return []
        lines = [f"### {title}", "", f"| | {column} | Where | Result |", "|---|---|---|---|"]

        def render(c: Change) -> list[str]:
            return [f"| {KIND_ICONS[c.kind]} | {self.name_cell(c, c.test.id)} | {self.where(c.test, c.kind == 'removed')} "
                    f"| {status_text(status_of(c, self.results))} |"]

        return lines + self.capped(items, render) + [""]

    def footer(self) -> list[str]:
        bits = [f"for {self.head_sha[:10]}" if self.head_sha else ""]
        if self.run_url:
            bits.append(f"[workflow run]({self.run_url})")
        bits.append("list from the PR diff" + (", results from *Game tests*" if self.results else
                                                ", results follow when *Game tests* finishes"))
        legend = " ".join(f"{v} {k}" for k, v in KIND_ICONS.items())
        return [f"<sub>{' · '.join(b for b in bits if b)}<br>{legend}</sub>"]

    def render(self) -> str:
        lines = self.headline()
        lines += ["<details><summary>Totals</summary>", "", *self.totals(), "", "</details>", ""]
        lines += self.section_cpp()
        lines += self.section_scenarios()
        lines += self.section_full()
        lines += self.section_simple("unittest", "Harness unit tests", "Test")
        lines += self.section_simple("regression", "AI/GS regression tests", "Test")
        lines += self.footer()
        return "\n".join(lines) + "\n"


def changed_files(tree_base: GitTree, head: str) -> list[str]:
    return tree_base.git("diff", "--name-only", "--no-renames", tree_base.rev, head).splitlines()


def merge_base(base: str, head: str) -> str:
    try:
        return GitTree(head).git("merge-base", base, head).strip()
    except subprocess.CalledProcessError:
        return base   # shallow clone: the workflow passes the merge base itself


def build_report(base: str, head: str, results: Results | None, repo_url: str | None, run_url: str | None) -> str:
    base = merge_base(base, head)
    head_tree, base_tree = GitTree(head), GitTree(base)
    head_sha = head_tree.git("rev-parse", head).strip()
    changed = set(changed_files(base_tree, head))
    parsed: dict[str, list[Test]] = {}
    head_snap = snapshot(head_tree, parsed=parsed)
    base_snap = snapshot(base_tree, reuse=parsed, changed=changed)
    game_files = sorted(f for f in changed if f.startswith("src/") and not f.startswith((CPP_DIR, "src/3rdparty/")))
    report = None
    for limit in (60, 15, 5):
        report = Report(compare(base_snap, head_snap), head_snap, results, head_sha, repo_url, run_url, game_files,
                        limit).render()
        if len(report) <= MAX_COMMENT:
            break
    assert report is not None
    if len(report) > MAX_COMMENT:
        report = report[:MAX_COMMENT] + "\n\n… (cut, see the workflow run summary)\n"
    return report


# ---------------------------------------------------------------------------------------------------------------------
# Harness unit tests with a JSON result

class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records: list[dict] = []
        self._t0 = time.monotonic()

    def startTest(self, test):  # noqa: N802
        self._t0 = time.monotonic()
        super().startTest(test)

    def record(self, test, state: str, message: str = "") -> None:
        self.records.append({"id": test.id(), "state": state, "seconds": round(time.monotonic() - self._t0, 3),
                             "message": first_line(message, 500)})

    def addSuccess(self, test):  # noqa: N802
        super().addSuccess(test)
        self.record(test, "pass")

    def addFailure(self, test, err):  # noqa: N802
        super().addFailure(test, err)
        self.record(test, "fail", str(err[1]))

    def addError(self, test, err):  # noqa: N802
        super().addError(test, err)
        self.record(test, "error", f"{err[0].__name__}: {err[1]}")

    def addSkip(self, test, reason):  # noqa: N802
        super().addSkip(test, reason)
        self.record(test, "skip", reason)

    def addExpectedFailure(self, test, err):  # noqa: N802
        super().addExpectedFailure(test, err)
        self.record(test, "pass", "expected failure")

    def addUnexpectedSuccess(self, test):  # noqa: N802
        super().addUnexpectedSuccess(test)
        self.record(test, "fail", "unexpected success")


def run_unittests(start: Path, out: Path | None) -> int:
    suite = unittest.TestLoader().discover(str(start), top_level_dir=str(start))
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"tests": result.records}, indent=2), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("report", help="Markdown report of the tests a PR changes")
    rp.add_argument("--base", required=True, help="base revision (its merge base with --head is used)")
    rp.add_argument("--head", default="HEAD")
    rp.add_argument("--results", type=Path, help="downloaded Game tests artifacts")
    rp.add_argument("--expect", default="", help="comma separated tests of the Game tests run")
    rp.add_argument("--repo-url")
    rp.add_argument("--run-url")
    rp.add_argument("--out", type=Path)
    up = sub.add_parser("unittest", help="run the harness unit tests, write their results as JSON")
    up.add_argument("--start", type=Path, default=Path(__file__).resolve().parent / "tests")
    up.add_argument("--out", type=Path)
    args = ap.parse_args()

    if args.command == "unittest":
        return run_unittests(args.start, args.out)

    results = None
    if args.results is not None:
        results = load_results(args.results, {t.strip() for t in args.expect.split(",") if t.strip()})
    report = build_report(args.base, args.head, results, args.repo_url, args.run_url)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.buffer.write(report.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
