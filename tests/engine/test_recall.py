"""engine/tools/recall.py: session history and a ranked search over the team's memory."""

import datetime
import json
import os
import unittest

from . import support
from .test_session_open import session_start

recall = support.load_tool_module("recall")
T0 = datetime.datetime(2026, 11, 1, 9, 0, 0, tzinfo=datetime.timezone.utc)


def at(n):
    return T0 + datetime.timedelta(days=n)


class RecallTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.path("project")
        os.makedirs(self.root)
        support.write(os.path.join(self.root, "os.config.json"), json.dumps({"memory_caps": []}))
        self.env = support.clean_env(CLAUDE_PROJECT_DIR=self.root)

    def handoff(self, text):
        support.write(os.path.join(self.root, "memory", "HANDOFF.md"), text)

    def test_a_changed_handoff_is_kept_once(self):
        self.handoff("# Handoff\n\n- Pricing: we chose the monthly plan at 12 dollars.\n")
        self.assertEqual(recall.archive_handoff(self.root, at(0)), "handoff-20261101T090000Z.md")
        self.assertIsNone(recall.archive_handoff(self.root, at(1)), "unchanged: nothing new to keep")
        self.handoff("# Handoff\n\n- Newsletter: first issue drafted, waiting for the owner.\n")
        self.assertEqual(recall.archive_handoff(self.root, at(2)), "handoff-20261103T090000Z.md")
        self.assertEqual(len(recall.snapshots(self.root)), 2)

    def test_nothing_is_kept_without_a_config_or_a_handoff(self):
        self.assertIsNone(recall.archive_handoff(self.root, at(0)), "no handoff yet")
        self.handoff("   \n")
        self.assertIsNone(recall.archive_handoff(self.root, at(0)), "an empty note isn't history")
        self.handoff("# note\n")
        os.remove(os.path.join(self.root, "os.config.json"))
        self.assertIsNone(recall.archive_handoff(self.root, at(0)))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".sotacommand")))

    def test_only_the_newest_copies_stay(self):
        support.write(os.path.join(self.root, "os.config.json"), json.dumps({"recall": {"keep": 3}}))
        for n in range(6):
            self.handoff("# day %d\n" % n)
            recall.archive_handoff(self.root, at(n))
        self.assertEqual(recall.snapshots(self.root),
                         ["handoff-20261104T090000Z.md", "handoff-20261105T090000Z.md", "handoff-20261106T090000Z.md"])

    def build_history(self):
        self.handoff("# Handoff\n\n- Pricing: we chose the monthly plan at 12 dollars.\n\n- Logo: blue version approved.\n")
        recall.archive_handoff(self.root, at(0))
        self.handoff("# Handoff\n\n- Newsletter: first issue drafted, waiting for the owner.\n")
        recall.archive_handoff(self.root, at(2))
        support.write(os.path.join(self.root, "memory", "lessons.md"), "# Lessons\n\n- Ask before renaming public files.\n")
        support.write(os.path.join(self.root, "profile", "owner.md"), "# About the owner\n\n- **Voice:** calm, warm\n")

    def test_search_finds_an_old_decision_and_says_where(self):
        self.build_history()
        for use_fts in (True, False):
            with self.subTest(fts=use_fts):
                hits = recall.search(self.root, "what did we decide about pricing?", use_fts=use_fts)
                self.assertEqual(hits[0][0], ".sotacommand/history/handoff-20261101T090000Z.md")
                self.assertEqual(hits[0][1], 3)
                self.assertIn("monthly plan at 12 dollars", hits[0][2])
        self.assertEqual(recall.search(self.root, "renaming files")[0][0], "memory/lessons.md")
        self.assertEqual(recall.search(self.root, "voice")[0][0], "profile/owner.md")
        self.assertEqual(recall.search(self.root, "zebra crossing"), [])
        self.assertEqual(recall.search(self.root, "?? !"), [])

    def test_todays_note_is_not_listed_twice(self):
        self.build_history()
        files = [hit[0] for hit in recall.search(self.root, "newsletter issue drafted")]
        self.assertEqual(files, ["memory/HANDOFF.md"])

    def test_stemming_and_quotes_do_not_break_the_query(self):
        self.build_history()
        self.assertTrue(recall.search(self.root, "approving logos"), "porter stemming matches approved and logo")
        self.assertEqual(recall.search(self.root, '"pricing" OR ) NEAR(')[0][1], 3)

    def test_cli(self):
        self.build_history()
        found = support.run_tool("recall.py", None, env=self.env, args=("pricing", "plan", "--root", self.root))
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIn(".sotacommand/history/handoff-20261101T090000Z.md:3", found.stdout)
        self.assertIn("    - Pricing: we chose the monthly plan", found.stdout)
        missing = support.run_tool("recall.py", None, env=self.env, args=("zebra", "--root", self.root))
        self.assertEqual(missing.returncode, 1)
        self.assertIn("nothing in this project's memory matches 'zebra' (2 past handoff(s) kept)", missing.stderr)
        self.assertEqual(support.run_tool("recall.py", None, env=self.env, args=("x", "--limit", "0")).returncode, 2)

    def test_session_start_keeps_the_note_and_reports_it(self):
        self.handoff("# Handoff\n\n- First day.\n")
        first = support.run_tool("session_open.py", session_start(self.root), env=self.env)
        context = json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("history: 1 past handoff(s) kept in .sotacommand/history/", context)
        support.run_tool("session_open.py", session_start(self.root), env=self.env)
        self.assertEqual(len(recall.snapshots(self.root)), 1, "the same note is not kept twice")
        text_mode = support.run_tool("session_open.py", None, env=self.env, args=("--text", "--root", self.root))
        self.assertNotIn("history:", text_mode.stdout, "a health check doesn't write history")

    def test_agents_cannot_rewrite_history(self):
        with open(os.path.join(support.ENGINE_DIR, "templates", "os.config.json"), encoding="utf-8") as handle:
            support.write(os.path.join(self.root, "os.config.json"), handle.read())
        target = os.path.join(self.root, ".sotacommand", "history", "handoff-20261101T090000Z.md")
        write = {"cwd": self.root, "hook_event_name": "PreToolUse", "tool_name": "Edit",
                 "tool_input": {"file_path": target, "old_string": "a", "new_string": "b"}}
        self.assertIn('"deny"', support.run_tool("protect_paths.py", write, env=self.env).stdout)

    def test_boot_instructions_point_at_the_history(self):
        with open(os.path.join(support.ENGINE_DIR, "skills", "os-boot", "SKILL.md"), encoding="utf-8") as handle:
            boot = handle.read()
        self.assertIn(".sotacommand/history/", boot)
        self.assertIn("never fill the gap from guesswork", boot)


if __name__ == "__main__":
    unittest.main()
