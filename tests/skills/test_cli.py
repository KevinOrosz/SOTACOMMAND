"""skills.py plugins, the contract's rule table and the evals workflow's hardening."""

import re
import subprocess
import sys
import unittest

from .helpers import REPO, SKILLS_PY, TreeTestCase

from skillslib.lint import RULES  # noqa: E402

WORKFLOW = REPO / ".github" / "workflows" / "evals.yml"
CONTRACT = REPO / "tools" / "repo" / "skills" / "CONTRACT.md"


class PluginsCommandTests(TreeTestCase):
    def run_cli(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SKILLS_PY), "plugins", "--root", str(self.root)] + list(args),
            input=stdin,
            capture_output=True,
            text=True,
        )

    def setUp(self) -> None:
        super().setUp()
        self.tree(
            {
                "labs/lab/.claude-plugin/plugin.json": '{"name": "lab-pack"}',
                "packs/nomanifest/README.md": "# not a plugin yet\n",
            }
        )

    def test_lists_plugins_with_a_manifest(self) -> None:
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["packs/demo", "labs/lab"])

    def test_changed_files_select_plugins(self) -> None:
        changed = "packs/demo/skills/alpha/SKILL.md\nREADME.md\npacks/demolition/x\n\n"
        self.assertEqual(self.run_cli("--changed-files", "-", stdin=changed).stdout.splitlines(), ["packs/demo"])
        listing = self.root / "changed.txt"
        listing.write_text("labs/lab/README.md\n", encoding="utf-8")
        self.assertEqual(self.run_cli("--changed-files", str(listing)).stdout.splitlines(), ["labs/lab"])
        self.assertEqual(self.run_cli("--changed-files", "-", stdin="docs/x.md\n").stdout, "")


class ContractTests(unittest.TestCase):
    def test_contract_lists_every_rule_exactly(self) -> None:
        text = CONTRACT.read_text(encoding="utf-8")
        table = re.findall(r"^\| ([A-Z]{3}[0-9]{3}) \| (error|warning) \| (.+) \|$", text, re.MULTILINE)
        self.assertEqual([(code, sev, summary) for code, sev, summary in table], [(c, s, d) for c, (s, d) in RULES.items()])

    def test_contract_states_the_stable_thresholds(self) -> None:
        text = CONTRACT.read_text(encoding="utf-8")
        for fragment in ("at least **0.80**", "at least **+0.20**", "at least **3 runs**", "**pinned model**"):
            self.assertIn(fragment, text)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_actions_are_pinned_by_sha_with_a_version_comment(self) -> None:
        uses = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", self.text, re.MULTILINE)
        self.assertTrue(uses)
        for action, comment in uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
                self.assertRegex(comment, r"#\s*v[0-9]+\.[0-9]+\.[0-9]+")

    def test_hardening(self) -> None:
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read$")
        self.assertRegex(self.text, r"(?m)^  cancel-in-progress: true$")
        self.assertRegex(self.text, r"(?m)^    timeout-minutes: [0-9]+$")
        self.assertIn("persist-credentials: false", self.text)
        self.assertNotIn("pull_request_target", self.text)
        self.assertRegex(self.text, r'CLAUDE_CODE_VERSION: "[0-9]+\.[0-9]+\.[0-9]+"')

    def test_models_and_costs_come_from_repository_variables(self) -> None:
        self.assertIn("EVAL_MODEL: ${{ vars.EVAL_MODEL }}", self.text)
        self.assertIn("EVAL_JUDGE_MODEL: ${{ vars.EVAL_JUDGE_MODEL }}", self.text)
        self.assertIn('--model "$EVAL_MODEL"', self.text)
        self.assertIn('--judge-model "$EVAL_JUDGE_MODEL"', self.text)
        self.assertIn("vars.EVAL_MAX_COST_PR", self.text)
        self.assertIn("secrets.ANTHROPIC_API_KEY != ''", self.text)
        for flag in ("--threshold 0.8", "--trust-plugin", "--no-publish", "--json", "--max-cost-usd"):
            self.assertIn(flag, self.text)
        self.assertIn('echo "ablation=none"', self.text)
        self.assertIn('echo "ablation=with-without"', self.text)

    def test_no_expression_is_expanded_inside_a_run_script(self) -> None:
        lines = self.text.splitlines()
        inside, indent = False, 0
        for line in lines:
            stripped = line.lstrip(" ")
            current = len(line) - len(stripped)
            if inside and stripped and current <= indent:
                inside = False
            if re.match(r"(?:- )?run: \|$", stripped):
                inside, indent = True, current
                continue
            if inside:
                self.assertNotIn("${{", line, line)


if __name__ == "__main__":
    unittest.main()
