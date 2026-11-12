"""Path rules, self-protection of internal/, and which files a check covers."""

import os
import unittest

from tests.leakgate import support
import leakgate
from leakgate import UsageError

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256))


def rule_ids_by_path(findings):
    result = {}
    for finding in findings:
        result.setdefault(finding.path, []).append(finding.rule_id)
    return result


class SelfProtectionTest(unittest.TestCase):
    """internal/ must never ship: every file under it is a finding."""

    @unittest.skipUnless(os.path.isdir(os.path.join(support.REPO_ROOT, "internal")), "internal/ is only in the private staging repository")
    def test_every_real_file_under_internal_is_flagged(self):
        config = support.production_config()
        internal = os.path.join(support.REPO_ROOT, "internal")
        checked = 0
        for dirpath, _dirnames, filenames in os.walk(internal):
            for name in filenames:
                path = os.path.join(dirpath, name)
                relpath = os.path.relpath(path, support.REPO_ROOT).replace(os.sep, "/")
                findings = leakgate.scan([path], config, root=support.REPO_ROOT)
                with self.subTest(path=relpath):
                    self.assertIn((relpath, 0, 0, "internal-dir"), [(f.path, f.line, f.col, f.rule_id) for f in findings])
                checked += 1
        self.assertGreater(checked, 0, "internal/ should hold files in the staging repo")

    def test_any_kind_of_file_under_internal_is_flagged(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {
                "internal/notes.md": "plain words\n",
                "internal/deep/nested/data.json": "{}\n",
                "internal/.hidden": "x\n",
                "internal/empty.txt": "",
                "internal/image.png": PNG_BYTES,
            })
            if hasattr(os, "symlink"):
                os.symlink("notes.md", os.path.join(tmp, "internal", "link.md"))
            findings = leakgate.scan([tmp], support.production_config(), root=tmp)
            flagged = {f.path for f in findings if f.rule_id == "internal-dir"}
            expected = {"internal/notes.md", "internal/deep/nested/data.json", "internal/.hidden",
                        "internal/empty.txt", "internal/image.png"}
            if hasattr(os, "symlink"):
                expected.add("internal/link.md")
            self.assertEqual(expected, flagged)

    @unittest.skipUnless(os.path.isdir(os.path.join(support.REPO_ROOT, "internal")), "internal/ is only in the private staging repository")
    def test_cli_check_internal_plan_exits_1(self):
        completed = support.run_cli("check", "internal/PLAN.md")
        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertIn("internal/PLAN.md:0:0: [internal-dir @ file]", completed.stdout)

    @unittest.skipUnless(os.path.isdir(os.path.join(support.REPO_ROOT, "internal")), "internal/ is only in the private staging repository")
    def test_check_dot_flags_internal_unless_excluded(self):
        flagged = support.run_json("check", ".", "--json")
        self.assertEqual(1, flagged["_returncode"])
        internal_paths = {f["path"] for f in flagged["findings"] if f["rule_id"] == "internal-dir"}
        self.assertIn("internal/PLAN.md", internal_paths)
        self.assertIn("internal/SPEC.md", internal_paths)

        excluded = support.run_json("check", ".", "--exclude", "internal/**", "--json")
        self.assertIn(excluded["_returncode"], (0, 1))  # other lanes' fixtures may still have findings
        self.assertEqual([], [f["path"] for f in excluded["findings"] if f["path"].startswith("internal/")])

    def test_check_agents_md_exits_0(self):
        completed = support.run_cli("check", "AGENTS.md")
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)


