"""The role source contract (adapters/ROLES.md)."""

import os
import unittest

from . import support

from osgen import roles
from osgen.roles import RoleError


class SampleRolesTests(unittest.TestCase):
    """The synthetic sample roles follow SPEC section 4.1 and the M5 brief."""

    @classmethod
    def setUpClass(cls):
        cls.roles = roles.by_id(roles.load_roles(support.SAMPLE_ROLES))

    def test_six_roles_with_one_main(self):
        self.assertEqual(
            sorted(self.roles),
            ["builder", "orchestrator", "owner-auditor", "repurposer", "researcher", "sensor"],
        )
        self.assertEqual([r.id for r in self.roles.values() if r.main], ["orchestrator"])

    def test_tiers_are_capability_tiers(self):
        for role in self.roles.values():
            self.assertIn(role.tier, roles.TIERS)

    def test_sensor_is_read_only(self):
        sensor = self.roles["sensor"]
        write_capable = {"Write", "Edit", "NotebookEdit", "Bash", "PowerShell"}
        self.assertFalse(write_capable & set(sensor.allowed_tools))
        self.assertTrue({"Write", "Edit", "Bash"} <= set(sensor.disallowed_tools))
        self.assertIn("mcp__*", sensor.disallowed_tools)

    def test_repurposer_has_no_publishing_tools(self):
        repurposer = self.roles["repurposer"]
        for tool in ("Bash", "PowerShell", "WebFetch", "mcp__*"):
            self.assertNotIn(tool, repurposer.allowed_tools)
            self.assertIn(tool, repurposer.disallowed_tools)
        self.assertFalse(any(t.startswith("mcp__") for t in repurposer.allowed_tools))

    def test_builder_runs_in_a_worktree(self):
        self.assertEqual(self.roles["builder"].isolation, "worktree")
        others = [r.id for r in self.roles.values() if r.isolation and r.id != "builder"]
        self.assertEqual(others, [])

    def test_owner_auditor_reads_and_writes_memory_only(self):
        auditor = self.roles["owner-auditor"]
        self.assertEqual(auditor.write_paths, ("memory/**",))
        self.assertNotIn("Bash", auditor.allowed_tools)
        self.assertTrue({"Read", "Grep", "Glob", "Write", "Edit"} <= set(auditor.allowed_tools))

    def test_orchestrator_delegates_to_every_specialist(self):
        orchestrator = self.roles["orchestrator"]
        self.assertEqual(
            sorted(orchestrator.delegates_to()),
            ["builder", "owner-auditor", "repurposer", "researcher", "sensor"],
        )

    def test_optional_files_are_appended_in_fixed_order(self):
        auditor = self.roles["owner-auditor"]
        self.assertEqual(auditor.files, ("soul.md", "program.md", "rubric.md", "checks.md"))
        body = auditor.body
        positions = [body.index(h) for h in ("# Owner-auditor", "## Program", "## Rubric", "## Checks")]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("---", body.splitlines()[0])
        self.assertTrue(body.endswith("\n") and not body.endswith("\n\n"))

    def test_descriptions_say_when_to_delegate(self):
        for role in self.roles.values():
            self.assertTrue(role.description)
            self.assertLessEqual(len(role.description), roles.MAX_DESCRIPTION_LENGTH)


class ContractValidationTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.roles_dir = self.path("roles")
        support.minimal_roles(self.roles_dir)

    def assertInvalid(self, contains: str):
        with self.assertRaises(RoleError) as caught:
            roles.load_roles(self.roles_dir)
        self.assertIn(contains, str(caught.exception))

    def replace_worker(self, fields, **kwargs):
        support.make_role(self.roles_dir, "worker", fields, **kwargs)

    def test_minimal_roles_load(self):
        loaded = roles.by_id(roles.load_roles(self.roles_dir))
        self.assertEqual(loaded["lead"].write_paths, ("notes/**",))
        self.assertEqual(loaded["worker"].isolation, "worktree")
        self.assertEqual(loaded["lead"].delegates_to(), ("worker",))

    def test_id_must_match_folder(self):
        support.make_role(self.roles_dir, "other", [
            "id: not-other", "description: d", "tier: low"], folder="other")
        self.assertInvalid("must match the folder name")

    def test_required_keys(self):
        for missing in ("id", "description", "tier"):
            fields = {"id": "id: worker", "description": "description: d", "tier": "tier: low"}
            del fields[missing]
            with self.subTest(missing=missing):
                self.replace_worker(list(fields.values()))
                self.assertInvalid(missing)

    def test_unknown_keys_are_errors(self):
        self.replace_worker(["id: worker", "description: d", "tier: low", "model: inherit"])
        self.assertInvalid("unknown frontmatter key")

    def test_tier_is_never_a_model_name(self):
        self.replace_worker(["id: worker", "description: d", "tier: large-model-v2"])
        self.assertInvalid("never a model name")

    def test_isolation_values(self):
        self.replace_worker(["id: worker", "description: d", "tier: low", "isolation: container"])
        self.assertInvalid("isolation must be")

    def test_isolation_none_means_no_isolation(self):
        self.replace_worker(["id: worker", "description: d", "tier: low", "isolation: none"])
        self.assertIsNone(roles.by_id(roles.load_roles(self.roles_dir))["worker"].isolation)

    def test_exactly_one_main_role(self):
        self.replace_worker(["id: worker", "description: d", "tier: low", "main: true"])
        self.assertInvalid("exactly one role must set main: true (found 2")
        support.make_role(self.roles_dir, "lead", ["id: lead", "description: d", "tier: low"])
        self.replace_worker(["id: worker", "description: d", "tier: low"])
        self.assertInvalid("found 0")

    def test_main_must_be_boolean(self):
        self.replace_worker(["id: worker", "description: d", "tier: low", "main: 'true'"])
        self.assertInvalid("main must be true or false")

    def test_main_role_cannot_be_isolated(self):
        support.make_role(self.roles_dir, "lead", [
            "id: lead", "description: d", "tier: low", "main: true", "isolation: worktree"])
        self.assertInvalid("cannot set isolation")

    def test_tool_names_are_case_sensitive(self):
        self.replace_worker(["id: worker", "description: d", "tier: low", "allowed-tools: read, Grep"])
        self.assertInvalid("case-sensitive")

    def test_empty_allowlist_is_rejected(self):
        self.replace_worker(["id: worker", "description: d", "tier: low", "allowed-tools: []"])
        self.assertInvalid("allowed-tools is empty")

    def test_tool_in_both_lists(self):
        self.replace_worker([
            "id: worker", "description: d", "tier: low",
            "allowed-tools: Read, Bash", "disallowed-tools: Bash"])
        self.assertInvalid("both allowed-tools and disallowed-tools")

    def test_duplicate_tools(self):
        self.replace_worker(["id: worker", "description: d", "tier: low", "allowed-tools: Read, Read"])
        self.assertInvalid("twice")

    def test_agent_targets_must_be_known_role_ids(self):
        support.make_role(self.roles_dir, "lead", [
            "id: lead", "description: d", "tier: low", "main: true",
            "allowed-tools: Agent(worker, ghost), Read"])
        self.assertInvalid("unknown role 'ghost'")
        support.make_role(self.roles_dir, "lead", [
            "id: lead", "description: d", "tier: low", "main: true",
            "allowed-tools: Agent(plugin:worker), Read"])
        self.assertInvalid("must list role ids")

    def test_router_must_reach_every_delegate(self):
        support.make_role(self.roles_dir, "spare", ["id: spare", "description: d", "tier: low", "allowed-tools: Read"])
        support.make_role(self.roles_dir, "lead", [
            "id: lead", "description: d", "tier: low", "main: true",
            "allowed-tools: Agent(worker), Read"])
        self.assertInvalid("router can't reach it (missing: spare)")
        support.make_role(self.roles_dir, "lead", [
            "id: lead", "description: d", "tier: low", "main: true", "allowed-tools: Agent, Read"])
        roles.validate_set(roles.load_roles(self.roles_dir))

    def test_mcp_entries_are_accepted(self):
        self.replace_worker([
            "id: worker", "description: d", "tier: low",
            "allowed-tools: Read, mcp__github__get_issue, mcp__docs__*",
            "disallowed-tools: mcp__*"])
        worker = roles.by_id(roles.load_roles(self.roles_dir))["worker"]
        self.assertIn("mcp__docs__*", worker.allowed_tools)

    def test_write_paths_must_stay_relative(self):
        for bad, message in (("/etc/**", "relative"), ("../up/**", "'..'"), ("C:/x", "relative"),
                             ("a\\\\b", "'/' separators")):
            with self.subTest(path=bad):
                self.replace_worker(["id: worker", "description: d", "tier: low",
                                     'write-paths: ["%s"]' % bad])
                self.assertInvalid(message)

    def test_unknown_files_in_role_folder(self):
        self.replace_worker(["id: worker", "description: d", "tier: low"],
                            files={"notes.md": "## Notes\n"})
        self.assertInvalid("notes.md is not part of the role source contract")

    def test_prepare_comes_after_program(self):
        self.replace_worker(["id: worker", "description: d", "tier: low"],
                            files={"prepare.md": "## Prepare\n", "program.md": "## Program\n"})
        worker = roles.by_id(roles.load_roles(self.roles_dir))["worker"]
        self.assertEqual(worker.files, ("soul.md", "program.md", "prepare.md"))
        self.assertLess(worker.body.index("## Program"), worker.body.index("## Prepare"))

    def test_hidden_files_are_ignored(self):
        self.replace_worker(["id: worker", "description: d", "tier: low"],
                            files={".DS_Store": "x"})
        roles.load_roles(self.roles_dir)

    def test_optional_files_carry_no_frontmatter(self):
        self.replace_worker(["id: worker", "description: d", "tier: low"],
                            files={"program.md": "---\nid: x\n---\n"})
        self.assertInvalid("only soul.md carries frontmatter")

    def test_soul_needs_a_body(self):
        self.replace_worker(["id: worker", "description: d", "tier: low"], body="\n")
        self.assertInvalid("needs a body")

    def test_description_length_limit(self):
        self.replace_worker(["id: worker", "description: %s" % ("x" * 1025), "tier: low"])
        self.assertInvalid("the limit is 1024")

    def test_missing_soul(self):
        os.makedirs(self.path("roles", "empty"))
        self.assertInvalid("missing soul.md")

    def test_files_beside_role_folders_are_ignored(self):
        support.write(self.path("roles", "README.md"), "# Roles\n")
        self.assertEqual(len(roles.load_roles(self.roles_dir)), 2)

    def test_no_roles(self):
        with self.assertRaises(RoleError):
            roles.load_roles(self.path("nothing-here"))


class ToolListTests(unittest.TestCase):
    def test_commas_inside_parentheses_stay_together(self):
        self.assertEqual(
            roles.split_tool_list("Agent(a, b), Read, Bash(git log *)"),
            ["Agent(a, b)", "Read", "Bash(git log *)"],
        )

    def test_unbalanced_parentheses(self):
        with self.assertRaises(RoleError):
            roles.split_tool_list("Agent(a, Read")
        with self.assertRaises(RoleError):
            roles.split_tool_list("Read), Grep")

    def test_split_tool(self):
        self.assertEqual(roles.split_tool("Agent(a, b)"), ("Agent", "a, b"))
        self.assertEqual(roles.split_tool("Read"), ("Read", None))


if __name__ == "__main__":
    unittest.main()
