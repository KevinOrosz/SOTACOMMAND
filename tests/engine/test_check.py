"""--check drift detection and write behavior of adapters/generate.py."""

import os
import shutil
import unittest

from . import support


class CommittedOutputsTests(unittest.TestCase):
    """The committed outputs in this repository match the generators."""

    def test_check_with_explicit_arguments(self):
        result = support.run_generate("--roles", "fixtures/roles-sample", "--out", ".", "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("generated files match", result.stdout)

    def test_check_with_contract_defaults(self):
        # The AGENTS.md command contract: `python3 adapters/generate.py --check`.
        result = support.run_generate("--check", cwd=support.TOOLS_DIR)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DriftTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.roles_dir = self.path("roles")
        shutil.copytree(support.SAMPLE_ROLES, self.roles_dir)
        self.out = self.path("out")
        os.makedirs(self.out)
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def generate(self, *extra):
        return support.run_generate("--roles", self.roles_dir, "--out", self.out, *extra)

    def check(self):
        return self.generate("--check")

    def test_fresh_output_has_no_drift(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_hand_edit_is_drift_with_a_diff(self):
        agent = os.path.join(self.out, "engine", "agents", "builder.md")
        support.write(agent, support.read(agent).replace("model: inherit", "model: other"))
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("changed: engine/agents/builder.md", result.stdout)
        self.assertIn("-model: other", result.stdout)
        self.assertIn("+model: inherit", result.stdout)

    def test_missing_output_is_drift(self):
        os.remove(os.path.join(self.out, "engine", "hooks", "hooks.json"))
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing: engine/hooks/hooks.json", result.stdout)

    def test_stale_file_in_an_owned_directory_is_drift(self):
        support.write(os.path.join(self.out, "engine", "agents", "retired.md"), "---\nname: retired\n---\n")
        support.write(os.path.join(self.out, ".github", "agents", "retired.agent.md"), "x\n")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not generated (stale): engine/agents/retired.md", result.stdout)
        self.assertIn("not generated (stale): .github/agents/retired.agent.md", result.stdout)

    def test_role_source_change_is_drift(self):
        soul = os.path.join(self.roles_dir, "sensor", "soul.md")
        support.write(soul, support.read(soul).replace("tier: low", "tier: mid"))
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("changed: engine/agents/sensor.md", result.stdout)

    def test_bytecode_caches_and_crlf_are_not_drift(self):
        support.write(os.path.join(self.out, "engine", "tools", "__pycache__", "x.cpython-39.pyc"), "x")
        support.write(os.path.join(self.out, "engine", "tools", ".DS_Store"), "x")
        settings = os.path.join(self.out, "engine", "settings.json")
        with open(settings, "rb") as handle:
            content = handle.read()
        with open(settings, "wb") as handle:
            handle.write(content.replace(b"\n", b"\r\n"))
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_files_outside_owned_paths_are_left_alone(self):
        keep = support.write(os.path.join(self.out, "engine", "roles", "x", "soul.md"), "source\n")
        other = support.write(os.path.join(self.out, ".github", "workflows", "ci.yml"), "x\n")
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.generate().returncode, 0)
        self.assertTrue(os.path.isfile(keep) and os.path.isfile(other))

    def test_write_is_idempotent(self):
        result = self.generate()
        self.assertEqual(result.returncode, 0)
        self.assertIn(" 0 written,", result.stdout)

    def test_removed_role_removes_its_outputs(self):
        shutil.rmtree(os.path.join(self.roles_dir, "repurposer"))
        soul = os.path.join(self.roles_dir, "orchestrator", "soul.md")
        support.write(soul, support.read(soul).replace(", repurposer)", ")"))
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed engine/agents/repurposer.md", result.stdout)
        self.assertIn("removed .github/agents/repurposer.agent.md", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.out, "engine", "agents", "repurposer.md")))
        self.assertEqual(self.check().returncode, 0)


class UsageTests(unittest.TestCase):
    def test_usage_errors_exit_2(self):
        for args in (["--target", "x"], ["--harness", "codex"], ["--include-labs"],
                     ["--harness", "codex", "--target", "x", "--check"], ["--unknown"]):
            with self.subTest(args=args):
                self.assertEqual(support.run_generate(*args).returncode, 2)

    def test_role_errors_exit_2(self):
        result = support.run_generate("--roles", os.path.join(support.REPO_ROOT, "no-such-dir"), "--check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("roles directory not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
