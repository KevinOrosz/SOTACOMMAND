"""The root .claude-plugin/marketplace.json."""

import json
import os
import unittest

from . import support

from osgen import MARKETPLACE_NAME, OWNER_NAME, marketplace

RESERVED_MARKETPLACE_NAMES = {
    "claude-code-marketplace", "claude-code-plugins", "claude-plugins-official",
    "anthropic-marketplace", "anthropic-plugins", "agent-skills", "anthropic-agent-skills",
    "inline", "builtin", "skills-dir", "synced", "npm", "pip", "uv", "cargo", "github", "gh",
}


def add_plugin(root, group, folder, name, description="A test plugin."):
    manifest = {"name": name, "version": "0.1.0", "description": description}
    support.write(
        os.path.join(root, group, folder, ".claude-plugin", "plugin.json"), json.dumps(manifest)
    )


def add_skill(root, group, folder, skill, status="stable", text=None):
    """A SKILL.md in the portable format, with metadata as M4's contract defines it."""
    if text is None:
        text = (
            "---\n"
            "name: %s\n"
            "description: A test skill.\n"
            "license: Apache-2.0\n"
            "metadata:\n"
            '  version: "1.0.0"\n'
            "  status: %s\n"
            "  pack: %s\n"
            "  min-tier: low\n"
            "  roles: builder\n"
            "  gate: none\n"
            "---\n\nDo the thing.\n" % (skill, status, folder)
        )
    support.write(os.path.join(root, group, folder, "skills", skill, "SKILL.md"), text)


class MarketplaceTests(support.TempDirTestCase):
    def generate(self, *extra):
        return support.run_generate("--roles", support.SAMPLE_ROLES, "--out", self.tmp, *extra)

    def marketplace(self):
        return json.loads(support.read(self.path(".claude-plugin", "marketplace.json")))

    def test_engine_only_when_packs_and_labs_are_absent(self):
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.marketplace()
        self.assertEqual(data["name"], MARKETPLACE_NAME)
        self.assertEqual(data["owner"], {"name": OWNER_NAME})
        self.assertTrue(data["description"])
        self.assertEqual([p["name"] for p in data["plugins"]], ["sotacommand-engine"])
        self.assertEqual(data["plugins"][0]["source"], "./engine")

    def test_packs_and_labs_are_discovered(self):
        add_plugin(self.tmp, "packs", "beta", "beta-pack")
        add_plugin(self.tmp, "packs", "alpha", "alpha-pack", "Alpha skills.")
        add_plugin(self.tmp, "labs", "trial", "trial-lab")
        os.makedirs(self.path("packs", "unfinished", "skills"))
        support.write(self.path("labs", "README.md"), "# Labs\n")
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped packs/unfinished", result.stderr)
        plugins = self.marketplace()["plugins"]
        self.assertEqual(
            [(p["name"], p["source"]) for p in plugins],
            [("sotacommand-engine", "./engine"), ("alpha-pack", "./packs/alpha"),
             ("beta-pack", "./packs/beta"), ("trial-lab", "./labs/trial")],
        )
        by_name = {p["name"]: p for p in plugins}
        self.assertEqual(by_name["sotacommand-engine"]["category"], "engine")
        self.assertEqual(by_name["alpha-pack"]["category"], "skills")
        self.assertEqual(by_name["trial-lab"]["category"], "labs")
        for plugin in plugins:
            self.assertTrue(plugin["source"].startswith("./"))
            self.assertIsInstance(plugin["defaultEnabled"], bool)
            self.assertNotIn("version", plugin)
            self.assertNotIn("description", plugin)

    def enabled(self):
        return {p["name"]: p["defaultEnabled"] for p in self.marketplace()["plugins"]}

    def test_engine_is_always_enabled(self):
        self.assertEqual(self.generate().returncode, 0)
        self.assertIs(self.enabled()["sotacommand-engine"], True)

    def test_pack_with_only_stable_skills_is_enabled(self):
        add_plugin(self.tmp, "packs", "core", "core-pack")
        add_skill(self.tmp, "packs", "core", "first")
        add_skill(self.tmp, "packs", "core", "second", status='"stable"')
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(self.enabled()["core-pack"], True)
        self.assertNotIn("packs/core starts disabled", result.stderr)

    def test_one_unproven_skill_disables_the_pack(self):
        add_plugin(self.tmp, "packs", "mixed", "mixed-pack")
        add_skill(self.tmp, "packs", "mixed", "proven")
        add_skill(self.tmp, "packs", "mixed", "trial", status="experimental")
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(self.enabled()["mixed-pack"], False)
        self.assertIn("packs/mixed starts disabled (not every skill is stable: trial=experimental)",
                      result.stderr)

    def test_retired_skills_are_not_stable(self):
        add_plugin(self.tmp, "packs", "old", "old-pack")
        add_skill(self.tmp, "packs", "old", "legacy", status="retired")
        self.assertEqual(self.generate().returncode, 0)
        self.assertIs(self.enabled()["old-pack"], False)

    def test_pack_without_skills_is_disabled(self):
        add_plugin(self.tmp, "packs", "empty", "empty-pack")
        result = self.generate()
        self.assertIs(self.enabled()["empty-pack"], False)
        self.assertIn("no skills/<skill>/SKILL.md found", result.stderr)

    def test_unreadable_or_status_less_skill_is_not_stable(self):
        add_plugin(self.tmp, "packs", "odd", "odd-pack")
        add_skill(self.tmp, "packs", "odd", "good")
        add_skill(self.tmp, "packs", "odd", "broken", text="---\nname: broken\nmetadata:\n  status: [\n---\n")
        add_skill(self.tmp, "packs", "odd", "bare", text="---\nname: bare\ndescription: d\n---\n")
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(self.enabled()["odd-pack"], False)
        self.assertIn("bare=no readable status", result.stderr)
        self.assertIn("broken=no readable status", result.stderr)
        self.assertNotIn("good=", result.stderr)

    def test_status_is_read_the_way_skills_lint_reads_it(self):
        add_plugin(self.tmp, "packs", "noted", "noted-pack")
        add_skill(self.tmp, "packs", "noted", "commented", status="stable  # qualified 2026-11-01")
        add_skill(self.tmp, "packs", "noted", "single", status="'stable'")
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(self.enabled()["noted-pack"], True, result.stderr)
        for skill in ("commented", "single"):
            path = os.path.join(self.tmp, "packs", "noted", "skills", skill, "SKILL.md")
            self.assertEqual(marketplace.skill_status(path), "stable")

    def test_labs_are_disabled_even_when_stable(self):
        add_plugin(self.tmp, "labs", "trial", "trial-lab")
        add_skill(self.tmp, "labs", "trial", "ready")
        self.assertEqual(self.generate().returncode, 0)
        self.assertIs(self.enabled()["trial-lab"], False)

    def test_promoting_the_last_skill_to_stable_enables_the_pack(self):
        add_plugin(self.tmp, "packs", "core", "core-pack")
        add_skill(self.tmp, "packs", "core", "only", status="experimental")
        self.assertEqual(self.generate().returncode, 0)
        add_skill(self.tmp, "packs", "core", "only", status="stable")
        check = self.generate("--check")
        self.assertEqual(check.returncode, 1)
        self.assertIn("changed: .claude-plugin/marketplace.json", check.stdout)
        self.assertEqual(self.generate().returncode, 0)
        self.assertIs(self.enabled()["core-pack"], True)

    def test_pack_description_edits_are_not_drift(self):
        add_plugin(self.tmp, "packs", "alpha", "alpha-pack", "First wording.")
        self.assertEqual(self.generate().returncode, 0)
        add_plugin(self.tmp, "packs", "alpha", "alpha-pack", "Second wording.")
        self.assertEqual(self.generate("--check").returncode, 0)

    def test_new_packs_are_picked_up_on_the_next_run(self):
        self.assertEqual(self.generate().returncode, 0)
        self.assertEqual(self.generate("--check").returncode, 0)
        add_plugin(self.tmp, "packs", "late", "late-pack")
        check = self.generate("--check")
        self.assertEqual(check.returncode, 1)
        self.assertIn("changed: .claude-plugin/marketplace.json", check.stdout)
        self.assertEqual(self.generate().returncode, 0)
        self.assertEqual(self.generate("--check").returncode, 0)
        self.assertIn("late-pack", [p["name"] for p in self.marketplace()["plugins"]])

    def test_reserved_words_in_plugin_names_are_errors(self):
        for name in ("claude-tools", "my-anthropic-pack", "Claude"):
            with self.subTest(name=name):
                add_plugin(self.tmp, "packs", "bad", name)
                result = self.generate()
                self.assertEqual(result.returncode, 2)
                self.assertIn("must not contain", result.stderr)

    def test_invalid_plugin_names_are_errors(self):
        add_plugin(self.tmp, "packs", "bad", "has space")
        self.assertEqual(self.generate().returncode, 2)

    def test_duplicate_plugin_names_are_errors(self):
        add_plugin(self.tmp, "packs", "one", "same-name")
        add_plugin(self.tmp, "labs", "two", "same-name")
        result = self.generate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate plugin name", result.stderr)

    def test_malformed_manifest_is_an_error(self):
        support.write(self.path("packs", "broken", ".claude-plugin", "plugin.json"), "{not json")
        result = self.generate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("not valid JSON", result.stderr)


