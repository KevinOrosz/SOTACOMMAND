"""End to end on fixtures/fake-command: the report, refusals, a clean run, determinism."""

import json
import os
import re
import shutil
import sys
import unittest

from .support import (
    EXAMPLE_MANIFEST,
    FIXTURE,
    HANDLED_MANIFEST,
    REPO_ROOT,
    STUB_GATE,
    TempDirTestCase,
    ex,
    planted_markers,
    run_cli,
    stub,
    tree_snapshot,
)

sys.path.insert(0, str(REPO_ROOT / "adapters"))
sys.path.insert(0, str(REPO_ROOT / "tools" / "repo" / "skills"))
from osgen import roles as osgen_roles  # noqa: E402
from skillslib import lint as skills_lint  # noqa: E402

UNLISTED = {"(top level)": 3, "KB/": 2, "SKILLS/": 1, "output/": 1, "tasks/": 1}


def _cells(row):
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", row.strip().strip("|"))]


def _unquote(cell):
    return cell.strip("`").strip()


def _expand(cell):
    numbers = set()
    for part in cell.split(","):
        part = part.strip()
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if match:
            start = int(match.group(1))
            numbers.update(range(start, int(match.group(2) or start) + 1))
    return numbers


def parse_report(report):
    """Pull what the report says about source lines out of its markdown.

    Returns ``(flags, touched, kept)``: flagged ``(source, line)`` pairs, the
    source lines each file's rewrite table names, and each file's select ranges.
    """
    flags, touched, kept = set(), {}, {}
    section, source = None, None
    for line in report.splitlines():
        if line.startswith("## "):
            section, source = line[3:], None
            continue
        if section == "Flags" and line.startswith("| `"):
            cells = _cells(line)
            flags.add((_unquote(cells[0]), int(cells[1])))
        elif section == "Files":
            match = re.match(r"From `([^`]+)`", line)
            if match:
                source = match.group(1)
            elif source and line.startswith("| `"):
                cells = _cells(line)
                touched.setdefault(source, set()).update(_expand(cells[3]))
                selected = re.search(r"kept lines (\d+)-(\d+)", cells[1])
                if selected:
                    kept.setdefault(source, []).append((int(selected.group(1)), int(selected.group(2))))
    return flags, touched, kept


class FixtureRuns(TempDirTestCase):
    def run_fixture(self, manifest, **kwargs):
        kwargs.setdefault("gate", stub("--terms-from", str(manifest)))
        return self.run_tool(FIXTURE, manifest, **kwargs)


class DryRunReport(FixtureRuns):
    @classmethod
    def setUpClass(cls):
        cls.markers = planted_markers()

    def test_fixture_markers_exist(self):
        for marker in self.markers:
            with self.subTest(marker=marker["text"]):
                self.assertIn(marker["text"], (FIXTURE / marker["file"]).read_text(encoding="utf-8"))
                self.assertIn(marker["first_pass"], ("rewritten", "dropped", "flagged"))

    def test_every_planted_marker_is_rewritten_dropped_or_flagged(self):
        outcome, report = self.run_fixture(EXAMPLE_MANIFEST, dry_run=True)
        self.assertEqual(outcome.exit_code, ex.EXIT_REFUSED)
        by_source = {}
        for result in outcome.files:
            by_source.setdefault(result.entry.source, []).append(result)
        report_flags, touched, kept = parse_report(report)
        for marker in self.markers:
            text, source = marker["text"], marker["file"]
            lines = [n for n, line in enumerate((FIXTURE / source).read_text(encoding="utf-8").splitlines(), 1) if text in line]
            for number in lines:
                with self.subTest(file=source, line=number, text=text):
                    results = by_source[source]
                    exported = [r for r in results if r.line_map.get(number) is not None]
                    flagged = [
                        f for r in exported for f in r.flags
                        if f.line == number and (f.text.lower() in text.lower() or text.lower() in f.text.lower())
                    ]
                    if marker["first_pass"] == "flagged":
                        self.assertTrue(flagged, "expected a flag")
                        self.assertIn((source, number), report_flags)
                    elif marker["first_pass"] == "rewritten":
                        self.assertTrue(exported)
                        for result in exported:
                            self.assertNotIn(text, result.line_map[number])
                            self.assertTrue(any(e.line == number for e in result.events))
                        self.assertIn(number, touched.get(source, set()))
                    else:
                        self.assertEqual(exported, [], "expected the line to be dropped")
                        in_select = any(a <= number <= b for a, b in kept.get(source, []))
                        self.assertTrue(number in touched.get(source, set()) or (kept.get(source) and not in_select))

    def test_report_summarizes_the_run(self):
        outcome, report = self.run_fixture(EXAMPLE_MANIFEST, dry_run=True)
        self.assertEqual(outcome.unlisted, UNLISTED)
        self.assertIn("| Files exported | 34 |", report)
        self.assertIn("| Source files not in the manifest | 8 (counted, never copied) |", report)
        self.assertIn("| Gate canary | passed: the gate caught all 9 manifest terms |", report)
        for heading in ("## Result", "## Flags", "## Leak gate", "## Rewrites by rule", "## Files", "## Source files not in the manifest"):
            self.assertIn(heading, report)
        self.assertLess(report.index("## Flags"), report.index("## Files"))
        self.assertIn("| `KB/` | 2 |", report)
        self.assertEqual(len(outcome.flags), 19)


