"""The bench: recall 1.0 on the planted corpus, 0 false blocks on the clean one,
and proof that it notices when either direction regresses."""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests.leakgate import support
import leakgate

RULE_TYPES = {"regex", "term", "hashed-term", "env-denylist", "path"}


def corpus_files(directory):
    found = []
    for dirpath, _dirnames, filenames in os.walk(directory):
        for name in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, name), directory).replace(os.sep, "/"))
    return sorted(found)


def load_expected():
    with open(os.path.join(support.PLANTED, "expected.json"), encoding="utf-8") as handle:
        return json.load(handle)["findings"]


class BenchResultTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = leakgate.run_bench()

    def test_recall_is_one_and_nothing_is_falsely_blocked(self):
        result = self.result
        self.assertEqual([], result.missed)
        self.assertEqual([], [f.to_dict() for f in result.unexpected])
        self.assertEqual([], [f.to_dict() for f in result.false_blocks])
        self.assertEqual(1.0, result.recall)
        self.assertTrue(result.ok)

    def test_corpus_sizes_meet_the_brief(self):
        self.assertGreaterEqual(self.result.planted_files, 40)
        self.assertGreaterEqual(self.result.planted, 80)
        self.assertGreaterEqual(self.result.clean_files, 30)

    def test_every_rule_type_is_covered(self):
        self.assertEqual(RULE_TYPES, set(self.result.coverage))

    def test_cli_and_json_output(self):
        document = support.run_json("bench", "--json")
        self.assertEqual(0, document["_returncode"])
        self.assertTrue(document["ok"])
        self.assertEqual(document["recall"]["caught"], document["recall"]["planted"])
        self.assertEqual(0, document["false_blocks"])
        self.assertEqual(0, document["unexpected"])

    def test_bench_ignores_the_real_environment(self):
        # "Store A" appears in the clean corpus; a real secret must not reach the bench.
        completed = support.run_cli("bench", env={"LEAKGATE_DENYLIST": "Store A\nsotacommand", "LEAKGATE_SALT": "x" * 32})
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn("false_blocks=0", completed.stdout)


class CorpusShapeTest(unittest.TestCase):
    def test_every_planted_file_declares_its_findings(self):
        planted = set(corpus_files(support.PLANTED)) - {"expected.json"}
        declared = {item["path"] for item in load_expected()}
        self.assertEqual(planted, declared)

    def test_planted_corpus_covers_the_file_types(self):
        extensions = {os.path.splitext(path)[1] for path in corpus_files(support.PLANTED)}
        for required in (".md", ".json", ".py", ".sh"):
            self.assertIn(required, extensions)
        self.assertTrue({".yaml", ".yml"} & extensions)

    def test_clean_corpus_has_the_required_material(self):
        texts = []
        for relpath in corpus_files(support.CLEAN):
            with open(os.path.join(support.CLEAN, relpath), encoding="utf-8") as handle:
                texts.append(handle.read())
        blob = "\n".join(texts)
        for needle in ["Claude Code", "Codex", "Cursor", "Gemini CLI", "GitHub Copilot",
                       ".claude-plugin/marketplace.json", "~/.claude/skills", ".agents/skills"]:
            self.assertIn(needle, blob)
        for word in ["herald", "star", "ember", "sage", "rune"]:
            self.assertRegex(blob, r"\b%s\b" % word)
        self.assertRegex(blob, r"[\w.]+@example\.com")
        self.assertRegex(blob, r"\b\d+\.\d+\.\d+(?:-[\w.]+)?\b")
        self.assertIn(".claude-plugin/marketplace.json", corpus_files(support.CLEAN))

    def test_corpus_uses_invented_private_markers_only(self):
        with open(support.BENCH_CONFIG, encoding="utf-8") as handle:
            bench = json.load(handle)
        owner_terms = [t if isinstance(t, str) else t["term"] for g in bench["terms"] if g["id"] == "owner-name" for t in g["terms"]]
        self.assertEqual(["Avery Example"], owner_terms)
        self.assertIn("avery@example.invalid", bench["regex"][0]["always_flag"])

    @unittest.skipUnless(support.git_available(), "git is not installed")
    def test_no_corpus_file_is_ignored_by_git(self):
        completed = subprocess.run(["git", "check-ignore", "--stdin"], cwd=support.REPO_ROOT,
                                   input="\n".join(os.path.join("fixtures", "leak-corpus", p)
                                                   for d in ("planted", "clean", "samples")
                                                   for p in [os.path.join(d, f) for f in corpus_files(os.path.join(support.CORPUS, d))]),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if completed.returncode == 128:
            self.skipTest("not inside a git work tree")
        self.assertEqual("", completed.stdout, "git would never publish these, so CI could not bench them")


class BenchDetectsRegressionsTest(unittest.TestCase):
    """Copy the corpus, damage it, and require the bench to notice."""

    def setUp(self):
        self._tmp = support.temp_dir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.corpus = os.path.join(self.tmp, "leak-corpus")
        shutil.copytree(support.CORPUS, self.corpus, symlinks=True)
        bench_path = os.path.join(self.corpus, "bench.json")
        with open(bench_path, encoding="utf-8") as handle:
            bench = json.load(handle)
        bench["extends"] = support.RULES  # the copy no longer sits beside tools/
        support.write_json(bench_path, bench)

    def run_copy(self):
        return leakgate.run_bench(self.corpus)

    def edit(self, relpath, old, new):
        path = os.path.join(self.corpus, *relpath.split("/"))
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))

    def test_untouched_copy_passes(self):
        self.assertTrue(self.run_copy().ok)

    def test_a_removed_leak_is_missed(self):
        expected = load_expected()
        item = next(i for i in expected if i["path"] == "docs/house-rules.md" and i["line"] == 3)
        path = os.path.join(self.corpus, "planted", "docs", "house-rules.md")
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        lines[item["line"] - 1] = "Never ship a skill without evals."
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        result = self.run_copy()
        self.assertFalse(result.ok)
        self.assertEqual([("docs/house-rules.md", 3, "rule-code")], result.missed)
        completed = support.run_cli("bench", "--corpus", self.corpus)
        self.assertEqual(1, completed.returncode)
        self.assertRegex(completed.stdout, r"recall=(\d+)/(?!\1\b)\d+")

    def test_an_undeclared_leak_is_unexpected(self):
        email = support.lone_sample("email", support.bench_config())
        self.edit("planted/docs/team-roster.md", "# Team roster", "# Team roster for %s" % email)
        result = self.run_copy()
        self.assertEqual([("docs/team-roster.md", 1, "email")], [(f.path, f.line, f.rule_id) for f in result.unexpected])
        self.assertFalse(result.ok)

    def test_a_leak_in_the_clean_corpus_is_a_false_block(self):
        home_path = support.lone_sample("home-path", support.bench_config())
        self.edit("clean/docs/faq.md", "# FAQ", "# FAQ (see %s)" % home_path)
        result = self.run_copy()
        self.assertEqual([("docs/faq.md", 1, "home-path")], [(f.path, f.line, f.rule_id) for f in result.false_blocks])
        completed = support.run_cli("bench", "--corpus", self.corpus)
        self.assertEqual(1, completed.returncode)
        self.assertIn("false_blocks=1", completed.stdout)

    def test_a_broken_expected_file_is_a_config_error(self):
        path = os.path.join(self.corpus, "planted", "expected.json")
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["findings"][0]["rule_id"] = "no-such-rule"
        support.write_json(path, document)
        self.assertEqual(2, support.run_cli("bench", "--corpus", self.corpus).returncode)
        document["findings"][0] = {"path": "../escape.md", "line": 1, "rule_id": "email"}
        support.write_json(path, document)
        self.assertEqual(2, support.run_cli("bench", "--corpus", self.corpus).returncode)

    def test_bench_requires_both_corpora(self):
        shutil.rmtree(os.path.join(self.corpus, "clean"))
        self.assertEqual(2, support.run_cli("bench", "--corpus", self.corpus).returncode)


