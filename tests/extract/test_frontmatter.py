"""Old skill frontmatter -> the portable Agent Skills format."""

import json
import re
import unittest
from collections import OrderedDict

from .support import EXAMPLE_MANIFEST, FIXTURE, HANDLED_MANIFEST, TempDirTestCase, ex

PORTABLE = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
REQUIRED_METADATA = ("version", "status", "pack", "min-tier", "roles", "gate")


def parse(text):
    lines = [(i, line) for i, line in enumerate(text.split("\n"), 1)]
    return ex.parse_frontmatter(lines)


def read_portable(text):
    """Independent reader for the tool's output: the subset the portable format needs.

    Returns ``(top-level dict, metadata dict)`` and fails on anything else, so
    the tool can't emit a shape the skills linter wouldn't accept.
    """
    assert text.startswith("---\n"), "frontmatter must open on line 1"
    block, _, _ = text[4:].partition("\n---\n")
    top, metadata, in_metadata = OrderedDict(), OrderedDict(), False
    for line in block.split("\n"):
        if line == "metadata:":
            in_metadata = True
            top["metadata"] = metadata
            continue
        nested = re.fullmatch(r"  ([a-z0-9]+(?:-[a-z0-9]+)*): (\".*\")", line)
        if in_metadata and nested:
            metadata[nested.group(1)] = json.loads(nested.group(2))
            continue
        in_metadata = False
        match = re.fullmatch(r"([a-z-]+): (.*)", line)
        assert match, "unexpected frontmatter line: %r" % line
        value = match.group(2)
        top[match.group(1)] = json.loads(value) if value.startswith('"') else value
    return top, metadata


class Parser(unittest.TestCase):
    def test_scalars_lists_maps_and_comments(self):
        items, comments = parse(
            "# leading comment\n"
            "name: s\n"
            'description: "quoted: yes \\u00e9"\n'
            "single: 'it''s'\n"
            "plain: a: b # trailing\n"
            "version: 1.0\n"
            "roles: [ZEPHYR, \"QUILL\"]\n"
            "eval: { score: 9.4, method: blind-ab }\n"
            "tags:\n"
            "  - one\n"
            "  - 'two'\n"
            "nested:\n"
            "  key: value\n"
            "  list: [x, y]\n"
        )
        values = OrderedDict((item.key, item.value) for item in items)
        self.assertEqual(values["description"], "quoted: yes \u00e9")
        self.assertEqual(values["single"], "it's")
        self.assertEqual(values["plain"], "a: b")
        self.assertEqual(values["version"], "1.0")  # text, never a float
        self.assertEqual(values["roles"], ["ZEPHYR", "QUILL"])
        self.assertEqual(values["eval"], OrderedDict([("score", "9.4"), ("method", "blind-ab")]))
        self.assertEqual(values["tags"], ["one", "two"])
        self.assertEqual(values["nested"], OrderedDict([("key", "value"), ("list", ["x", "y"])]))
        self.assertEqual(comments, [1, 5])
        self.assertEqual([item.line_of(1) for item in items if item.key == "tags"], [11])

    def test_block_lists_may_sit_at_column_one(self):
        items, _ = parse("roles:\n- a\n- b\ngate: none\n")
        self.assertEqual([(i.key, i.value) for i in items], [("roles", ["a", "b"]), ("gate", "none")])

    def test_unsupported_yaml_is_an_error_not_a_guess(self):
        cases = {
            "description: >\n  folded\n": "block scalars",
            "description: |\n  literal\n": "block scalars",
            "a: &anchor x\n": "anchors",
            "a: !!str x\n": "tags",
            "a: x\na: y\n": "duplicate key",
            "a:\n  b:\n    c: d\n": "nesting deeper",
            "a: \"open\n": "close on the same line",
            "a: [x, y\n": "must close on the same line",
            "a: [x, [y]]\n": "nested flow",
            "  a: x\n": "unexpected indentation",
            "a:\n\t- x\n": "tabs",
            "a:\n  - x\n    - y\n": "share one indentation",
            "just text\n": "expected 'key: value'",
            "a: \"x\" y\n": "unexpected text after the value",
            "a: \"\\q\"\n": "unknown escape",
        }
        for text, fragment in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(ex.FrontmatterError) as caught:
                    parse(text)
                self.assertIn(fragment, caught.exception.message)

    def test_errors_carry_the_line(self):
        with self.assertRaises(ex.FrontmatterError) as caught:
            parse("name: s\ndescription: ok\nbad: |\n")
        self.assertEqual(caught.exception.line, 3)