class RealRuns(FixtureRuns):
    def test_unhandled_markers_refuse_and_leave_nothing(self):
        outcome, report = self.run_fixture(EXAMPLE_MANIFEST)
        self.assertEqual(outcome.exit_code, ex.EXIT_REFUSED)
        self.assertIn("**REFUSED**, exit 1", report)
        self.assert_no_output()

    def test_handled_manifest_writes_a_clean_tree(self):
        outcome, report = self.run_fixture(HANDLED_MANIFEST)
        self.assertEqual(outcome.exit_code, ex.EXIT_OK, report)
        self.assertEqual(outcome.flags, [])
        self.assertEqual(outcome.gate.status, "clean")
        stage = self.tmp / "stage"
        manifest = json.loads(HANDLED_MANIFEST.read_text(encoding="utf-8"))
        snapshot = tree_snapshot(stage)
        generated = [folder + name for folder in manifest["plugins"] for name in ("/README.md", "/.claude-plugin/plugin.json")]
        self.assertEqual(sorted(snapshot), sorted([item["to"] for item in manifest["files"]] + generated))
        texts = {path: data.decode("utf-8") for path, (data, _) in snapshot.items()}
        for marker in planted_markers():
            for path, text in texts.items():
                self.assertNotIn(marker["text"], text, "%s still holds %r" % (path, marker["text"]))
        persona = re.compile(r"(?<![^\W_])(?:%s)(?![^\W_])" % "|".join(manifest["persona_map"]))
        owner = re.compile(r"(?i)(?<![^\W_])(?:%s)(?![^\W_])" % "|".join(re.escape(t) for t in manifest["owner_terms"]))
        flags = [re.compile(item["pattern"]) for item in manifest["flag_patterns"]]
        for path, text in texts.items():
            for regex in [persona, owner, ex.HOME_PATH_RE] + flags:
                self.assertIsNone(regex.search(text), "%s matches %s" % (path, regex.pattern))
        self.assertEqual(snapshot["engine/scripts/session.sh"][1], 0o755)
        self.assertEqual(snapshot["engine/scripts/router.py"][1], 0o755)
        self.assertEqual(snapshot["AGENTS.md"][1], 0o644)
        self.assertEqual(outcome.warnings, [])

    def test_output_is_deterministic(self):
        first, report_one = self.run_fixture(HANDLED_MANIFEST)
        self.assertEqual(first.exit_code, ex.EXIT_OK)
        tree_one = tree_snapshot(self.tmp / "stage")
        shutil.rmtree(str(self.tmp / "stage"))
        second, report_two = self.run_fixture(HANDLED_MANIFEST)
        self.assertEqual(second.exit_code, ex.EXIT_OK)
        self.assertEqual(tree_snapshot(self.tmp / "stage"), tree_one)
        self.assertEqual(report_two, report_one)
        _, dry_one = self.run_fixture(EXAMPLE_MANIFEST, dry_run=True, out_name="other")
        _, dry_two = self.run_fixture(EXAMPLE_MANIFEST, dry_run=True, out_name="other")
        self.assertEqual(dry_one, dry_two)

    def test_handled_manifest_differs_only_by_handling(self):
        base = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
        handled = json.loads(HANDLED_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(sorted(base), sorted(handled))
        for key in base:
            if key not in ("description", "files"):
                self.assertEqual(base[key], handled[key], key)
        self.assertEqual(len(base["files"]), len(handled["files"]))
        for old, new in zip(base["files"], handled["files"]):
            self.assertTrue(set(new) - set(old) <= {"replace", "drop"}, new["to"])
            for key, value in old.items():
                self.assertEqual(new[key], value)


class CommandLine(TempDirTestCase):
    """The real command line, in a subprocess, from the repository root."""

    def gate(self, manifest):
        return "python3 %s --terms-from %s" % (STUB_GATE.relative_to(REPO_ROOT).as_posix(), manifest.relative_to(REPO_ROOT).as_posix())

    def test_dry_run_with_report(self):
        report = self.tmp / "report.md"
        proc = run_cli(
            "--source", "fixtures/fake-command", "--manifest", "export/manifest.example.json",
            "--out", self.tmp / "stage", "--dry-run", "--report", report, "--leakgate", self.gate(EXAMPLE_MANIFEST),
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("extract: REFUSED (dry run), exit 1: 19 flags", proc.stdout)
        self.assertIn("report written to", proc.stdout)
        self.assertTrue(report.read_text(encoding="utf-8").startswith("# Extraction report"))
        self.assertFalse((self.tmp / "stage").exists())

    def test_report_goes_to_stdout_without_report_flag(self):
        proc = run_cli(
            "--source", "fixtures/fake-command", "--manifest", "export/manifest.example.json",
            "--out", self.tmp / "stage", "--dry-run", "--leakgate", self.gate(EXAMPLE_MANIFEST),
        )
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stdout.startswith("# Extraction report"))

    def test_real_run_with_the_handled_manifest(self):
        proc = run_cli(
            "--source", "fixtures/fake-command", "--manifest", "export/manifest.handled.example.json",
            "--out", self.tmp / "stage", "--report", self.tmp / "report.md", "--leakgate", self.gate(HANDLED_MANIFEST),
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("extract: WRITTEN, exit 0: 34 files written", proc.stdout)
        self.assertTrue((self.tmp / "stage" / "AGENTS.md").is_file())

    def test_usage_errors_exit_2(self):
        proc = run_cli("--source", "fixtures/fake-command", "--manifest", "export/manifest.example.json")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("the following arguments are required: --out", proc.stderr)
        proc = run_cli("--source", "fixtures/fake-command", "--manifest", "export/manifest.example.json", "--out", self.tmp / "x", "--leakgate", " ")
        self.assertEqual(proc.returncode, 2)
        bad = self.tmp / "bad.json"
        bad.write_text('{"schema_version": "1", "files": [{"from": "nope.md", "to": "internal/x.md"}]}', encoding="utf-8")
        proc = run_cli("--source", "fixtures/fake-command", "--manifest", bad, "--out", self.tmp / "stage", "--report", self.tmp / "r.md")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("does not exist in the source", proc.stderr)
        self.assertIn("inside internal/", (self.tmp / "r.md").read_text(encoding="utf-8"))
        self.assertFalse((self.tmp / "stage").exists())


if __name__ == "__main__":
    unittest.main()


class StagedTreeIsUsable(FixtureRuns):
    """A clean run through the real leak gate, and a staged tree the repo's own loaders accept."""

    def run_real_gate(self):
        manifest = json.loads(HANDLED_MANIFEST.read_text(encoding="utf-8"))
        terms = list(manifest["persona_map"]) + list(manifest["owner_terms"])
        previous = os.environ.get("LEAKGATE_DENYLIST")
        os.environ["LEAKGATE_DENYLIST"] = "\n".join(terms)
        self.addCleanup(_restore_env, "LEAKGATE_DENYLIST", previous)
        work = self.tmp / "work"
        work.mkdir(exist_ok=True)
        opts = ex.Options(
            source=str(FIXTURE), manifest=str(HANDLED_MANIFEST), out=str(self.tmp / "stage"),
            report=str(self.tmp / "report.md"), leakgate=None, gate_timeout=120.0, work_dir=str(work),
        )
        outcome = ex.run_extraction(opts)
        return outcome, ex.render_report(outcome)

    def test_real_leak_gate_passes_the_handled_tree(self):
        outcome, report = self.run_real_gate()
        self.assertEqual(outcome.exit_code, ex.EXIT_OK, report)
        self.assertEqual(outcome.gate.status, "clean", report)
        self.assertIn("passed: the gate caught all", report)

    def test_staged_roles_load_and_validate(self):
        outcome, report = self.run_fixture(HANDLED_MANIFEST)
        self.assertEqual(outcome.exit_code, ex.EXIT_OK, report)
        roles = osgen_roles.load_roles(str(self.tmp / "stage" / "engine" / "roles"))
        osgen_roles.validate_set(roles)
        self.assertEqual(
            sorted(role.id for role in roles),
            ["builder", "orchestrator", "owner-auditor", "repurposer", "researcher", "sensor"],
        )
        self.assertEqual(osgen_roles.main_role(roles).id, "orchestrator")

    def test_staged_skills_lint_clean_and_none_are_stable(self):
        outcome, report = self.run_fixture(HANDLED_MANIFEST)
        self.assertEqual(outcome.exit_code, ex.EXIT_OK, report)
        result = skills_lint.lint_roots([self.tmp / "stage" / "labs"])
        self.assertEqual([str(p) for p in result.problems], [])
        self.assertEqual((result.plugins, result.skills), (4, 6))
        for path in (self.tmp / "stage" / "labs").glob("*/skills/*/SKILL.md"):
            self.assertNotIn('status: "stable"', path.read_text(encoding="utf-8"), path)

    def test_role_source_with_frontmatter_is_flagged(self):
        source = self.make_source({"soul.md": "---\nid: x\n---\n# Soul\n"})
        manifest = {
            "schema_version": "1", "persona_map": {"ZEPHYR": "orchestrator"},
            "owner_terms": {"Avery": "the owner"},
            "roles": {"orchestrator": {"description": "d", "tier": "high", "main": True}},
            "files": [{"from": "soul.md", "to": "engine/roles/orchestrator/soul.md"}],
        }
        outcome, _ = self.run_tool(source, self.write_manifest(manifest), dry_run=True)
        self.assertEqual([f.flag for f in outcome.files[0].flags], ["role-source-frontmatter"])


def _restore_env(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
