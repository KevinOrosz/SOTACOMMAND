"""Allowlist entries: scoped by path glob, rule (or rule type), match and mode."""

import os
import unittest

from tests.leakgate import support
import leakgate
from leakgate import ConfigError


class ProductionAllowlistTest(unittest.TestCase):
    """The shipped entries from the brief, each proven in both directions."""

    def setUp(self):
        self.config = support.production_config()
        self.samples = support.load_samples()
        self.model_id = self.samples["model-id"]["flag"][0]
        self.home_path = self.samples["home-path"]["flag"][0]

    def test_model_ids_are_allowed_only_under_eval_results(self):
        text = "model: %s\n" % self.model_id
        (allowed,) = leakgate.scan_text(text, self.config, path="packs/p/skills/s/evals/results/2026-10-30/summary.json")
        self.assertEqual(("eval-results-model-ids",), allowed.allowed_by)
        for path in ("packs/p/skills/s/evals/cases/basic/prompt.md", "docs/models.md", "evals/results.md"):
            (finding,) = leakgate.scan_text(text, self.config, path=path)
            self.assertTrue(finding.blocking, path)

    def test_eval_results_allow_covers_only_the_model_id_rule(self):
        text = "%s %s\n" % (self.model_id, self.home_path)
        findings = leakgate.scan_text(text, self.config, path="evals/results/run/transcript.md")
        self.assertEqual({"model-id": False, "home-path": True}, {f.rule_id: f.blocking for f in findings})

    def test_owner_handle_is_allowed_only_in_root_codeowners(self):
        with support.temp_dir() as tmp:
            denylist = os.path.join(tmp, "private.txt")
            with open(denylist, "w", encoding="utf-8") as handle:
                handle.write("zorblax-quill\nZorblax Quill\n")
            config = leakgate.load_config(support.RULES, denylist=(denylist,), env={})
        line = "* @zorblax-quill\n"
        allowed = leakgate.scan_text(line, config, path=".github/CODEOWNERS")
        self.assertTrue(allowed and all(f.allowed_by == ("codeowners-owner-handle",) for f in allowed))
        for path in ("docs/CODEOWNERS", "CODEOWNERS", "x/.github/CODEOWNERS", ".github/README.md"):
            findings = leakgate.scan_text(line, config, path=path)
            self.assertTrue(findings and all(f.blocking for f in findings), path)
        # A full name is not handle-shaped, so it blocks even in CODEOWNERS.
        (name,) = leakgate.scan_text("# maintained by Zorblax Quill\n", config, path=".github/CODEOWNERS")
        self.assertTrue(name.blocking)

    def test_planted_corpus_is_allowed_only_in_bench_mode(self):
        relpath = "fixtures/leak-corpus/planted/docs/notes.md"
        text = "%s\n" % self.home_path
        (check,) = leakgate.scan_text(text, self.config, path=relpath, mode="check")
        self.assertTrue(check.blocking)
        (bench,) = leakgate.scan_text(text, self.config, path=relpath, mode="bench")
        self.assertEqual(("planted-corpus",), bench.allowed_by)

    def test_planted_corpus_mode_scoping_through_the_cli(self):
        normal = support.run_cli("check", "fixtures/leak-corpus/planted")
        self.assertEqual(1, normal.returncode, normal.stderr)
        bench = support.run_json("check", "fixtures/leak-corpus/planted", "--mode", "bench", "--json")
        self.assertEqual(0, bench["_returncode"])
        self.assertEqual([], bench["findings"])
        self.assertGreater(bench["summary"]["allowed"], 80)

    def test_rules_file_may_spell_out_its_own_markers(self):
        markers = support.load_samples()["private-marker"]["flag"]
        text = "\n".join(markers) + "\n"
        allowed = leakgate.scan_text(text, self.config, path="tools/repo/leakgate/rules.json")
        self.assertTrue(allowed and all(not f.blocking for f in allowed))
        blocked = leakgate.scan_text(text, self.config, path="docs/rules.json")
        self.assertTrue(blocked and all(f.blocking for f in blocked))


