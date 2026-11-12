"""Manifest validation: every error is caught upfront and reported together."""

import json
import os
import unittest

from .support import EXAMPLE_MANIFEST, FIXTURE, HANDLED_MANIFEST, TempDirTestCase, base_manifest, ex, tilde_home


class ValidManifests(TempDirTestCase):
    def test_example_manifests_load(self):
        for path in (EXAMPLE_MANIFEST, HANDLED_MANIFEST):
            loaded, errors = ex.load_manifest(path, FIXTURE)
            self.assertEqual(errors, [], path.name)
            self.assertEqual(len(loaded.entries), 34)

    def test_minimal_manifest_and_defaults(self):
        root = self.make_source({"a.md": "hello\n"})
        loaded = self.assert_valid(base_manifest(files=[{"from": "a.md", "to": "docs/a.md"}]), root)
        entry = loaded.entries[0]
        self.assertEqual(entry.rewrites, ex.DEFAULT_REWRITES)
        self.assertEqual(len(loaded.sha256), 64)

    def test_rewrites_are_kept_in_application_order(self):
        root = self.make_source({"a.md": "x\n"})
        item = {"from": "a.md", "to": "a.md", "rewrites": ["persona_map", "drop_patterns", "owner_terms"]}
        loaded = self.assert_valid(base_manifest(files=[item]), root)
        self.assertEqual(loaded.entries[0].rewrites, ("drop_patterns", "owner_terms", "persona_map"))

    def test_defaults_rewrites_apply_to_entries_without_their_own(self):
        root = self.make_source({"a.md": "x\n", "b.md": "y\n"})
        manifest = base_manifest(
            defaults={"rewrites": ["owner_terms"]},
            files=[{"from": "a.md", "to": "a.md"}, {"from": "b.md", "to": "b.md", "rewrites": []}],
        )
        loaded = self.assert_valid(manifest, root)
        self.assertEqual([e.rewrites for e in loaded.entries], [("owner_terms",), ()])


