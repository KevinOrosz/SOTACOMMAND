"""GitHub Copilot custom agents in .github/agents/."""

import json
import os
import shutil
import tempfile
import unittest

from . import support

from osgen import copilot, roles
from osgen.roles import Role


def make_role(allowed=None, denied=(), write_paths=None, isolation=None, body="Body.\n"):
    return Role(
        id="sample", description="d", tier="low",
        allowed_tools=tuple(allowed) if allowed is not None else None,
        disallowed_tools=tuple(denied), isolation=isolation, main=False,
        write_paths=tuple(write_paths) if write_paths is not None else None,
        body=body, files=("soul.md",),
    )


def frontmatter(text):
    fields = {}
    for line in support.frontmatter_block(text).splitlines():
        if line.startswith("#"):
            continue
        key, _, value = line.partition(": ")
        fields[key] = json.loads(value) if value[:1] in '"[' else value
    return fields


class GeneratedCopilotAgentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="copilot-gen-")
        result = support.run_generate("--roles", support.SAMPLE_ROLES, "--out", cls.out)
        assert result.returncode == 0, result.stderr
        cls.dir = os.path.join(cls.out, ".github", "agents")
        cls.roles = roles.by_id(roles.load_roles(support.SAMPLE_ROLES))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def agent(self, role_id):
        text = support.read(os.path.join(self.dir, role_id + ".agent.md"))
        return frontmatter(text), text

    def test_one_profile_per_role_with_valid_file_names(self):
        names = sorted(os.listdir(self.dir))
        self.assertEqual(names, sorted("%s.agent.md" % r for r in self.roles))
        for name in names:
            self.assertRegex(name, r"^[A-Za-z0-9._-]+\.agent\.md$")

    def test_frontmatter_has_name_description_tools(self):
        for role_id, role in self.roles.items():
            fields, text = self.agent(role_id)
            self.assertEqual(set(fields), {"name", "description", "tools"}, role_id)
            self.assertEqual(fields["name"], role_id)
            self.assertEqual(fields["description"], role.description)
            self.assertIsInstance(fields["tools"], list)
            self.assertIn(role.body.strip(), text)

    def test_tool_aliases_follow_the_role_policy(self):
        expected = {
            "sensor": ["read", "search", "web"],
            "repurposer": ["read", "edit", "search"],
            "owner-auditor": ["read", "edit", "search"],
            "builder": ["execute", "read", "edit", "search"],
            "researcher": ["execute", "read", "edit", "search", "web"],
            "orchestrator": ["read", "edit", "search", "agent"],
        }
        for role_id, tools in expected.items():
            self.assertEqual(self.agent(role_id)[0]["tools"], tools, role_id)

    def test_unenforced_boundaries_are_stated(self):
        _, auditor = self.agent("owner-auditor")
        self.assertIn("## Boundaries in this harness", auditor)
        self.assertIn("`memory/**`", auditor)
        _, builder = self.agent("builder")
        self.assertIn("isolated branch or worktree", builder)
        _, orchestrator = self.agent("orchestrator")
        self.assertIn("Not available in this harness: Skill, Workflow.", orchestrator)
        _, sensor = self.agent("sensor")
        self.assertNotIn("Boundaries in this harness", sensor)


class MapToolsTests(unittest.TestCase):
    def test_unrestricted_role_omits_tools(self):
        self.assertEqual(copilot.map_tools(make_role()), (None, []))

    def test_deny_only_role_gets_a_narrowed_allowlist(self):
        tools, unmapped = copilot.map_tools(make_role(denied=["Bash", "WebFetch"]))
        self.assertEqual(tools, ["read", "edit", "search", "agent", "todo"])
        self.assertEqual(unmapped, [])

    def test_denial_in_an_inherit_all_role_removes_the_whole_alias(self):
        tools, _ = copilot.map_tools(make_role(denied=["Bash"]))
        self.assertNotIn("execute", tools)

    def test_denials_outside_an_allowlist_do_not_change_the_mapping(self):
        tools, _ = copilot.map_tools(make_role(allowed=["Write", "Read"], denied=["NotebookEdit"]))
        self.assertEqual(tools, ["read", "edit"])

    def test_specifiers_are_not_widened(self):
        tools, unmapped = copilot.map_tools(make_role(allowed=["Read", "Bash(git log *)"]))
        self.assertEqual(tools, ["read"])
        self.assertEqual(unmapped, ["Bash(git log *)"])

    def test_agent_allowlist_maps_to_agent_alias(self):
        tools, unmapped = copilot.map_tools(make_role(allowed=["Agent(a, b)", "Read"]))
        self.assertEqual(tools, ["read", "agent"])
        self.assertEqual(unmapped, [])

    def test_mcp_entries(self):
        tools, unmapped = copilot.map_tools(
            make_role(allowed=["Read", "mcp__github__get_issue", "mcp__docs__*", "mcp__wiki", "mcp__*"])
        )
        self.assertEqual(tools, ["read", "github/get_issue", "docs/*", "wiki/*"])
        self.assertEqual(unmapped, ["mcp__*"])

    def test_nothing_mappable_gives_an_empty_list(self):
        tools, unmapped = copilot.map_tools(make_role(allowed=["Skill"]))
        self.assertEqual(tools, [])
        self.assertEqual(unmapped, ["Skill"])

    def test_prompt_limit(self):
        with self.assertRaises(copilot.CopilotError):
            copilot.render_agent(make_role(body="x" * (copilot.MAX_PROMPT_CHARS + 1)))


if __name__ == "__main__":
    unittest.main()
