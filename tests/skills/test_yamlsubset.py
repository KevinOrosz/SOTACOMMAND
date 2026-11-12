"""The strict YAML subset: what it reads, how it resolves scalars, and what it rejects."""

import math
import unittest

from . import helpers  # noqa: F401  (puts the tool on sys.path)

from skillslib.yamlsubset import (  # noqa: E402
    FrontmatterError,
    Map,
    Scalar,
    Seq,
    YamlSubsetError,
    parse_document,
    split_frontmatter,
    to_python,
)


def parse(text: str):
    return to_python(parse_document(text))


class ScalarTests(unittest.TestCase):
    def test_plain_scalars_resolve_like_yaml_12_core(self) -> None:
        doc = parse(
            "a: hello world\nb: 42\nc: -7\nd: 0x1F\ne: 0o17\nf: 1.5\ng: .5\nh: 1e3\ni: true\nj: False\n"
            "k: null\nl: ~\nm:\nn: .inf\no: -.Inf\n"
        )
        self.assertEqual(doc["a"], "hello world")
        self.assertEqual((doc["b"], doc["c"], doc["d"], doc["e"]), (42, -7, 31, 15))
        self.assertEqual((doc["f"], doc["g"], doc["h"]), (1.5, 0.5, 1000.0))
        self.assertEqual((doc["i"], doc["j"]), (True, False))
        self.assertEqual((doc["k"], doc["l"], doc["m"]), (None, None, None))
        self.assertEqual((doc["n"], doc["o"]), (math.inf, -math.inf))
        self.assertTrue(math.isnan(parse("x: .nan\n")["x"]))

    def test_yaml_11_readings_are_recorded(self) -> None:
        cases = {
            "yes": "bool",
            "off": "bool",
            "Y": "bool",
            "2026-11-05": "timestamp",
            "1_000": "int",
            "010": "int",
            "1:30": "int",
            "1.0": "float",
            "1.0.0": "str",
            "builder, orchestrator": "str",
            "<<": "special",
        }
        for text, tag11 in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_document("k: {}\n".format(text)).items["k"].tag11, tag11)
        self.assertEqual(parse_document("k: '2026-11-05'\n").items["k"].tag11, "str")
        self.assertTrue(parse_document("k: 1.0.0\n").items["k"].is_portable_string)
        self.assertFalse(parse_document("k: yes\n").items["k"].is_portable_string)
        self.assertEqual(parse_document("k: yes\n").items["k"].tag, "str")

    def test_quoted_scalars(self) -> None:
        doc = parse_document(
            "a: 'it''s here'\n"
            'b: "tab\\tnew\\nquote\\" slash\\\\ uni\\u00e9 \\x41"\n'
            "c: '\"skill\"\\s*:\\s*\"x\"'\n"
            "d: \"1.0\"\n"
            "e: '#not a comment'   # but this is\n"
        )
        values = to_python(doc)
        self.assertEqual(values["a"], "it's here")
        self.assertEqual(values["b"], 'tab\tnew\nquote" slash\\ uni\u00e9 A')
        self.assertEqual(values["c"], '"skill"\\s*:\\s*"x"')
        self.assertEqual(values["d"], "1.0")
        self.assertEqual(values["e"], "#not a comment")
        for key in "abcde":
            self.assertTrue(doc.items[key].is_portable_string)
        self.assertEqual(doc.items["a"].style, "single")
        self.assertEqual(doc.items["b"].style, "double")

    def test_comments_and_hashes(self) -> None:
        doc = parse("# leading comment\na: value # trailing\nb: C#sharp\n\n  # indented comment\nc: x\n")
        self.assertEqual(doc, {"a": "value", "b": "C#sharp", "c": "x"})

    def test_urls_and_colons_without_spaces_stay_plain(self) -> None:
        doc = parse("a: https://example.com/x?y=1\nb: 12:30\nc: Bash(git status:*)\n")
        self.assertEqual(doc["a"], "https://example.com/x?y=1")
        self.assertEqual(doc["c"], "Bash(git status:*)")


