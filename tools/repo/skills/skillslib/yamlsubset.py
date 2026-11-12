"""A strict, documented YAML subset for skill frontmatter and eval case files.

YAML is not in the Python standard library, so the skills toolchain reads
every frontmatter block (``SKILL.md``, ``prompt.md``, ``graders/*.md``) and
every ``case.yaml`` with this parser. It accepts a small subset of YAML and
rejects everything else with a line-numbered :class:`YamlSubsetError`. It
never guesses: input it can't read with certainty is an error.

Supported
---------

* A document is a block mapping of ``key: value`` lines starting in column 1.
  Keys use letters, digits, ``_``, ``.`` and ``-``, and a colon followed by a
  space (or the end of the line) separates key and value.
* Scalars on one line: plain scalars, ``'single-quoted'`` strings (``''`` is
  a literal quote) and ``"double-quoted"`` strings with the YAML escapes.
* One-line flow collections of scalars: ``[a, "b", 3]`` and
  ``{source: file, path: out.md}``.
* Block lists of scalars under a top-level key, indented or not::

      tags:
        - smoke
        - csv

* One level of nested block mapping under a top-level key. Its values are
  scalars or one-line flow collections::

      metadata:
        version: "1.0.0"
        status: stable

* Blank lines, full-line comments and trailing `` # comments``.

Rejected (each is an error, never a guess)
------------------------------------------

Anchors, aliases, tags, block scalars (``|`` and ``>``), multi-line scalars
and flow collections, nesting deeper than one level, lists of mappings,
nested flow collections, duplicate keys, quoted or complex keys, tab
indentation, document markers inside the block, and directives.

Scalar resolution
-----------------

Plain scalars resolve under the YAML 1.2 core schema (null, bool, int, float,
otherwise string). Each scalar also records how a YAML 1.1 parser such as
PyYAML, which some skill packaging tools use, would read it (``tag11``). A
value only counts as a portable string when both readings are ``str``, so
``version: 1.0`` (a float) and ``enabled: yes`` (a YAML 1.1 boolean) are
caught.
"""

import re
from collections import OrderedDict
from typing import Dict, List, NamedTuple, Optional, Tuple, Union

__all__ = [
    "Frontmatter",
    "FrontmatterError",
    "Map",
    "Node",
    "Scalar",
    "Seq",
    "YamlSubsetError",
    "parse_document",
    "split_frontmatter",
    "to_python",
]


class YamlSubsetError(ValueError):
    """Input outside the supported YAML subset, with a 1-based line number."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__("line {}: {}".format(line, message))
        self.line = line
        self.message = message


class FrontmatterError(ValueError):
    """A Markdown file whose ``---`` frontmatter block is missing or malformed."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__("line {}: {}".format(line, message))
        self.line = line
        self.message = message


class Scalar(object):
    """A scalar value.

    ``value`` is the YAML 1.2 core reading, ``style`` is ``plain``,
    ``single`` or ``double``, ``tag`` is the YAML 1.2 core type (``null``,
    ``bool``, ``int``, ``float`` or ``str``) and ``tag11`` the YAML 1.1 type,
    which can also be ``timestamp``. Quoted scalars are always ``str``.
    """

    __slots__ = ("value", "style", "line", "tag", "tag11")

    def __init__(self, value, style: str, line: int, tag: str, tag11: str) -> None:
        self.value = value
        self.style = style
        self.line = line
        self.tag = tag
        self.tag11 = tag11

    @property
    def is_portable_string(self) -> bool:
        """True when every YAML reader sees a string, under 1.1 and 1.2 rules."""
        return self.tag == "str" and self.tag11 == "str"

    def __repr__(self) -> str:
        return "Scalar({!r}, {}, line {})".format(self.value, self.style, self.line)


class Seq(object):
    """A list of scalars, written in flow (``[a, b]``) or block (``- a``) style."""

    __slots__ = ("items", "style", "line")

    def __init__(self, items: List[Scalar], style: str, line: int) -> None:
        self.items = items
        self.style = style
        self.line = line

    def __repr__(self) -> str:
        return "Seq({!r}, {}, line {})".format(self.items, self.style, self.line)


