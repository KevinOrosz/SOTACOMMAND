"""Hashed terms: publishable lists that block names without printing them."""

import json
import os
import stat
import sys
import unittest

from tests.leakgate import support
import leakgate
from leakgate import ConfigError
from leakgatelib.terms import MAX_NGRAM, HashedMatcher, hash_specs, parse_term_lines

SALT = b"unit-test-salt-not-secret-0123456789"


def matcher_for(terms_text, salt=SALT):
    digests = hash_specs(parse_term_lines(terms_text, "terms.txt"), salt)
    return HashedMatcher(frozenset(digests), salt)


def spans(matcher, text):
    return [text[start:end] for start, end in matcher.finditer(text)]


class HashListTest(unittest.TestCase):
    def test_output_is_sorted_unique_hex_and_hides_terms(self):
        digests = hash_specs(parse_term_lines("Zorblax Quill\nzorblax quill\nQXJUNO\n", "t"), SALT)
        self.assertEqual(digests, sorted(set(digests)))
        for digest in digests:
            self.assertRegex(digest, r"\A[0-9a-f]{64}\Z")
        blob = json.dumps(digests).lower()
        self.assertNotIn("zorblax", blob)
        self.assertNotIn("qxjuno", blob)
        # "Zorblax Quill" twice (once as written, once run together) plus QXJUNO.
        self.assertEqual(3, len(digests))

    def test_salt_changes_every_digest(self):
        first = hash_specs(parse_term_lines("Zorblax\n", "t"), SALT)
        second = hash_specs(parse_term_lines("Zorblax\n", "t"), SALT + b"x")
        self.assertFalse(set(first) & set(second))

    def test_terms_longer_than_max_ngram_are_rejected(self):
        words = " ".join("w%d" % i for i in range(MAX_NGRAM + 1))
        with self.assertRaises(ConfigError) as caught:
            hash_specs(parse_term_lines(words + "\n", "t"), SALT)
        self.assertIn("t:1", str(caught.exception))
        self.assertNotIn("w0", str(caught.exception))

    def test_substring_terms_and_empty_terms_are_rejected(self):
        for text in ("[substring] zorb\n", "///\n"):
            with self.assertRaises(ConfigError):
                hash_specs(parse_term_lines(text, "t"), SALT)

    def test_published_fixture_regenerates_exactly(self):
        out = None
        with support.temp_dir() as tmp:
            out = os.path.join(tmp, "out.json")
            completed = support.run_cli(
                "hash-denylist", os.path.join(support.CORPUS, "hashed-terms.txt"),
                "--salt-file", os.path.join(support.CORPUS, "bench.salt"), "--out", out)
            self.assertEqual(0, completed.returncode, completed.stderr)
            with open(out, encoding="utf-8") as fresh, open(os.path.join(support.CORPUS, "hashed-denylist.json"), encoding="utf-8") as committed:
                self.assertEqual(committed.read(), fresh.read())


class HashedMatchingTest(unittest.TestCase):
    def test_ngrams_from_one_to_four_tokens(self):
        matcher = matcher_for("Zorblax\nZorblax Quill\nRed Quill Ink\nOne Two Three Four\n")
        self.assertEqual(["Zorblax"], spans(matcher, "see Zorblax now"))
        self.assertIn("Zorblax Quill", spans(matcher, "see Zorblax Quill"))
        self.assertEqual(["Red Quill Ink"], spans(matcher, "a Red Quill Ink pen"))
        self.assertEqual(["One Two Three Four"], spans(matcher, "One Two Three Four"))

    def test_partial_ngrams_do_not_match(self):
        matcher = matcher_for("Zorblax Quill\n")
        self.assertEqual([], spans(matcher, "Zorblax alone, and a quill"))

    def test_tokens_ignore_punctuation_and_line_breaks(self):
        matcher = matcher_for("Zorblax Quill\n")
        for text in ["zorblax-quill", "zorblax_quill", "ZORBLAX\nQUILL", "Zorblax, Quill", "ZorblaxQuill"]:
            self.assertEqual(1, len(spans(matcher, text)), repr(text))

    def test_case_sensitive_terms_only_match_their_case(self):
        matcher = matcher_for("QXJUNO\n")
        self.assertEqual(["QXJUNO"], spans(matcher, "the QXJUNO build"))
        self.assertEqual([], spans(matcher, "the qxjuno build and Qxjuno"))

    def test_lowercase_case_sensitive_term_still_matches(self):
        matcher = matcher_for("[case-sensitive] zorblax\n")
        self.assertEqual(["zorblax"], spans(matcher, "zorblax"))

    def test_wrong_salt_matches_nothing(self):
        digests = hash_specs(parse_term_lines("Zorblax\n", "t"), SALT)
        matcher = HashedMatcher(frozenset(digests), SALT + b"other")
        self.assertEqual([], spans(matcher, "Zorblax"))


class HashedConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.salt_file = os.path.join(self.tmp, "salt")
        with open(self.salt_file, "w", encoding="utf-8") as handle:
            handle.write(SALT.decode() + "\n")
        self.list_file = support.write_json(
            os.path.join(self.tmp, "hashed.json"), hash_specs(parse_term_lines("Zorblax Quill\n", "t"), SALT))
        self.doc = os.path.join(self.tmp, "doc.md")
        with open(self.doc, "w", encoding="utf-8") as handle:
            handle.write("Notes for zorblax quill.\n")

    def test_cli_finds_hashed_terms_with_a_salt_file(self):
        result = support.run_json("check", self.doc, "--hashed-denylist", self.list_file, "--salt-file", self.salt_file, "--json")
        self.assertEqual(1, result["_returncode"])
        self.assertEqual(["hashed-term"], [f["rule_id"] for f in result["findings"]])
        self.assertNotIn("zorblax", json.dumps(result["findings"]).lower())

    def test_cli_takes_the_salt_from_the_environment(self):
        result = support.run_json("check", self.doc, "--hashed-denylist", self.list_file, "--json",
                                  env={"LEAKGATE_SALT": SALT.decode()})
        self.assertEqual(1, result["_returncode"])

    def test_missing_salt_is_a_config_error(self):
        completed = support.run_cli("check", self.doc, "--hashed-denylist", self.list_file)
        self.assertEqual(2, completed.returncode)
        self.assertIn("salt", completed.stderr)

    def test_short_salt_is_rejected(self):
        with open(self.salt_file, "w", encoding="utf-8") as handle:
            handle.write("short\n")
        completed = support.run_cli("check", self.doc, "--hashed-denylist", self.list_file, "--salt-file", self.salt_file)
        self.assertEqual(2, completed.returncode)

    def test_malformed_lists_are_config_errors(self):
        for content in ('{"not": "a list"}', '["nothex"]', "not json"):
            with open(self.list_file, "w", encoding="utf-8") as handle:
                handle.write(content)
            completed = support.run_cli("check", self.doc, "--hashed-denylist", self.list_file, "--salt-file", self.salt_file)
            self.assertEqual(2, completed.returncode, content)

    def test_optional_config_list_without_salt_warns_and_continues(self):
        rules = support.write_rules(self.tmp, name="soft.json", extends=support.RULES,
                                    hashed_denylist={"files": [self.list_file], "required": False})
        config = leakgate.load_config(rules, env={})
        self.assertIsNone(config.hashed_rule)
        self.assertTrue(any("salt" in warning for warning in config.warnings))

    def test_config_salt_file_falls_back_to_the_environment(self):
        rules = support.write_rules(self.tmp, name="ci.json", extends=support.RULES,
                                    hashed_denylist={"files": [self.list_file], "salt_file": os.path.join(self.tmp, "absent")})
        config = leakgate.load_config(rules, env={"LEAKGATE_SALT": SALT.decode()})
        self.assertIsNotNone(config.hashed_rule)

    def test_create_salt_makes_a_private_file_once(self):
        terms = os.path.join(self.tmp, "terms.txt")
        with open(terms, "w", encoding="utf-8") as handle:
            handle.write("Zorblax\n")
        new_salt = os.path.join(self.tmp, "new.salt")
        completed = support.run_cli("hash-denylist", terms, "--salt-file", new_salt, "--create-salt")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(1, len(json.loads(completed.stdout)))
        with open(new_salt, encoding="utf-8") as handle:
            self.assertRegex(handle.read(), r"\A[0-9a-f]{64}\n\Z")
        if sys.platform != "win32":
            self.assertEqual(0o600, stat.S_IMODE(os.stat(new_salt).st_mode))
        again = support.run_cli("hash-denylist", terms, "--salt-file", new_salt, "--create-salt")
        self.assertEqual(0, again.returncode)
        self.assertEqual(completed.stdout, again.stdout)

    def test_hash_denylist_without_salt_file_fails(self):
        terms = os.path.join(self.tmp, "terms.txt")
        with open(terms, "w", encoding="utf-8") as handle:
            handle.write("Zorblax\n")
        completed = support.run_cli("hash-denylist", terms, "--salt-file", os.path.join(self.tmp, "absent"))
        self.assertEqual(2, completed.returncode)


if __name__ == "__main__":
    unittest.main()
