"""Each rewrite rule: word boundaries, case handling, order, and what gets flagged."""

import unittest

from .support import TempDirTestCase, linux_home, mac_home, tilde_home, windows_home


class PersonaMap(TempDirTestCase):
    def test_whole_words_only_and_case_sensitive(self):
        result = self.transform("Ask ZEPHYR. ZEPHYRS stay. zephyr is a wind. QUILLED too.\n")
        self.assertEqual(self.text(result), "Ask orchestrator. ZEPHYRS stay. zephyr is a wind. QUILLED too.\n")
        self.assertEqual(self.flags(result), [])

    def test_capitalized_where_a_sentence_starts(self):
        text = (
            "# ZEPHYR soul\n"
            "ZEPHYR routes work. ZEPHYR then waits; ZEPHYR never guesses.\n"
            "- QUILL researches.\n"
            "1. ONYX audits.\n"
            "> **ZEPHYR** decides.\n"
            "The plan goes to\n"
            "ZEPHYR for acceptance.\n"
            "| ZEPHYR | lead |\n"
        )
        expected = (
            "# Orchestrator soul\n"
            "Orchestrator routes work. Orchestrator then waits; orchestrator never guesses.\n"
            "- Researcher researches.\n"
            "1. Owner-auditor audits.\n"
            "> **Orchestrator** decides.\n"
            "The plan goes to\n"
            "orchestrator for acceptance.\n"
            "| orchestrator | lead |\n"
        )
        self.assertEqual(self.text(self.transform(text)), expected)

    def test_code_is_never_capitalized(self):
        result = self.transform('ROLE = "ZEPHYR"\nZEPHYR = 1\n', target="tools/x.py", source="x.py")
        self.assertEqual(self.text(result), 'ROLE = "orchestrator"\norchestrator = 1\n')

    def test_fenced_code_in_markdown_is_code(self):
        text = "Intro.\n\n```\nZEPHYR=1\n```\n\nZEPHYR leads.\n"
        self.assertEqual(self.text(self.transform(text)), "Intro.\n\n```\norchestrator=1\n```\n\nOrchestrator leads.\n")

    def test_possessive_and_punctuation(self):
        self.assertEqual(self.text(self.transform("See ZEPHYR's notes (ZEPHYR).\n")), "See orchestrator's notes (orchestrator).\n")

    def test_identifier_is_left_and_flagged(self):
        result = self.transform("ZEPHYR_HOME=/srv\n", target="x.sh", source="x.sh")
        self.assertEqual(self.text(result), "ZEPHYR_HOME=/srv\n")
        flags = self.flags(result, "persona-name")
        self.assertEqual([(f.line, f.text) for f in flags], [(1, "ZEPHYR")])

    def test_counts_and_lines_are_recorded(self):
        result = self.transform("ZEPHYR\nnothing\nZEPHYR and ZEPHYR\n")
        events = [(e.kind, e.rule, e.line) for e in result.events]
        self.assertEqual(events, [("persona_map", "ZEPHYR", 1), ("persona_map", "ZEPHYR", 3), ("persona_map", "ZEPHYR", 3)])


class OwnerTerms(TempDirTestCase):
    def test_exact_case_with_sentence_case(self):
        text = "Avery steers. Then Avery decides.\nAsk Avery.\n"
        self.assertEqual(self.text(self.transform(text)), "The owner steers. Then the owner decides.\nAsk the owner.\n")

    def test_longest_term_wins_and_whitespace_is_flexible(self):
        text = "Avery Example wrote it; Avery  Example signed it; Avery's call.\n"
        self.assertEqual(self.text(self.transform(text)), "The owner wrote it; the owner signed it; the owner's call.\n")

    def test_all_caps_variant_is_rewritten_in_caps(self):
        self.assertEqual(self.text(self.transform("NOTE FROM AVERY\n")), "NOTE FROM THE OWNER\n")

    def test_other_case_variants_are_flagged_not_rewritten(self):
        result = self.transform("handle: avery-example\n")
        self.assertEqual(self.text(result), "handle: avery-example\n")
        self.assertEqual([f.text for f in self.flags(result, "owner-term")], ["avery"])

    def test_inside_words_is_untouched(self):
        result = self.transform("Bravery and Averyville stay.\n")
        self.assertEqual(self.text(result), "Bravery and Averyville stay.\n")
        self.assertEqual(self.flags(result), [])

    def test_addresses_are_terms_too(self):
        result = self.transform("Mail avery@example.invalid today.\n", owner_terms={"avery@example.invalid": "the owner", "Avery": "the owner"})
        self.assertEqual(self.text(result), "Mail the owner today.\n")