class Map(object):
    """A mapping. ``key_lines`` records the line each key was written on."""

    __slots__ = ("items", "key_lines", "style", "line")

    def __init__(self, style: str, line: int) -> None:
        self.items = OrderedDict()  # type: Dict[str, Node]
        self.key_lines = {}  # type: Dict[str, int]
        self.style = style
        self.line = line

    def __repr__(self) -> str:
        return "Map({!r}, {}, line {})".format(dict(self.items), self.style, self.line)


Node = Union[Scalar, Seq, Map]


def to_python(node: Node):
    """Convert a parsed node to plain Python values (dict, list, scalars)."""
    if isinstance(node, Scalar):
        return node.value
    if isinstance(node, Seq):
        return [to_python(item) for item in node.items]
    return OrderedDict((key, to_python(value)) for key, value in node.items.items())


# ---------------------------------------------------------------------------
# Scalar resolution
# ---------------------------------------------------------------------------

_NULL12 = frozenset(["", "~", "null", "Null", "NULL"])
_BOOL12 = {"true": True, "True": True, "TRUE": True, "false": False, "False": False, "FALSE": False}
_INT12_DEC = re.compile(r"[-+]?[0-9]+")
_INT12_OCT = re.compile(r"0o[0-7]+")
_INT12_HEX = re.compile(r"0x[0-9a-fA-F]+")
_FLOAT12 = re.compile(r"[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?")
_INF12 = re.compile(r"([-+]?)\.(?:inf|Inf|INF)")
_NAN12 = re.compile(r"\.(?:nan|NaN|NAN)")

# The YAML 1.1 implicit resolvers, as PyYAML implements them, plus the y/n
# booleans from the YAML 1.1 type repository.
_BOOL11 = re.compile(r"yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF|y|Y|n|N")
_INT11 = re.compile(
    r"[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+"
    r"|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+"
)
_FLOAT11 = re.compile(
    r"[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9_]+(?:[eE][-+][0-9]+)?"
    r"|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
)
_NULL11 = re.compile(r"~|null|Null|NULL|")
_TIMESTAMP11 = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?"
    r"(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?"
)


def _resolve12(text: str) -> Tuple[str, object]:
    """Resolve a plain scalar under the YAML 1.2 core schema."""
    if text in _NULL12:
        return "null", None
    if text in _BOOL12:
        return "bool", _BOOL12[text]
    if _INT12_DEC.fullmatch(text):
        return "int", int(text)
    if _INT12_OCT.fullmatch(text):
        return "int", int(text[2:], 8)
    if _INT12_HEX.fullmatch(text):
        return "int", int(text[2:], 16)
    if _FLOAT12.fullmatch(text):
        return "float", float(text)
    inf = _INF12.fullmatch(text)
    if inf:
        return "float", float("-inf") if inf.group(1) == "-" else float("inf")
    if _NAN12.fullmatch(text):
        return "float", float("nan")
    return "str", text


def _resolve11(text: str) -> str:
    """Return the type a YAML 1.1 reader (such as PyYAML) gives a plain scalar."""
    if text in ("=", "<<"):
        return "special"  # the YAML 1.1 value and merge keys; PyYAML can't load them as strings
    if _NULL11.fullmatch(text):
        return "null"
    if _BOOL11.fullmatch(text):
        return "bool"
    if _INT11.fullmatch(text):
        return "int"
    if _FLOAT11.fullmatch(text):
        return "float"
    if _TIMESTAMP11.fullmatch(text):
        return "timestamp"
    return "str"


def _plain(text: str, line: int) -> Scalar:
    tag, value = _resolve12(text)
    return Scalar(value, "plain", line, tag, _resolve11(text))


def _quoted(text: str, style: str, line: int) -> Scalar:
    return Scalar(text, style, line, "str", "str")


# ---------------------------------------------------------------------------
# Quoted scalars
# ---------------------------------------------------------------------------