class PathRuleTest(unittest.TestCase):
    def test_each_denied_location(self):
        files = {
            "KB/a.md": "x", "docs/KB/b.md": "x", "output/r.md": "x", "x/tasks/t.md": "x",
            ".env": "x", "config/.env.production": "x", "fixture-owner.md": "x", "a/fixture-x.md": "x",
            "HANDOFF.md": "x", "memory/HANDOFF.md": "x", "ok/readme.md": "x", "kb/lower.md": "x",
        }
        with support.temp_dir() as tmp:
            support.write_files(tmp, files)
            by_path = rule_ids_by_path(leakgate.scan([tmp], support.production_config(), root=tmp))
        self.assertEqual(["private-dir"], by_path["KB/a.md"])
        self.assertEqual(["private-dir"], by_path["docs/KB/b.md"])
        self.assertEqual(["private-dir"], by_path["output/r.md"])
        self.assertEqual(["private-dir"], by_path["x/tasks/t.md"])
        self.assertEqual(["env-file"], by_path[".env"])
        self.assertEqual(["env-file"], by_path["config/.env.production"])
        self.assertEqual(["owner-fixture"], by_path["fixture-owner.md"])
        self.assertEqual(["owner-fixture"], by_path["a/fixture-x.md"])
        self.assertEqual(["handoff-file"], by_path["HANDOFF.md"])
        self.assertEqual(["handoff-file"], by_path["memory/HANDOFF.md"])
        self.assertNotIn("ok/readme.md", by_path)
        self.assertNotIn("kb/lower.md", by_path)

    def test_memory_templates_are_allowed(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"engine/templates/memory/HANDOFF.md": "# Handoff\n"})
            self.assertEqual([], leakgate.scan([tmp], support.production_config(), root=tmp))

    def test_the_file_path_itself_is_scanned(self):
        with support.temp_dir() as tmp:
            doc = os.path.join(tmp, "notes", "QXJUNO-plan.md")
            support.write_files(tmp, {"notes/QXJUNO-plan.md": "nothing private inside\n"})
            config = leakgate.load_config(support.RULES, env={"LEAKGATE_DENYLIST": "QXJUNO"})
            (finding,) = leakgate.scan([doc], config, root=tmp)
            self.assertEqual(("notes/QXJUNO-plan.md", 0, 7, "env-denylist"), (finding.path, finding.line, finding.col, finding.rule_id))
            self.assertNotIn("QXJUNO", finding.excerpt)


class RootResolutionTest(unittest.TestCase):
    def test_directory_outside_git_is_its_own_root(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"stage/internal/x.md": "x", "stage/ok.md": "x"})
            report = leakgate.scan_report([os.path.join(tmp, "stage")], support.production_config(), cwd=support.REPO_ROOT)
            self.assertEqual(["internal/x.md"], [f.path for f in report.findings])
            self.assertEqual(2, report.files_scanned)

    def test_file_outside_cwd_and_git_is_relative_to_its_directory(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"a/HANDOFF.md": "x"})
            findings = leakgate.scan([os.path.join(tmp, "a", "HANDOFF.md")], support.production_config(), cwd=support.REPO_ROOT)
            self.assertEqual(["HANDOFF.md"], [f.path for f in findings])

    def test_file_inside_cwd_is_relative_to_cwd(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"internal/x.md": "x"})
            findings = leakgate.scan(["internal/x.md"], support.production_config(), cwd=tmp)
            self.assertEqual([("internal/x.md", "internal-dir")], [(f.path, f.rule_id) for f in findings])

    def test_explicit_root(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"top/internal/x.md": "x"})
            target = os.path.join(tmp, "top", "internal")
            findings = leakgate.scan([target], support.production_config(), root=os.path.join(tmp, "top"))
            self.assertEqual(["internal/x.md"], [f.path for f in findings])
            with self.assertRaises(UsageError):
                leakgate.scan([target], support.production_config(), root=os.path.join(tmp, "elsewhere"))
            os.makedirs(os.path.join(tmp, "elsewhere"))
            with self.assertRaises(UsageError):
                leakgate.scan([target], support.production_config(), root=os.path.join(tmp, "elsewhere"))

    def test_missing_path_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            leakgate.scan(["/definitely/not/here"], support.production_config())
        self.assertEqual(2, support.run_cli("check", "no-such-file.md").returncode)


