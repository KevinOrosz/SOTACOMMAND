"""Leak gate integration: findings refuse the run, and every failure fails closed.

These tests use the stub gate in tests/extract/stub_leakgate.py, never the real
one, so they pass whether or not tools/repo/leakgate/ is in the checkout.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from .support import TempDirTestCase, base_manifest, ex, stub


class GateTestCase(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.make_source({"docs/a.md": "Ask ZEPHYR about Avery's plan.\n", "notes/private.md": "never copied\n"})
        self.manifest = self.write_manifest(base_manifest(files=[{"from": "docs/a.md", "to": "docs/a.md"}]))

    def run_with(self, *stub_args, **kwargs):
        gate = stub("--terms-from", str(self.manifest), *stub_args)
        return self.run_tool(self.source, self.manifest, gate=gate, **kwargs)

    def assert_fails_closed(self, outcome, report, reason):
        self.assertEqual(outcome.exit_code, ex.EXIT_GATE_UNAVAILABLE, report)
        self.assertIn(reason, outcome.gate.reason)
        self.assertIn("**GATE UNAVAILABLE**", report)
        self.assert_no_output()


class CleanRuns(GateTestCase):
    def test_clean_run_writes_the_tree(self):
        outcome, report = self.run_with()
        self.assertEqual(outcome.exit_code, ex.EXIT_OK, report)
        self.assertTrue(outcome.written)
        self.assertEqual((self.tmp / "stage" / "docs" / "a.md").read_text(encoding="utf-8"), "Ask orchestrator about the owner's plan.\n")
        self.assertEqual(outcome.canary.status, "passed")
        self.assertEqual(outcome.canary.total, 5)
        self.assertEqual(outcome.unlisted, {"notes/": 1})
        self.assertIn("**WRITTEN**", report)
        self.assertEqual(sorted(p.name for p in (self.tmp / "work").iterdir()), [])

    def test_dry_run_never_writes(self):
        outcome, report = self.run_with(dry_run=True)
        self.assertEqual(outcome.exit_code, ex.EXIT_OK)
        self.assertIn("**CLEAN** (dry run)", report)
        self.assert_no_output()

    def test_an_existing_empty_out_folder_is_replaced(self):
        (self.tmp / "stage").mkdir()
        outcome, _ = self.run_with()
        self.assertEqual(outcome.exit_code, ex.EXIT_OK)
        self.assertTrue((self.tmp / "stage" / "docs" / "a.md").is_file())

    def test_every_json_shape_the_gate_may_print(self):
        for shape in ("object", "list", "lines"):
            with self.subTest(shape=shape):
                outcome, report = self.run_with("--shape", shape, dry_run=True)
                self.assertEqual(outcome.exit_code, ex.EXIT_OK, report)


class FindingsRefuse(GateTestCase):
    def test_findings_refuse_the_run_and_map_to_the_source(self):
        (self.source / "docs" / "a.md").write_text("Ship it.\nQX-PLANTED-TOKEN here.\n", encoding="utf-8")
        outcome, report = self.run_with("--term", "QX-PLANTED-TOKEN")
        self.assertEqual(outcome.exit_code, ex.EXIT_REFUSED, report)
        self.assertEqual(outcome.flags, [])
        self.assertEqual([(f.path, f.line) for f in outcome.gate.findings], [("docs/a.md", 2)])
        self.assertIn("| `docs/a.md` | 2 | `term` |", report)
        self.assertIn("0 flags and 1 leak-gate finding", report)
        self.assert_no_output()

    def test_flags_refuse_even_when_the_gate_is_unavailable(self):
        (self.source / "docs" / "a.md").write_text("ZEPHYR_HOME=x\n", encoding="utf-8")
        outcome, report = self.run_with("--behave", "blind")
        self.assertEqual(outcome.exit_code, ex.EXIT_REFUSED)
        self.assertEqual(outcome.gate.status, "unavailable")
        self.assertIn("**REFUSED**", report)
        self.assert_no_output()


class FailClosed(GateTestCase):
    def test_missing_default_gate(self):
        with mock.patch.object(ex, "DEFAULT_GATE_SCRIPT", self.tmp / "absent" / "leakgate.py"):
            opts = ex.Options(source=str(self.source), manifest=str(self.manifest), out=str(self.tmp / "stage"), work_dir=str(self.tmp))
            outcome = ex.run_extraction(opts)
        self.assert_fails_closed(outcome, ex.render_report(outcome), "not installed at tools/repo/leakgate/leakgate.py")

    def test_missing_gate_script(self):
        outcome, report = self.run_tool(self.source, self.manifest, gate=[sys.executable, str(self.tmp / "missing.py")])
        self.assert_fails_closed(outcome, report, "exited 2")

    def test_missing_gate_command(self):
        outcome, report = self.run_tool(self.source, self.manifest, gate=["extract-test-no-such-command"])
        self.assert_fails_closed(outcome, report, "could not start")

    def test_gate_crash(self):
        outcome, report = self.run_with("--behave", "crash")
        self.assert_fails_closed(outcome, report, "without readable findings")
        self.assertIn("RuntimeError: simulated leak gate crash", report)

    def test_gate_config_error(self):
        outcome, report = self.run_with("--behave", "usage-error")
        self.assert_fails_closed(outcome, report, "exited 2 (usage or config error)")

    def test_unreadable_output(self):
        outcome, report = self.run_with("--behave", "garbage")
        self.assert_fails_closed(outcome, report, "output is not JSON")

    def test_exit_code_that_contradicts_the_output(self):
        outcome, report = self.run_with("--behave", "lie")
        self.assert_fails_closed(outcome, report, "exited 0 but reported")

    def test_exit_1_without_findings(self):
        outcome, report = self.run_with("--behave", "exit1-empty")
        self.assert_fails_closed(outcome, report, "exited 1 without readable findings")

    def test_timeout(self):
        outcome, report = self.run_with("--behave", "sleep=10", timeout=1.0)
        self.assert_fails_closed(outcome, report, "timed out after 1 seconds")

    def test_temp_dir_inside_a_git_work_tree(self):
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        work = repo / "work"
        work.mkdir()
        opts = ex.Options(
            source=str(self.source), manifest=str(self.manifest), out=str(self.tmp / "stage"),
            leakgate=stub("--terms-from", str(self.manifest)), work_dir=str(work),
        )
        outcome = ex.run_extraction(opts)
        self.assertEqual(outcome.exit_code, ex.EXIT_GATE_UNAVAILABLE)
        self.assertIn("inside the git work tree", outcome.gate.reason)
        self.assertEqual(list(work.iterdir()), [])
        self.assertFalse((self.tmp / "stage").exists())


class Canary(GateTestCase):
    def test_a_blind_gate_fails_the_canary(self):
        outcome, report = self.run_with("--behave", "blind")
        self.assert_fails_closed(outcome, report, "canary failed")
        self.assertEqual(outcome.canary.status, "failed")
        self.assertEqual(len(outcome.canary.missed), 5)
        self.assertIn("missed owner_terms term 2 (A***y)", report)
        self.assertNotIn("Avery", "\n".join(outcome.canary.missed))

    def test_a_gate_without_the_private_denylist_fails_the_canary(self):
        gate = stub("--term", "ZEPHYR", "--term", "QUILL", "--term", "ONYX")
        outcome, report = self.run_tool(self.source, self.manifest, gate=gate)
        self.assert_fails_closed(outcome, report, "missed 2 manifest terms of 5")
        self.assertEqual(outcome.canary.missed, ["owner_terms term 1 (A***********e)", "owner_terms term 2 (A***y)"])

    def test_the_denylist_variable_reaches_the_gate(self):
        gate = stub("--term", "ZEPHYR", "--term", "QUILL", "--term", "ONYX")
        with mock.patch.dict(os.environ, {"LEAKGATE_DENYLIST": "Avery Example\nAvery\n"}):
            outcome, report = self.run_tool(self.source, self.manifest, gate=gate)
        self.assertEqual(outcome.exit_code, ex.EXIT_OK, report)

    def test_canary_is_skipped_without_terms(self):
        manifest = self.write_manifest(base_manifest(persona_map={}, owner_terms={}, files=[{"from": "docs/a.md", "to": "docs/a.md"}]), "bare.json")
        outcome, report = self.run_tool(self.source, manifest, gate=stub())
        self.assertEqual(outcome.exit_code, ex.EXIT_OK, report)
        self.assertEqual(outcome.canary.status, "skipped")
        self.assertIn("gate canary skipped", report)


class Hygiene(GateTestCase):
    def test_reports_never_quote_temporary_paths(self):
        (self.source / "docs" / "a.md").write_text("QX-PLANTED-TOKEN\n", encoding="utf-8")
        for args in (("--term", "QX-PLANTED-TOKEN"), ("--behave", "crash")):
            with self.subTest(args=args):
                _, report = self.run_with(*args)
                for fragment in ("extract-build-", "extract-canary-", str(self.tmp / "work")):
                    self.assertNotIn(fragment, report)

    def test_gate_environment_drops_git_overrides(self):
        target = self.tmp / "work" / "build"
        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere/.git", "GIT_WORK_TREE": "/elsewhere"}):
            env = ex._gate_env(target)
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_WORK_TREE", env)
        self.assertTrue(env["GIT_CEILING_DIRECTORIES"].startswith(str(ex._resolve(self.tmp / "work"))))

    def test_finding_parser(self):
        self.assertEqual(ex._loads_lenient('{"findings": []}'), (True, {"findings": []}))
        ok, rows = ex._loads_lenient('{"path": "a"}\n{"path": "b"}\n')
        self.assertTrue(ok)
        self.assertEqual([f.path for f in ex._extract_findings(rows)], ["a", "b"])
        ok, data = ex._loads_lenient('scanning...\n[{"path": "x", "line": 3, "rule_id": "r"}]\n')
        self.assertEqual([(f.path, f.line, f.rule_id) for f in ex._extract_findings(data)], [("x", 3, "r")])
        self.assertEqual(ex._loads_lenient("not json"), (False, None))
        self.assertIsNone(ex._extract_findings({"ok": True}))
        self.assertIsNone(ex._extract_findings([1, 2]))


if __name__ == "__main__":
    unittest.main()