_SIMPLE_ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "\t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\x85",
    "_": "\xa0",
    "L": " ",
    "P": " ",
}
_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _scan_double(text: str, start: int, line: int) -> Tuple[str, int]:
    """Scan a double-quoted scalar starting at ``text[start] == '"'``.

    Returns the decoded string and the index just past the closing quote.
    """
    out = []
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            return "".join(out), i + 1
        if ch == "\\":
            if i + 1 >= n:
                raise YamlSubsetError(
                    line, "a double-quoted string can't continue onto the next line in this YAML subset"
                )
            esc = text[i + 1]
            if esc in _SIMPLE_ESCAPES:
                out.append(_SIMPLE_ESCAPES[esc])
                i += 2
                continue
            if esc in _HEX_ESCAPES:
                width = _HEX_ESCAPES[esc]
                digits = text[i + 2 : i + 2 + width]
                if len(digits) != width or not all(d in _HEX_DIGITS for d in digits):
                    raise YamlSubsetError(line, "invalid \\{} escape in a double-quoted string".format(esc))
                code = int(digits, 16)
                if 0xD800 <= code <= 0xDFFF or code > 0x10FFFF:
                    raise YamlSubsetError(line, "escape \\{}{} is not a valid character".format(esc, digits))
                out.append(chr(code))
                i += 2 + width
                continue
            raise YamlSubsetError(line, "unknown escape \\{} in a double-quoted string".format(esc))
        out.append(ch)
        i += 1
    raise YamlSubsetError(
        line, "double-quoted string is not closed on this line (multi-line strings are unsupported)"
    )


def _scan_single(text: str, start: int, line: int) -> Tuple[str, int]:
    """Scan a single-quoted scalar starting at ``text[start] == "'"``."""
    out = []
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            if i + 1 < n and text[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise YamlSubsetError(
        line, "single-quoted string is not closed on this line (multi-line strings are unsupported)"
    )


def _expect_line_end(text: str, index: int, line: int) -> None:
    """Allow only whitespace and an optional comment after a complete value."""
    rest = text[index:]
    stripped = rest.lstrip(" \t")
    if not stripped:
        return
    if stripped.startswith("#") and len(stripped) < len(rest):
        return
    raise YamlSubsetError(line, "unexpected text after the value: {!r}".format(stripped))


# ---------------------------------------------------------------------------
# Plain scalars and flow collections
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_.\-]*)[ ]*:(?=[ \t]|$)")

# Characters that can't start a plain scalar here, with the reason.
_UNSUPPORTED_START = {
    "&": "anchors (&name) are unsupported",
    "*": "aliases (*name) are unsupported",
    "!": "tags (!tag) are unsupported",
    "|": "block scalars (| and >) are unsupported; write the value on one line, quoted if needed",
    ">": "block scalars (| and >) are unsupported; write the value on one line, quoted if needed",
    "%": "a value can't start with '%' unless it is quoted",
    "@": "a value can't start with '@' unless it is quoted",
    "`": "a value can't start with a backtick unless it is quoted",
    ",": "a value can't start with ',' unless it is quoted",
    "]": "a value can't start with ']' unless it is quoted",
    "}": "a value can't start with '}' unless it is quoted",
}


def _check_plain_start(text: str, line: int) -> None:
    first = text[0]
    if first in _UNSUPPORTED_START:
        raise YamlSubsetError(line, _UNSUPPORTED_START[first])
    if first in "?:-" and (len(text) == 1 or text[1] in " \t"):
        if first == "-":
            raise YamlSubsetError(line, "a list item can't start on the same line as its key")
        if first == "?":
            raise YamlSubsetError(line, "complex keys ('? ') are unsupported")
        raise YamlSubsetError(line, "unexpected ':'")


def _scan_block_plain(text: str, line: int) -> Scalar:
    """Read a plain scalar that runs to the end of the line (block context)."""
    _check_plain_start(text, line)
    end = len(text)
    for i in range(1, len(text)):
        if text[i] == "#" and text[i - 1] in " \t":
            end = i
            break
    value = text[:end].rstrip(" \t")
    if re.search(r":(?:[ \t]|$)", value):
        raise YamlSubsetError(
            line, "a plain value can't contain ': ' or end with ':'; quote the whole value"
        )
    return _plain(value, line)