@unittest.skipUnless(support.git_available(), "git is not installed")
class GitSelectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        support.write_files(self.tmp, {
            "sub/tracked.md": "x",
            "sub/untracked.md": "x",
            "sub/ignored.log": "x",
            "sub/KB/notes.md": "x",
            "cache/only.log": "x",
        })
        support.init_git_repo(self.tmp, ["sub/tracked.md", ".gitignore"], gitignore="*.log\n")

    def scanned(self, report):
        return report.files_scanned

    def test_tracked_and_untracked_files_are_scanned_ignored_are_not(self):
        report = leakgate.scan_report([os.path.join(self.tmp, "sub")], support.production_config(), cwd=support.REPO_ROOT)
        self.assertEqual(3, report.files_scanned)  # tracked.md, untracked.md, KB/notes.md
        self.assertEqual(["sub/KB/notes.md"], [f.path for f in report.findings])
        self.assertTrue(any("git-ignored" in warning for warning in report.warnings))

    def test_all_files_includes_ignored_files(self):
        report = leakgate.scan_report([os.path.join(self.tmp, "sub")], support.production_config(), all_files=True, cwd=support.REPO_ROOT)
        self.assertEqual(4, report.files_scanned)

    def test_directory_of_only_ignored_files_fails_closed(self):
        with self.assertRaises(UsageError):
            leakgate.scan_report([os.path.join(self.tmp, "cache")], support.production_config(), cwd=support.REPO_ROOT)
        completed = support.run_cli("check", os.path.join(self.tmp, "cache"))
        self.assertEqual(2, completed.returncode)
        self.assertIn("--all-files", completed.stderr)

    def test_explicit_ignored_file_is_scanned(self):
        report = leakgate.scan_report([os.path.join(self.tmp, "sub", "ignored.log")], support.production_config(), cwd=support.REPO_ROOT)
        self.assertEqual(1, report.files_scanned)

    def test_paths_are_relative_to_the_work_tree_top(self):
        support.write_files(self.tmp, {"sub/internal/x.md": "x", "internal/y.md": "x"})
        targets = [os.path.join(self.tmp, "sub"), os.path.join(self.tmp, "internal")]
        findings = leakgate.scan(targets, support.production_config(), cwd=support.REPO_ROOT)
        # "internal/**" is anchored at the work tree's top level, as written.
        self.assertEqual(["internal/y.md"], [f.path for f in findings if f.rule_id == "internal-dir"])
        self.assertIn("sub/KB/notes.md", [f.path for f in findings])

    def test_tracked_file_deleted_from_the_work_tree(self):
        os.remove(os.path.join(self.tmp, "sub", "tracked.md"))
        report = leakgate.scan_report([os.path.join(self.tmp, "sub")], support.production_config(), cwd=support.REPO_ROOT)
        self.assertTrue(any("missing" in warning for warning in report.warnings))
        self.assertEqual(0, len(report.errors))


class FileKindTest(unittest.TestCase):
    def test_binary_files_get_path_rules_but_no_content_scan(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"KB/photo.png": PNG_BYTES, "docs/photo.png": PNG_BYTES})
            report = leakgate.scan_report([tmp], support.production_config(), root=tmp)
            self.assertEqual(2, report.files_binary)
            self.assertEqual([("KB/photo.png", "private-dir")], [(f.path, f.rule_id) for f in report.findings])

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks")
    def test_symlinks_are_scanned_as_their_target_path_and_never_followed(self):
        samples = support.load_samples()
        home_path = samples["home-path"]["flag"][0]
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"elsewhere/secret.md": "QXJUNO\n"})
            os.makedirs(os.path.join(tmp, "repo"))
            os.symlink(home_path, os.path.join(tmp, "repo", "dangling"))
            os.symlink(os.path.join("..", "elsewhere"), os.path.join(tmp, "repo", "dirlink"))
            config = leakgate.load_config(support.RULES, env={"LEAKGATE_DENYLIST": "QXJUNO"})
            findings = leakgate.scan([os.path.join(tmp, "repo")], config, root=os.path.join(tmp, "repo"))
            self.assertEqual([("dangling", 1, "home-path")], [(f.path, f.line, f.rule_id) for f in findings])

    def test_git_metadata_is_never_scanned(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {".git/config": "x", "KB/.git": "gitdir: x", "a.md": "x"})
            report = leakgate.scan_report([tmp], support.production_config(), root=tmp, all_files=True)
            self.assertEqual(1, report.files_scanned)

    @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0, "root can read anything")
    def test_unreadable_file_fails_closed(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"a.md": "x"})
            path = os.path.join(tmp, "a.md")
            os.chmod(path, 0)
            try:
                completed = support.run_cli("check", path)
                self.assertEqual(2, completed.returncode)
                self.assertIn("can't read", completed.stderr)
            finally:
                os.chmod(path, 0o600)

    def test_exclude_globs(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"KB/a.md": "x", "KB/b.md": "x", "docs/.env": "x", "logs/KB/debug.log": "x"})
            findings = leakgate.scan([tmp], support.production_config(), root=tmp, exclude=["KB/a.md", "*.log"])
            self.assertEqual(["KB/b.md", "docs/.env"], [f.path for f in findings])
            # gitignore semantics: "*" also matches an empty name, so "*.env" covers ".env".
            self.assertEqual(["KB/b.md"], [f.path for f in leakgate.scan([tmp], support.production_config(), root=tmp, exclude=["KB/a.md", "*.env", "*.log"])])
            self.assertEqual(2, support.run_cli("check", tmp, "--exclude", "bad\\glob").returncode)


if __name__ == "__main__":
    unittest.main()
