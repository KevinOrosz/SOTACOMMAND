"""engine/tools/protect_team.py: no work for a helper the owner turned off."""

import json
import os
import subprocess
import sys
import unittest

from . import support
from .test_session_open import session_start

REPO = support.REPO_ROOT
WIZARD = os.path.join(REPO, "tools", "wizard", "wizard.py")


def agent_call(root, subagent_type, tool="Agent"):
    return {"session_id": "abc", "cwd": root, "hook_event_name": "PreToolUse", "tool_name": tool,
            "agent_type": "sotacommand-engine:orchestrator",
            "tool_input": {"subagent_type": subagent_type, "description": "d", "prompt": "p"}}


class ProtectTeamTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.path("project")
        os.makedirs(self.root)
        self.env = support.clean_env(CLAUDE_PROJECT_DIR=self.root)

    def helpers(self, value):
        text = value if isinstance(value, str) else json.dumps({"name": "Sam", "helpers": value})
        support.write(os.path.join(self.root, "profile", "answers.json"), text)

    def decision(self, subagent_type, tool="Agent"):
        result = support.run_tool("protect_team.py", agent_call(self.root, subagent_type, tool), env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return "allow", ""
        out = json.loads(result.stdout)["hookSpecificOutput"]
        return out["permissionDecision"], out["permissionDecisionReason"]

    def test_before_the_interview_every_helper_is_on(self):
        for role in ("builder", "sotacommand-engine:sensor"):
            self.assertEqual(self.decision(role), ("allow", ""))

    def test_a_helper_that_is_off_gets_no_work(self):
        self.helpers(["researcher", "repurposer"])
        for name in ("sotacommand-engine:builder", "builder", "sotacommand-engine:sensor", "owner-auditor"):
            with self.subTest(agent=name):
                decision, reason = self.decision(name)
                self.assertEqual(decision, "deny")
                self.assertIn("turned the %s helper off" % name.split(":")[-1], reason)
                self.assertIn("Don't route around it", reason)
        self.assertEqual(self.decision("builder", tool="Task")[0], "deny", "the tool's older name is covered too")

    def test_helpers_that_are_on_work(self):
        self.helpers(["researcher", "repurposer"])
        for name in ("sotacommand-engine:researcher", "repurposer"):
            self.assertEqual(self.decision(name), ("allow", ""))

    def test_other_agents_and_the_main_role_are_not_its_business(self):
        self.helpers([])
        for name in ("general-purpose", "Explore", "other-plugin:builder", "sotacommand-engine:orchestrator", ""):
            with self.subTest(agent=name):
                self.assertEqual(self.decision(name), ("allow", ""))
        result = support.run_tool("protect_team.py", dict(agent_call(self.root, "builder"), tool_name="Bash"), env=self.env)
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_a_broken_answers_file_fails_closed(self):
        for text in ("{not json", json.dumps({"helpers": "builder"}), json.dumps(["builder"])):
            with self.subTest(text=text):
                self.helpers(text)
                decision, reason = self.decision("builder")
                self.assertEqual(decision, "deny")
                self.assertIn("until it is fixed", reason)
                self.assertEqual(self.decision("general-purpose")[0], "allow")
        self.assertEqual(support.run_tool("protect_team.py", raw="nope", env=self.env).returncode, 2)

    def test_the_interview_feeds_the_guard(self):
        answers = {"name": "Maya", "project": "Maya Clay", "kind": "artist", "what": "Mugs.", "helpers": ["researcher"]}
        support.write(os.path.join(self.root, "a.json"), json.dumps(answers))
        done = subprocess.run([sys.executable, WIZARD, "interview", "--target", self.root, "--answers", os.path.join(self.root, "a.json")],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.decision("sotacommand-engine:researcher")[0], "allow")
        self.assertEqual(self.decision("sotacommand-engine:builder")[0], "deny")
        with open(os.path.join(self.root, "TEAM.md"), encoding="utf-8") as handle:
            self.assertIn("In Claude Code the tool refuses it", handle.read())

    def test_agents_cannot_switch_a_helper_back_on(self):
        with open(os.path.join(support.ENGINE_DIR, "templates", "os.config.json"), encoding="utf-8") as handle:
            support.write(os.path.join(self.root, "os.config.json"), handle.read())
        self.helpers(["researcher"])
        target = os.path.join(self.root, "profile", "answers.json")
        write = {"cwd": self.root, "hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": target, "content": "{}"}}
        self.assertIn('"deny"', support.run_tool("protect_paths.py", write, env=self.env).stdout)
        shell = {"cwd": self.root, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                 "tool_input": {"command": "echo '{\"helpers\":[\"builder\"]}' > profile/answers.json"}}
        self.assertIn('"deny"', support.run_tool("protect_shell.py", shell, env=self.env).stdout)

    def test_session_start_states_the_team(self):
        def context():
            result = support.run_tool("session_open.py", session_start(self.root), env=self.env)
            return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("team: the owner hasn't done the interview", context())
        self.helpers(["researcher", "repurposer"])
        self.assertIn("team: on: repurposer, researcher; off: builder, owner-auditor, sensor.", context())
        self.helpers("{broken")
        self.assertIn("team: profile/answers.json is unreadable", context())


if __name__ == "__main__":
    unittest.main()