class AllowEntrySemanticsTest(unittest.TestCase):
    def config_with(self, tmp, *entries, **extra):
        groups = [{"id": "codename", "description": "test", "terms": ["Zorblax", "QXJUNO"]}]
        rules = support.write_rules(tmp, terms=groups, allow=list(entries), **extra)
        return leakgate.load_config(rules, env={})

    def test_path_rule_and_match_scopes(self):
        with support.temp_dir() as tmp:
            config = self.config_with(tmp, {
                "id": "only-zorblax-in-docs", "reason": "test", "paths": ["docs/**"], "rules": ["codename"],
                "match": "(?i)zorblax",
            })
            findings = leakgate.scan_text("Zorblax QXJUNO\n", config, path="docs/a.md")
            self.assertEqual({"Zorblax": False, "QXJUNO": True}, {
                ("Zorblax" if f.col == 1 else "QXJUNO"): f.blocking for f in findings})
            (outside,) = [f for f in leakgate.scan_text("Zorblax\n", config, path="src/a.md")]
            self.assertTrue(outside.blocking)

    def test_types_and_rule_wildcards(self):
        with support.temp_dir() as tmp:
            config = self.config_with(
                tmp,
                {"id": "by-type", "reason": "test", "paths": ["a/**"], "types": ["term"]},
                {"id": "by-wildcard", "reason": "test", "paths": ["b/**"], "rules": ["code*"]},
            )
            self.assertFalse(leakgate.scan_text("Zorblax\n", config, path="a/x.md")[0].blocking)
            self.assertEqual(("by-wildcard",), leakgate.scan_text("Zorblax\n", config, path="b/x.md")[0].allowed_by)

    def test_path_rules_can_be_allowed(self):
        with support.temp_dir() as tmp:
            config = self.config_with(tmp, {"id": "env-example", "reason": "template without values", "paths": ["**/.env.example"], "rules": ["env-file"]}, extends=support.RULES)
            support.write_files(tmp, {"repo/.env.example": "KEY=\n", "repo/.env": "KEY=\n"})
            findings = leakgate.scan([os.path.join(tmp, "repo")], config, root=os.path.join(tmp, "repo"), include_allowed=True)
            self.assertEqual({".env.example": False, ".env": True}, {f.path: f.blocking for f in findings})

    def test_allowed_findings_are_kept_for_audit(self):
        with support.temp_dir() as tmp:
            config = self.config_with(tmp, {"id": "all-docs", "reason": "test", "paths": ["docs/**"], "rules": ["*"]})
            repo = os.path.join(tmp, "repo")  # keep the rules file itself out of the scan
            support.write_files(repo, {"docs/a.md": "Zorblax\n"})
            self.assertEqual([], leakgate.scan([repo], config, root=repo))
            (kept,) = leakgate.scan([repo], config, root=repo, include_allowed=True)
            self.assertEqual(("all-docs",), kept.allowed_by)
            shown = support.run_cli("check", repo, "--config", os.path.join(tmp, "rules.json"), "--root", repo, "--show-allowed")
            self.assertEqual(0, shown.returncode, shown.stdout + shown.stderr)
            self.assertIn("(allowed by all-docs)", shown.stdout)

    def test_invalid_entries_are_config_errors(self):
        bad_entries = [
            {"id": "no-reason", "paths": ["**"], "rules": ["codename"]},
            {"id": "no-paths", "reason": "x", "rules": ["codename"]},
            {"id": "no-rules", "reason": "x", "paths": ["**"]},
            {"id": "typo", "reason": "x", "paths": ["**"], "rules": ["codenme"]},
            {"id": "bad-type", "reason": "x", "paths": ["**"], "types": ["word"]},
            {"id": "bad-mode", "reason": "x", "paths": ["**"], "rules": ["codename"], "modes": ["ci"]},
            {"id": "no-modes", "reason": "x", "paths": ["**"], "rules": ["codename"], "modes": []},
            {"id": "bad-glob", "reason": "x", "paths": ["a\\b"], "rules": ["codename"]},
            {"id": "bad-match", "reason": "x", "paths": ["**"], "rules": ["codename"], "match": "("},
            {"id": "dead-wildcard", "reason": "x", "paths": ["**"], "rules": ["nothing-*"]},
        ]
        for entry in bad_entries:
            with self.subTest(entry=entry["id"]), support.temp_dir() as tmp:
                with self.assertRaises(ConfigError):
                    self.config_with(tmp, entry)


if __name__ == "__main__":
    unittest.main()
