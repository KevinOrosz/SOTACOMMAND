"""Text handling shared by every rule: decoding, positions, normalization.

Normalization exists so that name lists can't be dodged by accident. A name
typed with an accent, in fullwidth letters, or with an invisible zero-width
character in the middle still matches. :class:`NormalizedView` removes those
differences and keeps a map back to the original offsets, so findings still
point at the right line and column of the file as written.
"""

import bisect
import codecs
import re
import unicodedata
from typing import List, Optional, Tuple

#: Bytes examined to decide whether a file is binary (a NUL byte means binary).
BINARY_SNIFF_BYTES = 8192

#: Invisible characters removed before name matching: soft hyphen, combining
#: grapheme joiner, Arabic letter mark, Mongolian vowel separator, zero-width
#: space/non-joiner/joiner, directional marks, embeddings and isolates, word
#: joiner and invisible operators, and the byte order mark.
INVISIBLE_CHARS = frozenset(
    "­͏؜᠎​‌‍‎‏"
    "‪‫‬‭‮⁠⁡⁢⁣⁤"
    "⁦⁧⁨⁩﻿"
)

#: A token is a maximal run of letters and digits (underscore separates).
TOKEN_RE = re.compile(r"[^\W_]+")


def decode_bytes(data: bytes) -> Optional[str]:
    """Decode file content, or return ``None`` when it looks binary.

    Order: byte order marks (UTF-8/16/32), then a NUL-byte sniff for binary,
    then strict UTF-8. Text that is mostly UTF-8 with a few bad bytes keeps its
    UTF-8 reading (bad bytes become U+FFFD); anything else falls back to
    Windows-1252 and finally Latin-1, which never fails.
    """
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace")
    for bom, encoding in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                return None
    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    lenient = data.decode("utf-8", errors="replace")
    if any(ord(ch) > 127 and ch != "�" for ch in lenient):
        return lenient
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


class LineIndex(object):
    """Map character offsets to 1-based (line, column) pairs.

    Lines end at ``\\n``, as git and editors count them. Columns count
    characters, not bytes, and a tab counts as one column.
    """

    __slots__ = ("_starts", "_length")

    def __init__(self, text: str) -> None:
        starts = [0]
        find = text.find
        index = find("\n")
        while index != -1:
            starts.append(index + 1)
            index = find("\n", index + 1)
        self._starts = starts
        self._length = len(text)

    def position(self, offset: int) -> Tuple[int, int]:
        line = bisect.bisect_right(self._starts, offset) - 1
        return line + 1, offset - self._starts[line] + 1

    def bounds(self, line: int) -> Tuple[int, int]:
        """Offsets ``(start, end)`` of a 1-based line, excluding its ``\\n``."""
        start = self._starts[line - 1]
        if line < len(self._starts):
            end = self._starts[line] - 1
        else:
            end = self._length
        return start, end

    @property
    def line_count(self) -> int:
        return len(self._starts)


def _fold_char(ch: str) -> str:
    """NFKD-decompose one character and drop combining and invisible marks."""
    if ch in INVISIBLE_CHARS:
        return ""
    decomposed = unicodedata.normalize("NFKD", ch)
    return "".join(
        part
        for part in decomposed
        if not unicodedata.combining(part) and part not in INVISIBLE_CHARS
    )


class NormalizedView(object):
    """A copy of ``text`` with compatibility forms, accents and invisible
    characters removed, plus a map from each view offset to the original.

    Case is preserved: case-insensitive rules apply ``casefold`` or
    ``re.IGNORECASE`` on top of the view. ASCII text is its own view.
    """

    __slots__ = ("text", "_map")

    def __init__(self, original: str) -> None:
        if original.isascii():
            self.text = original
            self._map = None  # type: Optional[List[int]]
            return
        out = []
        mapping = []
        for index, ch in enumerate(original):
            if ch.isascii():
                out.append(ch)
                mapping.append(index)
                continue
            folded = _fold_char(ch)
            for part in folded:
                out.append(part)
                mapping.append(index)
        self.text = "".join(out)
        self._map = mapping

    def to_original(self, start: int, end: int) -> Tuple[int, int]:
        """Translate a non-empty view span back to an original span."""
        if self._map is None:
            return start, end
        return self._map[start], self._map[end - 1] + 1


def normalize_text(text: str) -> str:
    """The normalized form of ``text`` (see :class:`NormalizedView`)."""
    return NormalizedView(text).text


def tokens_of(text: str) -> List[str]:
    """Letter-and-digit tokens of already normalized text."""
    return TOKEN_RE.findall(text)


def sanitize_for_display(text: str) -> str:
    """Make a snippet safe for one line of terminal or log output."""
    out = []
    for ch in text:
        if ch == "\t":
            out.append(" ")
        elif ch in ("\r", "\n"):
            continue
        elif unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)
