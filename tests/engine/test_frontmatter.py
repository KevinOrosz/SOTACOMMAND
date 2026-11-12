"""The strict YAML-subset frontmatter parser used for role sources and skills."""

import glob
import os
import unittest

from . import support

from osgen import frontmatter
from osgen.frontmatter import FrontmatterError


def parse(*lines: str):
    return frontmatter.parse("---\n" + "\n".join(lines) + "\n---\nBody text\n")


class SupportedSyntaxTests(unittest.TestCase):
    def test_scalars_quotes_booleans_and_lists(self):
        fields, body = parse(
            "# a comment line",
            "id: builder",
            'description: "Quoted: with a colon and \\"escapes\\""',
            "tier: 'it''s mid'",
            "main: true",
            "flag: false",
            "allowed-tools: Agent(a, b), Read",
            'flow: [Read, "Two, items", \'single\']',
            "block:",
            "  - first",
            "  - 'second: quoted'",
            "",
            "empty-flow: []",
        )
        self.assertEqual(fields["id"], "builder")
        self.assertEqual(fields["description"], 'Quoted: with a colon and "escapes"')
        self.assertEqual(fields["tier"], "it's mid")
        self.assertIs(fields["main"], True)
        self.assertIs(fields["flag"], False)
        self.assertEqual(fields["allowed-tools"], "Agent(a, b), Read")
        self.assertEqual(fields["flow"], ["Read", "Two, items", "single"])
        self.assertEqual(fields["block"], ["first", "second: quoted"])
        self.assertEqual(fields["empty-flow"], [])
        self.assertEqual(body, "Body text\n")

    def test_bare_key_without_items_is_null(self):
        fields, _ = parse("isolation:", "tier: low")
        self.assertIsNone(fields["isolation"])
        self.assertEqual(fields["tier"], "low")

    def test_globs_and_mcp_wildcards_are_plain_scalars(self):
        fields, _ = parse("write-paths: [memory/**]", "deny: Bash, mcp__*")
        self.assertEqual(fields["write-paths"], ["memory/**"])
        self.assertEqual(fields["deny"], "Bash, mcp__*")

    def test_byte_order_mark_and_crlf_are_tolerated(self):
        fields, body = frontmatter.parse("﻿---\r\nid: x\r\n---\r\nBody\r\n")
        self.assertEqual(fields, {"id": "x"})
        self.assertIn("Body", body)


class NestedMapTests(unittest.TestCase):
    """One level of map, as a skill's metadata block uses; opt-in only."""

    SKILL = (
        "---\n"
        "name: csv-summary\n"
        "description: Summarize a CSV file.\n"
        "license: Apache-2.0\n"
        "metadata:\n"
        '  version: "0.1.0"\n'
        "  status: experimental\n"
        "  min-tier: low\n"
        "  roles: builder, researcher\n"
        "allowed-tools: Read\n"
        "---\n\nBody\n"
    )

    def test_metadata_block_parses_when_enabled(self):
        fields, body = frontmatter.parse(self.SKILL, nested_maps=True)
        self.assertEqual(
            fields["metadata"],
            {"version": "0.1.0", "status": "experimental", "min-tier": "low", "roles": "builder, researcher"},
        )
        self.assertEqual(fields["allowed-tools"], "Read")
        self.assertEqual(body.strip(), "Body")

    def test_maps_stay_rejected_by_default(self):
        with self.assertRaises(FrontmatterError):
            frontmatter.parse(self.SKILL)

    def test_maps_nest_one_level_only(self):
        with self.assertRaises(FrontmatterError) as caught:
            frontmatter.parse("---\nmetadata:\n  inner:\n    deep: x\n---\n", nested_maps=True)
        self.assertIn("one level", str(caught.exception))

    def test_lists_and_maps_do_not_mix(self):
        for block in ("metadata:\n  a: b\n  - c", "metadata:\n  - c\n  a: b"):
            with self.subTest(block=block), self.assertRaises(FrontmatterError):
                frontmatter.parse("---\n%s\n---\n" % block, nested_maps=True)

    def test_duplicate_map_keys(self):
        with self.assertRaises(FrontmatterError):
            frontmatter.parse("---\nmetadata:\n  a: b\n  a: c\n---\n", nested_maps=True)

    def test_committed_skills_parse(self):
        found = 0
        for group in ("packs", "labs"):
            base = os.path.join(support.REPO_ROOT, group)
            for path in sorted(glob.glob(os.path.join(base, "*", "skills", "*", "SKILL.md"))):
                found += 1
                with self.subTest(skill=os.path.relpath(path, support.REPO_ROOT)):
                    fields, _ = frontmatter.parse(support.read(path), nested_maps=True)
                    self.assertIsInstance(fields.get("metadata"), dict)
        if not found:
            self.skipTest("no committed skills yet")


class RejectedSyntaxTests(unittest.TestCase):
    def assertRejected(self, *lines: str, contains: str = ""):
        with self.assertRaises(FrontmatterError) as caught:
            parse(*lines)
        if contains:
            self.assertIn(contains, str(caught.exception))

    def test_missing_opening_marker(self):
        with self.assertRaises(FrontmatterError):
            frontmatter.parse("id: x\n---\n")

    def test_missing_closing_marker(self):
        with self.assertRaises(FrontmatterError):
            frontmatter.parse("---\nid: x\n")

    def test_duplicate_key(self):
        self.assertRejected("id: a", "id: b", contains="duplicate")

    def test_nested_map(self):
        self.assertRejected("metadata:", "  key: value", contains="nested")

    def test_tabs(self):
        self.assertRejected("id:\tx", contains="tabs")

    def test_plain_value_with_colon_space(self):
        self.assertRejected("description: Use when: always", contains="quote")

    def test_trailing_comment(self):
        self.assertRejected("tier: low # cheap", contains="comment")

    def test_indicator_characters(self):
        for value in ("&anchor", "*alias", "|", ">", "!tag", "@x", "{a: b}"):
            with self.subTest(value=value):
                self.assertRejected("key: %s" % value)

    def test_ambiguous_yaml_words(self):
        for value in ("yes", "no", "on", "off", "null", "True"):
            with self.subTest(value=value):
                self.assertRejected("main: %s" % value, contains="ambiguous")

    def test_uppercase_or_snake_case_keys(self):
        self.assertRejected("Tier: low")
        self.assertRejected("allowed_tools: Read")

    def test_unterminated_and_trailing_quotes(self):
        self.assertRejected('description: "open')
        self.assertRejected('description: "closed" extra')
        self.assertRejected("description: 'open")

    def test_list_item_without_key(self):
        self.assertRejected("  - orphan", contains="list item")

    def test_nested_flow_collections(self):
        self.assertRejected("tools: [[Read], Grep]")
        self.assertRejected("tools: [Read, Grep")


if __name__ == "__main__":
    unittest.main()
