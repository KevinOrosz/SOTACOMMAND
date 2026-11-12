"""Terms from $LEAKGATE_DENYLIST: matched like a plaintext list, never printed."""

import os
import unittest

from tests.leakgate import support
import leakgate

SECRET_TERMS = "# a comment line\nZorblax Quill\nQXJUNO\n[substring] vexmorn\n"


class EnvDenylistTest(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.doc = os.path.join(self.tmp, "notes.md")
        with open(self.doc, "w", encoding="utf-8") as handle:
            handle.write("Met zorblax-quill.\nThe QXJUNO pilot.\nunvexmornable\n")

    def test_terms_come_from_the_environment(self):
        config = leakgate.load_config(support.RULES, env={"LEAKGATE_DENYLIST": SECRET_TERMS})
        self.assertEqual(3, config.env_term_count)
        findings = leakgate.scan([self.doc], config)
        self.assertEqual([(1, "env-denylist"), (2, "env-denylist"), (3, "env-denylist")],
                         [(f.line, f.rule_id) for f in findings])

    def test_unset_or_blank_means_no_env_rule(self):
        for env in ({}, {"LEAKGATE_DENYLIST": "  \n# only a comment\n"}):
            config = leakgate.load_config(support.RULES, env=env)
            self.assertNotIn("env-denylist", [rule.id for rule in config.term_rules])
            self.assertEqual([], leakgate.scan([self.doc], config))

    def test_windows_line_endings(self):
        config = leakgate.load_config(support.RULES, env={"LEAKGATE_DENYLIST": "Zorblax Quill\r\nQXJUNO\r\n"})
        self.assertEqual(2, config.env_term_count)

    def test_cli_output_never_contains_the_terms(self):
        for args in (("--json",), (), ("--context", "40"), ("--json", "--show-allowed", "--context", "40")):
            completed = support.run_cli("check", self.doc, *args, env={"LEAKGATE_DENYLIST": SECRET_TERMS})
            self.assertEqual(1, completed.returncode, completed.stderr)
            output = (completed.stdout + completed.stderr).lower()
            for term in ("zorblax", "quill", "qxjuno", "vexmorn"):
                self.assertNotIn(term, output, "%s leaked with %s" % (term, args))

    def test_require_env_denylist(self):
        clean = os.path.join(self.tmp, "clean.md")
        with open(clean, "w", encoding="utf-8") as handle:
            handle.write("nothing private here\n")
        missing = support.run_cli("check", clean, "--require-env-denylist")
        self.assertEqual(2, missing.returncode)
        present = support.run_cli("check", clean, "--require-env-denylist", env={"LEAKGATE_DENYLIST": "QXJUNO"})
        self.assertEqual(0, present.returncode, present.stderr)

    def test_variable_name_and_switch_are_configurable(self):
        rules = support.write_rules(self.tmp, extends=support.RULES, env_denylist={"variable": "MY_TERMS"})
        config = leakgate.load_config(rules, env={"MY_TERMS": "QXJUNO", "LEAKGATE_DENYLIST": "Zorblax Quill"})
        self.assertEqual([(2, "env-denylist")], [(f.line, f.rule_id) for f in leakgate.scan([self.doc], config)])
        off = support.write_rules(self.tmp, name="off.json", extends=support.RULES, env_denylist={"enabled": False})
        config = leakgate.load_config(off, env={"LEAKGATE_DENYLIST": SECRET_TERMS})
        self.assertEqual([], leakgate.scan([self.doc], config))


if __name__ == "__main__":
    unittest.main()
