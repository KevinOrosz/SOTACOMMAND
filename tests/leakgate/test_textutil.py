"""Decoding, positions and normalization."""

import codecs
import unittest

from tests.leakgate import support  # noqa: F401
from leakgatelib.textutil import LineIndex, NormalizedView, decode_bytes, sanitize_for_display


class DecodeTest(unittest.TestCase):
    def test_utf8_and_bom(self):
        self.assertEqual(decode_bytes("café".encode("utf-8")), "café")
        self.assertEqual(decode_bytes(codecs.BOM_UTF8 + b"abc"), "abc")

    def test_utf16_with_bom(self):
        self.assertEqual(decode_bytes("hello".encode("utf-16")), "hello")

    def test_binary_is_none(self):
        self.assertIsNone(decode_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"))

    def test_legacy_encoding_falls_back(self):
        self.assertEqual(decode_bytes("naïve café".encode("cp1252")), "naïve café")

    def test_mostly_utf8_keeps_utf8(self):
        data = "café ".encode("utf-8") + b"\xff"
        self.assertTrue(decode_bytes(data).startswith("café"))


class LineIndexTest(unittest.TestCase):
    def test_positions_are_one_based(self):
        index = LineIndex("ab\ncd\n\nef")
        self.assertEqual(index.position(0), (1, 1))
        self.assertEqual(index.position(1), (1, 2))
        self.assertEqual(index.position(3), (2, 1))
        self.assertEqual(index.position(7), (4, 1))
        self.assertEqual(index.bounds(2), (3, 5))
        self.assertEqual(index.bounds(4), (7, 9))
        self.assertEqual(index.line_count, 4)

    def test_crlf_counts_lines_like_git(self):
        index = LineIndex("a\r\nb\r\n")
        self.assertEqual(index.position(3), (2, 1))


class NormalizedViewTest(unittest.TestCase):
    def test_ascii_is_identity(self):
        view = NormalizedView("plain ascii")
        self.assertEqual(view.text, "plain ascii")
        self.assertEqual(view.to_original(0, 5), (0, 5))

    def test_accents_fullwidth_and_invisible_characters_are_removed(self):
        original = "Jóse ＡＢ a​b c­d"
        view = NormalizedView(original)
        self.assertEqual(view.text, "Jose AB ab cd")

    def test_offsets_map_back_to_the_original(self):
        original = "x ＡＢＣ y"
        view = NormalizedView(original)
        start = view.text.index("ABC")
        self.assertEqual(view.to_original(start, start + 3), (2, 5))
        self.assertEqual(original[2:5], "ＡＢＣ")

    def test_ligature_expansion_maps_to_one_character(self):
        original = "ﬁle"  # the "fi" ligature, then "le"
        view = NormalizedView(original)
        self.assertEqual(view.text, "file")
        self.assertEqual(view.to_original(0, 2), (0, 1))


class SanitizeTest(unittest.TestCase):
    def test_controls_become_visible_and_newlines_vanish(self):
        self.assertEqual(sanitize_for_display("a\tb\x1bc\r\n​"), "a b?c?")


if __name__ == "__main__":
    unittest.main()
