"""Glob semantics shared by path rules, allowlist scopes and --exclude."""

import unittest

from tests.leakgate import support  # noqa: F401  (puts leakgate on sys.path)
from leakgatelib.globs import Glob, GlobSet, translate


class GlobMatchingTest(unittest.TestCase):
    def assertMatches(self, pattern, yes, no):
        glob = Glob(pattern)
        for path in yes:
            self.assertTrue(glob.matches(path), "%s should match %s" % (pattern, path))
        for path in no:
            self.assertFalse(glob.matches(path), "%s should not match %s" % (pattern, path))

    def test_anchored_directory_glob(self):
        self.assertMatches(
            "internal/**",
            yes=["internal/PLAN.md", "internal/missions/M2.md", "internal/.hidden"],
            no=["internal", "xinternal/a", "docs/internal/a", "fixtures/leak-corpus/planted/internal/a"],
        )

    def test_any_depth_directory_glob(self):
        self.assertMatches(
            "**/KB/**",
            yes=["KB/a.md", "x/KB/a/b.md", "a/b/KB/c"],
            no=["KB", "KBX/a", "xKB/a", "kb/a", "a/KB"],
        )

    def test_dotfile_prefix_glob(self):
        self.assertMatches(
            "**/.env*",
            yes=[".env", ".env.local", "config/.env.production", ".envrc"],
            no=["env", "a/.env/x", "dotenv", "x.env"],
        )

    def test_name_pattern_with_star(self):
        self.assertMatches(
            "**/fixture-*.md",
            yes=["fixture-owner.md", "a/b/fixture-x.md", "fixture-.md"],
            no=["fixtures/x.md", "fixture-x.mdx", "a/fixture-x.txt"],
        )

    def test_exact_file(self):
        self.assertMatches(".github/CODEOWNERS", yes=[".github/CODEOWNERS"], no=["docs/.github/CODEOWNERS", "CODEOWNERS"])

    def test_pattern_without_slash_matches_any_depth(self):
        self.assertMatches("*.pem", yes=["key.pem", "a/b/key.pem"], no=["key.pem.txt", "a/pem"])

    def test_leading_slash_anchors(self):
        self.assertMatches("/expected.json", yes=["expected.json"], no=["sub/expected.json"])
        self.assertMatches("./expected.json", yes=["expected.json"], no=["sub/expected.json"])

    def test_trailing_slash_means_directory(self):
        self.assertMatches("docs/", yes=["docs/a", "x/docs/a/b"], no=["docs", "docsx/a"])
        self.assertMatches("/docs/", yes=["docs/a"], no=["x/docs/a"])

    def test_middle_double_star(self):
        self.assertMatches("a/**/b", yes=["a/b", "a/x/b", "a/x/y/b"], no=["a/xb", "b", "a/b/c"])
        self.assertMatches("**/evals/results/**", yes=["packs/p/skills/s/evals/results/2026/summary.json", "evals/results/x"], no=["evals/cases/x"])

    def test_question_mark_and_classes(self):
        self.assertMatches("M?.md", yes=["M2.md", "x/M6.md"], no=["M12.md", "M/.md"])
        self.assertMatches("M[0-9].md", yes=["M3.md"], no=["Mx.md"])
        self.assertMatches("M[!0-9].md", yes=["Mx.md"], no=["M3.md", "M/.md"])

    def test_star_never_crosses_a_separator(self):
        self.assertMatches("docs/*.md", yes=["docs/a.md"], no=["docs/sub/a.md"])

    def test_double_star_alone_matches_everything(self):
        self.assertMatches("**", yes=["a", "a/b/c", ".x"], no=[])

    def test_case_sensitive(self):
        self.assertMatches("**/HANDOFF.md", yes=["HANDOFF.md", "memory/HANDOFF.md"], no=["handoff.md"])

    def test_invalid_globs_raise(self):
        for bad in ["", "  ", " a", "a ", "a\\b", "a//b", "/"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                translate(bad)

    def test_globset_reports_first_match(self):
        globs = GlobSet(["**/KB/**", "**/tasks/**"])
        self.assertEqual(globs.first_match("x/tasks/y.md"), "**/tasks/**")
        self.assertIsNone(globs.first_match("x/y.md"))
        self.assertFalse(GlobSet([]))


if __name__ == "__main__":
    unittest.main()