class Conversion(TempDirTestCase):
    def convert(self, text, folder="demo", **frontmatter):
        manifest = {"frontmatter": frontmatter} if frontmatter else {}
        entry = {"rewrites": ["persona_map", "owner_terms", "frontmatter"]}
        result = self.transform(text, entry=entry, target="packs/p/skills/%s/SKILL.md" % folder, source="SKILLS/old.md", **manifest)
        return result, self.text(result)

    OLD = (
        "---\n"
        "name: old-name\n"
        "description: ZEPHYR closes the session for Avery. Use it at every session end.\n"
        "version: 1.2.0\n"
        "status: stable\n"
        "pack: ops\n"
        "min_tier: low\n"
        "roles: [ZEPHYR, QUILL]\n"
        "gate: mandatory\n"
        "allowed-tools: [Read, Grep]\n"
        "license: Apache-2.0\n"
        "eval: { score: 9.4, baseline: 4.2 }   # private note\n"
        "---\n"
        "\n"
        "# Body\n"
        "ZEPHYR runs it.\n"
    )

    def test_portable_shape(self):
        result, text = self.convert(self.OLD, drop_keys=["eval"])
        self.assertEqual(
            text,
            "---\n"
            "name: demo\n"
            'description: "Orchestrator closes the session for the owner. Use it at every session end."\n'
            'license: "Apache-2.0"\n'
            'allowed-tools: "Read Grep"\n'
            "metadata:\n"
            '  gate: "mandatory"\n'
            '  min-tier: "low"\n'
            '  pack: "ops"\n'
            '  roles: "orchestrator, researcher"\n'
            '  status: "experimental"\n'
            '  version: "1.2.0"\n'
            "---\n"
            "\n"
            "# Body\n"
            "Orchestrator runs it.\n",
        )
        self.assertEqual(result.flags, [])
        changes = [e.change for e in result.events if e.kind == "frontmatter"]
        self.assertIn("name `old-name` \u2192 `demo` (from the skill folder)", changes)
        self.assertIn("`eval` dropped", changes)
        self.assertIn("comment removed", changes)
        self.assertIn("`min_tier` \u2192 `metadata.min-tier`", changes)

    def test_nested_maps_flatten_and_values_stay_text(self):
        _, text = self.convert(self.OLD)
        top, metadata = read_portable(text)
        self.assertEqual(metadata["eval-score"], "9.4")
        self.assertEqual(metadata["eval-baseline"], "4.2")
        self.assertEqual(set(top), {"name", "description", "license", "allowed-tools", "metadata"})

    def test_rename_keys_and_separator(self):
        _, text = self.convert(self.OLD, rename_keys={"pack": "bundle"}, list_separator=",", drop_keys=["eval"])
        _, metadata = read_portable(text)
        self.assertEqual(metadata["bundle"], "ops")
        self.assertEqual(metadata["roles"], "orchestrator,researcher")

    def test_existing_metadata_map_is_merged(self):
        _, text = self.convert("---\ndescription: d\nmetadata:\n  owner_team: core\nstatus: stable\n---\n")
        _, metadata = read_portable(text)
        self.assertEqual(metadata, OrderedDict([("owner-team", "core"), ("status", "experimental")]))

    def test_description_problems_are_flagged(self):
        cases = {
            "---\nname: x\n---\n": "description is missing",
            "---\ndescription:\n---\n": "description is empty",
            "---\ndescription: use <b>bold</b>\n---\n": "holds < or >",
            "---\ndescription: %s\n---\n" % ("x" * 1025): "the limit is 1024",
            "---\ndescription: [a, b]\n---\n": "must be a single value",
        }
        for text, fragment in cases.items():
            with self.subTest(text=text[:40]):
                result, _ = self.convert(text)
                self.assertTrue(any(fragment in f.message for f in result.flags), result.flags)

    def test_key_and_role_problems_are_flagged(self):
        cases = {
            "---\ndescription: d\nmin_tier: low\nmin-tier: mid\n---\n": ("frontmatter-key", "collides"),
            "---\ndescription: d\nbad.key: x\n---\n": ("frontmatter-key", "isn't kebab-case"),
            "---\ndescription: d\nroles: [ZEPHYR, stranger]\n---\n": ("frontmatter-role", "'stranger'"),
            "---\ndescription: d\ncompatibility: %s\n---\n" % ("c" * 501): ("frontmatter-field", "limit is 500"),
            "---\ndescription: d\nmetadata: flat\n---\n": ("frontmatter-field", "metadata must be a map"),
        }
        for text, (flag, fragment) in cases.items():
            with self.subTest(text=text[:50]):
                result, _ = self.convert(text)
                self.assertTrue(any(f.flag == flag and fragment in f.message for f in result.flags), result.flags)

    def test_syntax_errors_and_missing_frontmatter_are_flagged(self):
        result, _ = self.convert("---\ndescription: >\n  folded\n---\n")
        self.assertEqual([(f.flag, f.line) for f in result.flags], [("frontmatter-syntax", 2)])
        result, _ = self.convert("# no frontmatter\n")
        self.assertEqual([f.flag for f in result.flags], ["frontmatter-missing"])

    def test_persona_names_in_keys_are_flagged(self):
        result, _ = self.convert("---\ndescription: d\nZEPHYR_only: yes\n---\n")
        self.assertIn("persona-name", [f.flag for f in result.flags])

    def test_yaml_quote_escapes(self):
        self.assertEqual(ex.yaml_quote('a "b" \\ c'), '"a \\"b\\" \\\\ c"')
        self.assertEqual(ex.yaml_quote("tab\tline\u2028"), '"tab\\tline\\u2028"')
        self.assertEqual(json.loads(ex.yaml_quote("plain \u00e9")), "plain \u00e9")


