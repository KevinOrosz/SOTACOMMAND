"""tools/wizard/wizard.py: the deterministic half of the one-word WIZARD boot."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WIZARD = os.path.join(REPO, "tools", "wizard", "wizard.py")


def run(*args):
    return subprocess.run(
        [sys.executable, WIZARD] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
    )


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.target = os.path.realpath(tempfile.mkdtemp(prefix="wizard-test-"))
        self.addCleanup(shutil.rmtree, self.target, True)

    def path(self, *parts):
        return os.path.join(self.target, *parts)

    def test_doctor_reports_python(self):
        result = run("doctor")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertRegex(result.stdout, r"OK   python 3\.\d+")

    def test_generic_install_is_ready(self):
        result = run("install", "--target", self.target)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for relative in ("os.config.json", "memory/HANDOFF.md", "memory/lessons.md", "AGENTS.md"):
            self.assertTrue(os.path.isfile(self.path(*relative.split("/"))), relative)
        self.assertTrue(os.path.isfile(self.path(".agents", "skills", "csv-summary", "SKILL.md")))
        self.assertFalse(os.path.exists(self.path(".github")), "copilot files only on request")
        config = json.load(open(self.path("os.config.json"), encoding="utf-8"))
        self.assertIn("os.config.json", config["protected_paths"])
        check = run("check", "--target", self.target)
        self.assertEqual(check.returncode, 0, check.stdout)
        self.assertIn("wizard: ready", check.stdout)

    def test_existing_files_are_never_overwritten(self):
        os.makedirs(self.path("memory"))
        with open(self.path("memory", "HANDOFF.md"), "w", encoding="utf-8") as handle:
            handle.write("# Mine\n")
        with open(self.path("AGENTS.md"), "w", encoding="utf-8") as handle:
            handle.write("# My agents\n")
        result = run("install", "--target", self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kept existing memory/HANDOFF.md", result.stdout)
        self.assertEqual(open(self.path("memory", "HANDOFF.md"), encoding="utf-8").read(), "# Mine\n")
        self.assertEqual(open(self.path("AGENTS.md"), encoding="utf-8").read(), "# My agents\n")

    def test_copilot_and_claude(self):
        result = run("install", "--target", self.target, "--harness", "copilot", "--harness", "claude")
        self.assertEqual(result.returncode, 0, result.stderr)
        agents = sorted(os.listdir(self.path(".github", "agents")))
        self.assertIn("orchestrator.agent.md", agents)
        self.assertEqual(len(agents), 6)
        self.assertIn("claude plugin install sotacommand-engine@sotacommand", result.stdout)
        self.assertFalse(os.path.exists(self.path("AGENTS.md")), "generic files only on request")

    def test_dry_run_writes_nothing(self):
        result = run("install", "--target", self.target, "--dry-run", "--harness", "generic", "--harness", "copilot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.listdir(self.target), [])
        self.assertIn("dry run, nothing written", result.stdout)

    def test_check_on_an_empty_folder_needs_attention(self):
        result = run("check", "--target", self.target)
        self.assertEqual(result.returncode, 1)
        self.assertIn("MISSING os.config.json", result.stdout)

    def test_cursor_gemini_and_flat(self):
        result = run("install", "--target", self.target, "--harness", "cursor", "--harness", "gemini", "--harness", "flat")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(self.path("AGENTS.md")), "cursor and gemini read AGENTS.md")
        self.assertIn("@./AGENTS.md", self.read("GEMINI.md"))
        rule = self.read(".cursor", "rules", "sotacommand.mdc")
        self.assertTrue(rule.startswith("---\ndescription: "))
        self.assertIn("alwaysApply: true", rule)
        flat = self.read("SOTACOMMAND.prompt.md")
        self.assertIn("## Project instructions", flat)
        self.assertIn("## Skill: csv-summary", flat)
        self.assertIn("### Roles", flat)
        self.assertNotIn("\nname: csv-summary\n", flat, "frontmatter is stripped")

    def test_hermes_gets_the_neutral_install_and_its_own_steps(self):
        result = run("install", "--target", self.target, "--harness", "hermes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(self.path("AGENTS.md")))
        self.assertTrue(os.path.isfile(self.path(".agents", "skills", "csv-summary", "SKILL.md")))
        self.assertIn('hermes skills trust "%s"' % self.target, result.stdout)
        self.assertIn("run `git init` in it first", result.stdout)
        self.assertNotIn("Heads up", result.stdout)
        os.makedirs(self.path(".git"))
        with open(self.path(".hermes.md"), "w", encoding="utf-8") as handle:
            handle.write("# mine\n")
        again = run("install", "--target", self.target, "--harness", "hermes")
        self.assertNotIn("git init", again.stdout)
        self.assertIn("Heads up: .hermes.md is in this folder, and Hermes reads it INSTEAD of AGENTS.md", again.stdout)
        self.assertEqual(open(self.path(".hermes.md"), encoding="utf-8").read(), "# mine\n", "the owner's file is never touched")
        self.assertIn("INFO    .hermes.md present: Hermes Agent reads it instead of AGENTS.md", run("check", "--target", self.target).stdout)

    def test_flat_alone_writes_no_agents_md(self):
        result = run("install", "--target", self.target, "--harness", "flat")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(self.path("SOTACOMMAND.prompt.md")))
        self.assertFalse(os.path.exists(self.path("AGENTS.md")))

    def test_uninstall_removes_only_unchanged_files(self):
        result = run("install", "--target", self.target, "--harness", "generic", "--harness", "copilot",
                     "--harness", "cursor", "--harness", "gemini", "--harness", "flat")
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(self.path("memory", "lessons.md"), "a", encoding="utf-8") as handle:
            handle.write("- a lesson the owner wrote\n")
        with open(self.path("notes.md"), "w", encoding="utf-8") as handle:
            handle.write("not ours\n")
        dry = run("uninstall", "--target", self.target, "--dry-run")
        self.assertEqual(dry.returncode, 1, dry.stdout)
        self.assertIn("would remove AGENTS.md", dry.stdout)
        self.assertTrue(os.path.isfile(self.path("AGENTS.md")), "a dry run removes nothing")
        result = run("uninstall", "--target", self.target)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("kept memory/lessons.md (changed since install)", result.stdout)
        left = sorted(
            os.path.relpath(os.path.join(folder, name), self.target).replace(os.sep, "/")
            for folder, _, files in os.walk(self.target) for name in files
        )
        self.assertEqual(left, [".sotacommand/install.json", "memory/lessons.md", "notes.md"])
        os.remove(self.path("memory", "lessons.md"))
        again = run("uninstall", "--target", self.target)
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertEqual(os.listdir(self.target), ["notes.md"])

    def test_uninstall_without_a_record(self):
        result = run("uninstall", "--target", self.target)
        self.assertEqual(result.returncode, 0)
        self.assertIn("nothing to uninstall", result.stdout)

    def test_reinstall_keeps_the_original_hashes_of_untouched_files(self):
        self.assertEqual(run("install", "--target", self.target).returncode, 0)
        first = json.loads(self.read(".sotacommand", "install.json"))["files"]
        self.assertEqual(run("install", "--target", self.target, "--harness", "gemini").returncode, 0)
        second = json.loads(self.read(".sotacommand", "install.json"))["files"]
        self.assertEqual({k: v for k, v in second.items() if k in first and not k.endswith(".sotacommand-install.json")},
                         {k: v for k, v in first.items() if not k.endswith(".sotacommand-install.json")})
        self.assertIn("GEMINI.md", second)

    def read(self, *parts):
        with open(self.path(*parts), encoding="utf-8") as handle:
            return handle.read()

    def test_usage_errors(self):
        self.assertEqual(run("install", "--target", self.path("nope")).returncode, 2)
        self.assertEqual(run("install", "--target", REPO).returncode, 2)
        self.assertEqual(run("check", "--target", self.path("nope")).returncode, 2)
        self.assertEqual(run("uninstall", "--target", self.path("nope")).returncode, 2)
        self.assertEqual(run().returncode, 2)


ANSWERS = {
    "name": "Sam", "project": "Sam's Bakery", "kind": "artist",
    "what": "We bake sourdough bread and sell it at two weekend markets.",
    "audience": "busy families", "goals": ["an online order page", "a weekly newsletter"],
    "voice": "warm, direct", "never": ["never promise delivery dates"],
    "helpers": ["researcher", "repurposer"], "focus": {"researcher": "find three markets we could join"},
    "second_brain": "yes",
}


class InterviewTests(unittest.TestCase):
    def setUp(self):
        self.target = os.path.realpath(tempfile.mkdtemp(prefix="wizard-interview-"))
        self.addCleanup(shutil.rmtree, self.target, True)

    def path(self, *parts):
        return os.path.join(self.target, *parts)

    def read(self, *parts):
        with open(self.path(*parts), encoding="utf-8") as handle:
            return handle.read()

    def answers(self, **changes):
        data = dict(ANSWERS, **changes)
        os.makedirs(self.path(".sotacommand"), exist_ok=True)
        with open(self.path(".sotacommand", "answers.json"), "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return self.path(".sotacommand", "answers.json")

    def test_questions_are_plain_and_complete(self):
        text = run("questions").stdout
        for key in ("[name]", "[project]", "[kind]", "[what]", "[audience]", "[goals]", "[voice]", "[never]",
                    "[approval]", "[helpers]", "[tool]", "[second_brain]", "[focus.<helper id>]"):
            self.assertIn(key, text)
        script = json.loads(run("questions", "--json").stdout)
        jargon = re.compile(r"subagent|orchestrat|harness|frontmatter|worktree|LLM|prompt|token", re.I)
        for question in script["questions"]:
            self.assertIsNone(jargon.search(question["ask"]), question["ask"])
            self.assertLessEqual(len(question["ask"]), 160)
        self.assertEqual([h["id"] for h in script["helpers"]], ["builder", "researcher", "owner-auditor", "sensor", "repurposer"])
        self.assertEqual(sorted(script["recommended_helpers"]), sorted(c["id"] for c in script["questions"][2]["choices"]))

    def test_answers_file_writes_profile_and_team_card(self):
        result = run("interview", "--target", self.target, "--answers", self.answers())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Helpers on: researcher, repurposer.", result.stdout)
        profile = self.read("profile", "owner.md")
        for text in ("**Owner:** Sam", "**Project:** Sam's Bakery", "busy families", "warm, direct",
                     "- never promise delivery dates", "Anything that gets published, sent to someone, or costs money."):
            self.assertIn(text, profile)
        team = self.read("TEAM.md")
        self.assertIn("# Sam's Bakery: your AI team", team)
        self.assertIn("| The Researcher | `researcher` |", team)
        self.assertIn("*Researcher: find three markets we could join*", team)
        self.assertIn("*Writer: turn this approved piece into three short drafts in my voice.*", team)
        off = team.split("## Helpers that are off", 1)[1].split("\n## ", 1)[0]
        for helper in ("`builder`", "`owner-auditor`", "`sensor`"):
            self.assertIn(helper, off)
        self.assertNotIn("| The Builder |", team)
        self.assertIn("SOTASECONDBRAIN", team)
        self.assertIn("type `WIZARD` there too", team)
        saved = json.loads(self.read("profile", "answers.json"))
        self.assertEqual(saved["helpers"], ["researcher", "repurposer"])
        record = json.loads(self.read(".sotacommand", "install.json"))["files"]
        self.assertIn("TEAM.md", record)

    def test_never_replaced_without_redo(self):
        answers = self.answers()
        self.assertEqual(run("interview", "--target", self.target, "--answers", answers).returncode, 0)
        again = run("interview", "--target", self.target, "--answers", self.answers(name="Alex"))
        self.assertEqual(again.returncode, 1)
        self.assertIn("Your answers are safe", again.stderr)
        self.assertIn("**Owner:** Sam", self.read("profile", "owner.md"))
        redo = run("interview", "--target", self.target, "--answers", self.answers(name="Alex"), "--redo")
        self.assertEqual(redo.returncode, 0, redo.stderr)
        self.assertIn("**Owner:** Alex", self.read("profile", "owner.md"))

    def test_defaults_follow_the_kind_of_project(self):
        minimal = {"name": "Kai", "project": "Kai Paints", "kind": "artist", "what": "I paint murals."}
        os.makedirs(self.path("x"))
        with open(self.path("x", "a.json"), "w", encoding="utf-8") as handle:
            json.dump(minimal, handle)
        result = run("interview", "--target", self.target, "--answers", self.path("x", "a.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Helpers on: researcher, owner-auditor, repurposer.", result.stdout)
        self.assertIn("**Voice:** clear, friendly, honest", self.read("profile", "owner.md"))

    def test_bad_answers_are_explained_and_nothing_is_written(self):
        cases = {
            "name is required": dict(ANSWERS, name=""),
            "unknown helper(s) wizard": dict(ANSWERS, helpers=["wizard"]),
            "kind must be one of": dict(ANSWERS, kind="robot"),
            "unknown answer(s): colour": dict(ANSWERS, colour="blue"),
            "longer than 400 characters": dict(ANSWERS, what="x" * 401),
        }
        for message, data in cases.items():
            with self.subTest(message=message):
                with open(self.path("bad.json"), "w", encoding="utf-8") as handle:
                    json.dump(data, handle)
                result = run("interview", "--target", self.target, "--answers", self.path("bad.json"))
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertFalse(os.path.exists(self.path("TEAM.md")))

    def test_terminal_interview(self):
        typed = "\n".join([
            "Sam", "", "Sam's Bakery", "9", "1", "We bake bread.", "", "an order page, a newsletter", "", "", "",
            "1,2", "3", "", "make an order page", "",
        ]) + "\n"
        result = subprocess.run([sys.executable, WIZARD, "interview", "--target", self.target], input=typed,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("This one is needed", result.stdout)
        self.assertIn("Please type a number from the list.", result.stdout)
        self.assertIn("Helpers on: builder, researcher.", result.stdout)
        team = self.read("TEAM.md")
        self.assertIn("*Builder: make an order page*", team)
        self.assertIn("1. Open **Cursor**.", team)
        self.assertIn("the Builder works in its own copy", result.stdout)
        self.assertIn("--harness cursor", result.stdout)

    def test_full_flow_install_interview_check_uninstall(self):
        self.assertEqual(run("install", "--target", self.target).returncode, 0)
        before = run("check", "--target", self.target)
        self.assertIn("INFO    TEAM.md (your team card; not written yet: run the interview)", before.stdout)
        self.assertEqual(run("interview", "--target", self.target, "--answers", self.answers()).returncode, 0)
        after = run("check", "--target", self.target)
        self.assertEqual(after.returncode, 0, after.stdout)
        self.assertIn("OK      profile/owner.md", after.stdout)
        agents = self.read("AGENTS.md")
        self.assertIn("Read `profile/owner.md` and `TEAM.md` if they exist", agents)
        self.assertIn("never hand work to a helper it lists as off", agents)
        self.assertFalse(os.path.exists(self.path(".sotacommand", "answers.json")), "the temporary answers file is removed")
        self.assertEqual(run("uninstall", "--target", self.target).returncode, 0)
        self.assertEqual(os.listdir(self.target), [])

    def test_team_card_opens_with_exact_steps_for_the_tool(self):
        expected = {"claude": "Type `claude` and press Enter.", "codex": "Type `codex` and press Enter.",
                    "gemini": "Type `gemini` and press Enter.", "hermes": "type `hermes skills trust` and press Enter", "cursor": "Open **Cursor**.",
                    "copilot": "pick the `orchestrator` agent", "chat": "SOTACOMMAND.prompt.md", "other": "File → Open Folder"}
        for tool, text in expected.items():
            with self.subTest(tool=tool):
                result = run("interview", "--target", self.target, "--answers", self.answers(tool=tool), "--redo")
                self.assertEqual(result.returncode, 0, result.stderr)
                team = self.read("TEAM.md")
                steps = team.split("## How to open your team", 1)[1].split("\n## ", 1)[0]
                self.assertIn(text, steps)
                self.assertLess(team.index("## How to open your team"), team.index("## Your helpers"))
                if tool not in ("chat",):
                    self.assertIn(os.path.basename(self.target), steps)

    def test_cd_line_works_in_a_shell(self):
        sys.path.insert(0, os.path.dirname(WIZARD))
        import interview
        self.assertEqual(interview.cd_argument("~/maya-clay-studio"), "~/maya-clay-studio")
        self.assertEqual(interview.cd_argument("~/Maya Studio"), '~/"Maya Studio"')
        self.assertEqual(interview.cd_argument("/tmp/a b"), '"/tmp/a b"')
        if os.name == "nt":
            return  # the shell check below needs a POSIX sh
        home = os.path.realpath(tempfile.mkdtemp(prefix="wizard-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        os.makedirs(os.path.join(home, "Maya Studio"))
        for folder in ("~/Maya Studio",):
            out = subprocess.run(["sh", "-c", "cd %s && pwd" % interview.cd_argument(folder)], env=dict(os.environ, HOME=home),
                                 stdout=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(os.path.realpath(out.stdout.strip()), os.path.join(home, "Maya Studio"))

    def test_builder_without_git_gets_a_plain_hint(self):
        result = run("interview", "--target", self.target, "--answers", self.answers(helpers=["builder"]))
        self.assertIn("git init", result.stdout)
        self.assertIn('INFO    git project (the Builder needs one', run("check", "--target", self.target).stdout)
        os.makedirs(self.path(".git"))
        self.assertIn("OK      git project", run("check", "--target", self.target).stdout)
        quiet = run("interview", "--target", self.target, "--answers", self.answers(helpers=["researcher"]), "--redo")
        self.assertNotIn("git init", quiet.stdout)

    def test_install_can_create_the_folder(self):
        new = self.path("maya-clay-studio")
        dry = run("install", "--target", new, "--create", "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertFalse(os.path.exists(new))
        self.assertIn("would create the folder", dry.stdout)
        self.assertIn("would write os.config.json", dry.stdout)
        self.assertIn("would write AGENTS.md", dry.stdout)
        self.assertEqual(run("install", "--target", new).returncode, 2, "without --create a missing folder is still an error")
        made = run("install", "--target", new, "--create")
        self.assertEqual(made.returncode, 0, made.stderr)
        self.assertTrue(os.path.isfile(os.path.join(new, "os.config.json")))

    def test_usage_errors(self):
        self.assertEqual(run("interview", "--target", self.path("nope")).returncode, 2)
        self.assertEqual(run("interview", "--target", REPO).returncode, 2)
        self.assertEqual(run("interview", "--target", self.target, "--answers", self.path("none.json")).returncode, 2)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.target = os.path.realpath(tempfile.mkdtemp(prefix="wizard-update-"))
        self.addCleanup(shutil.rmtree, self.target, True)
        done = run("install", "--target", self.target, "--harness", "generic", "--harness", "gemini", "--harness", "copilot")
        self.assertEqual(done.returncode, 0, done.stderr)

    def path(self, *parts):
        return os.path.join(self.target, *parts)

    def read(self, *parts):
        with open(self.path(*parts), encoding="utf-8", newline="") as handle:
            return handle.read()

    def write(self, text, *parts):
        with open(self.path(*parts), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def record(self):
        return json.loads(self.read(".sotacommand", "install.json"))

    def pretend_old(self, relative, old_text="old text from an earlier version\n"):
        """Make ``relative`` look like an untouched file from an older release."""
        import hashlib
        self.write(old_text, *relative.split("/"))
        data = self.record()
        data["files"][relative] = hashlib.sha256(old_text.encode("utf-8")).hexdigest()
        data["version"] = "0.0.9"
        self.write(json.dumps(data), ".sotacommand", "install.json")

    def snapshot(self):
        out = {}
        for folder, _, names in os.walk(self.target):
            for name in names:
                with open(os.path.join(folder, name), "rb") as handle:
                    out[os.path.relpath(os.path.join(folder, name), self.target)] = handle.read()
        return out

    def test_fresh_install_is_up_to_date(self):
        before = self.snapshot()
        result = run("update", "--target", self.target)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("everything is already up to date", result.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("OK      version", run("check", "--target", self.target).stdout)

    def test_unchanged_files_are_replaced_and_edited_ones_kept(self):
        self.pretend_old("GEMINI.md")
        self.pretend_old(".agents/skills/csv-summary/SKILL.md")
        mine = self.read("AGENTS.md") + "\nmy own line\n"
        self.write(mine, "AGENTS.md")
        self.assertIn("installed with version 0.0.9", run("check", "--target", self.target).stdout)
        result = run("update", "--target", self.target)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("updated GEMINI.md", result.stdout)
        self.assertIn("updated .agents/skills/csv-summary/SKILL.md", result.stdout)
        self.assertIn("kept your AGENTS.md (you changed it); the new version is beside it as AGENTS.md.new", result.stdout)
        self.assertIn("version: 0.0.9 -> ", result.stdout)
        self.assertIn("@./AGENTS.md", self.read("GEMINI.md"))
        self.assertIn("name: csv-summary", self.read(".agents", "skills", "csv-summary", "SKILL.md"))
        self.assertEqual(self.read("AGENTS.md"), mine)
        self.assertNotIn("my own line", self.read("AGENTS.md.new"))
        self.assertNotEqual(self.record()["version"], "0.0.9")
        after = self.snapshot()
        again = run("update", "--target", self.target)
        self.assertEqual(again.returncode, 1, "the kept file still needs the owner's eyes")
        self.assertEqual(self.snapshot(), after, "a second run changes nothing")

    def test_settings_only_gain_what_they_lack(self):
        config = json.loads(self.read("os.config.json"))
        del config["shell_guard"], config["ledger"]
        config["protected_paths"].remove(".sotacommand/**")
        config["protected_paths"].append("secrets/**")
        config["close_gate"]["verify"] = "make test"
        config["mine"] = {"keep": True}
        original = json.dumps(config, indent=2)
        self.write(original, "os.config.json")
        result = run("update", "--target", self.target)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for line in ("added setting shell_guard", "added setting ledger", "added protected path .sotacommand/**"):
            self.assertIn(line, result.stdout)
        merged = json.loads(self.read("os.config.json"))
        self.assertEqual(merged["close_gate"]["verify"], "make test")
        self.assertEqual(merged["mine"], {"keep": True})
        self.assertIn("secrets/**", merged["protected_paths"])
        self.assertIn(".sotacommand/**", merged["protected_paths"])
        self.assertIn("ask", merged["shell_guard"])
        self.assertEqual(self.read("os.config.json.bak"), original)
        self.assertIn("nothing to add", run("update", "--target", self.target).stdout)

    def test_dry_run_changes_nothing(self):
        self.pretend_old("GEMINI.md")
        config = json.loads(self.read("os.config.json"))
        del config["ledger"]
        self.write(json.dumps(config), "os.config.json")
        before = self.snapshot()
        result = run("update", "--target", self.target, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("would update GEMINI.md", result.stdout)
        self.assertIn("added setting ledger", result.stdout)
        self.assertIn("dry run, nothing changed", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_team_card_is_rebuilt_from_saved_answers(self):
        os.makedirs(self.path(".sotacommand"), exist_ok=True)
        self.write(json.dumps(ANSWERS), ".sotacommand", "answers.json")
        self.assertEqual(run("interview", "--target", self.target, "--answers", self.path(".sotacommand", "answers.json")).returncode, 0)
        card = self.read("TEAM.md")
        self.pretend_old("TEAM.md")
        self.assertEqual(run("update", "--target", self.target).returncode, 0)
        self.assertEqual(self.read("TEAM.md"), card)
        self.assertIn("**Owner:** Sam", self.read("profile", "owner.md"))

    def test_profile_memory_and_removed_files_are_left_alone(self):
        self.write("# my note\n", "memory", "HANDOFF.md")
        os.remove(self.path("GEMINI.md"))
        result = run("update", "--target", self.target)
        self.assertIn("left alone: GEMINI.md (you removed it)", result.stdout)
        self.assertFalse(os.path.exists(self.path("GEMINI.md")))
        self.assertEqual(self.read("memory", "HANDOFF.md"), "# my note\n")

    def test_refusals(self):
        empty = os.path.realpath(tempfile.mkdtemp(prefix="wizard-empty-"))
        self.addCleanup(shutil.rmtree, empty, True)
        none = run("update", "--target", empty)
        self.assertEqual(none.returncode, 2)
        self.assertIn("run install first", none.stderr)
        self.pretend_old("GEMINI.md")
        self.write("{broken", "os.config.json")
        before = self.snapshot()
        broken = run("update", "--target", self.target)
        self.assertEqual(broken.returncode, 2)
        self.assertIn("os.config.json can't be read", broken.stderr)
        self.assertEqual(self.snapshot(), before, "nothing is written when the settings file is broken")

    def test_uninstall_after_update_removes_the_new_files_too(self):
        self.write(self.read("AGENTS.md") + "\nmine\n", "AGENTS.md")
        self.pretend_old("GEMINI.md")
        run("update", "--target", self.target)
        self.assertTrue(os.path.isfile(self.path("AGENTS.md.new")))
        run("uninstall", "--target", self.target)
        self.assertFalse(os.path.exists(self.path("AGENTS.md.new")))
        self.assertTrue(os.path.isfile(self.path("AGENTS.md")), "the owner's edited file is kept")


class WizardDocsTests(unittest.TestCase):
    """The one-word boot is wired up wherever an agent starts reading."""

    def read(self, *parts):
        with open(os.path.join(REPO, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_agents_md_routes_wizard_first(self):
        text = self.read("AGENTS.md")
        first_section = text.split("\n## ", 2)[1]
        self.assertIn("WIZARD", first_section)
        self.assertIn("WIZARD.md", first_section)

    def test_wizard_md_names_only_real_commands(self):
        text = self.read("WIZARD.md")
        for command in re.findall(r"python3 tools/wizard/wizard\.py (\w+)", text):
            self.assertIn(command, ("doctor", "install", "check", "uninstall", "questions", "interview", "update"))
        self.assertIn("## Done condition", text)

    def test_wizard_is_the_first_thing_a_newcomer_sees(self):
        readme = self.read("README.md")
        top = readme.split("\n## ", 1)[0]
        self.assertIn("New to AI agents? Start here.", top)
        self.assertIn("Type **`WIZARD`**", top)
        self.assertLess(readme.index("## What happens when you type WIZARD"), readme.index("## TL;DR"))
        for name in ("README.md", "START-HERE.md", "WIZARD.md", "integrations/second-brain/README.md"):
            self.assertIn("https://github.com/KevinOrosz/SOTASECONDBRAIN", self.read(*name.split("/")), name)
        wizard = self.read("WIZARD.md")
        for step in ("wizard.py questions", "wizard.py interview --target", "one question at a time", "TEAM.md",
                     "--create", "How to open your team", "this session is not their team", "git init"):
            self.assertIn(step, wizard)
        self.assertNotIn("Give me its full path", wizard)
        skill = self.read("engine", "skills", "wizard", "SKILL.md")
        self.assertNotIn("python3", skill.split("## 2. Check it", 1)[1].split("\n## ", 1)[0], "the Lead can't run commands")
        self.assertIn("When the owner says `close`", self.read("engine", "skills", "os-boot", "SKILL.md"))
        for boot in ("CLAUDE.md", "GEMINI.md"):
            self.assertIn("AGENTS.md", self.read(boot))
            self.assertIn("WIZARD", self.read(boot))

    def test_readme_and_start_here_have_tldr_and_eli10(self):
        for name in ("README.md", "START-HERE.md"):
            text = self.read(name)
            with self.subTest(file=name):
                self.assertIn("TL;DR", text)
                self.assertIn("Explain it like I'm 10", text)
                self.assertIn("WIZARD", text)


if __name__ == "__main__":
    unittest.main()


class ReviewFindingsWizardTests(unittest.TestCase):
    """The final pre-launch review's install, update and uninstall findings."""

    def setUp(self):
        self.target = os.path.realpath(tempfile.mkdtemp(prefix="wizard-review-"))
        self.addCleanup(shutil.rmtree, self.target, True)
        self.assertEqual(run("install", "--target", self.target).returncode, 0)

    def path(self, *parts):
        return os.path.join(self.target, *parts)

    def read(self, *parts):
        with open(self.path(*parts), encoding="utf-8", newline="") as handle:
            return handle.read()

    def write(self, text, *parts, mode="w"):
        os.makedirs(os.path.dirname(self.path(*parts)), exist_ok=True)
        with open(self.path(*parts), mode, encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def test_reinstall_keeps_an_edited_skill(self):
        self.write("\nMYEDIT\n", ".agents", "skills", "changelog-entry", "SKILL.md", mode="a")
        self.write("my notes\n", ".agents", "skills", "changelog-entry", "notes.md")
        again = run("install", "--target", self.target)
        self.assertIn("kept your changed skill changelog-entry", again.stdout)
        self.assertIn("MYEDIT", self.read(".agents", "skills", "changelog-entry", "SKILL.md"))
        self.assertEqual(self.read(".agents", "skills", "changelog-entry", "notes.md"), "my notes\n")
        self.assertIn("installed skill csv-summary", again.stdout, "untouched skills are still refreshed")
        update = run("update", "--target", self.target)
        self.assertIn("kept your .agents/skills/changelog-entry/SKILL.md (you changed it)", update.stdout)

    def test_reinstall_of_an_untouched_skill_still_works(self):
        os.remove(self.path(".agents", "skills", "csv-summary", "SKILL.md"))
        again = run("install", "--target", self.target)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertTrue(os.path.isfile(self.path(".agents", "skills", "csv-summary", "SKILL.md")), "a deleted file is restored")

    def test_update_never_replaces_a_backup_or_an_owner_new_file(self):
        def drop_setting(key):
            config = json.loads(self.read("os.config.json"))
            config.pop(key, None)
            self.write(json.dumps(config), "os.config.json")
            return json.dumps(config)
        first = drop_setting("ledger")
        self.assertEqual(run("update", "--target", self.target).returncode, 0)
        drop_setting("shell_guard")
        run("update", "--target", self.target)
        self.assertEqual(self.read("os.config.json.bak"), first, "the first backup survives a second update")
        self.assertTrue(os.path.isfile(self.path("os.config.json.bak2")))
        self.write("my own notes\n", "AGENTS.md.new")
        self.write(self.read("AGENTS.md") + "\nmine\n", "AGENTS.md")
        result = run("update", "--target", self.target)
        self.assertIn("beside it as AGENTS.md.new2", result.stdout)
        self.assertEqual(self.read("AGENTS.md.new"), "my own notes\n")

    def test_update_does_not_treat_an_odd_encoding_as_missing(self):
        with open(self.path("AGENTS.md"), "wb") as handle:
            handle.write("café notes\n".encode("latin-1"))
        result = run("update", "--target", self.target)
        self.assertNotIn("you removed it", result.stdout)
        self.assertIn("kept your AGENTS.md (you changed it)", result.stdout)
        with open(self.path("AGENTS.md"), "rb") as handle:
            self.assertEqual(handle.read(), "café notes\n".encode("latin-1"))

    def test_after_uninstall_keeps_a_skill_install_still_recognizes_it(self):
        self.write("\nMYEDIT\n", ".agents", "skills", "changelog-entry", "SKILL.md", mode="a")
        run("uninstall", "--target", self.target)
        self.assertIn("MYEDIT", self.read(".agents", "skills", "changelog-entry", "SKILL.md"))
        again = run("install", "--target", self.target)
        self.assertNotIn("was not installed by this tool", again.stdout)
        self.assertIn("kept your changed skill changelog-entry", again.stdout)
