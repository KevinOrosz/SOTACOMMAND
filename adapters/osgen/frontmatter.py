"""Strict frontmatter parser for role sources.

YAML is not in the Python standard library, so role sources use a small,
documented subset of YAML. Anything outside the subset is an error, never a
guess. Each kind of file has exactly one parser, so two tools never disagree
about one file: role files use this one; SKILL.md files use
tools/repo/skills/skillslib (lint, the registry and the marketplace generator);
old-style private sources use the extraction tool's; site pages use the site
builder's. The subset (also documented in adapters/ROLES.md):

* The file starts with a line that is exactly ``---`` and the block ends at the
  next line that is exactly ``---``.
* Each entry is ``key: value`` on one line. Keys are lowercase kebab-case.
* A value is one of:
  - a plain scalar (``high``, ``Read, Grep, Glob``), which must not contain
    ``": "`` or ``" #"`` and must not start with a YAML indicator character;
  - a double-quoted string with JSON escapes (``"text: with a colon"``);
  - a single-quoted string, where ``''`` stands for one quote;
  - ``true`` or ``false``;
  - a flow list of scalars or quoted strings (``[a, b, "c"]``);
  - nothing, followed by an indented block list (``- item`` lines).
* Full-line comments start with ``#``. Blank lines are ignored.

Duplicate keys, tabs, nested maps, anchors, multi-line scalars and trailing
comments are errors.
"""

import json
import re
from typing import Dict, List, Optional, Tuple, Union

Value = Union[str, bool, List[str], Dict[str, object], None]

_KEY_RE = re.compile(r"^([a-z][a-z0-9]*(?:-[a-z0-9]+)*):(?:[ ]+(.*))?$")
_LIST_ITEM_RE = re.compile(r"^[ ]+-(?:[ ]+(.*))?$")
# An indented "name: value" entry inside a one-level map (opt-in).
_MAP_ENTRY_RE = re.compile(r"^[ ]+([A-Za-z0-9][A-Za-z0-9_.-]*):(?:[ ]+(.*))?$")
# Characters that always start something other than a plain scalar in YAML.
_INDICATORS = set("[]{}&*!|>'\"%@`,#")
# Characters that start a plain scalar only when a non-space character follows.
_SOFT_INDICATORS = set("?:-")
# Plain scalars that YAML 1.1 or 1.2 would read as something other than a string.
_AMBIGUOUS_WORDS = {
    "null", "~", "yes", "no", "on", "off", "y", "n", "true", "false",
    "True", "False", "TRUE", "FALSE", "Null", "NULL", "Yes", "No", "On", "Off",
}


class FrontmatterError(ValueError):
    """A frontmatter block that falls outside the supported subset."""

    def __init__(self, message: str, line: Optional[int] = None) -> None:
        self.message = message
        self.line = line
        super().__init__(message if line is None else "line %d: %s" % (line, message))


def split_frontmatter(text: str) -> Tuple[List[Tuple[int, str]], str, int]:
    """Split ``text`` into frontmatter lines and body.

    Returns ``(lines, body, body_start_line)`` where ``lines`` holds
    ``(line_number, text)`` pairs for the lines between the two ``---``
    markers.
    """
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != "---":
        raise FrontmatterError("the file must start with a '---' line", 1)
    block: List[Tuple[int, str]] = []
    for index in range(1, len(lines)):
        line = lines[index].rstrip("\r")
        if line == "---":
            body = "\n".join(lines[index + 1:])
            return block, body, index + 2
        block.append((index + 1, line))
    raise FrontmatterError("the frontmatter block has no closing '---' line", len(lines))


def parse(text: str, nested_maps: bool = False) -> Tuple[Dict[str, Value], str]:
    """Parse a document with frontmatter. Returns ``(fields, body)``.

    With ``nested_maps``, a ``key:`` line may open one level of indented
    ``name: value`` entries, as a skill's ``metadata`` block does. Role
    sources never allow it.
    """
    block, body, _ = split_frontmatter(text)
    return parse_block(block, nested_maps), body


def parse_block(block: List[Tuple[int, str]], nested_maps: bool = False) -> Dict[str, Value]:
    """Parse the lines between the ``---`` markers.

    A ``key:`` line with no value opens a block list (or, with
    ``nested_maps``, a one-level map). If nothing follows it, the key's value
    is ``None`` (YAML null).
    """
    fields: Dict[str, Value] = {}
    open_key: Optional[str] = None
    keys_without_items = set()
    for number, line in block:
        if "\t" in line:
            raise FrontmatterError("tabs are not allowed in frontmatter", number)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = _LIST_ITEM_RE.match(line)
        if item:
            if open_key is None:
                raise FrontmatterError("a list item must follow a 'key:' line with no value", number)
            raw = item.group(1)
            if raw is None or not raw.strip():
                raise FrontmatterError("empty list item", number)
            current = fields[open_key]
            if not isinstance(current, list):
                raise FrontmatterError("a block cannot mix list items and map entries", number)
            current.append(_parse_list_scalar(raw.strip(), number))
            keys_without_items.discard(open_key)
            continue
        if line[0] == " ":
            entry = _MAP_ENTRY_RE.match(line)
            if not nested_maps or open_key is None or not entry:
                raise FrontmatterError("unexpected indentation (nested maps are not supported)", number)
            current = fields[open_key]
            if current == []:
                current = fields[open_key] = {}
            if not isinstance(current, dict):
                raise FrontmatterError("a block cannot mix list items and map entries", number)
            name, raw_value = entry.group(1), entry.group(2)
            if name in current:
                raise FrontmatterError("duplicate key %r in %r" % (name, open_key), number)
            if raw_value is None or not raw_value.strip():
                raise FrontmatterError("maps nest only one level deep", number)
            current[name] = _parse_value(raw_value.strip(), number)
            keys_without_items.discard(open_key)
            continue
        match = _KEY_RE.match(line)
        if not match:
            raise FrontmatterError(
                "expected 'key: value' with a lowercase kebab-case key, got %r" % line, number
            )
        key, raw_value = match.group(1), match.group(2)
        if key in fields:
            raise FrontmatterError("duplicate key %r" % key, number)
        if raw_value is None or not raw_value.strip():
            fields[key] = []
            open_key = key
            keys_without_items.add(key)
            continue
        open_key = None
        fields[key] = _parse_value(raw_value.strip(), number)
    for key in keys_without_items:
        fields[key] = None
    return fields


