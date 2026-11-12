"""Excerpts show the rule and position, never the matched text."""

import os
import unittest

from tests.leakgate import support
import leakgate
from leakgatelib.discovery import discover
from leakgatelib.scanner import find_matches
from leakgatelib.textutil import decode_bytes


def planted_secrets(config):
    """Every (relpath, matched text) the bench config finds in planted file contents."""
    secrets = []
    for target in discover([support.PLANTED], root=support.PLANTED, all_files=True, exclude=["/expected.json"]).files:
        with open(target.abspath, "rb") as handle:
            text = decode_bytes(handle.read())
        if not text:
            continue
        for match in find_matches(text, config):
            secrets.append((target.relpath, text[match.start : match.end]))
    return secrets


class RedactionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = support.bench_config()
        cls.secrets = planted_secrets(cls.config)

    def test_corpus_yields_plenty_of_secrets(self):
        self.assertGreater(len(self.secrets), 80)

    def test_no_excerpt_contains_its_match_with_or_without_context(self):
        for context in (0, 8, 40, 400):
            findings = leakgate.scan([support.PLANTED], self.config, root=support.PLANTED, all_files=True,
                                     exclude=["/expected.json"], mode="bench", context=context)
            by_path = {}
            for finding in findings:
                by_path.setdefault(finding.path, []).append(finding.excerpt)
            for relpath, secret in self.secrets:
                for excerpt in by_path.get(relpath, []):
                    with self.subTest(context=context, path=relpath):
                        self.assertNotIn(secret, excerpt)

    def test_excerpt_format(self):
        sample = support.load_samples()["email"]["flag"][0]
        config = support.production_config()
        (plain,) = leakgate.scan_text("Contact %s today\n" % sample, config)
        self.assertEqual("[email @ 1:9]", plain.excerpt)
        (wide,) = leakgate.scan_text("Contact %s today\n" % sample, config, context=8)
        self.assertEqual("[email @ 1:9] Contact «…» today", wide.excerpt)
        (narrow,) = leakgate.scan_text("A long prefix, Contact %s today and more\n" % sample, config, context=4)
        self.assertEqual("[email @ 1:24] …act «…» tod…", narrow.excerpt)

    def test_context_masks_every_detected_span_on_the_line(self):
        samples = support.load_samples()
        first, second = samples["email"]["flag"][0], samples["home-path"]["flag"][0]
        config = support.production_config()
        findings = leakgate.scan_text("%s and %s\n" % (first, second), config, context=200)
        self.assertEqual(2, len(findings))
        for finding in findings:
            self.assertNotIn(first, finding.excerpt)
            self.assertNotIn(second, finding.excerpt)
            self.assertEqual(2, finding.excerpt.count("«…»"))

    def test_cli_output_contains_no_long_secret(self):
        long_secrets = sorted({secret for _path, secret in self.secrets if len(secret) >= 20})
        self.assertGreater(len(long_secrets), 10)
        for args in ((), ("--json",), ("--context", "30"), ("--json", "--show-allowed", "--context", "30")):
            completed = support.run_cli("check", support.PLANTED, "--config", support.BENCH_CONFIG,
                                        "--root", support.PLANTED, "--all-files", *args,
                                        env={"LEAKGATE_DENYLIST": self.config_env()})
            self.assertEqual(1, completed.returncode, completed.stderr)
            output = completed.stdout + completed.stderr
            for secret in long_secrets:
                self.assertNotIn(secret, output, "leaked with %s" % (args,))

    def config_env(self):
        return support.bench_config().bench_env["LEAKGATE_DENYLIST"]

    def test_path_rule_excerpt_names_the_glob(self):
        with support.temp_dir() as tmp:
            support.write_files(tmp, {"KB/a.md": "x"})
            (finding,) = leakgate.scan([os.path.join(tmp, "KB", "a.md")], support.production_config(), root=tmp)
            self.assertEqual("[private-dir @ file] path matches denied glob '**/KB/**'", finding.excerpt)


if __name__ == "__main__":
    unittest.main()
