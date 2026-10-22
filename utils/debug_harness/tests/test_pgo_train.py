"""Unit tests of pgo_train.py: python -m unittest discover -s utils/debug_harness/tests"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pgo_train  # noqa: E402


class ProfileFilesTest(unittest.TestCase):
    def test_finds_profile_data_of_every_compiler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "openttd.pgd").write_bytes(b"")          # MSVC database: written by the link, not by training
            (d / "openttd!1.pgc").write_bytes(b"")        # MSVC
            (d / "gcc" / "x").mkdir(parents=True)
            (d / "gcc" / "x" / "a.gcda").write_bytes(b"")  # GCC, in subdirectories
            (d / "openttd-12.profraw").write_bytes(b"")   # Clang
            (d / "run").mkdir()
            (d / "run" / "openttd.cfg").write_text("")
            self.assertEqual(pgo_train.profile_files(d), ["gcc/x/a.gcda", "openttd!1.pgc", "openttd-12.profraw"])

    def test_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pgo_train.profile_files(Path(tmp)), [])


class MergeClangProfilesTest(unittest.TestCase):
    def test_merges_all_profraw_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in ("openttd-2.profraw", "openttd-1.profraw"):
                (d / name).write_bytes(b"")
            with mock.patch.object(pgo_train.subprocess, "run") as run:
                out = pgo_train.merge_clang_profiles(d, "llvm-profdata-18")
            self.assertEqual(out, d / "default.profdata")
            run.assert_called_once_with(["llvm-profdata-18", "merge", "-o", str(d / "default.profdata"),
                                         str(d / "openttd-1.profraw"), str(d / "openttd-2.profraw")], check=True)

    def test_nothing_to_merge_for_msvc_or_gcc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "openttd!1.pgc").write_bytes(b"")
            with mock.patch.object(pgo_train.subprocess, "run") as run:
                self.assertIsNone(pgo_train.merge_clang_profiles(d, "llvm-profdata"))
            run.assert_not_called()


class StepTest(unittest.TestCase):
    def test_steps_in_chunks_up_to_the_total(self) -> None:
        b = mock.Mock()
        pgo_train.step(b, 60, 25)
        self.assertEqual([c.args[0] for c in b.step.call_args_list], [25, 25, 10])


if __name__ == "__main__":
    unittest.main()
