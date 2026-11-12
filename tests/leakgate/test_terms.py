"""Literal terms: case, whole-word, substring, separators and normalization.

Terms here are invented (Zorblax, QXJUNO). Persona names are read from the
bench config at run time so this file never spells one out.
"""

import json
import os
import unittest

from tests.leakgate import support
import leakgate
from leakgate import ConfigError
from leakgatelib.terms import is_all_caps, parse_term_lines


def term_config(directory, terms_text=None, groups=None):
    sections = {}
    if terms_text is not None:
        path = os.path.join(directory, "terms.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(terms_text)
        sections["denylist_files"] = [path]
    if groups is not None:
        sections["terms"] = groups
    return leakgate.load_config(support.write_rules(directory, **sections), env={})


def ids(text, config, path="doc.md"):
    return [f.rule_id for f in leakgate.scan_text(text, config, path=path)]


class AllCapsHeuristicTest(unittest.TestCase):
    def test_heuristic(self):
        self.assertTrue(is_all_caps("QXJUNO"))
        self.assertTrue(is_all_caps("QX JUNO 2"))
        self.assertFalse(is_all_caps("Qxjuno"))
        self.assertFalse(is_all_caps("Q"))
        self.assertFalse(is_all_caps("/srv/qx/"))
        self.assertFalse(is_all_caps("1234"))


class PlaintextListTest(unittest.TestCase):
    def test_all_caps_terms_are_case_sensitive_whole_words(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "QXJUNO\n")
            self.assertEqual(["denylist-term"], ids("ship QXJUNO today", config))
            for clean in ["ship qxjuno today", "ship Qxjuno today", "QXJUNOS", "preQXJUNO"]:
                self.assertEqual([], ids(clean, config), clean)

    def test_mixed_case_terms_are_case_insensitive(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "Zorblax Quill\n")
            for text in ["Zorblax Quill", "zorblax quill", "ZORBLAX QUILL"]:
                self.assertEqual(["denylist-term"], ids(text, config), text)

    def test_whole_word_boundaries(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "Zorblax\n")
            for text in ["Zorblax's", "(Zorblax)", "zorblax_tool", "zorblax-tool", "**Zorblax**", "path/zorblax/x"]:
                self.assertEqual(["denylist-term"], ids(text, config), text)
            for text in ["Zorblaxian", "preZorblax", "zorblax2", "2zorblax"]:
                self.assertEqual([], ids(text, config), text)

    def test_flexible_separators_between_words(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "Zorblax Quill\n")
            for text in ["zorblax-quill", "zorblax_quill", "ZorblaxQuill", "zorblax.quill", "zorblax\nquill", "zorblax   quill"]:
                self.assertEqual(["denylist-term"], ids(text, config), repr(text))
            for text in ["zorblax, quill", "zorblax and quill", "zorblax"]:
                self.assertEqual([], ids(text, config), text)

    def test_substring_flag(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "[substring] zorb\n")
            self.assertEqual(["denylist-term"], ids("unzorbable", config))

    def test_explicit_flags_override_the_heuristic(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "[case-sensitive] Quill\n[case-insensitive] QXJ\n[case-sensitive, substring] QZ\n")
            self.assertEqual([], ids("quill", config))
            self.assertEqual(["denylist-term"], ids("Quill", config))
            self.assertEqual(["denylist-term"], ids("qxj", config))
            self.assertEqual(["denylist-term"], ids("aQZb", config))
            self.assertEqual([], ids("aqzb", config))

    def test_comments_blank_lines_and_literal_brackets(self):
        specs = parse_term_lines("# comment\n\n  [Draft] notes  \nTicket #42 owner\n", "list.txt")
        self.assertEqual(["[Draft] notes", "Ticket #42 owner"], [s.term for s in specs])
        self.assertEqual(["list.txt:3", "list.txt:4"], [s.origin for s in specs])

    def test_flags_without_a_term_are_an_error(self):
        with self.assertRaises(ConfigError):
            parse_term_lines("[substring]\n", "list.txt")

    def test_spec_repr_never_shows_the_term(self):
        (spec,) = parse_term_lines("Zorblax Quill\n", "list.txt")
        self.assertNotIn("Zorblax", repr(spec))

    def test_missing_denylist_file_is_a_config_error(self):
        with support.temp_dir() as tmp:
            with self.assertRaises(ConfigError):
                leakgate.load_config(support.RULES, denylist=(os.path.join(tmp, "missing.txt"),), env={})


class NormalizationTest(unittest.TestCase):
    def test_accents_fullwidth_and_zero_width_do_not_hide_a_term(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "Zorblax Quill\nQXJUNO\n")
            for text in ["Zörblax Quill", "Zor​blax Quill", "Zorblax­Quill", "ＱＸＪＵＮＯ"]:
                self.assertEqual(1, len(ids(text, config)), repr(text))

    def test_accented_terms_match_plain_text(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "Zörblax\n")
            self.assertEqual(["denylist-term"], ids("zorblax", config))

    def test_columns_count_original_characters(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "Zorblax\n")
            (finding,) = leakgate.scan_text("éé Ｚorblax", config)
            self.assertEqual((1, 4), (finding.line, finding.col))

    def test_a_term_wrapped_across_lines_reports_its_first_line(self):
        with support.temp_dir() as tmp:
            config = term_config(tmp, "Zorblax Quill\n")
            (finding,) = leakgate.scan_text("one\nsee Zorblax\nQuill later\n", config)
            self.assertEqual((2, 5), (finding.line, finding.col))


class TermGroupTest(unittest.TestCase):
    def test_group_defaults_and_per_term_overrides(self):
        groups = [{
            "id": "codenames",
            "description": "test group",
            "case_sensitive": True,
            "terms": ["Quill", {"term": "zorb", "whole_word": False, "case_sensitive": False}],
        }]
        with support.temp_dir() as tmp:
            config = term_config(tmp, groups=groups)
            self.assertEqual(["codenames"], ids("Quill", config))
            self.assertEqual([], ids("quill", config))
            self.assertEqual(["codenames"], ids("UNZORBABLE", config))

    def test_persona_style_terms_ignore_ordinary_prose(self):
        with open(support.BENCH_CONFIG, encoding="utf-8") as handle:
            groups = {group["id"]: group for group in json.load(handle)["terms"]}
        config = support.bench_config()
        for term in groups["persona-name"]["terms"]:
            with self.subTest(term=term.lower()):
                self.assertIn("persona-name", ids("the %s line" % term, config))
                for clean in (term.lower(), term.capitalize(), term + "S", "x" + term):
                    self.assertNotIn("persona-name", ids("the %s line" % clean, config))


if __name__ == "__main__":
    unittest.main()
