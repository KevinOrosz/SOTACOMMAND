"""llms.txt and llms-full.txt: every page is listed, in the llms.txt convention."""

import re
import tempfile
import unittest
from pathlib import Path

from tests.site import support
from tests.site.support import REPO, build

SITE = REPO / "site"


def committed(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def page_sources():
    """(path, front matter, body) for every docs page, read independently of build.py's page loader."""
    pages = []
    for path in sorted(SITE.glob("*.md")):
        meta, body = build.split_front_matter(path.read_text(encoding="utf-8"), path.name)
        pages.append((path.name, meta, body))
    return pages


class CommittedLlmsTests(unittest.TestCase):
    def test_llms_txt_lists_every_page(self):
        text = committed(build.LLMS)
        pages = page_sources()
        self.assertGreaterEqual(len(pages), 6)
        for name, meta, _body in pages:
            with self.subTest(page=name):
                url = name[: -len(".md")] + ".html"
                self.assertIn("- [{}]({}): {}\n".format(meta["title"], url, meta["description"]), text)

    def test_llms_txt_follows_the_convention(self):
        lines = committed(build.LLMS).splitlines()
        self.assertRegex(lines[0], r"^# \S")
        self.assertTrue(lines[2].startswith("> "), "a blockquote summary follows the title")
        self.assertEqual([l for l in lines if l.startswith("#")][1:], ["## Docs", "## Eval results", "## Optional"])
        entries = [l for l in lines if l.startswith("- ")]
        for line in entries:
            self.assertRegex(line, r"^- \[[^\]]+\]\([^)\s]+\): \S")
        self.assertIn("- [Eval dashboard](evals/index.html): ", "\n".join(entries))
        self.assertIn("- [Full text](llms-full.txt): ", "\n".join(entries))

    def test_llms_txt_follows_nav_order(self):
        text = committed(build.LLMS)
        ordered = sorted(page_sources(), key=lambda page: int(page[1]["nav_order"]))
        positions = [text.index("({})".format(name[:-3] + ".html")) for name, _m, _b in ordered]
        self.assertEqual(positions, sorted(positions))

    def test_llms_full_holds_every_page(self):
        text = committed(build.LLMS_FULL)
        for name, _meta, body in page_sources():
            with self.subTest(page=name):
                self.assertIn("Source: {}\n".format(name[:-3] + ".html"), text)
                first_heading = next(line for line in body.splitlines() if line.startswith("# "))
                self.assertIn("\n" + first_heading + "\n", text)
                for heading in re.findall(r"^## .+$", body, re.MULTILINE):
                    self.assertIn(heading, text)

    def test_llms_full_points_links_at_published_pages(self):
        text = committed(build.LLMS_FULL)
        page_names = [name for name, _m, _b in page_sources()]
        for name in page_names:
            self.assertNotIn("]({})".format(name), text)
            self.assertNotIn("]({}#".format(name), text)
        self.assertIn("](qualify-a-skill.html)", text)
        self.assertIn("](harnesses.html#claude-code)", text)

    def test_committed_files_match_the_pages(self):
        outputs, _ = build.generate(REPO)
        self.assertEqual(outputs[build.LLMS], committed(build.LLMS), "run python3 tools/repo/site/build.py")
        self.assertEqual(outputs[build.LLMS_FULL], committed(build.LLMS_FULL), "run python3 tools/repo/site/build.py")


class GeneratedLlmsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = support.make_root(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def set_config(self, extra: str):
        config = self.root / "site" / "_config.yml"
        config.write_text(config.read_text(encoding="utf-8") + extra, encoding="utf-8")

    def test_a_new_page_is_listed(self):
        (self.root / "site" / "glossary.md").write_text(
            '---\ntitle: Glossary\nnav_order: 7\ndescription: "Terms used in these docs."\n---\n\n# Glossary\n\nText.\n',
            encoding="utf-8",
        )
        outputs, _ = build.generate(self.root)
        self.assertIn("- [Glossary](glossary.html): Terms used in these docs.\n", outputs[build.LLMS])
        self.assertIn("Source: glossary.html\n\n# Glossary\n", outputs[build.LLMS_FULL])

    def test_absolute_links_when_the_site_url_is_set(self):
        self.set_config('\nurl: "https://docs.example.org"\nbaseurl: "/handbook"\n')
        outputs, _ = build.generate(self.root)
        self.assertIn("(https://docs.example.org/handbook/concepts.html)", outputs[build.LLMS])
        self.assertIn("Source: https://docs.example.org/handbook/faq.html", outputs[build.LLMS_FULL])
        self.assertIn("](https://docs.example.org/handbook/qualify-a-skill.html)", outputs[build.LLMS_FULL])
        self.assertIn("(https://docs.example.org/handbook/evals/index.html)", outputs[build.README])

    def test_relative_links_without_a_site_url(self):
        outputs, _ = build.generate(self.root)
        self.assertNotRegex(outputs[build.LLMS], r"\]\(https?:")

    def test_links_inside_code_blocks_are_left_alone(self):
        (self.root / "site" / "extra.md").write_text(
            '---\ntitle: Extra\nnav_order: 9\ndescription: "Code sample."\n---\n\n# Extra\n\n'
            "```markdown\nSee [the FAQ](faq.md).\n```\n\nSee [the FAQ](faq.md).\n",
            encoding="utf-8",
        )
        text = build.generate(self.root)[0][build.LLMS_FULL]
        self.assertIn("```markdown\nSee [the FAQ](faq.md).\n```\n\nSee [the FAQ](faq.html).", text)

    def test_a_page_without_a_description_is_an_error(self):
        (self.root / "site" / "bad.md").write_text("---\ntitle: Bad\nnav_order: 8\n---\n\n# Bad\n", encoding="utf-8")
        result = support.run_cli("--root", str(self.root), "--check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("site/bad.md: front matter needs description", result.stderr)

    def test_an_unquoted_colon_is_an_error(self):
        (self.root / "site" / "bad.md").write_text(
            "---\ntitle: Bad\nnav_order: 8\ndescription: Two parts: this breaks YAML\n---\n\n# Bad\n", encoding="utf-8"
        )
        with self.assertRaises(build.InputError) as caught:
            build.generate(self.root)
        self.assertIn("site/bad.md:4: quote values that contain ': '", str(caught.exception))

    def test_pages_in_underscore_folders_are_not_pages(self):
        (self.root / "site" / "_drafts").mkdir()
        (self.root / "site" / "_drafts" / "idea.md").write_text("no front matter\n", encoding="utf-8")
        outputs, _ = build.generate(self.root)
        self.assertNotIn("idea", outputs[build.LLMS])


if __name__ == "__main__":
    unittest.main()