class PathMap(TempDirTestCase):
    MAP = {"~/repo/": "", "~/repo": ".", "~/.claude/": "~/.claude/", "docs/": "site/docs/"}

    def run_paths(self, text, target="notes.md"):
        return self.transform(text, target=target, path_map=self.MAP)

    def test_tilde_keys_match_every_home_form(self):
        homes = [mac_home("avery", "repo", "a.md"), linux_home("dev", "repo", "a.md"), "~/repo/a.md", "$HOME/repo/a.md", "${HOME}/repo/a.md"]
        result = self.run_paths("\n".join(homes) + "\n")
        self.assertEqual(self.text(result), "a.md\n" * len(homes))
        self.assertEqual(self.flags(result), [])

    def test_longest_key_wins_and_boundaries_hold(self):
        text = "cd %s\ncd %s/x\nsee %sx/a\n" % (mac_home("avery", "repo"), mac_home("avery", "repo"), mac_home("avery", "rep"))
        result = self.run_paths(text)
        self.assertEqual(self.text(result).splitlines()[:2], ["cd .", "cd x"])
        self.assertEqual([f.flag for f in result.flags], ["home-path", "owner-term"])

    def test_home_then_relative_keys_chain(self):
        result = self.run_paths("open %s\n" % mac_home("avery", "repo", "docs", "guide.md"))
        self.assertEqual(self.text(result), "open site/docs/guide.md\n")

    def test_relative_keys_match_at_a_path_boundary_and_keep_dot_slash(self):
        result = self.run_paths("docs/a.md ./docs/b.md mydocs/c.md x/docs/d.md\n")
        self.assertEqual(self.text(result), "site/docs/a.md ./site/docs/b.md mydocs/c.md x/docs/d.md\n")

    def test_identity_mappings_are_not_counted(self):
        result = self.run_paths("keep ~/.claude/skills\n")
        self.assertEqual(self.text(result), "keep ~/.claude/skills\n")
        self.assertEqual([e for e in result.events if e.kind == "path_map"], [])
        result = self.run_paths("keep %s\n" % mac_home("avery", ".claude", "skills"))
        self.assertEqual(self.text(result), "keep ~/.claude/skills\n")
        self.assertEqual([e.rule for e in result.events if e.kind == "path_map"], ["~/.claude/"])

    def test_shell_defaults_map_too(self):
        result = self.run_paths('ROOT="${ROOT:-%s}"\n' % linux_home("ci", "repo"), target="x.sh")
        self.assertEqual(self.text(result), 'ROOT="${ROOT:-.}"\n')


class HomePathCheck(TempDirTestCase):
    """Home paths no path_map entry covers are flagged, in every form."""

    def flagged(self, text):
        result = self.transform(text + "\n", target="x.txt", source="x.txt", owner_terms={})
        return [f.text for f in self.flags(result, "home-path")]

    def test_posix_homes(self):
        self.assertEqual(self.flagged("see %s" % mac_home("sam", "notes.txt")), [mac_home("sam", "notes.txt")])
        self.assertEqual(self.flagged("X=${X:-%s}" % linux_home("sam")), [linux_home("sam")])
        self.assertEqual(self.flagged("file://%s" % mac_home("sam", "a")), [mac_home("sam", "a")])

    def test_windows_wsl_and_tilde_user_homes(self):
        self.assertEqual(len(self.flagged("dir %s" % windows_home("sam", "x"))), 1)
        self.assertEqual(len(self.flagged("dir C:/%s" % "/".join(["Users", "sam", "x"]))), 1)
        self.assertEqual(len(self.flagged("cd /mnt/c/%s" % "/".join(["Users", "sam"]))), 1)
        self.assertEqual(len(self.flagged("cd %s" % tilde_home("sam", "projects"))), 1)

    def test_relative_paths_and_bare_tilde_are_fine(self):
        self.assertEqual(self.flagged("app/home/page.tsx ~/notes $HOME/x"), [])


