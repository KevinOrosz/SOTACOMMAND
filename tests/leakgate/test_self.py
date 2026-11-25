"""The gate holds itself to its own rules, and its config files stay wired up.

A gate that blocks its own source gets switched off, so the leak gate's
shipped files (everything in this lane outside fixtures/) must pass the gate,
both with the public rules and with the owner's private seed list.
"""

import json
import ast
import os
import re
import unittest

from tests.leakgate import support

OWN_PATHS = ["tools/repo/leakgate", "tests/leakgate", ".gitleaks.toml", ".pre-commit-config.yaml"]
SEED = os.path.join(support.REPO_ROOT, "internal", "denylist.seed.txt")


def read(relpath):
    with open(os.path.join(support.REPO_ROOT, relpath), encoding="utf-8") as handle:
        return handle.read()


class OwnFilesPassTheGateTest(unittest.TestCase):
    def test_with_the_public_rules(self):
        completed = support.run_cli("check", "--all-files", "--exclude", "__pycache__/", *OWN_PATHS)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    @unittest.skipUnless(os.path.exists(SEED), "the private seed list exists only in the staging repo")
    def test_with_the_private_seed_list(self):
        completed = support.run_cli("check", "--all-files", "--exclude", "__pycache__/", "--denylist", SEED, *OWN_PATHS)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_python_sources_use_python_3_9_grammar(self):
        for directory in ("tools/repo/leakgate", "tests/leakgate"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(support.REPO_ROOT, directory)):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for name in filenames:
                    if name.endswith(".py"):
                        path = os.path.join(dirpath, name)
                        with open(path, encoding="utf-8") as handle:
                            source = handle.read()
                        with self.subTest(path=os.path.relpath(path, support.REPO_ROOT)):
                            ast.parse(source, filename=path, feature_version=(3, 9))


class HookAndScannerConfigTest(unittest.TestCase):
    def test_pre_commit_runs_the_gate_on_staged_files(self):
        text = read(".pre-commit-config.yaml")
        self.assertRegex(text, r"(?m)^\s*-\s*repo:\s*local\s*$")
        self.assertRegex(text, r"(?m)^\s*-\s*id:\s*leakgate\s*$")
        self.assertRegex(text, r"(?m)^\s*entry:\s*python3 \.github/scripts/check_public_files\.py\s*$")
        self.assertRegex(text, r"(?m)^\s*language:\s*system\s*$")
        self.assertRegex(text, r"(?m)^\s*pass_filenames:\s*true\s*$")
        self.assertNotRegex(text, r"(?m)^\s*exclude:")
        fixture_hashes = json.loads(read(".github/scripts/fixture-digests.json"))
        self.assertTrue(fixture_hashes)
        for path, digest in fixture_hashes.items():
            self.assertTrue(path.startswith("fixtures/"))
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertNotIn("\t", text)

    def test_gitleaks_exempts_only_historical_fixture_findings(self):
        text = read(".gitleaks.toml")
        self.assertIn("useDefault = true", text)
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            tomllib = None
        if tomllib is not None:
            config = tomllib.loads(text)
            self.assertTrue(config["extend"]["useDefault"])
            paths = [p for entry in config["allowlists"] for p in entry.get("paths", [])]
        else:
            paths = re.findall(r"'''([^']+)'''", text)
        compiled = [re.compile(p) for p in paths]
        self.assertFalse(any(p.search("fixtures/leak-corpus/planted/ops/aws.ini") for p in compiled))
        for item in read(".gitleaksignore").splitlines():
            if item and not item.startswith("#"):
                self.assertRegex(item, r"^[a-f0-9]{40}:fixtures/leak-corpus/(planted|samples)/[^:]+:[\w-]+:[0-9]+$")
        self.assertFalse(any(p.search("tools/repo/leakgate/rules.json") for p in compiled))


class ReadmeContractTest(unittest.TestCase):
    def test_readme_documents_the_contract(self):
        text = read("tools/repo/leakgate/README.md")
        for needle in ["leakgate.py check", "leakgate.py bench", "leakgate.py hash-denylist", "--json",
                       "LEAKGATE_DENYLIST", "LEAKGATE_SALT", "--all-files", "expected.json", "recall=",
                       "false_blocks=", "Exit codes"]:
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
