"""The docs pages: required content, Jekyll safety, working links and neutral copy."""

import re
import unittest

from tests.site.support import REPO, build

SITE = REPO / "site"
REQUIRED = ("index.md", "quickstart.md", "concepts.md", "harnesses.md", "qualify-a-skill.md", "faq.md")


def read(name: str) -> str:
    return (SITE / name).read_text(encoding="utf-8")


def body(name: str) -> str:
    return build.split_front_matter(read(name), name)[1]


def outside_code(text: str) -> str:
    """The text with fenced code blocks removed."""
    return re.sub(r"^```.*?^```[ \t]*$", "", text, flags=re.MULTILINE | re.DOTALL)


def heading_ids(text: str):
    """Heading ids as kramdown generates them for these simple headings."""
    ids = set()
    for heading in re.findall(r"^#{1,6} (.+?)\s*$", outside_code(text), re.MULTILINE):
        slug = re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip().replace(" ", "-")
        ids.add(slug)
    return ids


def section(text: str, heading: str) -> str:
    match = re.search(r"^## {}\n(.*?)(?=^## |\Z)".format(re.escape(heading)), text, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError("no section " + heading)
    return match.group(1)


class RequiredPagesTests(unittest.TestCase):
    def test_required_pages_exist_with_front_matter(self):
        orders = []
        for name in REQUIRED:
            with self.subTest(page=name):
                meta, text = build.split_front_matter(read(name), name)
                for key in ("title", "description", "nav_order"):
                    self.assertTrue(meta.get(key), key)
                self.assertLessEqual(len(meta["description"]), 200, "one line for llms.txt")
                self.assertRegex(text.lstrip(), r"^# \S", "each page opens with its own heading")
                self.assertGreater(len(text.split()), 150, "pages are drafts, not stubs")
                orders.append(int(meta["nav_order"]))
        self.assertEqual(len(orders), len(set(orders)), "nav_order is unique")

    def test_index_is_the_pitch(self):
        text = body("index.md")
        for heading in ("Four layers in one place", "Proof, not claims", "A role that audits your time", "Works where you work"):
            self.assertIn("## " + heading, text)
        for layer in ("**Team**", "**Skills**", "**Memory**", "**Governance**"):
            self.assertIn(layer, text)

    def test_concepts_has_a_diagram_for_each_layer(self):
        text = body("concepts.md")
        for heading in ("Team", "Skills", "Memory", "Governance"):
            with self.subTest(section=heading):
                self.assertIn("```text\n", section(text, heading), "one plain diagram per layer")
        for diagram in re.findall(r"```text\n(.*?)```", text, re.DOTALL):
            for line in diagram.splitlines():
                self.assertLessEqual(len(line), 80, "diagrams fit a narrow terminal")
                self.assertRegex(line, r"^[\x20-\x7e]*$", "diagrams are plain ASCII")

    def test_harnesses_cover_each_harness(self):
        text = body("harnesses.md")
        self.assertIn("claude plugin marketplace add KevinOrosz/SOTACOMMAND", text)
        self.assertIn("claude plugin install <plugin>@sotacommand", text)
        self.assertIn("`.agents/skills/`", text)
        self.assertIn("`AGENTS.md`", text)
        self.assertIn("`.github/agents/`", text)
        self.assertIn("`.cursor/rules/sotacommand.mdc`", text)
        self.assertIn("`GEMINI.md`", text)
        self.assertIn("`SOTACOMMAND.prompt.md`", text)
        self.assertNotIn("Coming in v0.2", text)
        for harness in ("cursor", "gemini", "hermes", "flat"):
            self.assertIn("--harness %s" % harness, text)
        self.assertIn("wizard.py uninstall", text)

    def test_qualify_a_skill_gives_the_rule_and_the_baseline_run(self):
        text = body("qualify-a-skill.md")
        for needle in ("**0.80**", "**+0.20**", "**3 runs**", "--ablation with-without", "claude plugin eval packs/<pack>"):
            self.assertIn(needle, text)

    def test_quickstart_is_short_and_starts_with_wizard(self):
        text = body("quickstart.md")
        self.assertIn("**The one-word start.**", text)
        self.assertIn("`WIZARD`", text)
        for command in ("doctor", "install", "check"):
            self.assertIn("tools/wizard/wizard.py " + command, text)
        self.assertLess(len(text.split()), 400)

    def test_faq_points_setup_help_at_the_cta_slot(self):
        text = body("faq.md")
        answer = section(text, "Can someone set this up for me?")
        self.assertIn("<!-- cta -->", answer)
        self.assertEqual(text.count("<!-- cta -->"), 1)


class JekyllSafetyTests(unittest.TestCase):
    def test_pages_hold_no_liquid(self):
        for path in sorted(SITE.glob("*.md")):
            with self.subTest(page=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("{{", text)
                self.assertNotIn("{%", text)

    def test_config_keeps_pages_defaults_working(self):
        config = (SITE / "_config.yml").read_text(encoding="utf-8")
        active = [line for line in config.splitlines() if line and not line.startswith("#")]
        self.assertFalse(any(line.startswith(("url:", "baseurl:")) for line in active), "Pages fills these in only while unset")
        self.assertIn("theme: null", active)

    def test_layout_uses_relative_urls(self):
        layout = (SITE / "_layouts" / "default.html").read_text(encoding="utf-8")
        self.assertIn("{{ '/assets/css/site.css' | relative_url }}", layout)
        self.assertNotRegex(layout, r'(href|src)="/')
        self.assertNotIn("http", layout)

    def test_stylesheet_has_no_external_requests(self):
        css = (SITE / "assets" / "css" / "site.css").read_text(encoding="utf-8")
        self.assertNotIn("http", css)
        self.assertNotIn("@import", css)
        self.assertIn("prefers-color-scheme: dark", css)


class LinkTests(unittest.TestCase):
    def test_relative_links_resolve(self):
        pages = {path.name: path.read_text(encoding="utf-8") for path in SITE.glob("*.md")}
        for name, text in pages.items():
            for target in re.findall(r"\]\(([^)\s]+)\)", outside_code(text)):
                if re.match(r"^[a-z]+:", target):
                    continue
                with self.subTest(page=name, link=target):
                    path, _, anchor = target.partition("#")
                    if not path:
                        self.assertIn(anchor, heading_ids(text))
                    elif path.endswith(".md"):
                        self.assertIn(path, pages)
                        if anchor:
                            self.assertIn(anchor, heading_ids(pages[path]))
                    else:
                        self.assertTrue((SITE / path).exists(), path)


class NeutralCopyTests(unittest.TestCase):
    """No private names in shipped docs. The private term list lives only in internal/."""

    def shipped_files(self):
        files = [REPO / "README.md"]
        files += [p for p in sorted(SITE.rglob("*")) if p.is_file() and p.suffix in (".md", ".html", ".txt", ".yml", ".css")]
        return files

    def test_no_denylisted_terms(self):
        denylist = REPO / "internal" / "denylist.seed.txt"
        if not denylist.exists():
            self.skipTest("the private term list is not in this checkout")
        terms = [line.strip() for line in denylist.read_text(encoding="utf-8").splitlines()]
        terms = [term for term in terms if term and not term.startswith("#")]
        self.assertTrue(terms)
        for path in self.shipped_files():
            # The repository's own address and its companion's are public by definition.
            text = re.sub(r"(?:github\.com/)?[A-Za-z0-9-]+/SOTA[A-Z]+\b", "", path.read_text(encoding="utf-8"))
            for index, term in enumerate(terms):
                if re.fullmatch(r"[A-Z]+", term):
                    found = re.search(r"\b{}\b".format(term), text)
                else:
                    found = term.lower() in text.lower()
                # Report the term by its line number so the failure output doesn't repeat it.
                self.assertFalse(found, "{} contains denylist term #{}".format(path.relative_to(REPO), index + 1))

    def test_no_personal_paths_or_addresses(self):
        for path in self.shipped_files():
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=str(path.relative_to(REPO))):
                self.assertNotRegex(text, r"/(Users|home)/[A-Za-z]")
                self.assertNotRegex(text, r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}")


if __name__ == "__main__":
    unittest.main()