class FixtureSkills(TempDirTestCase):
    """Every converted fixture skill passes the portable-format rules."""

    def test_converted_skills_are_portable(self):
        for manifest_path in (EXAMPLE_MANIFEST, HANDLED_MANIFEST):
            loaded, errors = ex.load_manifest(manifest_path, FIXTURE)
            self.assertEqual(errors, [])
            compiled = ex.Compiled.build(loaded)
            skills = [e for e in loaded.entries if e.target.endswith("/SKILL.md")]
            self.assertEqual(len(skills), 6)
            for entry in skills:
                with self.subTest(manifest=manifest_path.name, skill=entry.target):
                    result = ex.transform_entry(compiled, entry, (FIXTURE / entry.source).read_bytes())
                    self.assertEqual([f for f in result.flags if f.flag.startswith("frontmatter")], [])
                    top, metadata = read_portable(result.data.decode("utf-8"))
                    folder = entry.target.split("/")[-2]
                    self.assertTrue(set(top) <= PORTABLE)
                    self.assertEqual(top["name"], folder)
                    self.assertTrue(0 < len(top["description"]) <= 1024)
                    for key in REQUIRED_METADATA:
                        self.assertIn(key, metadata)
                    self.assertTrue(all(isinstance(v, str) for v in metadata.values()))
                    self.assertIn(metadata["min-tier"], ("low", "mid", "high"))
                    roles = [r.strip() for r in metadata["roles"].split(",")]
                    self.assertTrue(set(roles) <= set(loaded.persona_map.values()))
                    if metadata["status"] == "retired":
                        self.assertIn("replaces", metadata)
                    self.assertNotIn("eval", metadata)


if __name__ == "__main__":
    unittest.main()
