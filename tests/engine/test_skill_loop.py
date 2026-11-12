"""engine/tools/skill_loop.py: a repeated problem becomes a skill, and stable is earned."""

import datetime
import json
import os
import unittest

from . import support
from .test_session_open import session_start

ledger = support.load_tool_module("ledger")
loop = support.load_tool_module("skill_loop")
T0 = datetime.datetime(2026, 11, 1, 9, 0, 0, tzinfo=datetime.timezone.utc)
MATCH = r"shell_guard.ask \bgit\s+push"
PROPOSAL = "---\nname: release-checklist\ndescription: Use before any release to run the checklist in order.\nmatch: %s\n---\n1. Run the tests.\n2. Ask the owner.\n" % MATCH


def day(n, hour=10):
    return T0 + datetime.timedelta(days=n, hours=hour - 9)


class SkillLoopTests(support.TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.path("project")
        os.makedirs(self.root)
        support.write(os.path.join(self.root, "os.config.json"), json.dumps({"memory_caps": []}))
        self.env = support.clean_env(CLAUDE_PROJECT_DIR=self.root)

    def proposal(self, text=PROPOSAL, name="release-checklist"):
        support.write(os.path.join(self.root, "memory", "skill-proposals", name + ".md"), text)

    def ask(self, when):
        ledger.record(self.root, "asked", now=when, detail=MATCH)

    def session(self, when):
        ledger.record(self.root, "session_start", now=when)

    def skill(self, folder=".claude"):
        with open(os.path.join(self.root, folder, "skills", "release-checklist", "SKILL.md"), encoding="utf-8") as handle:
            return handle.read()

    def adopt(self, repeats_before=3):
        for n in range(repeats_before):
            self.ask(day(n))
        self.proposal()
        return loop.draft(self.root, "release-checklist", now=day(5))

    def verdict(self):
        return loop.verdicts(self.root)[0]

    def test_draft_writes_an_experimental_skill_with_a_baseline(self):
        os.makedirs(os.path.join(self.root, ".agents", "skills"))
        lines = self.adopt()
        self.assertIn("baseline: 3 matching refusal(s) in the last 28 days", lines[-1])
        text = self.skill()
        self.assertTrue(text.startswith('---\nname: release-checklist\ndescription: "Use before any release'))
        for line in ('  status: "experimental"', '  origin: "owner-auditor proposal"', '  baseline-repeats: "3"', '  adopted: "2026-11-06T10:00:00Z"'):
            self.assertIn(line, text)
        self.assertIn("1. Run the tests.", text)
        self.assertEqual(self.skill(".agents"), text, "the neutral skills folder gets the same file when it exists")
        with self.assertRaises(loop.LoopError):
            loop.draft(self.root, "release-checklist", now=day(6))

    def test_drafted_skills_lint_as_portable(self):
        self.adopt()
        text = self.skill()
        head = text.split("\n---\n", 1)[0]
        keys = [line.split(":", 1)[0] for line in head.splitlines()[1:] if not line.startswith(" ")]
        self.assertEqual(keys, ["name", "description", "metadata"])

    def test_bad_proposals_are_refused_with_a_reason(self):
        cases = {
            "no proposal at": (None, "release-checklist"),
            "lowercase words joined by dashes": (PROPOSAL, "Bad Name"),
            "must be 'other', the same as its file name": (PROPOSAL, "other"),
            "needs a description": (PROPOSAL.replace("description: Use before any release to run the checklist in order.", "description:"), "release-checklist"),
            "takes only name, description and match": (PROPOSAL.replace("match:", "status: stable\nmatch:"), "release-checklist"),
            "has no steps": ("---\nname: release-checklist\ndescription: d\n---\n", "release-checklist"),
        }
        for message, (text, name) in cases.items():
            with self.subTest(message=message):
                if text is not None and " " not in name:
                    self.proposal(text, name)
                with self.assertRaises(loop.LoopError) as caught:
                    loop.draft(self.root, name, now=day(5))
                self.assertIn(message, str(caught.exception))

    def test_measuring_then_qualifies_then_promote(self):
        self.adopt()
        self.assertEqual(self.verdict()["verdict"], "measuring")
        with self.assertRaises(loop.LoopError) as caught:
            loop.promote(self.root, "release-checklist", now=day(6))
        self.assertIn("stays experimental: measuring: 0 of 5 sessions", str(caught.exception))
        for n in range(4):
            self.session(day(6 + n))
        self.assertEqual(self.verdict()["why"], "4 of 5 sessions, no matching refusal so far")
        self.session(day(10))
        self.assertEqual(self.verdict()["verdict"], "qualifies")
        lines = loop.promote(self.root, "release-checklist", now=day(11))
        self.assertIn("skill release-checklist is stable: 5 sessions without a matching refusal; baseline 3", lines[-1])
        text = self.skill()
        self.assertIn('  status: "stable"', text)
        self.assertIn('  qualified: "2026-11-12"', text)
        with self.assertRaises(loop.LoopError):
            loop.promote(self.root, "release-checklist", now=day(12))

    def test_a_repeat_after_adoption_blocks_promotion(self):
        self.adopt()
        for n in range(6):
            self.session(day(6 + n))
        self.ask(day(8))
        self.assertEqual(self.verdict()["verdict"], "not helping yet")
        with self.assertRaises(loop.LoopError) as caught:
            loop.promote(self.root, "release-checklist", now=day(13))
        self.assertIn("1 matching refusal(s) since it was adopted (baseline 3 in 28 days)", str(caught.exception))
        self.assertIn('  status: "experimental"', self.skill())

    def test_unmeasured_skills_never_become_stable(self):
        self.proposal(PROPOSAL.replace("match: %s\n" % MATCH, ""))
        loop.draft(self.root, "release-checklist", now=day(5))
        for n in range(9):
            self.session(day(6 + n))
        self.assertEqual(self.verdict()["verdict"], "unmeasured")
        with self.assertRaises(loop.LoopError):
            loop.promote(self.root, "release-checklist", now=day(20))
        os.remove(os.path.join(self.root, ".claude", "skills", "release-checklist", "SKILL.md"))
        self.adopt(repeats_before=0)
        self.assertIn("no matching refusal before it was adopted", self.verdict()["why"])

    def test_a_hand_edited_stable_is_called_out(self):
        self.adopt()
        path = os.path.join(self.root, ".claude", "skills", "release-checklist", "SKILL.md")
        support.write(path, self.skill().replace('status: "experimental"', 'status: "stable"'))
        verdict = self.verdict()
        self.assertEqual((verdict["claimed"], verdict["verdict"]), ("stable", "measuring"))
        self.assertIn("its file says stable, but it was not promoted by this tool", verdict["why"])

    def test_min_sessions_comes_from_the_config(self):
        support.write(os.path.join(self.root, "os.config.json"), json.dumps({"skill_loop": {"min_sessions": 2}}))
        self.adopt()
        self.session(day(6)); self.session(day(7))
        self.assertEqual(self.verdict()["verdict"], "qualifies")

    def test_other_skills_are_ignored(self):
        support.write(os.path.join(self.root, ".claude", "skills", "mine", "SKILL.md"), "---\nname: mine\ndescription: d\n---\nx\n")
        self.assertEqual(loop.verdicts(self.root), [])

    def test_cli_and_session_start(self):
        self.proposal()
        args = ("--root", self.root)
        self.assertIn("no skills drafted", support.run_tool("skill_loop.py", None, env=self.env, args=("status",) + args).stdout)
        made = support.run_tool("skill_loop.py", None, env=self.env, args=("draft", "release-checklist") + args)
        self.assertEqual(made.returncode, 0, made.stderr)
        refused = support.run_tool("skill_loop.py", None, env=self.env, args=("promote", "release-checklist") + args)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("stays experimental", refused.stderr)
        self.assertEqual(support.run_tool("skill_loop.py", None, env=self.env, args=("draft",) + args).returncode, 2)
        boot = support.run_tool("session_open.py", session_start(self.root), env=self.env)
        context = json.loads(boot.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("session-open: skill release-checklist (experimental): unmeasured", context)

    def test_roles_and_boot_skill_describe_the_loop(self):
        with open(os.path.join(support.ENGINE_DIR, "agents", "owner-auditor.md"), encoding="utf-8") as handle:
            self.assertIn("memory/skill-proposals/<name>.md", handle.read())
        with open(os.path.join(support.ENGINE_DIR, "skills", "os-boot", "SKILL.md"), encoding="utf-8") as handle:
            boot = handle.read()
        self.assertIn("skill_loop.py\" draft <name>", boot)
        self.assertIn("never mark a skill stable by editing its file", boot)


if __name__ == "__main__":
    unittest.main()
