"""The eval dashboard renders the sample summary, marks regressions and stays self-contained."""

import re
import tempfile
import unittest
from pathlib import Path

from tests.site import support
from tests.site.support import build

# Expected values for tests/site/fixtures/summary.sample.json, written out by
# hand so the test doesn't check the formatter against itself.
CASES = {
    "release-notes-basic": ("0.93", "0.46", "+0.47", False),
    "release-notes-breaking-change": ("0.87", "0.54", "+0.33", False),
    "release-notes-empty-history": ("0.90", "0.50", "+0.40", False),
    "report-digest-weekly": ("0.83", "0.66", "+0.17", False),
    "report-digest-empty-input": ("0.67", "0.74", "-0.07", True),
    "report-digest-long-thread": ("0.75", "0.70", "+0.05", False),
    "inbox-triage-priority": ("0.50", "0.50", "0.00", True),
    "inbox-triage-spam": ("0.42", "0.58", "-0.16", True),
    "inbox-triage-vacation": ("0.46", "0.54", "-0.08", True),
}
# skill -> (score, delta, runs, status label)
SKILLS = {
    "release-notes": ("0.90", "+0.40", "3", "Meets the stable bar"),
    "report-digest": ("0.75", "+0.05", "3", "Below the stable bar"),
    "inbox-triage": ("0.46", "-0.08", "3", "Regression"),
}


def render(root: Path) -> str:
    outputs, _warnings = build.generate(root)
    return outputs[build.DASHBOARD]


class SampleDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = support.sample_root(Path(cls._tmp.name))
        cls.html = render(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_case_shows_its_score_and_delta(self):
        for name, (score, without, delta, _regression) in CASES.items():
            with self.subTest(case=name):
                row = support.element(self.html, "case-sample-pack-" + name)
                self.assertIn('<th scope="row">{}</th>'.format(name), row)
                self.assertIn('<td class="num">{}<span class="meter"'.format(score), row)
                self.assertIn('<td class="num">{}</td>'.format(without), row)
                self.assertIn('<td class="num">{}<span class="dbar"'.format(delta), row)

    def test_every_skill_shows_score_delta_runs_and_date(self):
        for skill, (score, delta, runs, label) in SKILLS.items():
            with self.subTest(skill=skill):
                row = support.element(self.html, "skill-sample-pack-" + skill)
                self.assertIn('<td class="num">{}<span class="meter"'.format(score), row)
                self.assertIn('<td class="num">{}<span class="dbar"'.format(delta), row)
                self.assertIn('<td class="num">{}</td>'.format(runs), row)
                self.assertIn('<time datetime="2026-11-01">2026-11-01</time>', row)
                self.assertIn(label, row)

    def test_regression_cases_are_marked(self):
        for name, (_score, _without, _delta, regression) in CASES.items():
            with self.subTest(case=name):
                row = support.element(self.html, "case-sample-pack-" + name)
                self.assertEqual('class="is-regression"' in row, regression)
                self.assertEqual("Regression</span>" in row, regression)

    def test_regression_skills_are_marked_and_listed(self):
        for skill in SKILLS:
            with self.subTest(skill=skill):
                row = support.element(self.html, "skill-sample-pack-" + skill)
                self.assertEqual('class="is-regression"' in row, skill == "inbox-triage")
        callout = re.search(r'<section class="callout callout-critical".*?</section>', self.html, re.DOTALL)
        self.assertIsNotNone(callout, "regressions get a callout")
        self.assertIn('<a href="#skill-sample-pack-inbox-triage">inbox-triage</a>', callout.group(0))
        self.assertNotIn("release-notes", callout.group(0))

    def test_regression_marking_does_not_rely_on_color(self):
        row = support.element(self.html, "skill-sample-pack-inbox-triage")
        self.assertIn('<span class="status-icon" aria-hidden="true">', row)
        self.assertIn("Regression</span>", row, "a text label carries the meaning")
        self.assertIn("worse than no skill", row)

    def test_zero_delta_counts_as_a_regression(self):
        row = support.element(self.html, "case-sample-pack-inbox-triage-priority")
        self.assertIn('<td class="num">0.00<span class="dbar"', row)
        self.assertIn('class="is-regression"', row)
        self.assertIn('<span class="reason">no better than no skill</span>', row)
        worse = support.element(self.html, "case-sample-pack-inbox-triage-spam")
        self.assertIn('<span class="reason">worse than no skill</span>', worse)

    def test_summary_tiles(self):
        tiles = re.search(r'<dl class="tiles">.*?</dl>', self.html, re.DOTALL).group(0)
        self.assertIn('<dt>Skills measured</dt><dd class="value">3</dd>', tiles)
        self.assertIn('<dt>Meet the stable bar</dt><dd class="value">1 of 3</dd>', tiles)
        self.assertIn('<div class="tile is-alert"><dt>Regressions</dt><dd class="value">1</dd>', tiles)
        self.assertIn('<dt>Mean Δ vs no skill</dt><dd class="value">+0.12</dd>', tiles)

    def test_run_details(self):
        runs = re.search(r'<section aria-labelledby="runs-heading">.*?</section>', self.html, re.DOTALL).group(0)
        for text in ("0.70", "+0.12", '4 of 9 <span class="muted">at 0.80</span>', "$2.41", "8 min 58 s", "2.1.318", "sample data, used above"):
            self.assertIn(text, runs)

    def test_sample_data_is_labelled(self):
        self.assertIn('<p class="sample-banner" role="note"><strong>Sample data.</strong>', self.html)

    def test_no_http_references(self):
        self.assertNotRegex(self.html, re.compile("http", re.IGNORECASE))

    def test_self_contained(self):
        for pattern in (r"<script", r"<link\b", r"<img\b", r"<iframe", r"\bsrc=", r"url\(", r"@import", r'href="//'):
            with self.subTest(pattern=pattern):
                self.assertNotRegex(self.html, pattern)
        for href in re.findall(r'href="([^"]+)"', self.html):
            with self.subTest(href=href):
                self.assertRegex(href, r"^(#|\.\./)", "links stay inside the page or the docs site")

    def test_readable_in_light_and_dark_mode(self):
        self.assertIn('<meta name="color-scheme" content="light dark">', self.html)
        self.assertIn("@media (prefers-color-scheme: dark)", self.html)

    def test_accessible_tables_not_canvas(self):
        self.assertNotIn("<canvas", self.html)
        self.assertGreaterEqual(self.html.count("<table>"), 3)
        self.assertEqual(self.html.count("<table>"), self.html.count("<caption"))
        self.assertIn('<th scope="col"', self.html)
        self.assertIn('<th scope="row"', self.html)
        self.assertIn('<html lang="en">', self.html)
        for bar in re.findall(r'<span class="(?:meter|dbar)"[^>]*>', self.html):
            self.assertIn('aria-hidden="true"', bar, "bars repeat a printed number")

    def test_ids_are_unique(self):
        ids = re.findall(r'\bid="([^"]+)"', self.html)
        self.assertEqual(len(ids), len(set(ids)))
        for target in re.findall(r'href="#([^"]+)"', self.html):
            self.assertIn(target, ids)

    def test_output_is_deterministic(self):
        self.assertEqual(render(self.root), self.html)


class EmptyStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = support.make_root(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_state_renders(self):
        html = render(self.root)
        self.assertIn('<section class="empty" id="empty-state"', html)
        self.assertIn("No eval results yet", html)
        self.assertNotIn('<dl class="tiles">', html)
        self.assertNotIn("<table>", html)
        self.assertIn("No eval summaries are committed yet.", html)
        self.assertNotRegex(html, re.compile("http", re.IGNORECASE))

    def test_summary_without_cases_keeps_the_empty_state_and_lists_the_run(self):
        data = support.load_sample()
        data["cases"] = []
        support.write_json(self.root / support.SAMPLE_REL, data)
        html = render(self.root)
        self.assertIn('id="empty-state"', html)
        self.assertIn('aria-labelledby="runs-heading"', html)
        self.assertIn("no cases", html)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_text_from_summaries_is_escaped(self):
        data = support.load_sample()
        data["cases"][0]["name"] = '<script>alert("x")</script>'
        data["cases"][0]["skill"] = "release-notes"
        support.sample_root(self.root, data)
        html = render(self.root)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", html)

    def test_unknown_and_raw_fields_never_render(self):
        data = support.load_sample()
        data["suite"] = {"modelOverride": "secret-model-name", "root": "/private/path", "threshold": 0.8}
        data["cases"][0]["model"] = "secret-model-name"
        data["cases"][0]["promptMarkdown"] = "a prompt that must stay private"
        support.sample_root(self.root, data)
        outputs, warnings = build.generate(self.root)
        for text in outputs.values():
            self.assertNotIn("secret-model-name", text)
            self.assertNotIn("/private/path", text)
            self.assertNotIn("must stay private", text)
        self.assertEqual(len(warnings), 1)
        self.assertIn("model, modelOverride, promptMarkdown", warnings[0])
        self.assertIn("at 0.80", outputs[build.DASHBOARD], "suite.threshold is still read")


if __name__ == "__main__":
    unittest.main()
