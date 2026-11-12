"""README.md: the hero, the generated proof block, install steps and the CTA slot."""

import re
import tempfile
import unittest
from pathlib import Path

from tests.site import support
from tests.site.support import REPO, build

README = REPO / "README.md"


class ReadmeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = README.read_text(encoding="utf-8")

    def test_opens_with_a_one_line_pitch(self):
        lines = self.text.splitlines()
        self.assertRegex(lines[0], r"^# \S")
        self.assertEqual(lines[1], "")
        self.assertTrue(lines[2] and not lines[3], "the pitch is one paragraph line")

    def test_hero_is_a_clearly_labelled_owner_auditor_report(self):
        hero = self.text.split("## What your agent team cost you this week", 1)[1].split("\n## ", 1)[0]
        self.assertIn("*Example report with synthetic numbers.", hero)
        report = re.search(r"```text\n(.*?)```", hero, re.DOTALL).group(1)
        self.assertIn("EXAMPLE: SYNTHETIC NUMBERS", report)
        for line in ("Owner time", "Interventions", "Top fix"):
            self.assertIn(line, report)
        self.assertLess(self.text.index("## What your agent team cost"), self.text.index("## Proof, not claims"))

    def test_proof_section_holds_the_generated_block_and_links_the_dashboard(self):
        proof = self.text.split("## Proof, not claims", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(len(build.PROOF_BLOCK.findall(proof)), 1)
        self.assertIn("eval dashboard](site/evals/index.html", proof)

    def test_install_for_claude_code_and_codex(self):
        install = self.text.split("## Install", 1)[1].split("\n## ", 1)[0]
        self.assertIn("claude plugin marketplace add KevinOrosz/SOTACOMMAND\nclaude plugin install sotacommand-engine@sotacommand", install)
        self.assertIn("### Codex", install)
        self.assertIn(".agents/skills", install)

    def test_links_to_every_docs_page(self):
        for page in ("index", "quickstart", "concepts", "harnesses", "qualify-a-skill", "faq"):
            self.assertIn("](site/{}.md)".format(page), self.text)
        for target in re.findall(r"\]\((site/[^)#\s]+)", self.text):
            with self.subTest(link=target):
                self.assertTrue((REPO / target).exists(), target)

    def test_cta_slot_is_empty_and_near_the_end(self):
        self.assertEqual(self.text.count("<!-- cta -->"), 1)
        tail = [line for line in self.text.splitlines() if line.strip()][-3:]
        self.assertIn("<!-- cta -->", tail)
        self.assertEqual(self.text.rstrip().splitlines()[-1], "<!-- cta -->", "nothing fills the slot yet")

    def test_one_demo_image_with_alt_text_and_no_badges(self):
        images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", self.text)
        self.assertEqual([target for _, target in images], ["site/assets/img/wizard-demo.gif"])
        self.assertGreater(len(images[0][0]), 60, "the alt text describes the demo for people who can't see it")
        self.assertTrue((REPO / images[0][1]).is_file())
        self.assertLess((REPO / images[0][1]).stat().st_size, 2_000_000, "keep the README light")
        self.assertIn("A real run of the interview", self.text)
        self.assertLess(self.text.index("wizard-demo.gif"), self.text.index("## TL;DR"))
        self.assertNotIn("shields.io", self.text)
        self.assertNotIn("<img", self.text)


class ProofBlockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def proof(self):
        readme = build.generate(self.root)[0][build.README]
        return build.PROOF_BLOCK.search(readme).group(0)

    def test_each_skill_with_its_delta(self):
        support.sample_root(self.root)
        proof = self.proof()
        expected = {
            "release-notes": ("0.90", "+0.40", "✓ Meets the stable bar"),
            "report-digest": ("0.75", "+0.05", "Below the bar: score 0.75 is under 0.80; Δ +0.05 is under +0.20"),
            "inbox-triage": ("0.46", "-0.08", "▼ Regression: worse than no skill"),
        }  # three cases each, three runs, pinned model
        for skill, (score, delta, status) in expected.items():
            with self.subTest(skill=skill):
                line = next(l for l in proof.splitlines() if l.startswith("| [{}]".format(skill)))
                self.assertIn("(site/evals/index.html#skill-sample-pack-{})".format(skill), line)
                self.assertIn("| {} | {} | 3 | 2026-11-01 | {} |".format(score, delta, status), line)
        self.assertIn("**Sample data:**", proof)

    def test_empty_block_makes_no_claims(self):
        support.make_root(self.root)
        proof = self.proof()
        self.assertIn("makes no claims", proof)
        self.assertNotIn("| Skill |", proof)

    def test_the_rest_of_the_readme_is_untouched(self):
        support.sample_root(self.root)
        before = (self.root / "README.md").read_text(encoding="utf-8")
        after = build.generate(self.root)[0][build.README]
        self.assertEqual(build.PROOF_BLOCK.sub("", before), build.PROOF_BLOCK.sub("", after))


if __name__ == "__main__":
    unittest.main()
