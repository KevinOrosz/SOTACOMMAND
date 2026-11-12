"""Shapes of the generated Claude Code engine plugin."""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest

from . import support

from osgen import PLUGIN_NAME, roles

# Plugin agent frontmatter fields the generator may emit (components docs).
AGENT_FIELDS = {"name", "description", "tools", "disallowedTools", "model", "isolation"}
SKILL_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def parse_generated_frontmatter(text):
    """Parse the frontmatter the generator writes: comments, scalars, one metadata map."""
    fields, current_map = {}, None
    for line in support.frontmatter_block(text).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line.startswith("  ")
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if value.startswith('"'):
            value = json.loads(value)
        if indented:
            fields[current_map][key] = value
        elif value == "":
            fields[key], current_map = {}, key
        else:
            fields[key] = value
    return fields


class GeneratedEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="engine-gen-")
        result = support.run_generate("--roles", support.SAMPLE_ROLES, "--out", cls.out)
        assert result.returncode == 0, result.stderr
        cls.engine = os.path.join(cls.out, "engine")
        cls.roles = roles.by_id(roles.load_roles(support.SAMPLE_ROLES))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def read(self, *parts):
        return support.read(os.path.join(self.engine, *parts))

    def json(self, *parts):
        return json.loads(self.read(*parts))

    def agent(self, role_id):
        text = self.read("agents", role_id + ".md")
        return parse_generated_frontmatter(text), text

    def test_plugin_manifest(self):
        manifest = self.json(".claude-plugin", "plugin.json")
        self.assertEqual(manifest["name"], "sotacommand-engine")
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        self.assertTrue(manifest["description"])
        self.assertEqual(manifest["license"], "Apache-2.0")
        self.assertEqual(manifest["author"], {"name": "SOTACOMMAND"})
        self.assertLessEqual(
            set(manifest), {"name", "version", "description", "author", "license", "keywords"}
        )

    def test_settings_make_the_main_role_the_session_agent(self):
        self.assertEqual(self.json("settings.json"), {"agent": "orchestrator"})

    def test_one_agent_per_role(self):
        files = sorted(os.listdir(os.path.join(self.engine, "agents")))
        self.assertEqual(files, sorted(role_id + ".md" for role_id in self.roles))

    def test_agent_frontmatter_fields(self):
        for role_id, role in self.roles.items():
            with self.subTest(role=role_id):
                fields, text = self.agent(role_id)
                self.assertLessEqual(set(fields), AGENT_FIELDS)
                self.assertEqual(fields["name"], role_id)
                self.assertEqual(fields["description"], role.description)
                self.assertEqual(fields["model"], "inherit")
                self.assertTrue(text.endswith(role.body), "body must be the assembled role body")

    def test_isolation_only_where_declared(self):
        for role_id in self.roles:
            fields, _ = self.agent(role_id)
            expected = "worktree" if role_id == "builder" else None
            self.assertEqual(fields.get("isolation"), expected, role_id)

    def test_sensor_tools_are_read_only(self):
        fields, _ = self.agent("sensor")
        tools = [t.strip() for t in fields["tools"].split(",")]
        self.assertEqual(tools, ["Read", "Grep", "Glob", "WebFetch", "WebSearch"])
        denied = [t.strip() for t in fields["disallowedTools"].split(",")]
        for tool in ("Write", "Edit", "NotebookEdit", "Bash", "mcp__*"):
            self.assertIn(tool, denied)

    def test_repurposer_cannot_post(self):
        fields, _ = self.agent("repurposer")
        tools = [t.strip() for t in fields["tools"].split(",")]
        for tool in ("Bash", "WebFetch", "Agent"):
            self.assertNotIn(tool, tools)
        self.assertIn("mcp__*", fields["disallowedTools"])

    def test_orchestrator_delegates_with_plugin_scoped_names(self):
        fields, _ = self.agent("orchestrator")
        match = re.match(r"^Agent\(([^)]*)\)", fields["tools"])
        self.assertIsNotNone(match)
        targets = [t.strip() for t in match.group(1).split(",")]
        self.assertEqual(
            targets,
            ["%s:%s" % (PLUGIN_NAME, r) for r in ("researcher", "builder", "owner-auditor", "sensor", "repurposer")],
        )
        self.assertNotIn("Bash", fields["tools"])

    def test_hooks_use_documented_events_and_exec_form(self):
        hooks = self.json("hooks", "hooks.json")
        self.assertEqual(set(hooks), {"description", "hooks"})
        self.assertEqual(set(hooks["hooks"]), {"SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"})
        expected = {
            "SessionStart": [(None, "session_open.py")],
            "UserPromptSubmit": [(None, "ledger.py")],
            "PreToolUse": [("Write|Edit", "protect_paths.py"), ("Bash|PowerShell", "protect_shell.py"),
                           ("Agent|Task", "protect_team.py")],
            "Stop": [(None, "session_close_gate.py")],
        }
        for event, groups in hooks["hooks"].items():
            self.assertEqual(len(groups), len(expected[event]), event)
            for group, (matcher, script) in zip(groups, expected[event]):
                self.assertEqual(group.get("matcher"), matcher)
                (handler,) = group["hooks"]
                self.assertEqual(handler["type"], "command")
                self.assertEqual(handler["command"], "python3")
                self.assertEqual(handler["args"][0], "${CLAUDE_PLUGIN_ROOT}/tools/%s" % script)
                self.assertEqual(handler["args"][1:], ["--event", "owner_message"] if script == "ledger.py" else [])
                self.assertTrue(os.path.isfile(os.path.join(self.engine, "tools", script)))
                self.assertIsInstance(handler["timeout"], int)
        for group in hooks["hooks"]["PreToolUse"]:
            self.assertEqual(group["hooks"][0]["onFailure"], "block")

    def test_role_policy_carries_write_scopes(self):
        policy = self.json("tools", "role_policy.json")
        self.assertEqual(policy["main_role"], "orchestrator")
        self.assertEqual(policy["write_paths"]["owner-auditor"], ["memory/**"])
        self.assertEqual(sorted(policy["write_paths"]), ["orchestrator", "owner-auditor"])

    def test_tools_are_copied_from_templates(self):
        for name in ("os_config.py", "protect_paths.py", "session_open.py", "session_close_gate.py"):
            template = support.read(os.path.join(support.ADAPTERS_DIR, "templates", "engine", "tools", name))
            self.assertEqual(self.read("tools", name), template, name)

    def test_tools_compile(self):
        for name in os.listdir(os.path.join(self.engine, "tools")):
            if name.endswith(".py"):
                compile(self.read("tools", name), name, "exec")

    def test_tools_write_no_bytecode_into_the_plugin(self):
        env = support.clean_env()
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        for name in ("session_open.py", "protect_paths.py", "session_close_gate.py"):
            result = support.run([sys.executable, os.path.join(self.engine, "tools", name)],
                                 stdin="{}", env=env, cwd=self.out)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.engine, "tools", "__pycache__")))

    def test_tool_scripts_are_executable(self):
        if os.name == "nt":
            self.skipTest("POSIX file modes")
        for name in ("protect_paths.py", "session_open.py", "session_close_gate.py"):
            mode = os.stat(os.path.join(self.engine, "tools", name)).st_mode
            self.assertTrue(mode & 0o100, name)

    def test_workflow_shape(self):
        script = self.read("workflows", "convergence.js")
        self.assertTrue(script.startswith("export const meta = {\n"))
        self.assertIn("await pipeline(", script)
        self.assertEqual(script.count("agent(passPrompt"), 1)
        self.assertEqual(script.count("agent(synthesisPrompt"), 1)

    def test_os_boot_skill_uses_portable_frontmatter(self):
        text = self.read("skills", "os-boot", "SKILL.md")
        fields = parse_generated_frontmatter(text)
        self.assertLessEqual(set(fields), SKILL_FIELDS)
        self.assertEqual(fields["name"], "os-boot")
        self.assertLessEqual(len(fields["description"]), 1024)
        self.assertTrue(all(isinstance(v, str) for v in fields["metadata"].values()))
        self.assertEqual(fields["metadata"]["roles"], "orchestrator")
        self.assertNotIn("{{", text)
        for role_id in self.roles:
            self.assertIn("`%s`" % role_id, text)
        self.assertTrue(os.path.isfile(os.path.join(self.engine, "skills", "os-boot", "references", "os-config.md")))

    def test_no_claude_md_and_no_bin_dir(self):
        self.assertFalse(os.path.exists(os.path.join(self.engine, "CLAUDE.md")))
        self.assertFalse(os.path.exists(os.path.join(self.engine, "bin")))

    def test_os_config_template_is_valid_json(self):
        config = self.json("templates", "os.config.json")
        self.assertIn("os.config.json", config["protected_paths"])
        self.assertEqual(config["close_gate"]["verify"], "")
        for entry in config["memory_caps"]:
            # memory/<name>.md is copied from templates/memory/<name>.template.md.
            template = entry["path"][: -len(".md")] + ".template.md"
            self.assertTrue(os.path.isfile(os.path.join(self.engine, "templates", template)), template)

    def test_templates_avoid_the_handoff_deny_glob(self):
        for current, _, files in os.walk(os.path.join(self.engine, "templates")):
            self.assertNotIn("HANDOFF.md", files, current)

    def test_generation_is_deterministic(self):
        second = tempfile.mkdtemp(prefix="engine-gen-again-")
        self.addCleanup(shutil.rmtree, second, True)
        result = support.run_generate("--roles", support.SAMPLE_ROLES, "--out", second)
        self.assertEqual(result.returncode, 0, result.stderr)
        for current, _, files in os.walk(self.out):
            for name in files:
                path = os.path.join(current, name)
                relative = os.path.relpath(path, self.out)
                with open(path, "rb") as a, open(os.path.join(second, relative), "rb") as b:
                    self.assertEqual(a.read(), b.read(), relative)

    def test_outputs_carry_no_private_markers(self):
        # Term lists (names, brands) belong to the leak gate, which CI runs on
        # every tracked file; this test checks the shapes a generator could emit.
        patterns = {
            "model other than inherit": re.compile(r"(?m)^model: (?!inherit$)"),
            "home path": re.compile(r"(/Users/[A-Za-z]|/home/[A-Za-z]|[A-Za-z]:\\Users\\)"),
            "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
            "rule code": re.compile(r"\b(Rule \d+|lesson #\d+)"),
        }
        for current, _, files in os.walk(self.out):
            for name in files:
                path = os.path.join(current, name)
                text = support.read(path)
                for label, pattern in patterns.items():
                    self.assertIsNone(pattern.search(text), "%s in %s" % (label, os.path.relpath(path, self.out)))


if __name__ == "__main__":
    unittest.main()