class MarketplaceConstantsTests(unittest.TestCase):
    def test_marketplace_name_is_allowed(self):
        marketplace.check_name(MARKETPLACE_NAME, "marketplace")
        self.assertNotIn(MARKETPLACE_NAME, RESERVED_MARKETPLACE_NAMES)
        self.assertFalse(MARKETPLACE_NAME.lower().startswith("claudeai-"))

    def test_committed_marketplace_lists_the_engine_first(self):
        data = json.loads(support.read(os.path.join(support.REPO_ROOT, ".claude-plugin", "marketplace.json")))
        self.assertEqual(data["plugins"][0]["name"], "sotacommand-engine")
        self.assertEqual(data["plugins"][0]["source"], "./engine")
        self.assertIs(data["plugins"][0]["defaultEnabled"], True)
        for plugin in data["plugins"]:
            source = os.path.join(support.REPO_ROOT, plugin["source"])
            self.assertTrue(os.path.isfile(os.path.join(source, ".claude-plugin", "plugin.json")), source)

    def test_committed_marketplace_follows_the_stable_rule(self):
        data = json.loads(support.read(os.path.join(support.REPO_ROOT, ".claude-plugin", "marketplace.json")))
        for plugin in data["plugins"][1:]:
            source = os.path.join(support.REPO_ROOT, plugin["source"])
            expected = plugin["source"].startswith("./packs/") and marketplace.stable_pack(source)[0]
            self.assertIs(plugin["defaultEnabled"], expected, plugin["name"])


if __name__ == "__main__":
    unittest.main()