class JsonErrors(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.make_source({"a.md": "x\n"})

    def load_text(self, text):
        path = self.tmp / "m.json"
        path.write_text(text, encoding="utf-8")
        return ex.load_manifest(path, self.root)

    def test_invalid_json_reports_line_and_column(self):
        loaded, errors = self.load_text('{\n  "schema_version": "1",\n  "files": [\n}')
        self.assertIsNone(loaded)
        self.assertRegex(errors[0], r"invalid JSON at line 4 column \d+")

    def test_duplicate_keys_are_rejected(self):
        loaded, errors = self.load_text('{"schema_version": "1", "persona_map": {"ZEPHYR": "a", "ZEPHYR": "b"}, "files": []}')
        self.assertIsNone(loaded)
        self.assertIn("duplicate key 'ZEPHYR'", errors[0])

    def test_top_level_must_be_an_object(self):
        loaded, errors = self.load_text("[]")
        self.assertIn("top level must be a JSON object", errors[0])

    def test_missing_manifest_file(self):
        loaded, errors = ex.load_manifest(self.tmp / "absent.json", self.root)
        self.assertIsNone(loaded)
        self.assertIn("can't read", errors[0])


class StructureErrors(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.make_source({"a.md": "x\n", "docs/b.md": "y\n", "KB/notes.md": "z\n", "bin.dat": b"\x00\x01"})

    def entry(self, **fields):
        item = {"from": "a.md", "to": "out/a.md"}
        item.update(fields)
        return base_manifest(files=[item])

    def test_schema_version_is_required_and_exact(self):
        manifest = base_manifest(files=[{"from": "a.md", "to": "a.md"}])
        del manifest["schema_version"]
        self.assert_error(manifest, self.root, "missing required key 'schema_version'")
        manifest["schema_version"] = 1
        self.assert_error(manifest, self.root, "schema_version: must be the string '1'")

    def test_unknown_keys_are_errors_not_ignored(self):
        manifest = base_manifest(files=[{"from": "a.md", "to": "a.md", "rewites": []}], extra=True)
        self.assert_error(manifest, self.root, "unknown key 'extra'", "unknown key 'rewites'")

    def test_files_must_be_present_and_non_empty(self):
        manifest = base_manifest()
        del manifest["files"]
        self.assert_error(manifest, self.root, "missing required key 'files'")
        self.assert_error(base_manifest(files=[]), self.root, "files: lists no files")

    def test_from_must_exist(self):
        self.assert_error(self.entry(**{"from": "missing.md"}), self.root, "'missing.md' does not exist in the source")

    def test_from_must_match_letter_case_on_disk(self):
        self.assert_error(self.entry(**{"from": "kb/notes.md"}), self.root, "differs in letter case")

    def test_from_must_be_a_regular_file(self):
        self.assert_error(self.entry(**{"from": "docs"}), self.root, "'docs' is not a regular file")

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_from_symlink_escaping_the_source_is_rejected(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        try:
            os.symlink(str(outside), str(self.root / "link.md"))
        except OSError:
            self.skipTest("symlinks not permitted here")
        self.assert_error(self.entry(**{"from": "link.md"}), self.root, "resolves outside the source")

    def test_dotdot_and_absolute_paths_are_rejected(self):
        self.assert_error(self.entry(**{"from": "../a.md"}), self.root, "must not contain '..'")
        self.assert_error(self.entry(to="docs/../../escape.md"), self.root, "must not contain '..'")
        self.assert_error(self.entry(to="/abs/a.md"), self.root, "must be relative")
        self.assert_error(self.entry(to="C:/a.md"), self.root, "must be relative")

    def test_malformed_paths_are_rejected(self):
        self.assert_error(self.entry(to="docs\\a.md"), self.root, "use '/' as the separator")
        self.assert_error(self.entry(to="docs//a.md"), self.root, "empty segment")
        self.assert_error(self.entry(to="docs/./a.md"), self.root, "'.' segments")
        self.assert_error(self.entry(to="docs/ a.md"), self.root, "whitespace")
        self.assert_error(self.entry(to="docs/a\tb.md"), self.root, "control characters")

    def test_target_inside_internal_or_git_is_rejected(self):
        self.assert_error(self.entry(to="internal/a.md"), self.root, "inside internal/")
        self.assert_error(self.entry(to="Internal/a.md"), self.root, "inside internal/")
        self.assert_error(self.entry(to="docs/.git/hooks/pre-commit"), self.root, "must not be inside .git")

    def test_targets_are_unique_ignoring_case(self):
        manifest = base_manifest(files=[{"from": "a.md", "to": "docs/A.md"}, {"from": "docs/b.md", "to": "docs/a.md"}])
        self.assert_error(manifest, self.root, "duplicates the target of files[0]")

    def test_a_target_cannot_also_be_a_folder(self):
        manifest = base_manifest(files=[{"from": "a.md", "to": "docs"}, {"from": "docs/b.md", "to": "docs/b.md"}])
        self.assert_error(manifest, self.root, "is also a folder")

    def test_the_same_source_may_feed_several_targets(self):
        manifest = base_manifest(files=[{"from": "a.md", "to": "one.md"}, {"from": "a.md", "to": "two.md"}])
        self.assert_valid(manifest, self.root)

    def test_never_export_blocks_a_listed_source(self):
        manifest = self.entry(**{"from": "KB/notes.md"})
        manifest["never_export"] = ["**/kb/**"]
        self.assert_error(manifest, self.root, "matches never_export pattern '**/kb/**'")

    def test_never_export_name_patterns_match_at_any_depth(self):
        regex = ex.glob_regex("fixture-*.md")
        self.assertTrue(regex.fullmatch("fixture-owner.md"))
        self.assertTrue(regex.fullmatch("deep/dir/FIXTURE-x.md"))
        self.assertFalse(regex.fullmatch("fixture-x.txt"))
        self.assertTrue(ex.glob_regex("KB/").fullmatch("KB/a/b.md"))
        self.assertFalse(ex.glob_regex("KB/**").fullmatch("docs/KB/a.md"))
        self.assertTrue(ex.glob_regex("**/KB/**").fullmatch("docs/KB/a.md"))

    def test_unknown_and_repeated_rewrite_names(self):
        self.assert_error(self.entry(rewrites=["persona_map", "persona-map"]), self.root, "unknown rewrite 'persona-map'")
        self.assert_error(self.entry(rewrites=["owner_terms", "owner_terms"]), self.root, "'owner_terms' is listed twice")

    def test_every_error_is_reported_at_once(self):
        manifest = base_manifest(
            files=[
                {"from": "missing.md", "to": "internal/x.md"},
                {"from": "a.md", "to": "a.md", "rewrites": ["nope"]},
            ],
            rewrite_patterns=[{"id": "bad", "pattern": "(", "replace": ""}],
        )
        errors = self.assert_error(manifest, self.root, "does not exist", "inside internal/", "unknown rewrite", "invalid regular expression")
        self.assertGreaterEqual(len(errors), 4)

    def test_entry_errors_name_the_target(self):
        errors = self.assert_error(self.entry(**{"from": "missing.md"}), self.root)
        self.assertIn("files[0].from (to out/a.md)", errors[0])


class TermAndPathMaps(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.make_source({"a.md": "x\n"})
        self.files = [{"from": "a.md", "to": "a.md"}]

    def test_persona_values_must_be_role_ids(self):
        self.assert_error(base_manifest(persona_map={"ZEPHYR": "The Orchestrator"}, files=self.files), self.root, "lowercase kebab-case")

    def test_terms_must_be_trimmed_single_lines(self):
        self.assert_error(base_manifest(owner_terms={" Avery": "the owner"}, files=self.files), self.root, "without surrounding spaces")
        self.assert_error(base_manifest(owner_terms={"": "x"}, files=self.files), self.root, "non-empty")

    def test_a_term_cannot_be_both_persona_and_owner(self):
        manifest = base_manifest(persona_map={"AVERY": "orchestrator"}, files=self.files)
        self.assert_error(manifest, self.root, "is also a persona_map term")

    def test_home_keys_use_the_tilde_slash_form(self):
        self.assert_error(base_manifest(path_map={tilde_home("repo", ""): ""}, files=self.files), self.root, "a home key starts with '~/'")


class PatternErrors(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.make_source({"a.md": "x\n"})
        self.files = [{"from": "a.md", "to": "a.md"}]

    def check(self, fragment, **sections):
        self.assert_error(base_manifest(files=self.files, **sections), self.root, fragment)

    def test_invalid_regex(self):
        self.check("invalid regular expression", flag_patterns=[{"id": "x", "pattern": "a(b"}])

    def test_patterns_that_match_empty_text(self):
        self.check("can match an empty string", flag_patterns=[{"id": "x", "pattern": "a*"}])
        self.check("can match an empty string", rewrite_patterns=[{"id": "x", "pattern": "\\b", "replace": "!"}])

    def test_drop_patterns_that_match_every_line(self):
        self.check("matches every line", drop_patterns=[{"id": "x", "pattern": "."}])
        self.check("matches every line", drop_patterns=[{"id": "x", "start": "^", "end": "x"}])

    def test_inline_flags_must_come_first(self):
        self.check("must come first", flag_patterns=[{"id": "x", "pattern": "abc(?i)def"}])
        self.assert_valid(base_manifest(files=self.files, flag_patterns=[{"id": "x", "pattern": "(?i)abc"}]), self.root)

    def test_replacement_templates_are_checked(self):
        self.check("invalid replacement", rewrite_patterns=[{"id": "x", "pattern": "(a)", "replace": "\\2"}])
        self.check("invalid replacement", rewrite_patterns=[{"id": "x", "pattern": "(a)", "replace": "\\g<name>"}])
        self.check("must not insert line breaks", rewrite_patterns=[{"id": "x", "pattern": "a", "replace": "b\\nc"}])

    def test_rule_ids(self):
        self.check("duplicate id 'x'", flag_patterns=[{"id": "x", "pattern": "a"}, {"id": "x", "pattern": "b"}])
        self.check("must use lowercase", flag_patterns=[{"id": "Bad Id", "pattern": "a"}])
        self.check("is a built-in flag id", flag_patterns=[{"id": "home-path", "pattern": "a"}])

    def test_drop_rule_shapes(self):
        self.check("set either 'pattern'", drop_patterns=[{"id": "x"}])
        self.check("set either 'pattern'", drop_patterns=[{"id": "x", "pattern": "a", "start": "b", "end": "c"}])
        self.check("exactly one of 'end'", drop_patterns=[{"id": "x", "start": "a"}])
        self.check("exactly one of 'end'", drop_patterns=[{"id": "x", "start": "a", "end": "b", "before": "c"}])
        self.check("only go with 'start'", drop_patterns=[{"id": "x", "pattern": "a", "end": "b"}])
        self.check("missing required key 'id'", drop_patterns=[{"pattern": "a"}])

    def test_frontmatter_section(self):
        self.check("must be a kebab-case key", frontmatter={"rename_keys": {"old": "New Key"}})
        self.check("unknown key 'drop'", frontmatter={"drop": []})


class EntryRules(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.make_source({"a.md": "x\n", "SKILLS/s.md": "---\nname: s\n---\n", "img.png": b"\x89PNG\x00"})

    def test_skill_targets_must_convert_frontmatter(self):
        item = {"from": "SKILLS/s.md", "to": "packs/p/skills/s/SKILL.md"}
        self.assert_error(base_manifest(files=[item]), self.root, "must list the 'frontmatter' rewrite")
        item["rewrites"] = ["frontmatter"]
        self.assert_valid(base_manifest(files=[item]), self.root)

    def test_frontmatter_needs_a_skill_target(self):
        item = {"from": "SKILLS/s.md", "to": "docs/s.md", "rewrites": ["frontmatter"]}
        self.assert_error(base_manifest(files=[item]), self.root, "only applies to a .../<skill-name>/SKILL.md target")

    def test_skill_folder_names_follow_the_portable_rules(self):
        for folder, fragment in (("Bad_Name", "lowercase letters"), ("a--b", "single hyphens"), ("x" * 65, "at most 64"), ("claude-helper", "must not contain")):
            item = {"from": "SKILLS/s.md", "to": "packs/p/skills/%s/SKILL.md" % folder, "rewrites": ["frontmatter"]}
            self.assert_error(base_manifest(files=[item]), self.root, fragment)

    def test_binary_entries_take_no_rules(self):
        item = {"from": "img.png", "to": "img.png", "binary": True, "rewrites": ["owner_terms"]}
        self.assert_error(base_manifest(files=[item]), self.root, "a binary entry is copied verbatim")
        self.assert_valid(base_manifest(files=[{"from": "img.png", "to": "img.png", "binary": True}]), self.root)

    def test_select_shapes(self):
        item = {"from": "a.md", "to": "a.md", "select": {"start": "^x"}}
        self.assert_error(base_manifest(files=[item]), self.root, "needs exactly one of 'end'")
        item = {"from": "SKILLS/s.md", "to": "packs/p/skills/s/SKILL.md", "rewrites": ["frontmatter"], "select": {"start": "^x", "end": "y"}}
        self.assert_error(base_manifest(files=[item]), self.root, "'select' can't be combined")

    def test_replace_rule_shapes(self):
        item = {"from": "a.md", "to": "a.md", "replace": [{"find": "", "with": "x"}]}
        self.assert_error(base_manifest(files=[item]), self.root, "must not be empty")
        item = {"from": "a.md", "to": "a.md", "replace": [{"find": "a\nb", "with": "x"}]}
        self.assert_error(base_manifest(files=[item]), self.root, "must be a single line")
        item = {"from": "a.md", "to": "a.md", "replace": [{"find": "a", "with": "x", "regex": "yes"}]}
        self.assert_error(base_manifest(files=[item]), self.root, "must be true or false")


class PathChecks(TempDirTestCase):
    """--source, --manifest, --out and --report are checked before anything runs."""

    def setUp(self):
        super().setUp()
        self.root = self.make_source({"a.md": "x\n"})
        self.manifest = self.write_manifest(base_manifest(files=[{"from": "a.md", "to": "a.md"}]))

    def options(self, **fields):
        values = dict(source=str(self.root), manifest=str(self.manifest), out=str(self.tmp / "stage"))
        values.update(fields)
        return ex.Options(**values)

    def test_out_must_not_hold_files(self):
        (self.tmp / "stage").mkdir()
        (self.tmp / "stage" / "old.md").write_text("old\n", encoding="utf-8")
        self.assertIn("already exists and is not empty", "\n".join(ex.check_paths(self.options())))

    def test_out_may_be_an_empty_folder(self):
        (self.tmp / "stage").mkdir()
        self.assertEqual(ex.check_paths(self.options()), [])

    def test_out_and_source_must_not_nest(self):
        self.assertIn("--out must not be inside --source", ex.check_paths(self.options(out=str(self.root / "stage"))))
        self.assertIn("--source must not be inside --out", ex.check_paths(self.options(out=str(self.tmp))))

    def test_report_stays_out_of_out_and_source(self):
        errors = ex.check_paths(self.options(report=str(self.tmp / "stage" / "report.md")))
        self.assertIn("--report must not be inside --out (the report quotes private text)", errors)
        errors = ex.check_paths(self.options(report=str(self.root / "report.md")))
        self.assertIn("--report must not be inside --source", errors)

    def test_missing_source_and_manifest(self):
        errors = ex.check_paths(self.options(source=str(self.tmp / "nope"), manifest=str(self.tmp / "nope.json")))
        self.assertTrue(any("is not a directory" in e for e in errors))
        self.assertTrue(any("is not a file" in e for e in errors))

    def test_usage_errors_exit_2_and_write_nothing(self):
        outcome = ex.run_extraction(self.options(source=str(self.tmp / "nope")))
        self.assertEqual(outcome.exit_code, ex.EXIT_USAGE)
        self.assertFalse((self.tmp / "stage").exists())
        self.assertIn("## Errors", ex.render_report(outcome))


if __name__ == "__main__":
    unittest.main()


class RolesAndPluginsSections(TempDirTestCase):
    """The manifest writes role frontmatter and plugin manifests."""

    def setUp(self):
        super().setUp()
        self.root = self.make_source({"soul.md": "# Soul\n", "skill.md": "---\ndescription: d\n---\n# S\n"})

    def manifest(self, **overrides):
        return base_manifest(**overrides)

    def test_role_target_needs_a_roles_entry(self):
        files = [{"from": "soul.md", "to": "engine/roles/orchestrator/soul.md"}]
        self.assert_error(self.manifest(files=files), self.root, "needs an entry in the roles section")

    def test_roles_entry_needs_a_target(self):
        files = [{"from": "soul.md", "to": "docs/soul.md"}]
        roles = {"orchestrator": {"description": "d", "tier": "high"}}
        self.assert_error(self.manifest(files=files, roles=roles), self.root, "no files entry targets engine/roles/orchestrator/soul.md")

    def test_tier_must_not_be_a_model_name(self):
        files = [{"from": "soul.md", "to": "engine/roles/orchestrator/soul.md"}]
        roles = {"orchestrator": {"description": "d", "tier": "big-model"}}
        self.assert_error(self.manifest(files=files, roles=roles), self.root, "never a model name")

    def test_role_frontmatter_is_written(self):
        files = [{"from": "soul.md", "to": "engine/roles/orchestrator/soul.md"}]
        roles = {"orchestrator": {"description": "Routes work.", "tier": "high", "main": True, "write-paths": ["memory/**"]}}
        loaded = self.assert_valid(self.manifest(files=files, roles=roles), self.root)
        compiled = ex.Compiled.build(loaded)
        result = ex.transform_entry(compiled, loaded.entries[0], b"# Soul\n")
        self.assertEqual(
            result.data.decode("utf-8"),
            '---\nid: orchestrator\ndescription: "Routes work."\ntier: high\nmain: true\nwrite-paths: ["memory/**"]\n---\n\n# Soul\n',
        )

    def test_plugin_folder_shape_and_skills(self):
        files = [{"from": "skill.md", "to": "labs/ops/skills/close/SKILL.md", "rewrites": ["frontmatter"]}]
        self.assert_error(self.manifest(files=files, plugins={"elsewhere/ops": {"name": "ops", "description": "d"}}), self.root, "packs/<name> or labs/<name>")
        self.assert_error(self.manifest(files=files, plugins={"labs/other": {"name": "other", "description": "d"}}), self.root, "no files entry puts a skill under labs/other/skills/")
        self.assert_error(self.manifest(files=files, plugins={"labs/ops": {"name": "claude-ops", "description": "d"}}), self.root, "kebab-case without")
        self.assert_error(self.manifest(files=files, plugins={"labs/ops": {"name": "ops", "description": "d", "version": "1"}}), self.root, "semver")
        loaded = self.assert_valid(self.manifest(files=files, plugins={"labs/ops": {"name": "ops", "description": "Ops."}}), self.root)
        generated = ex.generated_files(loaded)
        self.assertEqual([g.entry.target for g in generated], ["labs/ops/.claude-plugin/plugin.json", "labs/ops/README.md"])
        plugin = json.loads(generated[0].data.decode("utf-8"))
        self.assertEqual(plugin, {"name": "ops", "version": "0.1.0", "description": "Ops.", "author": {"name": "SOTACOMMAND"}, "license": "Apache-2.0"})
        self.assertIn("- `close`", generated[1].data.decode("utf-8"))

    def test_generated_file_cannot_also_be_a_files_entry(self):
        files = [{"from": "skill.md", "to": "labs/ops/skills/close/SKILL.md", "rewrites": ["frontmatter"]}, {"from": "soul.md", "to": "labs/ops/README.md"}]
        self.assert_error(self.manifest(files=files, plugins={"labs/ops": {"name": "ops", "description": "d"}}), self.root, "is generated")