if __name__ == "__main__":
    unittest.main()


class SealedTokenTests(unittest.TestCase):
    """Planted provider-shaped tokens are stored sealed, so GitHub's push protection never blocks a push."""

    # The token shapes GitHub's secret push protection blocks, and close relatives.
    PUSH_PROTECTED = re.compile(r"(?<![A-Za-z0-9])(?:sk_(?:test|live)_|rk_(?:test|live)_|xox[abposr]-|hf_)[A-Za-z0-9]{6}")

    def test_no_fixture_holds_an_unsealed_provider_token(self):
        found = []
        for folder, _dirs, files in os.walk(os.path.join(support.REPO_ROOT, "fixtures")):
            for name in files:
                path = os.path.join(folder, name)
                with open(path, "rb") as handle:
                    text = handle.read().decode("utf-8", "replace")
                for match in self.PUSH_PROTECTED.finditer(text):
                    found.append("%s: %s..." % (os.path.relpath(path, support.REPO_ROOT), match.group(0)[:8]))
        self.assertEqual(found, [], "store these with {{seal}} after the first character (see bench.py)")

    def test_the_scanner_never_unseals(self):
        sealed = "STRIPE_KEY=s{{seal}}k_test_PLANTEDplantedPLANTEDpla\n"
        config = support.production_config()
        self.assertEqual([f.rule_id for f in leakgate.scan_text(sealed, config, path="app/config.env")], [])
        unsealed = sealed.replace("{{seal}}", "")
        self.assertTrue(leakgate.scan_text(unsealed, config, path="app/config.env"), "the unsealed value is caught")
        scanner = os.path.join(support.REPO_ROOT, "tools", "repo", "leakgate", "leakgatelib", "scanner.py")
        with open(scanner, encoding="utf-8") as handle:
            self.assertNotIn("seal", handle.read().lower())

    def test_the_bench_leaves_the_corpus_untouched(self):
        path = os.path.join(support.CORPUS, "planted", "config", ".env.local")
        with open(path, "rb") as handle:
            before = handle.read()
        self.assertIn(b"{{seal}}", before)
        leakgate.run_bench()
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)