def _scan_flow_plain(text: str, start: int, line: int) -> Tuple[Scalar, int]:
    """Read a plain scalar inside a flow collection; stops at , ] } and ': '."""
    _check_plain_start(text[start:], line)
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ",[]{}":
            break
        if ch == "#" and i > start and text[i - 1] in " \t":
            break
        if ch == ":" and (i + 1 == n or text[i + 1] in " \t,[]{}"):
            break
        i += 1
    value = text[start:i].rstrip(" \t")
    if not value:
        raise YamlSubsetError(line, "empty entry in a flow collection")
    return _plain(value, line), i


def _skip_spaces(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t":
        i += 1
    return i


def _scan_flow_scalar(text: str, i: int, line: int) -> Tuple[Scalar, int]:
    ch = text[i]
    if ch == '"':
        value, j = _scan_double(text, i, line)
        return _quoted(value, "double", line), j
    if ch == "'":
        value, j = _scan_single(text, i, line)
        return _quoted(value, "single", line), j
    if ch in "[{":
        raise YamlSubsetError(line, "nested flow collections are unsupported")
    return _scan_flow_plain(text, i, line)


def _scan_flow(text: str, start: int, line: int) -> Tuple[Node, int]:
    """Read a one-line flow list or flow map starting at ``text[start]``."""
    opener = text[start]
    closer = "]" if opener == "[" else "}"
    unclosed = "flow collection is not closed on this line (multi-line flow collections are unsupported)"
    i = start + 1
    if opener == "[":
        seq = Seq([], "flow", line)
        while True:
            i = _skip_spaces(text, i)
            if i >= len(text):
                raise YamlSubsetError(line, unclosed)
            if text[i] == "]":
                return seq, i + 1
            if text[i] == ",":
                raise YamlSubsetError(line, "empty entry in a flow list")
            if text[i] == "#":
                raise YamlSubsetError(line, unclosed)
            item, i = _scan_flow_scalar(text, i, line)
            seq.items.append(item)
            i = _skip_spaces(text, i)
            if i < len(text) and text[i] == ",":
                i += 1
            elif i < len(text) and text[i] == "]":
                continue
            elif i >= len(text) or text[i] == "#":
                raise YamlSubsetError(line, unclosed)
            else:
                raise YamlSubsetError(line, "expected ',' or ']' in a flow list, found {!r}".format(text[i]))
    mapping = Map("flow", line)
    while True:
        i = _skip_spaces(text, i)
        if i >= len(text):
            raise YamlSubsetError(line, unclosed)
        if text[i] == closer:
            return mapping, i + 1
        if text[i] in "'\"":
            raise YamlSubsetError(line, "quoted keys are unsupported")
        match = _KEY_RE.match(text, i)
        if not match:
            raise YamlSubsetError(line, "expected 'key: value' in a flow mapping")
        key = match.group(1)
        if key in mapping.items:
            raise YamlSubsetError(line, "duplicate key {!r}".format(key))
        i = _skip_spaces(text, match.end())
        if i < len(text) and text[i] in ",}":
            value = Scalar(None, "plain", line, "null", "null")  # type: Node
        elif i >= len(text):
            raise YamlSubsetError(line, unclosed)
        else:
            value, i = _scan_flow_scalar(text, i, line)
        mapping.items[key] = value
        mapping.key_lines[key] = line
        i = _skip_spaces(text, i)
        if i < len(text) and text[i] == ",":
            i += 1
        elif i < len(text) and text[i] == closer:
            continue
        elif i >= len(text) or text[i] == "#":
            raise YamlSubsetError(line, unclosed)
        else:
            raise YamlSubsetError(line, "expected ',' or '}}' in a flow mapping, found {!r}".format(text[i]))


def _parse_inline(text: str, line: int) -> Optional[Node]:
    """Parse the value written on a key or list-item line.

    Returns ``None`` when nothing but an optional comment follows, which
    means a block collection (or null) follows on the next lines.
    """
    if not text or text.startswith("#"):
        return None
    first = text[0]
    if first == '"':
        value, end = _scan_double(text, 0, line)
        _expect_line_end(text, end, line)
        return _quoted(value, "double", line)
    if first == "'":
        value, end = _scan_single(text, 0, line)
        _expect_line_end(text, end, line)
        return _quoted(value, "single", line)
    if first in "[{":
        node, end = _scan_flow(text, 0, line)
        _expect_line_end(text, end, line)
        return node
    return _scan_block_plain(text, line)


# ---------------------------------------------------------------------------
# Block structure
# ---------------------------------------------------------------------------


class _Line(NamedTuple):
    number: int
    indent: int
    content: str


def _significant_lines(text: str, first_line: int) -> List[_Line]:
    lines = []
    for offset, raw in enumerate(text.split("\n")):
        number = first_line + offset
        if raw.endswith("\r"):
            raw = raw[:-1]
        body = raw.lstrip(" \t")
        if not body:
            continue
        leading = raw[: len(raw) - len(body)]
        if "\t" in leading:
            raise YamlSubsetError(number, "tabs can't be used for indentation")
        if body.startswith("#"):
            continue
        if not leading and (raw == "---" or raw.startswith("--- ") or raw == "..." or raw.startswith("... ")):
            raise YamlSubsetError(number, "document markers ('---' or '...') are unsupported here")
        if not leading and body.startswith("%"):
            raise YamlSubsetError(number, "YAML directives are unsupported")
        lines.append(_Line(number, len(leading), body.rstrip(" \t")))
    return lines


def _is_seq_item(content: str) -> bool:
    return content == "-" or content.startswith("- ") or content.startswith("-\t")


def _split_key(entry: _Line) -> Tuple[str, str]:
    content = entry.content
    if content[0] in "'\"":
        raise YamlSubsetError(entry.number, "quoted keys are unsupported")
    if content.startswith("? ") or content == "?":
        raise YamlSubsetError(entry.number, "complex keys ('? ') are unsupported")
    match = _KEY_RE.match(content)
    if not match:
        if ":" in content:
            raise YamlSubsetError(
                entry.number,
                "expected 'key: value' with a space after the colon; keys use letters, digits, '_', '.' and '-'",
            )
        raise YamlSubsetError(entry.number, "expected 'key: value'")
    return match.group(1), content[match.end() :].lstrip(" \t")


def _null(line: int) -> Scalar:
    return Scalar(None, "plain", line, "null", "null")


def _parse_block_seq(lines: List[_Line], i: int, indent: int) -> Tuple[Seq, int]:
    seq = Seq([], "block", lines[i].number)
    while i < len(lines):
        entry = lines[i]
        if entry.indent < indent:
            break
        if entry.indent > indent:
            raise YamlSubsetError(entry.number, "nested content inside a list item is unsupported")
        if not _is_seq_item(entry.content):
            if indent == 0:
                break  # a list written at column 1 ends at the next top-level key
            raise YamlSubsetError(entry.number, "expected a '- item' line")
        item_text = entry.content[1:].lstrip(" \t")
        if not item_text or item_text.startswith("#"):
            raise YamlSubsetError(entry.number, "empty list items are unsupported")
        if _is_seq_item(item_text):
            raise YamlSubsetError(entry.number, "nested lists are unsupported")
        if item_text[0] not in "'\"[{" and _KEY_RE.match(item_text):
            raise YamlSubsetError(entry.number, "lists of mappings are unsupported")
        node = _parse_inline(item_text, entry.number)
        if not isinstance(node, Scalar):
            raise YamlSubsetError(entry.number, "list items must be scalars in this YAML subset")
        seq.items.append(node)
        i += 1
    return seq, i


def _parse_block_map(lines: List[_Line], i: int, indent: int) -> Tuple[Map, int]:
    mapping = Map("block", lines[i].number)
    while i < len(lines):
        entry = lines[i]
        if entry.indent < indent:
            break
        if entry.indent > indent:
            raise YamlSubsetError(entry.number, "unexpected indentation inside a nested mapping")
        if _is_seq_item(entry.content):
            raise YamlSubsetError(entry.number, "a list item can't appear inside this mapping")
        key, rest = _split_key(entry)
        if key in mapping.items:
            raise YamlSubsetError(entry.number, "duplicate key {!r}".format(key))
        node = _parse_inline(rest, entry.number)
        i += 1
        if node is None:
            if i < len(lines) and lines[i].indent > indent:
                raise YamlSubsetError(
                    lines[i].number,
                    "nesting deeper than one level is unsupported; use a one-line flow list such as [a, b]",
                )
            node = _null(entry.number)
        elif i < len(lines) and lines[i].indent > indent:
            raise YamlSubsetError(
                lines[i].number, "multi-line values are unsupported; put the value on one line, quoted if needed"
            )
        mapping.items[key] = node
        mapping.key_lines[key] = entry.number
    return mapping, i


def parse_document(text: str, first_line: int = 1) -> Map:
    """Parse ``text`` as a YAML-subset block mapping.

    ``first_line`` is the file line number of the first line of ``text``, so
    errors point at the right line when ``text`` came from a frontmatter
    block. Raises :class:`YamlSubsetError` for anything outside the subset.
    """
    lines = _significant_lines(text, first_line)
    root = Map("block", lines[0].number if lines else first_line)
    i = 0
    while i < len(lines):
        entry = lines[i]
        if entry.indent != 0:
            raise YamlSubsetError(
                entry.number,
                "unexpected indentation; multi-line values and deeper nesting are unsupported",
            )
        if _is_seq_item(entry.content):
            raise YamlSubsetError(entry.number, "the document must be a mapping of 'key: value' lines")
        key, rest = _split_key(entry)
        if key in root.items:
            raise YamlSubsetError(entry.number, "duplicate key {!r}".format(key))
        node = _parse_inline(rest, entry.number)
        i += 1
        if node is None:
            if i < len(lines) and _is_seq_item(lines[i].content):
                # An indented list, or one written at column 1 under its key.
                node, i = _parse_block_seq(lines, i, lines[i].indent)
            elif i < len(lines) and lines[i].indent > 0:
                node, i = _parse_block_map(lines, i, lines[i].indent)
            else:
                node = _null(entry.number)
        elif i < len(lines) and lines[i].indent > 0:
            raise YamlSubsetError(
                lines[i].number, "multi-line values are unsupported; put the value on one line, quoted if needed"
            )
        root.items[key] = node
        root.key_lines[key] = entry.number
    return root


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


class Frontmatter(NamedTuple):
    """The parts of a Markdown file with a ``---`` frontmatter block."""

    yaml_text: str
    body: str
    yaml_first_line: int
    body_first_line: int
    has_bom: bool
    has_crlf: bool


def split_frontmatter(text: str) -> Frontmatter:
    """Split a Markdown file into its frontmatter YAML and its body.

    The file must start with a ``---`` line (a byte-order mark is noted, not
    accepted silently: see ``has_bom``) and the block ends at the next line
    that is exactly ``---``. CRLF line endings are normalized and reported
    through ``has_crlf`` so callers can decide whether that is acceptable.
    """
    has_bom = text.startswith("﻿")
    if has_bom:
        text = text[1:]
    has_crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    if lines[0].rstrip(" \t") != "---":
        raise FrontmatterError(1, "the file must start with a '---' line that opens the YAML frontmatter")
    for index in range(1, len(lines)):
        if lines[index].rstrip(" \t") == "---":
            return Frontmatter(
                yaml_text="\n".join(lines[1:index]),
                body="\n".join(lines[index + 1 :]),
                yaml_first_line=2,
                body_first_line=index + 2,
                has_bom=has_bom,
                has_crlf=has_crlf,
            )
    raise FrontmatterError(1, "the frontmatter is not closed by a '---' line")