class RewritePatterns(TempDirTestCase):
    def test_patterns_apply_in_order_per_line_with_groups(self):
        patterns = [
            {"id": "label", "pattern": "^(\\s*-\\s+)Policy \\d+:\\s+", "replace": "\\1"},
            {"id": "paren", "pattern": "\\s*\\(Policy \\d+\\)", "replace": ""},
        ]
        result = self.transform("- Policy 7: Ship small.\nShip small (Policy 7).\n", rewrite_patterns=patterns)
        self.assertEqual(self.text(result), "- Ship small.\nShip small.\n")
        self.assertEqual(sorted((e.rule, e.line) for e in result.events), [("label", 1), ("paren", 2)])

    def test_patterns_run_before_owner_terms(self):
        patterns = [{"id": "provenance", "pattern": "\\s*\\(Avery-signed\\)", "replace": ""}]
        result = self.transform("Status: live (Avery-signed).\n", rewrite_patterns=patterns)
        self.assertEqual(self.text(result), "Status: live.\n")

    def test_not_applied_when_not_listed(self):
        patterns = [{"id": "p", "pattern": "Policy \\d+", "replace": ""}]
        result = self.transform("Policy 7\n", entry={"rewrites": ["persona_map"]}, rewrite_patterns=patterns)
        self.assertEqual(self.text(result), "Policy 7\n")


class DropPatterns(TempDirTestCase):
    DROPS = [
        {"id": "block", "start": "<!-- private -->", "end": "<!-- /private -->"},
        {"id": "section", "start": "^## Secret", "before": "^## "},
        {"id": "lineage", "pattern": "^Lineage:"},
    ]

    def run_drops(self, text, **kwargs):
        return self.transform(text, drop_patterns=self.DROPS, **kwargs)

    def test_line_and_block_drops(self):
        text = "a\n<!-- private -->\nhidden\n<!-- /private -->\nb\nLineage: old\n## Secret\nx\n## Kept\nc\n"
        result = self.run_drops(text)
        self.assertEqual(self.text(result), "a\nb\n## Kept\nc\n")
        dropped = sorted(e.line for e in result.events if e.kind == "drop_patterns")
        self.assertEqual(dropped, [2, 3, 4, 6, 7, 8])
        self.assertEqual(result.line_map[3], None)
        self.assertEqual(result.line_map[5], "b")

    def test_before_blocks_may_run_to_the_end(self):
        self.assertEqual(self.text(self.run_drops("a\n## Secret\nx\ny\n")), "a\n")

    def test_a_block_on_one_line(self):
        self.assertEqual(self.text(self.run_drops("a <!-- private --> b <!-- /private -->\nkeep\n")), "keep\n")

    def test_unterminated_end_block_is_flagged(self):
        result = self.run_drops("a\n<!-- private -->\nhidden\n")
        self.assertEqual([(f.flag, f.line) for f in result.flags], [("unterminated-block", 2)])

    def test_dropped_lines_are_not_flag_checked(self):
        result = self.run_drops("<!-- private -->\nAvery's ZEPHYR_X notes\n<!-- /private -->\n")
        self.assertEqual(self.flags(result), [])
        self.assertEqual(self.text(result), "")
        self.assertIn("nothing left after the rewrites", result.warnings[0])


