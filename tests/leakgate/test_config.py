"""Rules files: strict validation and "extends" layering."""

import os
import unittest

from tests.leakgate import support
import leakgate
from leakgate import ConfigError


class LayeringTest(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)

    def test_overlay_patches_by_id_and_appends_new_ids(self):
        sample = support.lone_sample("email", support.production_config())
        overlay = support.write_rules(
            self.tmp, extends=support.RULES,
            regex=[{"id": "email", "always_flag": [sample]}, {"id": "ticket", "description": "t", "patterns": ["TCK-\\d+"]}],
        )
        config = leakgate.load_config(overlay, env={})
        email = next(rule for rule in config.regex_rules if rule.id == "email")
        self.assertTrue(email.patterns, "patching keeps the base patterns")
        self.assertIn(sample.casefold(), email.always_flag)
        self.assertEqual("ticket", config.regex_rules[-1].id)
        self.assertEqual(2, len(config.sources), "base and overlay are both recorded")

    def test_always_flag_overrides_exceptions(self):
        allowed = support.load_samples()["email"]["pass"][0]
        overlay = support.write_rules(self.tmp, extends=support.RULES, regex=[{"id": "email", "always_flag": [allowed.upper()]}])
        self.assertEqual([], leakgate.scan_text(allowed, support.production_config()))
        self.assertEqual(["email"], [f.rule_id for f in leakgate.scan_text(allowed, leakgate.load_config(overlay, env={}))])

    def test_disabled_removes_an_inherited_entry(self):
        overlay = support.write_rules(self.tmp, extends=support.RULES, paths=[{"id": "handoff-file", "disabled": True}],
                                      allow=[{"id": "memory-templates", "disabled": True}])
        config = leakgate.load_config(overlay, env={})
        self.assertNotIn("handoff-file", [rule.id for rule in config.path_rules])
        self.assertNotIn("memory-templates", [entry.id for entry in config.allow])

    def test_relative_paths_resolve_against_the_declaring_file(self):
        os.makedirs(os.path.join(self.tmp, "lists"))
        with open(os.path.join(self.tmp, "lists", "private.txt"), "w", encoding="utf-8") as handle:
            handle.write("Zorblax\n")
        base = support.write_rules(os.path.join(self.tmp, "lists"), name="base.json", denylist_files=["private.txt"])
        top = support.write_rules(self.tmp, name="top.json", extends=os.path.join("lists", "base.json"))
        config = leakgate.load_config(top, env={})
        self.assertEqual(1, config.denylist_term_count)
        self.assertTrue(os.path.exists(base))

    def test_optional_denylist_files_may_be_absent(self):
        rules = support.write_rules(self.tmp, denylist_files=[{"path": "absent.txt", "optional": True}])
        self.assertEqual(0, leakgate.load_config(rules, env={}).denylist_term_count)
        strict = support.write_rules(self.tmp, name="strict.json", denylist_files=["absent.txt"])
        with self.assertRaises(ConfigError):
            leakgate.load_config(strict, env={})

    def test_extends_cycle_is_an_error(self):
        support.write_rules(self.tmp, name="a.json", extends="b.json")
        support.write_rules(self.tmp, name="b.json", extends="a.json")
        with self.assertRaises(ConfigError) as caught:
            leakgate.load_config(os.path.join(self.tmp, "a.json"), env={})
        self.assertIn("cycle", str(caught.exception))

    def test_shape_errors_name_the_problem(self):
        cases = [
            ({"regex": [{"id": "x", "patterns": "not-a-list"}]}, "patterns"),
            ({"regex": [{"id": "x", "pattern": "typo"}]}, "unknown key"),
            ({"terms": [{"id": "x", "terms": [{"word": "q"}]}]}, "unknown key"),
            ({"terms": [{"id": "x", "case_sensitive": "yes", "terms": []}]}, "case_sensitive"),
            ({"paths": [{"id": "x", "globs": []}]}, "globs"),
            ({"env_denylist": {"variable": "BAD-NAME"}}, "variable"),
            ({"bench": {"env": {"A": 1}}}, "bench.env"),
            ({"hashed_denylist": {"files": [{"file": "x"}]}}, "unknown key"),
        ]
        for sections, needle in cases:
            with self.subTest(needle=needle):
                path = support.write_rules(self.tmp, name="case.json", **sections)
                with self.assertRaises(ConfigError) as caught:
                    leakgate.load_config(path, env={})
                self.assertIn(needle, str(caught.exception))

    def test_default_config_loads(self):
        config = leakgate.load_config(env={})
        self.assertEqual(leakgate.DEFAULT_CONFIG_PATH, support.RULES)
        self.assertTrue(config.regex_rules and config.path_rules and config.allow)


if __name__ == "__main__":
    unittest.main()
