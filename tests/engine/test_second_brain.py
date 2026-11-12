"""The optional second_brain block: boot report line, close-gate lint, KB/ write guard."""

import json
import os
import unittest

from . import support
from .test_close_gate import stop_input
from .test_protect_paths import pre_tool_use
from .test_session_open import session_start

LINT_OK = "#!/usr/bin/env bash\necho 'lint: 0 FAIL'\n"
LINT_FAIL = "#!/usr/bin/env bash\necho 'FAIL notes/x.md: missing header'\nexit 1\n"


class SecondBrainTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.path("project")
        self.brain = self.path("brain")
        os.makedirs(os.path.join(self.root, ".git"))
        support.write(os.path.join(self.brain, "bin", "kb"), LINT_OK)
        for page in ("KB/wiki/notes/welcome.md", "KB/wiki/notes/_index.md", "KB/wiki/cooking/bread.md"):
            support.write(os.path.join(self.brain, *page.split("/")), "# page\n")
        support.write(os.path.join(self.brain, "KB", "inbox", "RESEARCHER", "2026-11-06-mulch.md"), "# draft\n")
        support.write(os.path.join(self.brain, "KB", "inbox", "_objects", "abc.md"), "preimage\n")
        self.env = support.clean_env(CLAUDE_PROJECT_DIR=self.root)

    def config(self, **brain):
        brain.setdefault("path", "../brain")
        support.write(os.path.join(self.root, "os.config.json"), json.dumps({"second_brain": brain, "memory_caps": []}))

    def boot(self):
        result = support.run_tool("session_open.py", session_start(self.root), env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_boot_reports_pages_and_waiting_proposals(self):
        self.config(lint_on_close=True)
        context = self.boot()["hookSpecificOutput"]["additionalContext"]
        self.assertIn(
            "second brain: OK at ../brain: 2 wiki page(s), 1 proposal file(s) in KB/inbox for the owner to review; "
            "lint runs at close.", context)

    def test_missing_brain_is_reported(self):
        self.config(path="../nowhere")
        output = self.boot()
        self.assertIn("second brain: MISSING at ../nowhere", output["hookSpecificOutput"]["additionalContext"])
        self.assertIn("../nowhere was not found", output["systemMessage"])

    def test_invalid_block_is_reported(self):
        support.write(os.path.join(self.root, "os.config.json"), json.dumps({"second_brain": {"path": ""}}))
        output = self.boot()
        self.assertIn("second_brain.path must be", output["hookSpecificOutput"]["additionalContext"])

    def test_close_gate_runs_lint_only_when_asked(self):
        self.config()
        self.assertEqual(support.run_tool("session_close_gate.py", stop_input(self.root), env=self.env).returncode, 0)
        support.write(os.path.join(self.brain, "bin", "kb"), LINT_FAIL)
        self.assertEqual(support.run_tool("session_close_gate.py", stop_input(self.root), env=self.env).returncode, 0)
        self.config(lint_on_close=True)
        result = support.run_tool("session_close_gate.py", stop_input(self.root), env=self.env)
        self.assertEqual(result.returncode, 2, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("second brain lint exited 1", decision["reason"])
        self.assertIn("missing header", decision["reason"])
        support.write(os.path.join(self.brain, "bin", "kb"), LINT_OK)
        self.assertEqual(support.run_tool("session_close_gate.py", stop_input(self.root), env=self.env).returncode, 0)

    def test_agents_cannot_write_into_the_brain(self):
        self.config()
        for relative in ("KB/wiki/notes/welcome.md", "KB/inbox/RESEARCHER/new.md", "KB/kb.json"):
            target = os.path.join(self.brain, *relative.split("/"))
            result = support.run_tool("protect_paths.py", pre_tool_use(self.root, "Write", target, content="x"), env=self.env)
            self.assertEqual(result.returncode, 0, result.stderr)
            reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("bin/kb write put", reason)
        draft = os.path.join(self.root, "draft.md")
        result = support.run_tool("protect_paths.py", pre_tool_use(self.root, "Write", draft, content="x"), env=self.env)
        self.assertEqual(result.stdout, "", "a draft in the project is fine")

    def test_without_the_block_nothing_changes(self):
        support.write(os.path.join(self.root, "os.config.json"), json.dumps({"memory_caps": []}))
        context = self.boot()["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("second brain", context)


if __name__ == "__main__":
    unittest.main()