class CollectionTests(unittest.TestCase):
    def test_flow_and_block_lists(self) -> None:
        doc = parse(
            "a: [Read, 'Write', \"Edit\", 3]\nb: []\nc:\n  - one\n  - two  # comment\nd:\n- x\n- y\ne: [Bash(npm test *), Read,]\n"
        )
        self.assertEqual(doc["a"], ["Read", "Write", "Edit", 3])
        self.assertEqual(doc["b"], [])
        self.assertEqual(doc["c"], ["one", "two"])
        self.assertEqual(doc["d"], ["x", "y"])
        self.assertEqual(doc["e"], ["Bash(npm test *)", "Read"])

    def test_nested_block_map_and_flow_map(self) -> None:
        root = parse_document(
            "metadata:\n  version: \"1.0.0\"\n  status: stable\n  empty:\ntarget: {source: file, path: out.md}\nnext: 1\n"
        )
        self.assertIsInstance(root.items["metadata"], Map)
        self.assertEqual(root.items["metadata"].style, "block")
        self.assertEqual(root.items["target"].style, "flow")
        self.assertEqual(
            to_python(root),
            {
                "metadata": {"version": "1.0.0", "status": "stable", "empty": None},
                "target": {"source": "file", "path": "out.md"},
                "next": 1,
            },
        )
        self.assertEqual(root.key_lines["next"], 6)
        self.assertEqual(root.items["metadata"].key_lines["status"], 3)

    def test_node_types(self) -> None:
        root = parse_document("a: [x]\nb: x\n")
        self.assertIsInstance(root.items["a"], Seq)
        self.assertIsInstance(root.items["b"], Scalar)

    def test_first_line_offsets_error_lines(self) -> None:
        with self.assertRaises(YamlSubsetError) as caught:
            parse_document("a: 1\nb: |\n", first_line=10)
        self.assertEqual(caught.exception.line, 11)


class RejectionTests(unittest.TestCase):
    REJECTED = {
        "plain value with colon-space": "a: b: c\n",
        "plain value ending in colon": "a: b:\n",
        "block scalar literal": "a: |\n  text\n",
        "block scalar folded": "a: >-\n  text\n",
        "anchor": "a: &x 1\n",
        "alias": "a: *x\n",
        "tag": "a: !!str 1\n",
        "multi-line plain scalar": "a: one\n  two\n",
        "unclosed double quote": 'a: "open\n',
        "unclosed single quote": "a: 'open\n",
        "line continuation escape": 'a: "x\\\n',
        "unknown escape": 'a: "\\q"\n',
        "bad unicode escape": 'a: "\\u12"\n',
        "surrogate escape": 'a: "\\ud800"\n',
        "text after quoted value": "a: 'x' y\n",
        "unclosed flow list": "a: [1, 2\n",
        "unclosed flow map": "a: {b: 1\n",
        "nested flow collection": "a: [[1]]\n",
        "empty flow entry": "a: [1, , 2]\n",
        "deep nesting": "a:\n  b:\n    c: 1\n",
        "list of maps": "a:\n  - b: c\n",
        "nested list": "a:\n  - - b\n",
        "empty list item": "a:\n  -\n",
        "flow list inside a block list": "a:\n  - [b]\n",
        "duplicate key": "a: 1\na: 2\n",
        "duplicate nested key": "m:\n  a: 1\n  a: 2\n",
        "duplicate flow key": "a: {b: 1, b: 2}\n",
        "quoted key": '"a": 1\n',
        "complex key": "? a\n",
        "missing space after colon": "a:b\n",
        "top-level list": "- a\n",
        "tab indentation": "a:\n\tb: 1\n",
        "document marker": "a: 1\n---\nb: 2\n",
        "directive": "%YAML 1.2\na: 1\n",
        "indented first line": "  a: 1\n",
        "list item on the key line": "a: - b\n",
        "inconsistent nested indentation": "m:\n  a: 1\n    b: 2\n",
    }

    def test_everything_outside_the_subset_is_an_error(self) -> None:
        for label, text in self.REJECTED.items():
            with self.subTest(label):
                with self.assertRaises(YamlSubsetError) as caught:
                    parse_document(text)
                self.assertGreaterEqual(caught.exception.line, 1)
                self.assertIn("line {}".format(caught.exception.line), str(caught.exception))


class FrontmatterTests(unittest.TestCase):
    def test_split(self) -> None:
        parts = split_frontmatter("---\nname: x\n---\n\n# Body\n")
        self.assertEqual(parts.yaml_text, "name: x")
        self.assertEqual(parts.body, "\n# Body\n")
        self.assertEqual((parts.yaml_first_line, parts.body_first_line), (2, 4))
        self.assertFalse(parts.has_bom or parts.has_crlf)

    def test_crlf_and_bom_are_reported_not_hidden(self) -> None:
        parts = split_frontmatter("\ufeff---\r\nname: x\r\n---\r\nbody\r\n")
        self.assertTrue(parts.has_bom)
        self.assertTrue(parts.has_crlf)
        self.assertEqual(parts.yaml_text, "name: x")

    def test_missing_or_unclosed_frontmatter(self) -> None:
        for text in ("# Title\n", "", "---\nname: x\n", "text\n---\nname: x\n---\n"):
            with self.subTest(text=text):
                with self.assertRaises(FrontmatterError):
                    split_frontmatter(text)

    def test_empty_frontmatter_parses_to_an_empty_map(self) -> None:
        parts = split_frontmatter("---\n---\nbody\n")
        self.assertEqual(to_python(parse_document(parts.yaml_text)), {})


if __name__ == "__main__":
    unittest.main()
