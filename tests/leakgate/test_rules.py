"""Every production content rule, against positive and negative samples.

Samples live in fixtures/leak-corpus/samples/rule-samples.json so that no
leak-shaped literal sits in this file.
"""

import json
import unittest

from tests.leakgate import support
import leakgate


class ProductionRuleSamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = support.production_config()
        cls.samples = support.load_samples()

    def test_every_content_rule_has_samples_both_ways(self):
        content_rule_ids = {rule.id for rule in self.config.regex_rules} | {
            rule.id for rule in self.config.term_rules if rule.type == "term"
        }
        self.assertEqual(content_rule_ids, set(self.samples), "rules and samples must stay in step")
        for rule_id, sets in self.samples.items():
            self.assertTrue(sets["flag"], "%s needs flag samples" % rule_id)
            self.assertTrue(sets["pass"], "%s needs pass samples" % rule_id)

    def test_flag_samples_are_caught_by_their_rule(self):
        for rule_id, sets in self.samples.items():
            for sample in sets["flag"]:
                with self.subTest(rule=rule_id, sample=sample):
                    ids = [f.rule_id for f in leakgate.scan_text(sample, self.config)]
                    self.assertIn(rule_id, ids)

    def test_pass_samples_produce_no_finding_at_all(self):
        for rule_id, sets in self.samples.items():
            for sample in sets["pass"]:
                with self.subTest(rule=rule_id, sample=sample):
                    findings = leakgate.scan_text(sample, self.config)
                    self.assertEqual([], [(f.rule_id, f.col) for f in findings])

    def test_flag_samples_still_match_inside_a_sentence(self):
        for rule_id, sets in self.samples.items():
            for sample in sets["flag"]:
                with self.subTest(rule=rule_id, sample=sample):
                    text = "Before: %s :after\n" % sample
                    self.assertIn(rule_id, [f.rule_id for f in leakgate.scan_text(text, self.config)])

    def test_positions_point_at_the_match(self):
        sample = self.samples["email"]["flag"][0]
        text = "line one\n    " + sample + "\n"
        (finding,) = leakgate.scan_text(text, self.config)
        self.assertEqual((finding.line, finding.col, finding.rule_id), (2, 5, "email"))

    def test_overlapping_patterns_of_one_rule_merge(self):
        # A display name matches two model-id patterns; it must count once.
        sample = next(s for s in self.samples["model-id"]["flag"] if s.count(" ") == 2 and "." in s)
        findings = leakgate.scan_text(sample, self.config)
        self.assertEqual(1, len([f for f in findings if f.rule_id == "model-id"]), sample)


class RulesFileTest(unittest.TestCase):
    def test_rules_file_is_valid_and_documented(self):
        with open(support.RULES, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(1, data["version"])
        for section in ("regex", "terms", "paths"):
            for entry in data[section]:
                self.assertTrue(entry.get("description"), "%s %s needs a description" % (section, entry["id"]))
        for entry in data["allow"]:
            self.assertTrue(entry.get("reason"), "allow %s needs a reason" % entry["id"])

    def test_brief_path_globs_are_present(self):
        config = support.production_config()
        globs = {glob.pattern for rule in config.path_rules for glob in rule.globs.globs}
        for expected in ["internal/**", "**/KB/**", "**/output/**", "**/tasks/**", "**/.env*", "**/fixture-*.md", "**/HANDOFF.md"]:
            self.assertIn(expected, globs)

    def test_production_rules_name_no_private_terms(self):
        # Private names belong in hashed lists or the CI secret, never here.
        config = support.production_config()
        self.assertEqual(["private-marker"], [rule.id for rule in config.term_rules])

    def test_every_rule_type_is_configured(self):
        config = support.bench_config()
        types = set(config.rule_types[rule_id] for rule_id in config.rule_types)
        self.assertEqual({"regex", "term", "hashed-term", "env-denylist", "path"}, types)
        self.assertIsNotNone(config.hashed_rule)
        self.assertIn("env-denylist", [rule.id for rule in config.term_rules])
        self.assertIn("denylist-term", [rule.id for rule in config.term_rules])


if __name__ == "__main__":
    unittest.main()
