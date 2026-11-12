"""The Codex install: pack skills copied into .agents/skills/ plus AGENTS.md."""

import json
import os
import unittest

from . import support


def skill(root, group, plugin, name, extra=None):
    base = os.path.join(root, group, plugin, "skills", name)
    support.write(os.path.join(base, "SKILL.md"),
                  "---\nname: %s\ndescription: Test skill %s.\n---\n\nDo the thing.\n" % (name, name))
    for relative, text in (extra or {}).items():
        support.write(os.path.join(base, *relative.split("/")), text)
    return base


class CodexInstallTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.path("repo")
        self.target = self.path("project")
        os.makedirs(self.target)
        support.write(os.path.join(self.repo, "packs", "core", ".claude-plugin", "plugin.json"), '{"name": "core"}')
        self.alpha = skill(self.repo, "packs", "core", "alpha", {
            "references/guide.md": "# Guide\n",
            "evals/case-1/prompt.md": "prompt\n",
        })
        skill(self.repo, "packs", "extra", "beta")
        skill(self.repo, "labs", "trial", "gamma")
        if hasattr(os, "symlink") and os.name != "nt":
            support.write(os.path.join(self.repo, "shared.sh"), "echo shared\n")
            os.makedirs(os.path.join(self.alpha, "scripts"))
            os.symlink(os.path.join(self.repo, "shared.sh"), os.path.join(self.alpha, "scripts", "tool.sh"))

    def install(self, *extra):
        return support.run_generate("--harness", "codex", "--target", self.target,
                                    "--root", self.repo, "--roles", support.SAMPLE_ROLES, *extra)

    def skills_dir(self, *parts):
        return os.path.join(self.target, ".agents", "skills", *parts)

    def test_installs_pack_skills_as_copies(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(self.skills_dir("alpha", "SKILL.md")))
        self.assertTrue(os.path.isfile(self.skills_dir("alpha", "references", "guide.md")))
        self.assertTrue(os.path.isfile(self.skills_dir("beta", "SKILL.md")))
        self.assertFalse(os.path.exists(self.skills_dir("alpha", "evals")))
        self.assertFalse(os.path.exists(self.skills_dir("gamma")), "labs are opt-in")
        for current, dirs, files in os.walk(self.target):
            for name in dirs + files:
                self.assertFalse(os.path.islink(os.path.join(current, name)), "no symlinks: %s" % name)
        copied_tool = self.skills_dir("alpha", "scripts", "tool.sh")
        if os.path.exists(copied_tool):
            self.assertEqual(support.read(copied_tool), "echo shared\n")

    def test_writes_agents_md_from_the_roles(self):
        self.install()
        text = support.read(os.path.join(self.target, "AGENTS.md"))
        self.assertTrue(text.startswith("# AGENTS.md"))
        for role in ("orchestrator", "researcher", "builder", "owner-auditor", "sensor", "repurposer"):
            self.assertIn("`%s`" % role, text)
        self.assertIn("`alpha`", text)
        self.assertIn("`beta`", text)
        self.assertIn("writes only `memory/**`", text)
        self.assertNotIn("{{", text)

    def test_existing_agents_md_is_kept(self):
        support.write(os.path.join(self.target, "AGENTS.md"), "# Mine\n")
        result = self.install()
        self.assertEqual(result.returncode, 0)
        self.assertIn("kept existing AGENTS.md", result.stdout)
        self.assertEqual(support.read(os.path.join(self.target, "AGENTS.md")), "# Mine\n")

    def test_labs_are_installed_on_request(self):
        result = self.install("--include-labs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(self.skills_dir("gamma", "SKILL.md")))

    def test_reinstall_updates_and_removes_managed_skills(self):
        self.assertEqual(self.install().returncode, 0)
        support.write(os.path.join(self.alpha, "SKILL.md"), "---\nname: alpha\ndescription: v2.\n---\n\nNew.\n")
        os.remove(os.path.join(self.repo, "packs", "extra", "skills", "beta", "SKILL.md"))
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("New.", support.read(self.skills_dir("alpha", "SKILL.md")))
        self.assertIn("removed skill beta", result.stdout)
        self.assertFalse(os.path.exists(self.skills_dir("beta")))
        state = json.loads(support.read(self.skills_dir(".sotacommand-install.json")))
        self.assertEqual(state["managed"], ["alpha"])

    def test_unmanaged_skill_folders_are_not_replaced(self):
        own = support.write(self.skills_dir("alpha", "SKILL.md"), "mine\n")
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("skipped alpha", result.stdout)
        self.assertEqual(support.read(own), "mine\n")
        self.assertTrue(os.path.isfile(self.skills_dir("beta", "SKILL.md")))
        forced = self.install("--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertIn("Do the thing.", support.read(own))

    def test_duplicate_skill_names_are_an_error(self):
        skill(self.repo, "packs", "other", "alpha")
        result = self.install()
        self.assertEqual(result.returncode, 2)
        self.assertIn("skill names must be unique", result.stderr)

    def test_missing_target_is_an_error(self):
        result = support.run_generate("--harness", "codex", "--target", self.path("nope"),
                                      "--root", self.repo, "--roles", support.SAMPLE_ROLES)
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)

    def test_repo_without_packs_installs_agents_md_only(self):
        empty_repo = self.path("empty-repo")
        os.makedirs(empty_repo)
        result = support.run_generate("--harness", "codex", "--target", self.target,
                                      "--root", empty_repo, "--roles", support.SAMPLE_ROLES)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("(none installed yet)", support.read(os.path.join(self.target, "AGENTS.md")))


if __name__ == "__main__":
    unittest.main()