class EntryRules(TempDirTestCase):
    def test_replace_literal_and_regex_run_first(self):
        entry = {
            "replace": [
                {"find": "ZEPHYR_HOME", "with": "OS_HOME"},
                {"find": "tier \\d+", "with": "high tier", "regex": True},
            ]
        }
        result = self.transform("ZEPHYR_HOME uses tier 3; ask ZEPHYR.\n", entry=entry, target="x.txt", source="x.txt")
        self.assertEqual(self.text(result), "OS_HOME uses high tier; ask orchestrator.\n")
        self.assertEqual(self.flags(result), [])
        self.assertEqual(sorted(e.rule for e in result.events if e.kind == "replace"), ["replace[0]", "replace[1]"])

    def test_entry_drop_without_an_id(self):
        result = self.transform("keep\nMirror: somewhere\n", entry={"drop": [{"pattern": "^Mirror:"}]})
        self.assertEqual(self.text(result), "keep\n")

    def test_unmatched_entry_rules_warn(self):
        entry = {"replace": [{"find": "absent", "with": "x"}], "drop": [{"id": "gone", "pattern": "^never"}]}
        result = self.transform("text\n", entry=entry)
        self.assertEqual(len(result.warnings), 2)
        self.assertIn("replace[0] matched nothing", result.warnings[0])
        self.assertIn("drop rule gone matched nothing", result.warnings[1])

    def test_select_keeps_one_block(self):
        text = "# Title\n## One\nfirst\n## Two\nsecond\n"
        result = self.transform(text, entry={"select": {"start": "^## Two", "before": "^## "}})
        self.assertEqual(self.text(result), "## Two\nsecond\n")
        self.assertIsNone(result.line_map[1])
        result = self.transform(text, entry={"select": {"start": "^## One", "end": "^first"}})
        self.assertEqual(self.text(result), "## One\nfirst\n")

    def test_select_problems_are_flagged(self):
        result = self.transform("a\n", entry={"select": {"start": "^## Nope", "before": "^## "}})
        self.assertEqual([f.flag for f in result.flags], ["select-not-found"])
        result = self.transform("## A\nx\n", entry={"select": {"start": "^## A", "end": "^## B"}})
        self.assertEqual([f.flag for f in result.flags], ["unterminated-select"])


class ResidualChecks(TempDirTestCase):
    def test_residual_checks_run_whatever_the_entry_lists(self):
        text = "ZEPHYR and Avery at %s\n" % mac_home("sam")
        result = self.transform(text, entry={"rewrites": []})
        self.assertEqual(self.text(result), text)
        self.assertEqual(sorted(f.flag for f in result.flags), ["home-path", "owner-term", "persona-name"])

    def test_flag_patterns_check_the_rewritten_text(self):
        flags = [{"id": "forbidden", "pattern": "(?i)\\bforbidden\\b", "description": "never ship this word"}]
        result = self.transform("Forbidden here.\nfine here.\n", flag_patterns=flags)
        self.assertEqual([(f.flag, f.line, f.text, f.message) for f in result.flags], [("forbidden", 1, "Forbidden", "never ship this word")])
        handled = self.transform("Forbidden here.\n", flag_patterns=flags, entry={"replace": [{"find": "Forbidden", "with": "Allowed"}]})
        self.assertEqual(handled.flags, [])


class Bytes(TempDirTestCase):
    def test_line_endings_bom_and_final_newline_are_preserved(self):
        result = self.transform(b"\xef\xbb\xbfAsk ZEPHYR.\r\nThen QUILL.\rEnd")
        self.assertEqual(result.data, b"\xef\xbb\xbfAsk orchestrator.\r\nThen researcher.\rEnd")

    def test_executable_bit_is_kept_and_modes_are_normalized(self):
        self.assertEqual(self.transform("x\n", mode=0o100775).mode, 0o755)
        self.assertEqual(self.transform("x\n", mode=0o100600).mode, 0o644)

    def test_binary_entries_are_copied_verbatim(self):
        raw = b"\x89PNG\x00\x01ZEPHYR"
        result = self.transform(raw, entry={"binary": True}, target="img.png", source="img.png")
        self.assertEqual(result.data, raw)
        self.assertEqual(result.flags, [])

    def test_undeclared_binary_and_non_utf8_are_flagged(self):
        self.assertEqual([f.flag for f in self.transform(b"a\nb\x00c\n").flags], ["binary-content"])
        self.assertEqual([(f.flag, f.line) for f in self.transform(b"ok\n\xff\xfe\n").flags], [("not-utf8", 2)])


if __name__ == "__main__":
    unittest.main()
