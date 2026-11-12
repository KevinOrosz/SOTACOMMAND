"""engine/tools/ledger.py: counted events for the owner-auditor."""

import datetime
import json
import os
import unittest

from . import support
from .test_close_gate import stop_input
from .test_session_open import session_start

ledger = support.load_tool_module("ledger")
NOW = datetime.datetime(2026, 11, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)


def call(root, tool, tool_input, agent="sotacommand-engine:builder"):
    return {"session_id": "abcdef123456", "cwd": root, "hook_event_name": "PreToolUse", "tool_name": tool,
            "agent_type": agent, "tool_input": tool_input}


class LedgerTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.path("project")
        os.makedirs(os.path.join(self.root, ".git"))
        with open(os.path.join(support.ENGINE_DIR, "templates", "os.config.json"), encoding="utf-8") as handle:
            self.template = json.load(handle)
        self.config(self.template)
        self.env = support.clean_env(CLAUDE_PROJECT_DIR=self.root)

    def config(self, data):
        support.write(os.path.join(self.root, "os.config.json"), json.dumps(data))

    def lines(self):
        path = os.path.join(self.root, ".sotacommand", "ledger.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def run_tool(self, name, payload, args=()):
        return support.run_tool(name, payload, env=self.env, args=args)

    def test_every_hook_writes_its_event(self):
        self.run_tool("session_open.py", dict(session_start(self.root), session_id="abcdef123456"))
        self.run_tool("ledger.py", {"session_id": "abcdef123456", "cwd": self.root, "hook_event_name": "UserPromptSubmit",
                                    "prompt": "my secret plan is tacos"}, args=("--event", "owner_message"))
        self.run_tool("protect_paths.py", call(self.root, "Write", {"file_path": os.path.join(self.root, "os.config.json"), "content": "{}"}))
        self.run_tool("protect_shell.py", call(self.root, "Bash", {"command": "echo hunter2-token > os.config.json"}))
        self.run_tool("protect_shell.py", call(self.root, "Bash", {"command": "sudo ls"}))
        self.run_tool("protect_shell.py", call(self.root, "Bash", {"command": "git push origin main"}))
        support.write(os.path.join(self.root, "profile", "answers.json"), json.dumps({"helpers": ["researcher"]}))
        self.run_tool("protect_team.py", call(self.root, "Agent", {"subagent_type": "sotacommand-engine:sensor"}, "sotacommand-engine:orchestrator"))
        self.config(dict(self.template, close_gate={"verify": "exit 3"}))
        self.run_tool("session_close_gate.py", stop_input(self.root))
        entries = self.lines()
        self.assertEqual([e["kind"] for e in entries],
                         ["session_start", "owner_message", "denied", "denied", "denied", "asked", "denied", "close_failed"])
        self.assertEqual([e.get("guard") for e in entries if e["kind"] == "denied"], ["write", "shell", "shell", "team"])
        self.assertEqual(entries[1]["chars"], 23)
        self.assertEqual(entries[2]["session"], "abcdef12")
        self.assertEqual(entries[2]["role"], "builder")
        text = json.dumps(entries)
        self.assertNotIn("tacos", text, "the owner's words are never stored")
        self.assertNotIn("hunter2", text, "shell commands are never stored")
        for entry in entries:
            self.assertRegex(entry["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_allowed_actions_write_nothing(self):
        self.run_tool("protect_paths.py", call(self.root, "Write", {"file_path": os.path.join(self.root, "notes.md"), "content": "x"}))
        self.run_tool("protect_shell.py", call(self.root, "Bash", {"command": "ls"}))
        self.run_tool("session_close_gate.py", stop_input(self.root))
        self.assertEqual(self.lines(), [])

    def test_off_without_config_or_when_disabled(self):
        self.config(dict(self.template, ledger={"enabled": False}))
        self.run_tool("protect_shell.py", call(self.root, "Bash", {"command": "sudo ls"}))
        self.assertEqual(self.lines(), [])
        os.remove(os.path.join(self.root, "os.config.json"))
        self.run_tool("session_open.py", session_start(self.root))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".sotacommand")), "an unconfigured folder is left alone")
        self.assertFalse(ledger.record(self.root, "denied"))

    def test_a_guard_decision_survives_a_broken_ledger(self):
        support.write(os.path.join(self.root, ".sotacommand"), "a file where the folder should be")
        result = self.run_tool("protect_shell.py", call(self.root, "Bash", {"command": "sudo ls"}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"deny"', result.stdout)

    def test_summary_counts_a_window(self):
        def at(days_ago, kind, **fields):
            self.assertTrue(ledger.record(self.root, kind, now=NOW - datetime.timedelta(days=days_ago), **fields))
        at(0, "session_start"); at(0, "owner_message", chars=5); at(0, "owner_message", chars=9)
        at(1, "denied", guard="shell", detail="same reason"); at(2, "denied", guard="shell", detail="same reason")
        at(3, "denied", guard="team", detail="other"); at(6, "asked"); at(6, "close_failed")
        at(7, "session_start"); at(30, "denied", guard="write", detail="old")
        week = ledger.summarize(self.root, 7, NOW)
        self.assertEqual(dict(week["totals"]), {"session_start": 1, "owner_message": 2, "denied": 3, "asked": 1,
                                                "close_failed": 1, "boot_invalid": 0, "memory_over": 0})
        self.assertEqual(dict(week["denied_by"]), {"write": 0, "shell": 2, "team": 1})
        self.assertEqual(week["repeated_denials"], [("same reason", 2)])
        self.assertEqual(list(week["by_day"])[0], "2026-11-04")
        self.assertIn("1 session(s), 2 owner message(s), 3 refusal(s) (0 write, 2 shell, 1 team), 1 approval(s) asked, 1 failed close(s).",
                      ledger.line(week))
        self.assertEqual(ledger.summarize(self.root, 28, NOW)["totals"]["session_start"], 2)
        text = ledger.render(week)
        self.assertIn("| Messages the owner sent | 2 |", text)
        self.assertIn("- 2 times: same reason", text)
        self.assertIn("counts, not estimates", text)

    def test_session_start_reports_and_refreshes_the_summary(self):
        self.run_tool("protect_shell.py", call(self.root, "Bash", {"command": "sudo ls"}))
        result = self.run_tool("session_open.py", session_start(self.root))
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ledger, last 7 days (counted, not estimated): 1 session(s), 0 owner message(s), 1 refusal(s)", context)
        with open(os.path.join(self.root, ".sotacommand", "ledger-summary.md"), encoding="utf-8") as handle:
            self.assertIn("| ...shell commands refused | 1 |", handle.read())
        report = self.run_tool("ledger.py", None, args=("report", "--root", self.root))
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("# Ledger summary", report.stdout)

    def test_bad_lines_are_skipped_and_the_file_rotates(self):
        path = os.path.join(self.root, ".sotacommand", "ledger.jsonl")
        support.write(path, "not json\n{\"ts\": 5}\n")
        ledger.record(self.root, "asked", now=NOW)
        self.assertEqual(ledger.summarize(self.root, 7, NOW)["totals"]["asked"], 1)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("x" * (ledger.MAX_BYTES + 10) + "\n")
        ledger.record(self.root, "asked", now=NOW)
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".sotacommand", "ledger.1.jsonl")))
        self.assertEqual(ledger.summarize(self.root, 7, NOW)["totals"]["asked"], 2, "the older file still counts")

    def test_agents_cannot_edit_the_ledger(self):
        target = os.path.join(self.root, ".sotacommand", "ledger.jsonl")
        self.assertIn('"deny"', self.run_tool("protect_paths.py", call(self.root, "Write", {"file_path": target, "content": ""})).stdout)
        self.assertIn('"deny"', self.run_tool("protect_shell.py", call(self.root, "Bash", {"command": "rm -rf .sotacommand"})).stdout)
        self.assertIn('"deny"', self.run_tool("protect_shell.py", call(self.root, "Bash", {"command": "echo > .sotacommand/ledger.jsonl"})).stdout)

    def test_the_owner_auditor_starts_from_the_counts(self):
        with open(os.path.join(support.ENGINE_DIR, "agents", "owner-auditor.md"), encoding="utf-8") as handle:
            agent = handle.read()
        self.assertIn(".sotacommand/ledger-summary.md", agent)
        self.assertIn("never present an estimate as a count", agent)


if __name__ == "__main__":
    unittest.main()
