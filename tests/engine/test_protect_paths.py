"""engine/tools/protect_paths.py: the PreToolUse protected-path hook.

Each case feeds the hook the PreToolUse input documented in the Claude Code
hooks reference and checks the documented output: silence to allow, a
``permissionDecision: "deny"`` object to deny, exit 2 when it cannot decide.
"""

import json
import os
import unittest

from . import support

DEFAULT_CONFIG = {
    "protected_paths": ["os.config.json", "profile/**", "secrets", "**/.env*", "*.lock"],
    "role_write_paths": {"owner-auditor": ["reports/**"], "$comment": "ignored"},
}


def pre_tool_use(root, tool_name, file_path, agent_type=None, agent_id=None, **tool_input):
    """A PreToolUse payload shaped like the hooks reference example."""
    payload = {
        "session_id": "abc123",
        "transcript_path": os.path.join(root, ".transcript.jsonl"),
        "cwd": root,
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": dict(tool_input, file_path=file_path) if file_path is not None else dict(tool_input),
        "tool_use_id": "toolu_01ABC123",
    }
    if agent_id:
        payload["agent_id"] = agent_id
    if agent_type:
        payload["agent_type"] = agent_type
    return payload


class ProtectPathsTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.path("project")
        os.makedirs(os.path.join(self.root, ".git"))
        self.config(DEFAULT_CONFIG)
        self.env = support.clean_env(CLAUDE_PROJECT_DIR=self.root)

    def config(self, data):
        text = data if isinstance(data, str) else json.dumps(data)
        support.write(os.path.join(self.root, "os.config.json"), text)

    def run_hook(self, payload, env=None, args=()):
        return support.run_tool("protect_paths.py", payload, env=env or self.env, args=args)

    def write_call(self, relative, tool="Write", agent_type=None, agent_id=None):
        target = relative if os.path.isabs(relative) else os.path.join(self.root, *relative.split("/"))
        if tool == "Edit":
            extra = {"old_string": "a", "new_string": "b", "replace_all": False}
        else:
            extra = {"content": "text"}
        return self.run_hook(pre_tool_use(self.root, tool, target, agent_type, agent_id, **extra))

    def assertAllowed(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "", "allow means no output")

    def assertDenied(self, result, *fragments):
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(set(output), {"hookSpecificOutput"})
        decision = output["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PreToolUse")
        self.assertEqual(decision["permissionDecision"], "deny")
        for fragment in fragments:
            self.assertIn(fragment, decision["permissionDecisionReason"])
        return decision["permissionDecisionReason"]

    # Allow cases -----------------------------------------------------------

    def test_ordinary_write_is_allowed(self):
        self.assertAllowed(self.write_call("src/app.py"))

    def test_ordinary_edit_is_allowed(self):
        self.assertAllowed(self.write_call("docs/guide.md", tool="Edit"))

    def test_read_only_tools_are_never_blocked(self):
        payload = pre_tool_use(self.root, "Read", os.path.join(self.root, "profile", "owner.md"))
        self.assertAllowed(self.run_hook(payload))

    def test_calls_without_a_path_are_allowed(self):
        self.assertAllowed(self.run_hook(pre_tool_use(self.root, "Write", None, content="x")))

    def test_unscoped_agents_follow_protected_paths_only(self):
        for agent_type in ("sotacommand-engine:builder", "Explore", "general-purpose"):
            with self.subTest(agent=agent_type):
                self.assertAllowed(self.write_call("src/app.py", agent_type=agent_type, agent_id="a1"))

    def test_anchored_patterns_do_not_match_deeper(self):
        self.assertAllowed(self.write_call("pkg/deps.lock"))
        self.assertAllowed(self.write_call("docs/profile/notes.md"))

    # Protected paths -------------------------------------------------------

    def test_protected_glob_denies_write(self):
        reason = self.assertDenied(self.write_call("profile/owner.md"), "profile/owner.md", "'profile/**'")
        self.assertIn("os.config.json", reason)

    def test_protected_glob_denies_edit(self):
        self.assertDenied(self.write_call("os.config.json", tool="Edit"), "'os.config.json'")

    def test_directory_name_protects_its_contents(self):
        self.assertDenied(self.write_call("secrets/a/b.txt"), "'secrets'")

    def test_double_star_matches_at_any_depth(self):
        self.assertDenied(self.write_call("services/api/.env.local"), "'**/.env*'")

    def test_single_star_stays_in_one_segment(self):
        self.assertDenied(self.write_call("deps.lock"), "'*.lock'")

    def test_protected_paths_ignore_case(self):
        self.assertDenied(self.write_call("PROFILE/Owner.md"))

    def test_dot_dot_segments_are_normalized(self):
        self.assertDenied(self.write_call(os.path.join(self.root, "src", "..", "profile", "x.md")))

    def test_symlink_into_a_protected_directory(self):
        if not hasattr(os, "symlink") or os.name == "nt":
            self.skipTest("symlinks need POSIX here")
        os.makedirs(os.path.join(self.root, "profile"))
        os.symlink(os.path.join(self.root, "profile"), os.path.join(self.root, "alias"))
        self.assertDenied(self.write_call("alias/owner.md"), "profile/owner.md")

    def test_rules_hold_inside_worktrees(self):
        worktree = os.path.join(self.root, ".claude", "worktrees", "feature")
        support.write(os.path.join(worktree, ".git"), "gitdir: ../../../.git/worktrees/feature\n")
        self.assertDenied(self.write_call(".claude/worktrees/feature/profile/owner.md"), "'profile/**'")
        self.assertDenied(self.write_call(".claude/worktrees/feature/os.config.json"))
        self.assertAllowed(self.write_call(".claude/worktrees/feature/src/app.py"))

    def test_notebook_paths_are_checked(self):
        payload = pre_tool_use(self.root, "NotebookEdit", None,
                               notebook_path=os.path.join(self.root, "profile", "n.ipynb"),
                               new_source="x")
        self.assertDenied(self.run_hook(payload))

    def test_relative_paths_resolve_against_cwd(self):
        self.assertDenied(self.run_hook(pre_tool_use(self.root, "Write", "profile/owner.md", content="x")))

    # Role write scopes ---------------------------------------------------

    def test_owner_auditor_writes_memory(self):
        self.assertAllowed(self.write_call(
            "memory/owner-ledger.md", agent_type="sotacommand-engine:owner-auditor", agent_id="a1"))

    def test_owner_auditor_cannot_write_elsewhere(self):
        reason = self.assertDenied(
            self.write_call("src/app.py", agent_type="sotacommand-engine:owner-auditor", agent_id="a1"),
            "owner-auditor", "memory/**", "src/app.py",
        )
        self.assertIn("role policy", reason)

    def test_owner_auditor_edit_outside_scope(self):
        self.assertDenied(self.write_call(
            "README.md", tool="Edit", agent_type="sotacommand-engine:owner-auditor", agent_id="a1"))

    def test_owner_auditor_cannot_write_outside_the_project(self):
        outside = self.path("elsewhere", "memory", "x.md")
        self.assertDenied(
            self.write_call(outside, agent_type="sotacommand-engine:owner-auditor", agent_id="a1"),
            "the owner-auditor role may write only",
        )

    def test_scopes_are_case_sensitive(self):
        self.assertDenied(self.write_call(
            "MEMORY/x.md", agent_type="sotacommand-engine:owner-auditor", agent_id="a1"))

    def test_scope_cannot_bypass_protected_paths(self):
        config = dict(DEFAULT_CONFIG, protected_paths=["memory/locked.md"])
        self.config(config)
        self.assertDenied(self.write_call(
            "memory/locked.md", agent_type="sotacommand-engine:owner-auditor", agent_id="a1"),
            "'memory/locked.md'")

    def test_os_config_can_add_to_a_scope(self):
        self.assertAllowed(self.write_call(
            "reports/weekly.md", agent_type="sotacommand-engine:owner-auditor", agent_id="a1"))

    def test_os_config_can_give_an_unscoped_role_a_scope(self):
        self.config(dict(DEFAULT_CONFIG, role_write_paths={"builder": ["src/**"]}))
        builder = {"agent_type": "sotacommand-engine:builder", "agent_id": "a1"}
        self.assertAllowed(self.write_call("src/app.py", **builder))
        self.assertDenied(self.write_call("docs/x.md", **builder), "the builder role may write only src/**")

    def test_bare_role_names_work_too(self):
        self.assertDenied(self.write_call("src/app.py", agent_type="owner-auditor", agent_id="a1"))

    def test_main_thread_orchestrator_is_scoped(self):
        # On the main thread of an agent session, agent_type is set and agent_id is absent.
        self.assertAllowed(self.write_call("memory/HANDOFF.md", agent_type="sotacommand-engine:orchestrator"))
        self.assertDenied(self.write_call("src/app.py", agent_type="sotacommand-engine:orchestrator"),
                          "orchestrator")

    def test_scope_holds_inside_worktrees(self):
        worktree = os.path.join(self.root, ".claude", "worktrees", "feature")
        support.write(os.path.join(worktree, ".git"), "gitdir: x\n")
        self.assertAllowed(self.write_call(
            ".claude/worktrees/feature/memory/x.md", agent_type="sotacommand-engine:owner-auditor", agent_id="a1"))
        self.assertDenied(self.write_call(
            ".claude/worktrees/feature/src/x.py", agent_type="sotacommand-engine:owner-auditor", agent_id="a1"))

    # Missing or broken configuration ---------------------------------------

    def test_without_os_config_only_role_scopes_apply(self):
        os.remove(os.path.join(self.root, "os.config.json"))
        self.assertAllowed(self.write_call("profile/owner.md"))
        self.assertDenied(self.write_call("src/app.py", agent_type="sotacommand-engine:owner-auditor", agent_id="a1"))

    def test_invalid_os_config_fails_closed(self):
        self.config("{ not json")
        self.assertDenied(self.write_call("src/app.py"), "os.config.json", "blocked until it is fixed")

    def test_wrong_types_in_os_config_fail_closed(self):
        for config in ({"protected_paths": "profile/**"}, {"protected_paths": [""]},
                       {"role_write_paths": ["memory/**"]}, ["not", "an", "object"]):
            with self.subTest(config=config):
                self.config(config)
                self.assertDenied(self.write_call("src/app.py"))

    def test_missing_role_policy_fails_closed(self):
        result = self.run_hook(pre_tool_use(self.root, "Write", os.path.join(self.root, "a.txt"), content="x"),
                               args=["--policy", self.path("missing.json")])
        self.assertDenied(result, "role_policy.json")

    def test_input_that_is_not_json_blocks_with_exit_2(self):
        result = support.run_tool("protect_paths.py", raw="not json", env=self.env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not valid JSON", result.stderr)

    def test_tool_input_that_is_not_an_object_blocks_with_exit_2(self):
        payload = pre_tool_use(self.root, "Write", None)
        payload["tool_input"] = "profile/owner.md"
        self.assertEqual(self.run_hook(payload).returncode, 2)

    def test_project_root_from_cwd_when_env_is_absent(self):
        payload = pre_tool_use(os.path.join(self.root, "src"), "Write",
                               os.path.join(self.root, "profile", "owner.md"), content="x")
        self.assertDenied(self.run_hook(payload, env=support.clean_env()))

    def test_root_flag_overrides_the_environment(self):
        other = self.path("other")
        os.makedirs(other)
        payload = pre_tool_use(self.root, "Write", os.path.join(self.root, "profile", "x.md"), content="x")
        self.assertAllowed(self.run_hook(payload, args=["--root", other]))


class GlobAndPathUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = support.load_tool_module("protect_paths")

    def matches(self, pattern, path, ignore_case=False):
        return self.tool.match(path, self.tool.compile_patterns([pattern], ignore_case)) is not None

    def test_glob_semantics(self):
        cases = [
            ("memory/**", "memory/a.md", True),
            ("memory/**", "memory/a/b.md", True),
            ("memory", "memory/a/b.md", True),
            ("memory/", "memory/a.md", True),
            ("memory/**", "memoryx/a.md", False),
            ("*.md", "a.md", True),
            ("*.md", "docs/a.md", False),
            ("**/*.md", "docs/a.md", True),
            ("**/*.md", "a.md", True),
            ("a/**/b", "a/b", True),
            ("a/**/b", "a/x/y/b", True),
            ("docs/?.md", "docs/a.md", True),
            ("docs/?.md", "docs/ab.md", False),
            ("[abc].txt", "b.txt", True),
            ("[!abc].txt", "d.txt", True),
            ("[!abc].txt", "a.txt", False),
            ("./profile/**", "profile/x", True),
            ("/profile/**", "profile/x", True),
            ("**", "anything/at/all", True),
        ]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(self.matches(pattern, path), expected)

    def test_case_folding_is_opt_in(self):
        self.assertFalse(self.matches("memory/**", "MEMORY/a.md"))
        self.assertTrue(self.matches("memory/**", "MEMORY/a.md", ignore_case=True))

    def test_empty_pattern_is_invalid(self):
        with self.assertRaises(ValueError):
            self.tool.glob_to_regex("  ")

    def test_role_of_strips_plugin_prefixes(self):
        self.assertEqual(self.tool.role_of("sotacommand-engine:owner-auditor"), "owner-auditor")
        self.assertEqual(self.tool.role_of("plugin:sub:builder"), "builder")
        self.assertEqual(self.tool.role_of("sensor"), "sensor")
        self.assertIsNone(self.tool.role_of(""))
        self.assertIsNone(self.tool.role_of(None))

    def test_windows_style_paths(self):
        reason = self.tool.evaluate(
            "C:\\proj\\Profile\\owner.md", "C:\\proj", None, ["profile/**"], {})
        self.assertIsNotNone(reason)
        self.assertIsNone(self.tool.evaluate("C:\\proj\\src\\a.py", "C:\\proj", None, ["profile/**"], {}))
        scoped = {"owner-auditor": ["memory/**"]}
        self.assertIsNone(self.tool.evaluate("C:\\proj\\memory\\x.md", "C:\\proj", "owner-auditor", [], scoped))
        self.assertIsNotNone(self.tool.evaluate("C:\\proj\\src\\x.md", "C:\\proj", "owner-auditor", [], scoped))

    def test_relative_handles_outside_paths(self):
        self.assertIsNone(self.tool.relative("/a/b", "/c"))
        self.assertEqual(self.tool.relative("/a/b/c", "/a"), "b/c")
        self.assertEqual(self.tool.relative("/a", "/a"), "")


if __name__ == "__main__":
    unittest.main()