def _parse_value(raw: str, number: int) -> Value:
    if raw.startswith("["):
        return _parse_flow_list(raw, number)
    if raw in ("true", "false"):
        return raw == "true"
    return _parse_scalar(raw, number)


def _parse_list_scalar(raw: str, number: int) -> str:
    value = _parse_scalar(raw, number)
    if not value:
        raise FrontmatterError("empty list item", number)
    return value


def _parse_scalar(raw: str, number: int) -> str:
    if raw.startswith('"'):
        return _parse_double_quoted(raw, number)
    if raw.startswith("'"):
        return _parse_single_quoted(raw, number)
    return _parse_plain(raw, number)


def _parse_plain(raw: str, number: int) -> str:
    first = raw[0]
    if first in _INDICATORS or (
        first in _SOFT_INDICATORS and (len(raw) == 1 or raw[1] == " ")
    ):
        raise FrontmatterError(
            "a plain value cannot start with %r; quote the value" % first, number
        )
    if ": " in raw or raw.endswith(":"):
        raise FrontmatterError("a plain value cannot contain ': '; quote the value", number)
    if " #" in raw:
        raise FrontmatterError(
            "trailing comments are not supported; quote values that contain ' #'", number
        )
    if raw in _AMBIGUOUS_WORDS:
        raise FrontmatterError(
            "%r is ambiguous in YAML; use true or false for booleans, or quote the text" % raw,
            number,
        )
    return raw


def _parse_double_quoted(raw: str, number: int) -> str:
    end = _closing_double_quote(raw)
    if end != len(raw) - 1:
        raise FrontmatterError("unexpected text after a quoted value", number)
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise FrontmatterError("invalid double-quoted string: %s" % exc, number)
    if not isinstance(value, str):
        raise FrontmatterError("invalid double-quoted string", number)
    return value


def _closing_double_quote(raw: str) -> int:
    index = 1
    while index < len(raw):
        char = raw[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index
        index += 1
    return -1


def _parse_single_quoted(raw: str, number: int) -> str:
    out = []
    index = 1
    while index < len(raw):
        char = raw[index]
        if char == "'":
            if index + 1 < len(raw) and raw[index + 1] == "'":
                out.append("'")
                index += 2
                continue
            if index != len(raw) - 1:
                raise FrontmatterError("unexpected text after a quoted value", number)
            return "".join(out)
        out.append(char)
        index += 1
    raise FrontmatterError("unterminated single-quoted string", number)


def _parse_flow_list(raw: str, number: int) -> List[str]:
    if not raw.endswith("]"):
        raise FrontmatterError("a flow list must end with ']' on the same line", number)
    inner = raw[1:-1].strip()
    if not inner:
        return []
    items: List[str] = []
    for part in _split_flow_items(inner, number):
        part = part.strip()
        if not part:
            raise FrontmatterError("empty item in flow list", number)
        if part.startswith("[") or part.startswith("{"):
            raise FrontmatterError("nested collections are not supported", number)
        items.append(_parse_flow_scalar(part, number))
    return items


def _parse_flow_scalar(raw: str, number: int) -> str:
    if raw.startswith('"') or raw.startswith("'"):
        return _parse_scalar(raw, number)
    if any(char in raw for char in "[]{}"):
        raise FrontmatterError("quote flow-list items that contain brackets or braces", number)
    return _parse_plain(raw, number)


def _split_flow_items(inner: str, number: int) -> List[str]:
    """Split on commas outside quotes, following YAML flow-sequence rules."""
    parts: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    index = 0
    while index < len(inner):
        char = inner[index]
        if quote == '"':
            current.append(char)
            if char == "\\" and index + 1 < len(inner):
                current.append(inner[index + 1])
                index += 2
                continue
            if char == '"':
                quote = None
        elif quote == "'":
            current.append(char)
            if char == "'":
                if index + 1 < len(inner) and inner[index + 1] == "'":
                    current.append("'")
                    index += 2
                    continue
                quote = None
        elif char in ("'", '"') and not "".join(current).strip():
            quote = char
            current.append(char)
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    if quote is not None:
        raise FrontmatterError("unterminated quoted string in flow list", number)
    parts.append("".join(current))
    return parts
