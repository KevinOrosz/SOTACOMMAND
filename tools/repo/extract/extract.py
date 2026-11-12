#!/usr/bin/env python3
"""One-time extraction of an allowlisted private source tree into a staging tree.

The tool copies only the files a manifest lists, applies the manifest's safe
rewrite rules, flags what it can't rewrite safely, runs the leak gate on the
result, and moves the result into ``--out`` only when nothing was flagged and
the gate reports zero findings. It is meant to run once, locally, with the
owner reading the report and the diff.

Usage::

    python3 tools/repo/extract/extract.py --source DIR --manifest FILE --out DIR
        [--dry-run] [--report FILE] [--leakgate CMD]

Exit codes:

    0  written clean (with --dry-run: a real run would write clean)
    1  refused: flags or leak-gate findings; nothing was written
    2  usage, manifest or I/O error; nothing was written
    3  leak gate unavailable, failed, or failed its canary check; nothing was
       written (the tool fails closed)

Per line, the rewrite steps always run in this order, whatever order an entry
lists them in:

    entry select, entry drop, drop_patterns       (whole lines)
    entry replace                                  (exact text the owner wrote)
    rewrite_patterns                               (regular expressions)
    path_map                                       (path prefixes; "~/" = any home)
    home-path check                                (unmapped home paths are flagged)
    owner_terms                                    (whole words, case-aware)
    persona_map                                    (whole words, case-sensitive)
    flag_patterns and residual checks              (anything left is flagged)

``frontmatter`` converts old skill frontmatter to the portable Agent Skills
format: the six portable keys at the top level and every other key under
``metadata`` as a string.

The manifest format is documented in ``export/manifest.schema.json`` and the
runbook in ``tools/repo/extract/README.md``. Standard library only; Python 3.9
or newer. The tool makes no network calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

__all__ = [
    "EXIT_GATE_UNAVAILABLE",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_USAGE",
    "FrontmatterError",
    "Manifest",
    "Options",
    "Outcome",
    "PathMap",
    "TermMap",
    "convert_frontmatter",
    "load_manifest",
    "main",
    "parse_frontmatter",
    "render_report",
    "run_extraction",
    "transform_entry",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_GATE_UNAVAILABLE = 3

SCHEMA_VERSION = "1"

TOOL_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOL_DIR.parent.parent.parent
DEFAULT_GATE_SCRIPT = REPO_ROOT / "tools" / "repo" / "leakgate" / "leakgate.py"
DEFAULT_GATE_DISPLAY = "python3 tools/repo/leakgate/leakgate.py"
GATE_TIMEOUT_SECONDS = 600.0

#: Rule sets an entry can list in ``rewrites``, in the order they are applied.
RULE_SET_ORDER = (
    "drop_patterns",
    "rewrite_patterns",
    "path_map",
    "owner_terms",
    "persona_map",
    "frontmatter",
)
#: What an entry gets when neither it nor ``defaults`` lists rewrites.
DEFAULT_REWRITES = RULE_SET_ORDER[:-1]

#: The frontmatter keys claude.ai uploads and the Skills API accept.
PORTABLE_KEYS = ("name", "description", "license", "compatibility", "allowed-tools", "metadata")
SKILL_FILE = "SKILL.md"
SKILL_NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
RESERVED_NAME_WORDS = ("anthropic", "claude")

#: Files with these suffixes are prose: a rewritten term that starts a sentence
#: is capitalized. Everything else (and fenced code inside prose) is code.
PROSE_SUFFIXES = (".md", ".markdown", ".mdx", ".txt", ".rst")

#: Flags the tool raises itself. Manifest flag_patterns can't reuse these ids.
BUILTIN_FLAGS = OrderedDict(
    [
        ("persona-name", "persona name left in place (part of an identifier, or persona_map not applied)"),
        ("owner-term", "owner term left in place (case variant, part of an identifier, or owner_terms not applied)"),
        ("home-path", "absolute home-directory path that no path_map entry covers"),
        ("not-utf8", "not UTF-8 text; set \"binary\": true to copy the file verbatim"),
        ("binary-content", "contains NUL bytes; set \"binary\": true to copy the file verbatim"),
        ("unterminated-block", "a drop block's start matched but its end never did"),
        ("select-not-found", "the select start pattern matched no line"),
        ("unterminated-select", "the select end pattern matched no line after the start"),
        ("frontmatter-missing", "no --- frontmatter block to convert"),
        ("frontmatter-syntax", "frontmatter uses YAML outside the supported subset"),
        ("frontmatter-description", "description missing, empty, too long, or holding < or >"),
        ("frontmatter-field", "a portable field has the wrong shape or length"),
        ("frontmatter-key", "a metadata key isn't kebab-case, or two keys collide"),
        ("frontmatter-role", "a roles entry isn't a role id from persona_map"),
        ("role-source-frontmatter", "an engine/roles/<id>/soul.md source already has frontmatter; the manifest's roles section writes it"),
    ]
)

_HOME_ROOTS = ("Users", "home")  # macOS and Linux home-directory roots
_HOME_ALT = "|".join(_HOME_ROOTS)

#: What "~/" at the start of a path_map key matches: any home directory.
HOME_PREFIX_RE = r"(?:/(?:%s)/[A-Za-z0-9._-]+/|~/|\$HOME/|\$\{HOME\}/)" % _HOME_ALT

#: A home-directory path left after path_map: POSIX, Windows (either slash),
#: the WSL view of a Windows home, or a tilde followed by a user name.
#: Deliberately broad: anything
#: but a word character or ``.`` may come before it, so a home path after a
#: shell default (``:-``) or inside a ``file://`` URL is caught, while a
#: relative path that merely has a ``home`` folder in it is not.
HOME_PATH_RE = re.compile(
    r"(?<![\w.])(?:"
    r"/mnt/[A-Za-z]/(?:Users|Documents and Settings)/[^/\s\"'<>|]+"
    r"|/(?:%s)/[A-Za-z0-9._-]+"
    r"|[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s\"'<>|]+"
    r"|~[A-Za-z][A-Za-z0-9._-]*(?=/)"
    r")(?:[/\\][^\s\"'`<>()\[\]{}|;,]*)?" % _HOME_ALT
)

_ROLE_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
#: A role source target; the manifest's ``roles`` section writes its frontmatter.
ROLE_TARGET_RE = re.compile(r"engine/roles/([a-z][a-z0-9]*(?:-[a-z0-9]+)*)/soul\.md")
#: Role frontmatter keys the manifest may set (adapters/ROLES.md; ``id`` comes from the folder).
ROLE_KEYS = ("description", "tier", "allowed-tools", "disallowed-tools", "isolation", "main", "write-paths")
ROLE_TIERS = ("low", "mid", "high")
PLUGIN_KEYS = ("name", "description", "version")
_PLUGIN_DIR_RE = re.compile(r"(?:packs|labs)/[a-z0-9]+(?:-[a-z0-9]+)*")
_SEMVER_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_RULE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_SKILL_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_META_KEY_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_INLINE_FLAGS_RE = re.compile(r"(?<!\\)(?:\\\\)*\(\?[aiLmsux]+\)")
_TEMPLATE_BREAK_RE = re.compile(r"(?<!\\)(?:\\\\)*\\[nr]")
_EOL_RE = re.compile(r"\r\n|\n|\r")
_FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})")
_EMPTY_SAMPLES = ("", "a", " ", "a b", "\t", "0")
_LINE_SAMPLES = ("a", "lorem ipsum", "0", "-", "# heading", "plain text.")

#: Markdown block markers and an emphasis opener that may precede a sentence.
_BLOCK_PREFIX_RE = re.compile(r"[ \t]*(?:(?:#{1,6}|>|[-*+]|\d{1,9}[.)])[ \t]+)*(?:\*\*|__|\*|_)?")
#: The end of a sentence, then whitespace, then an optional emphasis opener.
_AFTER_STOP_RE = re.compile(r"[.!?][\"')\]]*[ \t]+(?:\*\*|__|\*|_)?$")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _shorten(text: str, limit: int = 80) -> str:
    """Collapse whitespace and cut ``text`` to ``limit`` characters."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _plural(count: int, word: str, plural: Optional[str] = None) -> str:
    return "%d %s" % (count, word if count == 1 else (plural or word + "s"))


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text[:1].islower() else text


def _mask(term: str) -> str:
    """Show a private term's shape without its text, for console messages."""
    if len(term) <= 2:
        return "*" * len(term)
    return term[0] + "*" * (len(term) - 2) + term[-1]


def _split_lines(text: str) -> List[Tuple[int, str, str]]:
    """Split text into ``(line number, content, line ending)`` triples.

    Only ``\\n``, ``\\r\\n`` and ``\\r`` end a line (``str.splitlines`` also
    splits on form feeds and Unicode separators, which would renumber lines).
    """
    lines = []
    start = 0
    number = 0
    for match in _EOL_RE.finditer(text):
        number += 1
        lines.append((number, text[start : match.start()], match.group(0)))
        start = match.end()
    if start < len(text):
        lines.append((number + 1, text[start:], ""))
    return lines


def _ranges(numbers: Iterable[int], limit: int = 20) -> str:
    """Format line numbers as compact ranges: ``1, 3-5, 9``."""
    ordered = sorted(set(numbers))
    if not ordered:
        return ""
    groups = []  # type: List[Tuple[int, int]]
    for number in ordered:
        if groups and number == groups[-1][1] + 1:
            groups[-1] = (groups[-1][0], number)
        else:
            groups.append((number, number))
    parts = ["%d" % a if a == b else "%d-%d" % (a, b) for a, b in groups]
    if len(parts) > limit:
        parts = parts[:limit] + ["\u2026 (+%d more)" % (len(parts) - limit)]
    return ", ".join(parts)


def _can_match_empty(regex: "re.Pattern[str]") -> bool:
    """True when ``regex`` produces a zero-width match on ordinary samples."""
    return any(m.start() == m.end() for sample in _EMPTY_SAMPLES for m in regex.finditer(sample))


def _matches_any_line(regex: "re.Pattern[str]") -> bool:
    """True when ``regex`` matches every sample line (a drop of everything)."""
    return all(regex.search(sample) for sample in _LINE_SAMPLES)


def _is_inside(path: Path, parent: Path) -> bool:
    """True when ``path`` is ``parent`` or below it (both already resolved)."""
    return path == parent or parent in path.parents


def _resolve(path: Union[str, Path]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path)))).resolve()


def find_enclosing_git(path: Union[str, Path]) -> Optional[Path]:
    """Return the first directory at or above ``path`` that holds ``.git``."""
    current = _resolve(path)
    for candidate in [current] + list(current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def glob_regex(pattern: str) -> "re.Pattern[str]":
    """Compile a gitignore-style glob, matched case-insensitively.

    ``*`` and ``?`` stay inside one path segment, ``**`` crosses segments, a
    trailing ``/`` means everything below, and a pattern without ``/`` matches
    a file or folder name at any depth. Case-insensitive, so a manifest can't
    slip past a rule on a case-insensitive filesystem.
    """
    body = pattern.strip()
    if body.endswith("/"):
        body += "**"
    if "/" not in body:
        body = "**/" + body
    out = []
    i = 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("/**", i) and i + 3 == len(body):
            out.append("(?:/.*)?")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        elif body[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    return re.compile("".join(out), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Manifest model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RewritePattern:
    """A global regular-expression rewrite (``rewrite_patterns``)."""

    id: str
    regex: Any
    replace: str
    description: str = ""


@dataclass(frozen=True)
class DropRule:
    """Drop one line (``pattern``) or a block (``start`` to ``end`` or ``before``)."""

    id: str
    scope: str  # "drop_patterns" (global) or "drop" (entry)
    pattern: Any = None
    start: Any = None
    end: Any = None  # inclusive
    before: Any = None  # exclusive; the block may also run to the end of the file
    reason: str = ""


@dataclass(frozen=True)
class FlagPattern:
    """Text that can't be rewritten safely; any match blocks the run."""

    id: str
    regex: Any
    description: str = ""


@dataclass(frozen=True)
class ReplaceRule:
    """An entry-scoped replacement the owner writes after reading the report."""

    index: int
    find: str
    replacement: str
    regex: Any = None  # compiled when the rule sets "regex": true
    reason: str = ""


@dataclass(frozen=True)
class SelectRule:
    """Keep only one block of the source: ``start`` to ``end`` or ``before``."""

    start: Any
    end: Any = None
    before: Any = None


@dataclass(frozen=True)
class FrontmatterConfig:
    drop_keys: Tuple[str, ...] = ()
    rename_keys: Tuple[Tuple[str, str], ...] = ()
    list_separator: str = ", "

    def rename(self, key: str) -> Optional[str]:
        for old, new in self.rename_keys:
            if old == key:
                return new
        return None


@dataclass
class Entry:
    """One allowlisted file: where it comes from, where it goes, what applies."""

    index: int
    source: str
    target: str
    rewrites: Tuple[str, ...]
    replace: Tuple[ReplaceRule, ...] = ()
    drop: Tuple[DropRule, ...] = ()
    select: Optional[SelectRule] = None
    binary: bool = False
    note: str = ""

    @property
    def label(self) -> str:
        return "files[%d] (%s)" % (self.index, self.target)


@dataclass
class Manifest:
    path: str
    sha256: str
    persona_map: "OrderedDict[str, str]"
    owner_terms: "OrderedDict[str, str]"
    path_map: "OrderedDict[str, str]"
    rewrite_patterns: List[RewritePattern]
    drop_patterns: List[DropRule]
    flag_patterns: List[FlagPattern]
    never_export: List[str]
    frontmatter: FrontmatterConfig
    entries: List[Entry]
    description: str = ""
    #: role id -> role frontmatter fields, written into engine/roles/<id>/soul.md
    roles: "OrderedDict[str, OrderedDict[str, Any]]" = field(default_factory=OrderedDict)
    #: plugin folder (packs/<x> or labs/<x>) -> name, description, version
    plugins: "OrderedDict[str, OrderedDict[str, str]]" = field(default_factory=OrderedDict)


class _DuplicateKeyError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _pairs_without_duplicates(pairs: List[Tuple[str, Any]]) -> "OrderedDict[str, Any]":
    result = OrderedDict()  # type: OrderedDict[str, Any]
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


_TOP_KEYS = (
    "$schema",
    "schema_version",
    "description",
    "defaults",
    "persona_map",
    "owner_terms",
    "path_map",
    "rewrite_patterns",
    "drop_patterns",
    "flag_patterns",
    "never_export",
    "frontmatter",
    "roles",
    "plugins",
    "files",
)
_ENTRY_KEYS = ("from", "to", "rewrites", "replace", "drop", "select", "binary", "note")


class _Validator(object):
    """Turns parsed manifest JSON into a :class:`Manifest`, collecting every error."""

    def __init__(self, source_root: Path) -> None:
        self.root = source_root
        self.real_root = _resolve(source_root)
        self.errors = []  # type: List[str]
        self.context = ""  # appended to every location, e.g. " (to engine/x.md)"
        self._listings = {}  # type: Dict[Path, Optional[Set[str]]]

    # -- primitives ----------------------------------------------------------

    def err(self, where: str, message: str) -> None:
        self.errors.append("%s%s: %s" % (where, self.context, message))

    def keys(self, obj: Dict[str, Any], allowed: Sequence[str], where: str, required: Sequence[str] = ()) -> None:
        for key in obj:
            if key not in allowed:
                self.err(where, "unknown key %r (allowed: %s)" % (key, ", ".join(allowed)))
        for key in required:
            if key not in obj:
                self.err(where, "missing required key %r" % key)

    def obj(self, value: Any, where: str) -> Optional[Dict[str, Any]]:
        if not isinstance(value, dict):
            self.err(where, "must be a JSON object")
            return None
        return value

    def array(self, value: Any, where: str) -> Optional[List[Any]]:
        if not isinstance(value, list):
            self.err(where, "must be a JSON array")
            return None
        return value

    def string(self, value: Any, where: str, allow_empty: bool = False, single_line: bool = True) -> Optional[str]:
        if not isinstance(value, str):
            self.err(where, "must be a string")
            return None
        if not allow_empty and not value:
            self.err(where, "must not be empty")
            return None
        if single_line and ("\n" in value or "\r" in value):
            self.err(where, "must be a single line")
            return None
        return value

    def regex(self, value: Any, where: str, line_rule: bool = False) -> Any:
        text = self.string(value, where)
        if text is None:
            return None
        for match in _INLINE_FLAGS_RE.finditer(text):
            group_start = match.end() - len(match.group(0).lstrip("\\"))
            if group_start > 0:  # Python 3.11+ rejects these; 3.9 only warns
                self.err(where, "inline flags such as (?i) must come first in the pattern")
                return None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                compiled = re.compile(text)
        except (re.error, Warning) as exc:
            self.err(where, "invalid regular expression: %s" % exc)
            return None
        if line_rule:
            if _matches_any_line(compiled):
                self.err(where, "pattern matches every line; make it specific")
                return None
        elif _can_match_empty(compiled):
            self.err(where, "pattern can match an empty string")
            return None
        return compiled

    def template(self, regex: Any, value: Any, where: str) -> Optional[str]:
        text = self.string(value, where, allow_empty=True)
        if text is None or regex is None:
            return text
        if _TEMPLATE_BREAK_RE.search(text):
            self.err(where, "replacement must not insert line breaks")
            return None
        try:
            regex.sub(text, "")
        except (re.error, IndexError) as exc:
            self.err(where, "invalid replacement: %s" % exc)
            return None
        return text

    def rule_id(self, value: Any, where: str, seen: Set[str]) -> Optional[str]:
        text = self.string(value, where)
        if text is None:
            return None
        if not _RULE_ID_RE.fullmatch(text):
            self.err(where, "id %r must use lowercase letters, digits, '.', '_' and '-'" % text)
            return None
        if text in seen:
            self.err(where, "duplicate id %r" % text)
            return None
        seen.add(text)
        return text

    # -- paths ---------------------------------------------------------------

    def rel_path(self, value: Any, where: str) -> Optional[str]:
        text = self.string(value, where)
        if text is None:
            return None
        problems = []
        if "\\" in text:
            problems.append("use '/' as the separator")
        if text.startswith("/") or re.match(r"[A-Za-z]:", text):
            problems.append("must be relative")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
            problems.append("must not contain control characters")
        parts = text.split("/")
        if any(part == "" for part in parts):
            problems.append("has an empty segment (leading, trailing or doubled '/')")
        if any(part == ".." for part in parts):
            problems.append("must not contain '..'")
        if any(part == "." for part in parts):
            problems.append("must not contain '.' segments")
        if any(part != part.strip() for part in parts if part):
            problems.append("a segment starts or ends with whitespace")
        if any(part.lower() == ".git" for part in parts):
            problems.append("must not be inside .git")
        for problem in problems:
            self.err(where, "%r %s" % (text, problem))
        return None if problems else text

    def _listing(self, folder: Path) -> Optional[Set[str]]:
        if folder not in self._listings:
            try:
                self._listings[folder] = set(os.listdir(str(folder)))
            except OSError:
                self._listings[folder] = None
        return self._listings[folder]

    def source_file(self, rel: str, where: str) -> bool:
        """Check that ``rel`` names a regular file inside the source, in exact case."""
        folder = self.root
        for part in rel.split("/"):
            names = self._listing(folder)
            if names is None or part not in names:
                if names is not None and part.lower() in {n.lower() for n in names}:
                    self.err(where, "%r differs in letter case from the file on disk" % rel)
                else:
                    self.err(where, "%r does not exist in the source" % rel)
                return False
            folder = folder / part
        real = _resolve(folder)
        if not _is_inside(real, self.real_root):
            self.err(where, "%r resolves outside the source (symlink)" % rel)
            return False
        if not real.is_file():
            self.err(where, "%r is not a regular file" % rel)
            return False
        return True

    # -- sections ------------------------------------------------------------

    def term_map(self, value: Any, where: str, role_values: bool) -> "OrderedDict[str, str]":
        result = OrderedDict()  # type: OrderedDict[str, str]
        mapping = self.obj(value, where)
        if mapping is None:
            return result
        for term, target in mapping.items():
            at = "%s[%r]" % (where, term)
            if not term or term != term.strip() or "\n" in term:
                self.err(at, "terms must be non-empty, single-line and without surrounding spaces")
                continue
            text = self.string(target, at, allow_empty=not role_values)
            if text is None:
                continue
            if role_values and not _ROLE_ID_RE.fullmatch(text):
                self.err(at, "role id %r must be lowercase kebab-case, like 'owner-auditor'" % text)
                continue
            result[term] = text
        return result

    def path_map(self, value: Any, where: str) -> "OrderedDict[str, str]":
        result = OrderedDict()  # type: OrderedDict[str, str]
        mapping = self.obj(value, where)
        if mapping is None:
            return result
        for key, target in mapping.items():
            at = "%s[%r]" % (where, key)
            if not key or key != key.strip() or "\n" in key:
                self.err(at, "keys must be non-empty, single-line and without surrounding spaces")
                continue
            if key.startswith("~") and not key.startswith("~/"):
                self.err(at, "a home key starts with '~/'")
                continue
            text = self.string(target, at, allow_empty=True)
            if text is not None:
                result[key] = text
        return result

    def rewrite_patterns(self, value: Any, where: str) -> List[RewritePattern]:
        out = []  # type: List[RewritePattern]
        items = self.array(value, where)
        seen = set()  # type: Set[str]
        for i, item in enumerate(items or []):
            at = "%s[%d]" % (where, i)
            obj = self.obj(item, at)
            if obj is None:
                continue
            self.keys(obj, ("id", "pattern", "replace", "description"), at, ("id", "pattern", "replace"))
            rid = self.rule_id(obj.get("id"), at + ".id", seen)
            regex = self.regex(obj.get("pattern"), at + ".pattern")
            replace = self.template(regex, obj.get("replace"), at + ".replace")
            description = self.string(obj.get("description", ""), at + ".description", allow_empty=True)
            if rid and regex is not None and replace is not None:
                out.append(RewritePattern(rid, regex, replace, description or ""))
        return out

    def drop_rules(self, value: Any, where: str, scope: str) -> List[DropRule]:
        out = []  # type: List[DropRule]
        items = self.array(value, where)
        seen = set()  # type: Set[str]
        for i, item in enumerate(items or []):
            at = "%s[%d]" % (where, i)
            obj = self.obj(item, at)
            if obj is None:
                continue
            required = ("id",) if scope == "drop_patterns" else ()
            self.keys(obj, ("id", "pattern", "start", "end", "before", "description", "reason"), at, required)
            rid = self.rule_id(obj["id"], at + ".id", seen) if "id" in obj else "drop[%d]" % i
            has_line, has_block = "pattern" in obj, "start" in obj
            if has_line == has_block:
                self.err(at, "set either 'pattern' (one line) or 'start' (a block)")
                continue
            if has_line and ("end" in obj or "before" in obj):
                self.err(at, "'end' and 'before' only go with 'start'")
                continue
            if has_block and ("end" in obj) == ("before" in obj):
                self.err(at, "a block needs exactly one of 'end' (inclusive) or 'before' (exclusive)")
                continue
            reason = self.string(obj.get("reason", obj.get("description", "")), at, allow_empty=True) or ""
            if has_line:
                pattern = self.regex(obj["pattern"], at + ".pattern", line_rule=True)
                if rid and pattern is not None:
                    out.append(DropRule(rid, scope, pattern=pattern, reason=reason))
                continue
            start = self.regex(obj["start"], at + ".start", line_rule=True)
            end = self.regex(obj["end"], at + ".end", line_rule=True) if "end" in obj else None
            before = self.regex(obj["before"], at + ".before", line_rule=True) if "before" in obj else None
            if rid and start is not None and (end is not None or before is not None):
                out.append(DropRule(rid, scope, start=start, end=end, before=before, reason=reason))
        return out

    def flag_patterns(self, value: Any, where: str) -> List[FlagPattern]:
        out = []  # type: List[FlagPattern]
        items = self.array(value, where)
        seen = set(BUILTIN_FLAGS)
        for i, item in enumerate(items or []):
            at = "%s[%d]" % (where, i)
            obj = self.obj(item, at)
            if obj is None:
                continue
            self.keys(obj, ("id", "pattern", "description"), at, ("id", "pattern"))
            if obj.get("id") in BUILTIN_FLAGS:
                self.err(at + ".id", "%r is a built-in flag id" % obj.get("id"))
                continue
            fid = self.rule_id(obj.get("id"), at + ".id", seen)
            regex = self.regex(obj.get("pattern"), at + ".pattern")
            description = self.string(obj.get("description", ""), at + ".description", allow_empty=True)
            if fid and regex is not None:
                out.append(FlagPattern(fid, regex, description or ""))
        return out

    def never_export(self, value: Any, where: str) -> List[str]:
        out = []
        for i, item in enumerate(self.array(value, where) or []):
            text = self.string(item, "%s[%d]" % (where, i))
            if text is not None:
                out.append(text)
        return out

    def frontmatter(self, value: Any, where: str) -> FrontmatterConfig:
        obj = self.obj(value, where)
        if obj is None:
            return FrontmatterConfig()
        self.keys(obj, ("drop_keys", "rename_keys", "list_separator"), where)
        drop = []
        for i, item in enumerate(self.array(obj.get("drop_keys", []), where + ".drop_keys") or []):
            text = self.string(item, "%s.drop_keys[%d]" % (where, i))
            if text is not None:
                drop.append(text)
        rename = []
        for old, new in (self.obj(obj.get("rename_keys", {}), where + ".rename_keys") or {}).items():
            text = self.string(new, "%s.rename_keys[%r]" % (where, old))
            if text is not None and not _META_KEY_RE.fullmatch(text) and text not in PORTABLE_KEYS:
                self.err("%s.rename_keys[%r]" % (where, old), "%r must be a kebab-case key" % text)
            elif text is not None:
                rename.append((old, text))
        separator = self.string(obj.get("list_separator", ", "), where + ".list_separator")
        return FrontmatterConfig(tuple(drop), tuple(rename), separator or ", ")

    def rewrites(self, value: Any, where: str) -> Optional[Tuple[str, ...]]:
        items = self.array(value, where)
        if items is None:
            return None
        names = []
        for i, item in enumerate(items):
            if item not in RULE_SET_ORDER:
                self.err("%s[%d]" % (where, i), "unknown rewrite %r (one of: %s)" % (item, ", ".join(RULE_SET_ORDER)))
            elif item in names:
                self.err("%s[%d]" % (where, i), "%r is listed twice" % item)
            else:
                names.append(item)
        return tuple(sorted(names, key=RULE_SET_ORDER.index))

    def replace_rules(self, value: Any, where: str) -> List[ReplaceRule]:
        out = []  # type: List[ReplaceRule]
        for i, item in enumerate(self.array(value, where) or []):
            at = "%s[%d]" % (where, i)
            obj = self.obj(item, at)
            if obj is None:
                continue
            self.keys(obj, ("find", "with", "regex", "reason"), at, ("find", "with"))
            use_regex = obj.get("regex", False)
            if not isinstance(use_regex, bool):
                self.err(at + ".regex", "must be true or false")
                continue
            reason = self.string(obj.get("reason", ""), at + ".reason", allow_empty=True) or ""
            if use_regex:
                regex = self.regex(obj.get("find"), at + ".find")
                replacement = self.template(regex, obj.get("with"), at + ".with")
                if regex is not None and replacement is not None:
                    out.append(ReplaceRule(i, regex.pattern, replacement, regex, reason))
            else:
                find = self.string(obj.get("find"), at + ".find")
                replacement = self.string(obj.get("with"), at + ".with", allow_empty=True)
                if find is not None and replacement is not None:
                    out.append(ReplaceRule(i, find, replacement, None, reason))
        return out

    def select(self, value: Any, where: str) -> Optional[SelectRule]:
        obj = self.obj(value, where)
        if obj is None:
            return None
        self.keys(obj, ("start", "end", "before"), where, ("start",))
        if ("end" in obj) == ("before" in obj):
            self.err(where, "needs exactly one of 'end' (inclusive) or 'before' (exclusive)")
            return None
        start = self.regex(obj.get("start"), where + ".start", line_rule=True)
        end = self.regex(obj["end"], where + ".end", line_rule=True) if "end" in obj else None
        before = self.regex(obj["before"], where + ".before", line_rule=True) if "before" in obj else None
        if start is None or (end is None and before is None):
            return None
        return SelectRule(start, end, before)

    def entry(self, item: Any, index: int, defaults: Tuple[str, ...], never: List[Tuple[str, Any]]) -> Optional[Entry]:
        where = "files[%d]" % index
        obj = self.obj(item, where)
        if obj is None:
            return None
        self.context = " (to %s)" % obj["to"] if isinstance(obj.get("to"), str) else ""
        try:
            return self._entry(obj, index, where, defaults, never)
        finally:
            self.context = ""

    def _entry(
        self, obj: Dict[str, Any], index: int, where: str, defaults: Tuple[str, ...], never: List[Tuple[str, Any]]
    ) -> Optional[Entry]:
        errors_before = len(self.errors)
        self.keys(obj, _ENTRY_KEYS, where, ("from", "to"))
        source = self.rel_path(obj.get("from"), where + ".from")
        target = self.rel_path(obj.get("to"), where + ".to")
        if target is not None and target.split("/")[0].lower() == "internal":
            self.err(where + ".to", "%r is inside internal/, which never ships" % target)
        if source is not None:
            for pattern, regex in never:
                if regex.fullmatch(source):
                    self.err(where + ".from", "%r matches never_export pattern %r" % (source, pattern))
            self.source_file(source, where + ".from")
        rewrites = self.rewrites(obj["rewrites"], where + ".rewrites") if "rewrites" in obj else defaults
        binary = obj.get("binary", False)
        if not isinstance(binary, bool):
            self.err(where + ".binary", "must be true or false")
            binary = False
        replace = self.replace_rules(obj.get("replace", []), where + ".replace")
        drop = self.drop_rules(obj.get("drop", []), where + ".drop", "drop")
        select = self.select(obj["select"], where + ".select") if "select" in obj else None
        note = self.string(obj.get("note", ""), where + ".note", allow_empty=True) or ""
        if rewrites is None:
            rewrites = ()
        is_skill = target is not None and target.split("/")[-1] == SKILL_FILE
        if binary and ("rewrites" in obj and rewrites or replace or drop or "select" in obj):
            self.err(where, "a binary entry is copied verbatim; drop 'rewrites', 'replace', 'drop' and 'select'")
        if binary:
            rewrites = ()
        if "frontmatter" in rewrites and target is not None and not is_skill:
            self.err(where, "the frontmatter rewrite only applies to a .../<skill-name>/SKILL.md target")
        if is_skill and "frontmatter" not in rewrites:
            self.err(where, "a SKILL.md target must list the 'frontmatter' rewrite so it ships in the portable format")
        if is_skill and target is not None:
            parent = PurePosixPath(target).parent.name
            if not parent or not _SKILL_NAME_RE.fullmatch(parent) or len(parent) > SKILL_NAME_MAX:
                self.err(where + ".to", "skill folder %r must be lowercase letters, digits and single hyphens, at most %d characters" % (parent, SKILL_NAME_MAX))
            elif any(word in parent for word in RESERVED_NAME_WORDS):
                self.err(where + ".to", "skill folder %r must not contain %s" % (parent, " or ".join(RESERVED_NAME_WORDS)))
        if select is not None and "frontmatter" in rewrites:
            self.err(where, "'select' can't be combined with the frontmatter rewrite")
        if len(self.errors) > errors_before or source is None or target is None:
            return None
        return Entry(index, source, target, rewrites, tuple(replace), tuple(drop), select, binary, note)

    def manifest(self, data: Dict[str, Any], path: str, sha256: str) -> Optional[Manifest]:
        self.keys(data, _TOP_KEYS, "manifest", ("schema_version", "files"))
        if data.get("schema_version") != SCHEMA_VERSION:
            self.err("manifest.schema_version", "must be the string %r" % SCHEMA_VERSION)
        description = self.string(data.get("description", ""), "manifest.description", allow_empty=True, single_line=False) or ""
        defaults = DEFAULT_REWRITES  # type: Tuple[str, ...]
        if "defaults" in data:
            obj = self.obj(data["defaults"], "defaults")
            if obj is not None:
                self.keys(obj, ("rewrites",), "defaults")
                if "rewrites" in obj:
                    defaults = self.rewrites(obj["rewrites"], "defaults.rewrites") or ()
        persona = self.term_map(data.get("persona_map", {}), "persona_map", role_values=True)
        owner = self.term_map(data.get("owner_terms", {}), "owner_terms", role_values=False)
        overlap = {t.casefold() for t in persona} & {t.casefold() for t in owner}
        for term in sorted(overlap):
            self.err("owner_terms", "%r is also a persona_map term" % term)
        paths = self.path_map(data.get("path_map", {}), "path_map")
        rewrites = self.rewrite_patterns(data.get("rewrite_patterns", []), "rewrite_patterns")
        drops = self.drop_rules(data.get("drop_patterns", []), "drop_patterns", "drop_patterns")
        flags = self.flag_patterns(data.get("flag_patterns", []), "flag_patterns")
        never = self.never_export(data.get("never_export", []), "never_export")
        never_compiled = [(pattern, glob_regex(pattern)) for pattern in never]
        frontmatter = self.frontmatter(data.get("frontmatter", {}), "frontmatter")
        entries = []  # type: List[Entry]
        files = self.array(data.get("files"), "files")
        if files is not None and not files:
            self.err("files", "lists no files")
        for index, item in enumerate(files or []):
            entry = self.entry(item, index, defaults, never_compiled)
            if entry is not None:
                entries.append(entry)
        roles = self.roles_section(data.get("roles", {}), "roles")
        plugins = self.plugins_section(data.get("plugins", {}), "plugins")
        self._check_targets(entries)
        self._check_roles_and_plugins(entries, roles, plugins)
        if self.errors:
            return None
        return Manifest(
            path, sha256, persona, owner, paths, rewrites, drops, flags, never, frontmatter, entries, description,
            roles, plugins,
        )

    def roles_section(self, value: Any, where: str) -> "OrderedDict[str, OrderedDict[str, Any]]":
        """Role frontmatter for each engine/roles/<id>/soul.md target (adapters/ROLES.md)."""
        result = OrderedDict()  # type: OrderedDict[str, OrderedDict[str, Any]]
        mapping = self.obj(value, where)
        for role_id, spec in (mapping or {}).items():
            at = "%s[%r]" % (where, role_id)
            if not _ROLE_ID_RE.fullmatch(role_id):
                self.err(at, "role id must be lowercase kebab-case, like 'owner-auditor'")
                continue
            obj = self.obj(spec, at)
            if obj is None:
                continue
            self.keys(obj, ROLE_KEYS, at, ("description", "tier"))
            errors_before = len(self.errors)
            description = self.string(obj.get("description"), at + ".description")
            if description is not None and len(description) > DESCRIPTION_MAX:
                self.err(at + ".description", "is longer than %d characters" % DESCRIPTION_MAX)
            if obj.get("tier") not in ROLE_TIERS:
                self.err(at + ".tier", "must be one of %s, never a model name" % ", ".join(ROLE_TIERS))
            for key in ("allowed-tools", "disallowed-tools"):
                if key in obj:
                    self.string(obj[key], "%s.%s" % (at, key))
            if "isolation" in obj and obj["isolation"] not in ("worktree", "none"):
                self.err(at + ".isolation", "must be 'worktree' or 'none'")
            if "main" in obj and not isinstance(obj["main"], bool):
                self.err(at + ".main", "must be true or false")
            if "write-paths" in obj:
                paths = obj["write-paths"]
                if not isinstance(paths, list) or not all(isinstance(p, str) and p.strip() for p in paths):
                    self.err(at + ".write-paths", "must be a list of path globs")
            if len(self.errors) == errors_before:
                result[role_id] = OrderedDict((key, obj[key]) for key in ROLE_KEYS if key in obj)
        return result

    def plugins_section(self, value: Any, where: str) -> "OrderedDict[str, OrderedDict[str, str]]":
        """Plugin manifests to generate for extracted skill packs."""
        result = OrderedDict()  # type: OrderedDict[str, OrderedDict[str, str]]
        mapping = self.obj(value, where)
        names = set()  # type: Set[str]
        for folder, spec in (mapping or {}).items():
            at = "%s[%r]" % (where, folder)
            if not _PLUGIN_DIR_RE.fullmatch(folder):
                self.err(at, "must be packs/<name> or labs/<name>, lowercase kebab-case")
                continue
            obj = self.obj(spec, at)
            if obj is None:
                continue
            self.keys(obj, PLUGIN_KEYS, at, ("name", "description"))
            name = self.string(obj.get("name"), at + ".name")
            description = self.string(obj.get("description"), at + ".description")
            version = self.string(obj.get("version", "0.1.0"), at + ".version")
            if name is not None:
                if not _SKILL_NAME_RE.fullmatch(name) or any(word in name for word in RESERVED_NAME_WORDS):
                    self.err(at + ".name", "%r must be kebab-case without %s" % (name, " or ".join(RESERVED_NAME_WORDS)))
                    name = None
                elif name in names:
                    self.err(at + ".name", "%r is used by another plugin" % name)
                    name = None
                else:
                    names.add(name)
            if version is not None and not _SEMVER_RE.fullmatch(version):
                self.err(at + ".version", "%r must be a semver version such as 0.1.0" % version)
                version = None
            if name and description and version:
                result[folder] = OrderedDict([("name", name), ("description", description), ("version", version)])
        return result

    def _check_roles_and_plugins(self, entries: List[Entry], roles: Dict[str, Any], plugins: Dict[str, Any]) -> None:
        """Every role target has role frontmatter, and generated plugin files collide with nothing."""
        role_targets = set()
        targets = {entry.target for entry in entries}
        for entry in entries:
            match = ROLE_TARGET_RE.fullmatch(entry.target)
            if match:
                role_targets.add(match.group(1))
                if match.group(1) not in roles:
                    self.err(entry.label, "role %r needs an entry in the roles section (adapters/ROLES.md frontmatter)" % match.group(1))
        for role_id in roles:
            if role_id not in role_targets:
                self.err("roles[%r]" % role_id, "no files entry targets engine/roles/%s/soul.md" % role_id)
        for folder in plugins:
            for generated in (folder + "/README.md", folder + "/.claude-plugin/plugin.json"):
                if generated in targets:
                    self.err("plugins[%r]" % folder, "%s is generated; remove the files entry that targets it" % generated)
            if not any(t.startswith(folder + "/skills/") for t in targets):
                self.err("plugins[%r]" % folder, "no files entry puts a skill under %s/skills/" % folder)

    def _check_targets(self, entries: List[Entry]) -> None:
        """Targets must be unique (ignoring case) and never both a file and a folder."""
        seen = {}  # type: Dict[str, Entry]
        folders = {}  # type: Dict[str, Entry]
        for entry in entries:
            key = entry.target.lower()
            if key in seen:
                self.err(entry.label + ".to", "duplicates the target of %s (letter case ignored)" % seen[key].label)
            seen.setdefault(key, entry)
            parts = key.split("/")
            for depth in range(1, len(parts)):
                folders.setdefault("/".join(parts[:depth]), entry)
        for key, entry in seen.items():
            if key in folders:
                self.err(entry.label + ".to", "is also a folder for %s" % folders[key].label)


def load_manifest(path: Union[str, Path], source_root: Union[str, Path]) -> Tuple[Optional[Manifest], List[str]]:
    """Load and validate a manifest against ``source_root``.

    Returns ``(manifest, [])`` when valid and ``(None, errors)`` otherwise.
    Every problem is reported, not just the first.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, ["manifest: can't read %s: %s" % (path, exc.strerror or exc)]
    sha256 = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, ["manifest: not UTF-8 text"]
    try:
        data = json.loads(text, object_pairs_hook=_pairs_without_duplicates)
    except _DuplicateKeyError as exc:
        return None, ["manifest: duplicate key %r" % exc.key]
    except ValueError as exc:
        lineno = getattr(exc, "lineno", None)
        where = " at line %d column %d" % (lineno, exc.colno) if lineno else ""  # type: ignore[attr-defined]
        return None, ["manifest: invalid JSON%s: %s" % (where, getattr(exc, "msg", exc))]
    if not isinstance(data, dict):
        return None, ["manifest: the top level must be a JSON object"]
    validator = _Validator(Path(source_root))
    manifest = validator.manifest(data, str(path), sha256)
    return (manifest, []) if manifest is not None else (None, validator.errors)


# ---------------------------------------------------------------------------
# Term and path matching
# ---------------------------------------------------------------------------


def _term_regex(term: str) -> str:
    """A term's regex: escaped words joined by flexible whitespace."""
    return r"\s+".join(re.escape(word) for word in term.split())


class TermMap(object):
    """Whole-word replacement of a set of terms.

    ``rewrite_re`` uses word-character boundaries, so ``X_Y`` is not touched.
    ``residual_re`` uses letter-and-digit boundaries, so it also finds a term
    inside ``X_Y`` identifiers; anything it finds after rewriting is flagged.
    """

    def __init__(self, mapping: "OrderedDict[str, str]", ignore_case: bool) -> None:
        self.mapping = OrderedDict(mapping)
        self.ignore_case = ignore_case
        terms = sorted(self.mapping, key=lambda term: (-len(term), term))
        self._by_key = {}  # type: Dict[str, str]
        for term in terms:
            self._by_key.setdefault(self._key(term), term)
        if terms:
            alternation = "|".join(_term_regex(term) for term in terms)
            flags = re.IGNORECASE if ignore_case else 0
            self.rewrite_re = re.compile(r"(?<!\w)(?:%s)(?!\w)" % alternation, flags)  # type: Optional[Any]
            self.residual_re = re.compile(r"(?<![^\W_])(?:%s)(?![^\W_])" % alternation, flags)  # type: Optional[Any]
        else:
            self.rewrite_re = None
            self.residual_re = None

    def _key(self, text: str) -> str:
        key = " ".join(text.split())
        return key.casefold() if self.ignore_case else key

    def term_for(self, matched: str) -> Optional[str]:
        """The manifest term a match came from."""
        return self._by_key.get(self._key(matched))


class _PathPass(object):
    """One combined regex over a group of path_map keys, longest key first."""

    def __init__(self, mapping: "OrderedDict[str, str]", boundary: str) -> None:
        self.items = []  # type: List[Tuple[str, Any, str]]
        alternatives = []
        for key in sorted(mapping, key=lambda k: (-len(k), k)):
            body = HOME_PREFIX_RE + re.escape(key[2:]) if key.startswith("~/") else re.escape(key)
            if not key.endswith("/"):
                body += r"(?![\w-]|\.\w)"
            self.items.append((key, re.compile(body), mapping[key]))
            alternatives.append("(?:%s)" % body)
        self.regex = re.compile(boundary + "(?:%s)" % "|".join(alternatives)) if alternatives else None

    def resolve(self, text: str) -> Tuple[str, str]:
        """The ``(key, replacement)`` whose pattern matches all of ``text``."""
        for key, regex, value in self.items:
            if regex.fullmatch(text):
                return key, value
        raise LookupError(text)  # unreachable: text came from self.regex


class PathMap(object):
    """Prefix replacement for paths. A key starting with ``~/`` matches any home.

    Absolute and home keys run first, then relative keys, so
    ``~/repo/docs/x.md`` can map through ``~/repo/`` and then ``docs/``.
    Keys match at a path boundary, never in the middle of another path, and
    the longest key wins. A relative key keeps a leading ``./``.
    """

    def __init__(self, mapping: "OrderedDict[str, str]") -> None:
        absolute = OrderedDict((k, v) for k, v in mapping.items() if k.startswith(("~/", "/")))
        relative = OrderedDict((k, v) for k, v in mapping.items() if not k.startswith(("~/", "/")))
        passes = (
            _PathPass(absolute, r"(?<![\w./~$])()"),
            _PathPass(relative, r"(?<![\w.\-/~$])(\./)?"),
        )
        self.passes = [p for p in passes if p.regex is not None]  # type: List[_PathPass]


@dataclass
class Compiled:
    """Manifest rules compiled once, shared by every entry."""

    manifest: Manifest
    owner: TermMap
    persona: TermMap
    paths: PathMap
    role_ids: Set[str]

    @classmethod
    def build(cls, manifest: Manifest) -> "Compiled":
        return cls(
            manifest,
            TermMap(manifest.owner_terms, ignore_case=True),
            TermMap(manifest.persona_map, ignore_case=False),
            PathMap(manifest.path_map),
            set(manifest.persona_map.values()),
        )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """One applied rewrite. ``summary`` groups events across files in the report."""

    kind: str
    rule: str
    change: str
    summary: str
    line: int
    count: int = 1


@dataclass(frozen=True)
class Flag:
    """Something the tool won't rewrite on its own. Every flag blocks the run."""

    flag: str
    line: int
    col: int
    text: str
    message: str


@dataclass
class FileResult:
    entry: Entry
    data: bytes = b""
    mode: int = 0o644
    events: List[Event] = field(default_factory=list)
    flags: List[Flag] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source_lines: int = 0
    output_lines: int = 0
    #: Source line -> output text, or None when the line was dropped. For a
    #: converted frontmatter block every source line maps to the whole new block.
    line_map: Dict[int, Optional[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Frontmatter: parse the old style, write the portable style
# ---------------------------------------------------------------------------


class FrontmatterError(ValueError):
    """Frontmatter outside the supported YAML subset, with a source line."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__("line %d: %s" % (line, message))
        self.line = line
        self.message = message


@dataclass
class FmItem:
    key: str
    value: Any  # str, list of str, or OrderedDict of str -> str | list of str
    line: int
    #: For a block list, the line of each item; for a block map, the line of
    #: each key. None when the value sits on the key's own line.
    value_lines: Any = None

    def line_of(self, index: Any) -> int:
        if isinstance(self.value_lines, list) and isinstance(index, int) and index < len(self.value_lines):
            return self.value_lines[index]
        if isinstance(self.value_lines, dict) and index in self.value_lines:
            return self.value_lines[index]
        return self.line


_FM_KEY_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*:(?:[ \t]+(.*))?")
_FM_ITEM_RE = re.compile(r"([ \t]*)-(?:[ \t]+(.*))?")
_FM_SUBKEY_RE = re.compile(r"([ \t]+)([A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*:(?:[ \t]+(.*))?")
_DOUBLE_ESCAPES = {
    "0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f",
    "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
    "L": "\u2028", "P": "\u2029",
}
_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}


def _scan_double(text: str, start: int, line: int) -> Tuple[str, int]:
    out = []
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == '"':
            return "".join(out), i + 1
        if ch == "\\":
            if i + 1 >= len(text):
                break
            esc = text[i + 1]
            if esc in _DOUBLE_ESCAPES:
                out.append(_DOUBLE_ESCAPES[esc])
                i += 2
                continue
            if esc in _HEX_ESCAPES:
                width = _HEX_ESCAPES[esc]
                digits = text[i + 2 : i + 2 + width]
                if len(digits) != width or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
                    raise FrontmatterError(line, "invalid \\%s escape" % esc)
                out.append(chr(int(digits, 16)))
                i += 2 + width
                continue
            raise FrontmatterError(line, "unknown escape \\%s in a double-quoted string" % esc)
        out.append(ch)
        i += 1
    raise FrontmatterError(line, "a double-quoted string must close on the same line")


def _scan_single(text: str, start: int, line: int) -> Tuple[str, int]:
    out = []
    i = start + 1
    while i < len(text):
        if text[i] == "'":
            if text[i + 1 : i + 2] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(text[i])
        i += 1
    raise FrontmatterError(line, "a single-quoted string must close on the same line")


def _scan_flow_scalar(text: str, i: int, line: int, stops: str) -> Tuple[str, int]:
    """Scan one scalar inside a flow collection, stopping at ``stops``."""
    if text[i] == '"':
        return _scan_double(text, i, line)
    if text[i] == "'":
        return _scan_single(text, i, line)
    if text[i] in "[{":
        raise FrontmatterError(line, "nested flow collections are not supported")
    j = i
    while j < len(text) and text[j] not in stops:
        j += 1
    return text[i:j].strip(), j


def _scan_flow(text: str, line: int) -> Tuple[Any, int]:
    """Scan ``[a, b]`` or ``{k: v}`` starting at ``text[0]``; one line only."""
    is_seq = text[0] == "["
    close = "]" if is_seq else "}"
    items = []  # type: List[str]
    pairs = OrderedDict()  # type: OrderedDict[str, str]
    i = 1
    while True:
        while i < len(text) and text[i] in " \t":
            i += 1
        if i >= len(text):
            raise FrontmatterError(line, "a flow collection must close on the same line")
        if text[i] == close:
            return (items if is_seq else pairs), i + 1
        if is_seq:
            value, i = _scan_flow_scalar(text, i, line, ",]")
            if value == "":
                raise FrontmatterError(line, "empty item in a flow list")
            items.append(value)
        else:
            key, i = _scan_flow_scalar(text, i, line, ":,}")
            if i >= len(text) or text[i] != ":" or not key:
                raise FrontmatterError(line, "expected 'key: value' in a flow mapping")
            i += 1
            while i < len(text) and text[i] in " \t":
                i += 1
            if i < len(text) and text[i] in ",}":
                raise FrontmatterError(line, "empty value for %r in a flow mapping" % key)
            value, i = _scan_flow_scalar(text, i, line, ",}")
            if key in pairs:
                raise FrontmatterError(line, "duplicate key %r in a flow mapping" % key)
            pairs[key] = value
        while i < len(text) and text[i] in " \t":
            i += 1
        if i < len(text) and text[i] == ",":
            i += 1
        elif i < len(text) and text[i] != close:
            raise FrontmatterError(line, "expected ',' or %r in a flow collection" % close)


def _parse_inline(text: str, line: int, allow_flow: bool = True) -> Tuple[Any, bool]:
    """Parse a value written on one line. Returns ``(value, had_comment)``."""
    text = text.strip()
    first = text[:1]
    if first == '"':
        value, end = _scan_double(text, 0, line)  # type: Tuple[Any, int]
    elif first == "'":
        value, end = _scan_single(text, 0, line)
    elif first in ("[", "{"):
        if not allow_flow:
            raise FrontmatterError(line, "a list item must be a single value")
        value, end = _scan_flow(text, line)
    elif first in ("|", ">"):
        raise FrontmatterError(line, "block scalars (| and >) are not supported; write the value on one line")
    elif first in ("&", "*", "!"):
        raise FrontmatterError(line, "anchors, aliases and tags are not supported")
    elif first in ("@", "`"):
        raise FrontmatterError(line, "a plain value can't start with %r; quote it" % first)
    elif first == "-" and text[1:2] in ("", " ", "\t"):
        raise FrontmatterError(line, "a list item can't appear here")
    else:
        match = re.search(r"[ \t]#", text)
        end = match.start() if match else len(text)
        value = text[:end].rstrip()
    rest = text[end:].strip()
    if rest and not rest.startswith("#"):
        raise FrontmatterError(line, "unexpected text after the value: %r" % _shorten(rest, 30))
    return value, bool(rest)


def parse_frontmatter(lines: Sequence[Tuple[int, str]]) -> Tuple[List[FmItem], List[int]]:
    """Parse old-style frontmatter lines (without the ``---`` markers).

    Supports ``key: value`` with plain, single- or double-quoted scalars,
    one-line flow lists and maps, block lists and one level of block map, and
    comments. Anything else raises :class:`FrontmatterError`. Values stay the
    text the source wrote (no number or boolean conversion), so ``1.0`` stays
    ``"1.0"``. Returns the items and the line numbers that held comments.
    """
    items = []  # type: List[FmItem]
    comments = []  # type: List[int]
    seen = set()  # type: Set[str]
    i = 0
    while i < len(lines):
        number, text = lines[i]
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            if stripped:
                comments.append(number)
            i += 1
            continue
        if text[:1] in (" ", "\t"):
            raise FrontmatterError(number, "unexpected indentation")
        match = _FM_KEY_RE.fullmatch(text.rstrip())
        if not match:
            raise FrontmatterError(number, "expected 'key: value'")
        key, raw = match.group(1), match.group(2)
        if key in seen:
            raise FrontmatterError(number, "duplicate key %r" % key)
        seen.add(key)
        if raw is not None and raw.strip():
            value, had_comment = _parse_inline(raw, number)
            if had_comment:
                comments.append(number)
            items.append(FmItem(key, value, number))
            i += 1
            continue
        block, value_lines, i = _parse_block(lines, i + 1, number, comments)
        items.append(FmItem(key, block, number, value_lines))
    return items, comments


def _parse_block(lines: Sequence[Tuple[int, str]], i: int, key_line: int, comments: List[int]) -> Tuple[Any, Any, int]:
    """Parse the block list or block map under the key on ``key_line``.

    Returns ``(value, value_lines, next index)``.
    """
    kind = None  # "list" or "map"
    indent = None  # type: Optional[str]
    items = []  # type: List[str]
    item_lines = []  # type: List[int]
    pairs = OrderedDict()  # type: OrderedDict[str, Any]
    pair_lines = {}  # type: Dict[str, int]
    while i < len(lines):
        number, text = lines[i]
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            if stripped:
                comments.append(number)
            i += 1
            continue
        lead = text[: len(text) - len(text.lstrip(" \t"))]
        if "\t" in lead:
            raise FrontmatterError(number, "tabs can't indent YAML")
        item = _FM_ITEM_RE.fullmatch(text.rstrip())
        if item and kind in (None, "list"):
            if indent is not None and item.group(1) != indent:
                raise FrontmatterError(number, "list items must share one indentation")
            if not (item.group(2) or "").strip():
                raise FrontmatterError(number, "empty or nested list items are not supported")
            value, had_comment = _parse_inline(item.group(2), number, allow_flow=False)
            if had_comment:
                comments.append(number)
            kind, indent = "list", item.group(1)
            items.append(value)
            item_lines.append(number)
            i += 1
            continue
        if not lead:
            break
        sub = _FM_SUBKEY_RE.fullmatch(text.rstrip())
        if sub and kind in (None, "map"):
            if indent is not None and sub.group(1) != indent:
                raise FrontmatterError(number, "map keys must share one indentation")
            sub_key, raw = sub.group(2), sub.group(3)
            if not (raw or "").strip():
                raise FrontmatterError(number, "nesting deeper than one level is not supported")
            if sub_key in pairs:
                raise FrontmatterError(number, "duplicate key %r" % sub_key)
            value, had_comment = _parse_inline(raw, number)
            if isinstance(value, OrderedDict):
                raise FrontmatterError(number, "nesting deeper than one level is not supported")
            if had_comment:
                comments.append(number)
            kind, indent = "map", sub.group(1)
            pairs[sub_key] = value
            pair_lines[sub_key] = number
            i += 1
            continue
        raise FrontmatterError(number, "unexpected line inside the block under line %d" % key_line)
    if kind == "list":
        return items, item_lines, i
    if kind == "map":
        return pairs, pair_lines, i
    return "", None, i


def _norm_key(key: str) -> str:
    return key.strip().lower().replace("_", "-")


def yaml_quote(text: str) -> str:
    """Quote ``text`` as a YAML double-quoted scalar any YAML reader sees as a string."""
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif code < 0x20 or 0x7F <= code <= 0x9F or ch in "\u2028\u2029\ufeff":
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


#: Callback that runs the in-line rewrite steps on one value:
#: ``(text, source line, prose) -> rewritten text``.
ValueRewriter = Callable[[str, int, bool], str]


def convert_frontmatter(
    items: Sequence[FmItem],
    target: str,
    config: FrontmatterConfig,
    role_ids: Set[str],
    rewrite: ValueRewriter,
    start_line: int = 1,
) -> Tuple[List[str], List[Event], List[Flag]]:
    """Convert parsed old-style frontmatter into portable frontmatter lines.

    * ``name`` comes from the skill folder in ``target``, so it always
      matches the folder (a different old name is reported as a rename).
    * ``description``, ``license``, ``compatibility`` and ``allowed-tools``
      stay at the top level; ``allowed-tools`` lists become one
      space-separated string.
    * Every other key moves under ``metadata``, normalized to kebab-case,
      with its value as a string: lists are joined with the configured
      separator and maps are flattened (``eval: {score: 9}`` becomes
      ``eval-score``). Keys in ``drop_keys`` are dropped.

    Returns the lines (``---`` markers included, no line endings), the events
    and the flags. Values go through ``rewrite`` before conversion.

    * ``status: stable`` becomes ``experimental``: stable is earned by a
      qualifying eval run in this repository, never carried over.
    """
    events = []  # type: List[Event]
    flags = []  # type: List[Flag]
    name = PurePosixPath(target).parent.name
    portable = OrderedDict()  # type: OrderedDict[str, str]
    metadata = OrderedDict()  # type: OrderedDict[str, str]
    origin = {}  # type: Dict[str, str]
    first_line = start_line

    def flag(kind: str, line: int, text: str, message: str) -> None:
        flags.append(Flag(kind, line, 0, text, message))

    def event(rule: str, change: str, summary: str, line: int) -> None:
        events.append(Event("frontmatter", rule, change, summary, line))

    def add_meta(key: str, value: Any, line: int, shown: str, item: Optional[FmItem] = None) -> None:
        if isinstance(value, OrderedDict):
            for sub_key, sub_value in value.items():
                sub_line = item.line_of(sub_key) if item is not None else line
                add_meta("%s-%s" % (key, _norm_key(sub_key)), sub_value, sub_line, "%s.%s" % (shown, sub_key))
            return
        if isinstance(value, list):
            text = config.list_separator.join(
                rewrite(part, item.line_of(i) if item is not None else line, False) for i, part in enumerate(value)
            )
        else:
            text = rewrite(value, line, False)
        if not _META_KEY_RE.fullmatch(key):
            flag("frontmatter-key", line, shown, "metadata key %r isn't kebab-case" % key)
            return
        if key in metadata or key in PORTABLE_KEYS:
            flag("frontmatter-key", line, shown, "%r collides with %r as metadata key %r" % (shown, origin.get(key, key), key))
            return
        metadata[key] = text
        origin[key] = shown
        suffix = " (list joined)" if isinstance(value, list) else ""
        event("metadata", "`%s` \u2192 `metadata.%s`%s" % (shown, key, suffix), "keys moved under metadata", line)

    for item in items:
        normalized = _norm_key(item.key)
        if item.key in config.drop_keys or normalized in config.drop_keys:
            event("drop-key", "`%s` dropped" % item.key, "keys dropped (drop_keys)", item.line)
            continue
        key = config.rename(item.key) or config.rename(normalized) or normalized
        if key == "name":
            old = item.value if isinstance(item.value, str) else ""
            if old != name:
                event("name", "name `%s` \u2192 `%s` (from the skill folder)" % (old, name), "names set from the skill folder", item.line)
            continue
        if key in ("description", "license", "compatibility"):
            if not isinstance(item.value, str):
                flag("frontmatter-field", item.line, item.key, "%s must be a single value" % key)
                continue
            portable[key] = rewrite(item.value, item.line, key != "license")
            continue
        if key == "allowed-tools":
            if isinstance(item.value, list):
                portable[key] = " ".join(rewrite(tool, item.line_of(i), False) for i, tool in enumerate(item.value))
                event("allowed-tools", "`allowed-tools` list \u2192 space-separated string", "allowed-tools lists joined", item.line)
            elif isinstance(item.value, str):
                portable[key] = rewrite(item.value, item.line, False)
            else:
                flag("frontmatter-field", item.line, item.key, "allowed-tools must be a string or a list")
            continue
        if key == "metadata":
            if not isinstance(item.value, OrderedDict):
                flag("frontmatter-field", item.line, item.key, "metadata must be a map")
                continue
            for sub_key, sub_value in item.value.items():
                add_meta(_norm_key(sub_key), sub_value, item.line_of(sub_key), "metadata.%s" % sub_key)
            continue
        add_meta(key, item.value, item.line, item.key, item)

    if metadata.get("status") == "stable":
        metadata["status"] = "experimental"
        event("status", "`status: stable` \u2192 `experimental` (stable is earned by an eval run here)",
              "stable skills demoted to experimental", first_line)

    description = portable.get("description")
    if description is None:
        flag("frontmatter-description", first_line, "description", "description is missing; write one in the source")
    elif not description.strip():
        flag("frontmatter-description", first_line, "description", "description is empty")
    else:
        if len(description) > DESCRIPTION_MAX:
            flag("frontmatter-description", first_line, _shorten(description, 40), "description has %d characters; the limit is %d" % (len(description), DESCRIPTION_MAX))
        if "<" in description or ">" in description:
            flag("frontmatter-description", first_line, _shorten(description, 40), "description holds < or >; uploads reject XML tags")
    compatibility = portable.get("compatibility")
    if compatibility is not None and len(compatibility) > COMPATIBILITY_MAX:
        flag("frontmatter-field", first_line, "compatibility", "compatibility has %d characters; the limit is %d" % (len(compatibility), COMPATIBILITY_MAX))
    if "roles" in metadata and role_ids:
        for role in (part.strip() for part in metadata["roles"].split(",")):
            if role not in role_ids:
                flag("frontmatter-role", first_line, role, "%r isn't a role id from persona_map (%s)" % (role, ", ".join(sorted(role_ids))))

    lines = ["---", "name: %s" % name]
    if description is not None:
        lines.append("description: %s" % yaml_quote(description))
    for key in ("license", "compatibility", "allowed-tools"):
        if key in portable:
            lines.append("%s: %s" % (key, yaml_quote(portable[key])))
    if metadata:
        lines.append("metadata:")
        lines.extend("  %s: %s" % (key, yaml_quote(metadata[key])) for key in sorted(metadata))
    lines.append("---")
    return lines, events, flags


# ---------------------------------------------------------------------------
# Transforming one file
# ---------------------------------------------------------------------------


def _sentence_start(prefix: str, fresh: bool) -> bool:
    """True when text after ``prefix`` starts a sentence (prose only).

    ``fresh`` says whether this line starts a new sentence when nothing but
    whitespace comes before the term (False for a wrapped continuation line).
    """
    if _BLOCK_PREFIX_RE.fullmatch(prefix):
        return fresh if not prefix.strip() else True
    return bool(_AFTER_STOP_RE.search(prefix))


def _starts_fresh(previous: Optional[str]) -> bool:
    """Does the line after ``previous`` start a new sentence?"""
    if previous is None:
        return True
    text = previous.strip()
    if not text or text.startswith(("#", "|", "```", "~~~", "<!--", "---")):
        return True
    return text.rstrip("*_\"')]").endswith((".", "!", "?", ":"))


class FileTransformer(object):
    """Applies one entry's rules to one file and records events and flags."""

    def __init__(self, compiled: Compiled, entry: Entry, result: FileResult) -> None:
        self.c = compiled
        self.m = compiled.manifest
        self.entry = entry
        self.result = result
        self.selected = set(entry.rewrites)
        self.replace_hits = [0] * len(entry.replace)
        self.drop_hits = {}  # type: Dict[str, int]

    # -- recording -----------------------------------------------------------

    def _event(self, kind: str, rule: str, change: str, line: int, count: int = 1, summary: Optional[str] = None) -> None:
        self.result.events.append(Event(kind, rule, change, summary or change, line, count))

    def _flag(self, kind: str, line: int, col: int, text: str, message: str) -> None:
        self.result.flags.append(Flag(kind, line, col, text, message))

    # -- in-line steps -------------------------------------------------------

    def rewrite(self, text: str, line: int, prose: bool, fresh: bool = True) -> str:
        """Run every in-line step on ``text`` (one line, or one frontmatter value)."""
        text = self._replace(text, line)
        if "rewrite_patterns" in self.selected:
            for pattern in self.m.rewrite_patterns:
                text, count = pattern.regex.subn(pattern.replace, text)
                if count:
                    label = "`%s`" % pattern.id + (": %s" % pattern.description if pattern.description else "")
                    self._event("rewrite_patterns", pattern.id, label, line, count)
        if "path_map" in self.selected:
            for path_pass in self.c.paths.passes:
                text = path_pass.regex.sub(lambda m: self._path_sub(m, path_pass, line), text)
        for match in HOME_PATH_RE.finditer(text):
            self._flag("home-path", line, match.start(), match.group(0), BUILTIN_FLAGS["home-path"])
        if "owner_terms" in self.selected and self.c.owner.rewrite_re is not None:
            text = self.c.owner.rewrite_re.sub(lambda m: self._term_sub(m, self.c.owner, "owner_terms", line, prose, fresh), text)
        if "persona_map" in self.selected and self.c.persona.rewrite_re is not None:
            text = self.c.persona.rewrite_re.sub(lambda m: self._term_sub(m, self.c.persona, "persona_map", line, prose, fresh), text)
        self.scan(text, line)
        return text

    def scan(self, text: str, line: int) -> None:
        """Flag what no rewrite may leave behind: flag patterns and residual terms."""
        for pattern in self.m.flag_patterns:
            for match in pattern.regex.finditer(text):
                self._flag(pattern.id, line, match.start(), match.group(0), pattern.description or "matches flag pattern %r" % pattern.id)
        for kind, terms in (("persona-name", self.c.persona), ("owner-term", self.c.owner)):
            if terms.residual_re is not None:
                for match in terms.residual_re.finditer(text):
                    self._flag(kind, line, match.start(), match.group(0), BUILTIN_FLAGS[kind])

    def _replace(self, text: str, line: int) -> str:
        for rule in self.entry.replace:
            if rule.regex is not None:
                text, count = rule.regex.subn(rule.replacement, text)
            else:
                count = text.count(rule.find)
                if count:
                    text = text.replace(rule.find, rule.replacement)
            if count:
                self.replace_hits[rule.index] += count
                change = "replace[%d]: `%s` \u2192 `%s`" % (rule.index, _shorten(rule.find, 40), _shorten(rule.replacement, 40))
                self._event("replace", "replace[%d]" % rule.index, change, line, count, "entry replace rules")
        return text

    def _path_sub(self, match: Any, path_pass: _PathPass, line: int) -> str:
        dot = match.group(1) or ""
        matched = match.group(0)[len(dot) :]
        key, value = path_pass.resolve(matched)
        if matched != value:  # an identity mapping such as ~/.claude/ is not a rewrite
            self._event("path_map", key, "`%s` \u2192 %s" % (key, "`%s`" % value if value else "(empty)"), line)
        return dot + value

    def _term_sub(self, match: Any, terms: TermMap, kind: str, line: int, prose: bool, fresh: bool) -> str:
        matched = match.group(0)
        term = terms.term_for(matched)
        if term is None:
            return matched
        replacement = terms.mapping[term]
        if " ".join(matched.split()) == term:
            out = replacement
            if prose and _sentence_start(match.string[: match.start()], fresh):
                out = _capitalize(out)
        elif matched.isupper() and not term.isupper():
            out = replacement.upper()
        else:
            return matched  # a case variant: left for the residual check to flag
        if out != matched:
            self._event(kind, term, "`%s` \u2192 `%s`" % (term, replacement), line)
        return out

    # -- whole-line steps ----------------------------------------------------

    def select(self, lines: List[Tuple[int, str, str]]) -> List[Tuple[int, str, str]]:
        rule = self.entry.select
        if rule is None:
            return lines
        start = next((i for i, (_, content, _) in enumerate(lines) if rule.start.search(content)), None)
        if start is None:
            self._flag("select-not-found", 1, 0, rule.start.pattern, BUILTIN_FLAGS["select-not-found"])
            return []
        stop = len(lines)
        for j in range(start + 1, len(lines)):
            content = lines[j][1]
            if rule.end is not None and rule.end.search(content):
                stop = j + 1
                break
            if rule.before is not None and rule.before.search(content):
                stop = j
                break
        else:
            if rule.end is not None:
                self._flag("unterminated-select", lines[start][0], 0, rule.end.pattern, BUILTIN_FLAGS["unterminated-select"])
        kept = lines[start:stop]
        if kept:
            self._event("select", "select", "kept lines %d-%d" % (kept[0][0], kept[-1][0]), kept[0][0], 1, "entry select")
        return kept

    def drops(self, lines: List[Tuple[int, str, str]]) -> List[Tuple[int, str, str]]:
        rules = list(self.entry.drop)
        if "drop_patterns" in self.selected:
            rules.extend(self.m.drop_patterns)
        if not rules:
            return lines
        kept = []
        i = 0
        while i < len(lines):
            number, content, _ = lines[i]
            hit = None
            start_match = None
            for rule in rules:
                if rule.pattern is not None and rule.pattern.search(content):
                    hit = rule
                    break
                if rule.start is not None:
                    start_match = rule.start.search(content)
                    if start_match:
                        hit = rule
                        break
            if hit is None:
                kept.append(lines[i])
                i += 1
                continue
            stop = i + 1
            if hit.start is not None and not (hit.end is not None and start_match is not None and hit.end.search(content, start_match.end())):
                stop = len(lines)
                for j in range(i + 1, len(lines)):
                    if hit.end is not None and hit.end.search(lines[j][1]):
                        stop = j + 1
                        break
                    if hit.before is not None and hit.before.search(lines[j][1]):
                        stop = j
                        break
                else:
                    if hit.end is not None:
                        self._flag("unterminated-block", number, 0, hit.id, BUILTIN_FLAGS["unterminated-block"] + " (%s)" % hit.id)
            for k in range(i, stop):
                kind = hit.scope
                summary = "`%s`: lines dropped" % hit.id if kind == "drop_patterns" else "entry drop rules"
                self._event(kind, hit.id, "`%s`: line dropped" % hit.id, lines[k][0], 1, summary)
                self.result.line_map[lines[k][0]] = None
            self.drop_hits[hit.id] = self.drop_hits.get(hit.id, 0) + 1
            i = stop
        return kept

    def text_lines(self, lines: List[Tuple[int, str, str]], prose_file: bool) -> List[Tuple[int, str, str]]:
        """Run the in-line steps on every kept line, tracking code fences."""
        out = []
        fence = None  # type: Optional[Tuple[str, int]]
        previous = None  # type: Optional[str]
        for number, content, eol in lines:
            in_code = not prose_file
            if prose_file:
                opener = _FENCE_RE.match(content)
                if fence is None and opener:
                    fence = (opener.group(1)[0], len(opener.group(1)))
                    in_code = True
                elif fence is not None:
                    in_code = True
                    closer = re.fullmatch(r" {0,3}(`{3,}|~{3,})[ \t]*", content)
                    if closer and closer.group(1)[0] == fence[0] and len(closer.group(1)) >= fence[1]:
                        fence = None
            new = self.rewrite(content, number, prose=not in_code, fresh=_starts_fresh(previous))
            self.result.line_map[number] = new
            out.append((number, new, eol))
            previous = new
        return out

    def frontmatter_file(self, lines: List[Tuple[int, str, str]]) -> List[Tuple[int, str, str]]:
        """Convert the frontmatter block, then transform the body as prose."""
        close = None
        if lines and re.fullmatch(r"---[ \t]*", lines[0][1]):
            close = next((i for i in range(1, len(lines)) if re.fullmatch(r"(?:---|\.\.\.)[ \t]*", lines[i][1])), None)
        if close is None:
            self._flag("frontmatter-missing", 1, 0, self.entry.source, BUILTIN_FLAGS["frontmatter-missing"])
            return self.text_lines(self.drops(lines), prose_file=True)
        block = lines[1:close]
        body = lines[close + 1 :]
        eol = lines[0][2] or "\n"
        try:
            items, comments = parse_frontmatter([(number, content) for number, content, _ in block])
        except FrontmatterError as exc:
            self._flag("frontmatter-syntax", exc.line, 0, exc.message, BUILTIN_FLAGS["frontmatter-syntax"] + ": " + exc.message)
            head = [lines[0]] + self.text_lines(block, prose_file=False) + [lines[close]]
            return head + self.text_lines(self.drops(body), prose_file=True)
        for number in comments:
            self._event("frontmatter", "comment", "comment removed", number, 1, "frontmatter comments removed")
        for item in items:
            self.scan(item.key, item.line)
        new_lines, events, flags = convert_frontmatter(
            items,
            self.entry.target,
            self.m.frontmatter,
            self.c.role_ids,
            lambda text, line, prose: self.rewrite(text, line, prose, fresh=True),
            start_line=lines[0][0],
        )
        self._event("frontmatter", "convert", "converted to the portable format", lines[0][0], 1, "frontmatter converted to the portable format")
        self.result.events.extend(events)
        self.result.flags.extend(flags)
        joined = "\n".join(new_lines)
        for number, _, _ in lines[: close + 1]:
            self.result.line_map[number] = joined
        head = [(0, text, eol) for text in new_lines]
        return head + self.text_lines(self.drops(body), prose_file=True)

    def finish(self) -> None:
        """Warn about entry rules that matched nothing (stale or mistyped)."""
        for rule in self.entry.replace:
            if not self.replace_hits[rule.index]:
                self.result.warnings.append("%s: replace[%d] matched nothing" % (self.entry.label, rule.index))
        for rule in self.entry.drop:
            if not self.drop_hits.get(rule.id):
                self.result.warnings.append("%s: drop rule %s matched nothing" % (self.entry.label, rule.id))


def transform_entry(compiled: Compiled, entry: Entry, raw: bytes, mode: int = 0o644) -> FileResult:
    """Apply ``entry``'s rules to the source bytes ``raw`` and return the result."""
    result = FileResult(entry, mode=0o755 if mode & 0o111 else 0o644)
    if entry.binary:
        result.data = raw
        result.events.append(Event("binary", "binary", "copied verbatim (binary)", "binary files copied verbatim", 1))
        return result
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = raw[: exc.start].count(b"\n") + 1
        result.flags.append(Flag("not-utf8", line, 0, entry.source, BUILTIN_FLAGS["not-utf8"]))
        result.data = raw
        return result
    if "\x00" in text:
        line = text[: text.index("\x00")].count("\n") + 1
        result.flags.append(Flag("binary-content", line, 0, entry.source, BUILTIN_FLAGS["binary-content"]))
        result.data = raw
        return result
    bom = text.startswith("\ufeff")
    if bom:
        text = text[1:]
    lines = _split_lines(text)
    result.source_lines = len(lines)
    transformer = FileTransformer(compiled, entry, result)
    if "frontmatter" in entry.rewrites:
        out = transformer.frontmatter_file(lines)
    else:
        prose = entry.target.lower().endswith(PROSE_SUFFIXES)
        selected = transformer.select(lines) if entry.select is not None else lines
        if entry.select is not None:
            kept = {number for number, _, _ in selected}
            for number, _, _ in lines:
                if number not in kept:
                    result.line_map[number] = None
        out = transformer.text_lines(transformer.drops(selected), prose_file=prose)
    transformer.finish()
    rendered = "".join(content + eol for _, content, eol in out)
    result.output_lines = len(out)
    if lines and not out:
        result.warnings.append("%s: nothing left after the rewrites; the file is empty" % entry.label)
    role = ROLE_TARGET_RE.fullmatch(entry.target)
    spec = compiled.manifest.roles.get(role.group(1)) if role else None
    if spec is not None:
        if rendered.startswith("---"):
            result.flags.append(Flag("role-source-frontmatter", 1, 0, entry.source, BUILTIN_FLAGS["role-source-frontmatter"]))
        else:
            rendered = render_role_frontmatter(role.group(1), spec) + "\n" + rendered
            result.events.append(Event("role", "frontmatter", "role frontmatter written from the manifest", "role frontmatter written", 1))
    result.data = (("\ufeff" if bom else "") + rendered).encode("utf-8")
    return result


def render_role_frontmatter(role_id: str, spec: Dict[str, Any]) -> str:
    """The adapters/ROLES.md frontmatter block for one role, ending in a newline."""
    lines = ["---", "id: %s" % role_id, "description: %s" % yaml_quote(spec["description"]), "tier: %s" % spec["tier"]]
    if spec.get("main"):
        lines.append("main: true")
    if spec.get("isolation") == "worktree":
        lines.append("isolation: worktree")
    for key in ("allowed-tools", "disallowed-tools"):
        if spec.get(key):
            lines.append("%s: %s" % (key, yaml_quote(spec[key])))
    if "write-paths" in spec:
        lines.append("write-paths: [%s]" % ", ".join(yaml_quote(path) for path in spec["write-paths"]))
    lines.append("---")
    return "\n".join(lines) + "\n"


def generated_files(manifest: "Manifest") -> List["FileResult"]:
    """plugin.json and README.md for each folder in the manifest's plugins section."""
    out = []  # type: List[FileResult]
    for index, (folder, spec) in enumerate(manifest.plugins.items()):
        skills = sorted(
            entry.target.split("/")[-2] for entry in manifest.entries
            if entry.target.startswith(folder + "/skills/") and entry.target.endswith("/" + SKILL_FILE)
        )
        plugin = OrderedDict([
            ("name", spec["name"]), ("version", spec["version"]), ("description", spec["description"]),
            ("author", OrderedDict([("name", "SOTACOMMAND")])), ("license", "Apache-2.0"),
        ])
        readme = "# %s\n\n%s\n\n## Skills\n\n%s\n" % (
            spec["name"], spec["description"], "\n".join("- `%s`" % name for name in skills) or "None yet."
        )
        for target, text in ((folder + "/.claude-plugin/plugin.json", json.dumps(plugin, indent=2) + "\n"),
                             (folder + "/README.md", readme)):
            entry = Entry(-1 - index, "(generated)", target, ())
            result = FileResult(entry, data=text.encode("utf-8"), source_lines=0, output_lines=text.count("\n"))
            result.events.append(Event("generated", "plugins", "generated from the manifest's plugins section", "plugin files generated", 0))
            out.append(result)
    return sorted(out, key=lambda r: r.entry.target)


# ---------------------------------------------------------------------------
# Leak gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One leak-gate finding, as the gate reports it (its excerpt is redacted)."""

    path: str
    line: Optional[int]
    col: Optional[int]
    rule_id: str
    excerpt: str


@dataclass
class GateResult:
    status: str  # "clean", "findings", "unavailable" or "not-run"
    reason: str = ""
    findings: List[Finding] = field(default_factory=list)
    exit_code: Optional[int] = None
    output_tail: str = ""


@dataclass
class CanaryResult:
    status: str  # "passed", "failed", "skipped" or "error"
    total: int = 0
    missed: List[str] = field(default_factory=list)
    reason: str = ""
    output_tail: str = ""


def _loads_lenient(text: str) -> Tuple[bool, Any]:
    """Parse JSON output: a document, JSON lines, or one document amid other text."""
    try:
        return True, json.loads(text)
    except ValueError:
        pass
    rows = [line for line in text.splitlines() if line.strip()]
    try:
        parsed = [json.loads(row) for row in rows]
        if parsed and all(isinstance(row, dict) for row in parsed):
            return True, parsed
    except ValueError:
        pass
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    end = max(text.rfind("]"), text.rfind("}"))
    if starts and end > min(starts):
        try:
            return True, json.loads(text[min(starts) : end + 1])
        except ValueError:
            pass
    return False, None


def _as_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _extract_findings(data: Any) -> Optional[List[Finding]]:
    """Findings from the gate's JSON: a list, or an object with a findings list."""
    if isinstance(data, dict):
        for key in ("findings", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return None
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        return None
    return [
        Finding(
            str(item.get("path", item.get("file", ""))),
            _as_int(item.get("line")),
            _as_int(item.get("col", item.get("column"))),
            str(item.get("rule_id", item.get("rule", ""))),
            str(item.get("excerpt", "")),
        )
        for item in data
    ]


def _gate_env(target: Path) -> Dict[str, str]:
    """The gate's environment: no inherited git repository, and a ceiling so
    git can't find one above the temporary directory either."""
    env = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(name, None)
    ceiling = str(_resolve(target).parent)
    previous = env.get("GIT_CEILING_DIRECTORIES")
    env["GIT_CEILING_DIRECTORIES"] = ceiling + (os.pathsep + previous if previous else "")
    return env


def _tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def invoke_gate(command: Sequence[str], target: Path, timeout: float, cwd: Optional[Path]) -> GateResult:
    """Run ``<command> check <target> --json`` and classify the result.

    Exit 0 with no findings is clean; exit 1 with readable findings is
    findings. Everything else (missing command, timeout, exit 2, a crash, an
    exit code that contradicts the output) means the gate is unavailable.
    """
    argv = list(command) + ["check", str(target), "--json"]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=_gate_env(target),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return GateResult("unavailable", "the leak gate timed out after %g seconds" % timeout)
    except OSError as exc:
        return GateResult("unavailable", "the leak gate could not start: %s" % (exc.strerror or exc))
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    tail = _tail(stderr) or _tail(stdout)
    ok, data = _loads_lenient(stdout) if stdout.strip() else (True, [])
    findings = _extract_findings(data) if ok else None
    if proc.returncode == 0:
        if not ok:
            return GateResult("unavailable", "the leak gate exited 0 but its output is not JSON", exit_code=0, output_tail=tail)
        if findings:
            return GateResult("unavailable", "the leak gate exited 0 but reported %s" % _plural(len(findings), "finding"), exit_code=0, output_tail=tail)
        return GateResult("clean", exit_code=0)
    if proc.returncode == 1:
        if findings:
            return GateResult("findings", findings=findings, exit_code=1, output_tail=tail)
        return GateResult("unavailable", "the leak gate exited 1 without readable findings (a crash?)", exit_code=1, output_tail=tail)
    meaning = "usage or config error" if proc.returncode == 2 else "unexpected exit code"
    return GateResult("unavailable", "the leak gate exited %d (%s)" % (proc.returncode, meaning), exit_code=proc.returncode, output_tail=tail)


def run_canary(
    command: Sequence[str], terms: Sequence[Tuple[str, str]], parent: Path, timeout: float, cwd: Optional[Path]
) -> CanaryResult:
    """Prove the gate catches every term the manifest promises to remove.

    Each term goes in its own file under a temporary directory created where
    the output is built, and the gate must flag every one of those files. A
    gate that misses one is blind to it (for example, run without the private
    denylist), so the run fails closed.
    """
    if not terms:
        return CanaryResult("skipped", reason="the manifest has no persona_map or owner_terms to test the gate with")
    folder = Path(tempfile.mkdtemp(prefix="extract-canary-", dir=str(parent)))
    try:
        for index, (_, term) in enumerate(terms, 1):
            sub = folder / ("c%03d" % index)
            sub.mkdir()
            (sub / "canary.txt").write_text(term + "\n", encoding="utf-8")
        gate = invoke_gate(command, folder, timeout, cwd)
        if gate.status == "unavailable":
            tail = _scrub(gate.output_tail, folder, "<canary>")
            return CanaryResult("error", len(terms), reason=gate.reason, output_tail=tail)
        caught = set()  # type: Set[str]
        for finding in gate.findings:
            for part in re.split(r"[\\/]+", finding.path):
                if re.fullmatch(r"c\d{3}", part):
                    caught.add(part)
        missed = []
        position = {}  # type: Dict[str, int]
        for index, (section, term) in enumerate(terms, 1):
            position[section] = position.get(section, 0) + 1
            if "c%03d" % index not in caught:
                missed.append("%s term %d (%s)" % (section, position[section], _mask(term)))
        if missed:
            return CanaryResult("failed", len(terms), missed, "the leak gate missed %s of %d" % (_plural(len(missed), "manifest term"), len(terms)))
        return CanaryResult("passed", len(terms))
    finally:
        shutil.rmtree(str(folder), ignore_errors=True)


# ---------------------------------------------------------------------------
# Running an extraction
# ---------------------------------------------------------------------------


@dataclass
class Options:
    """What to run. ``leakgate`` is a command prefix (string or argv list);
    None means ``python3 tools/repo/leakgate/leakgate.py`` from this repo."""

    source: str
    manifest: str
    out: str
    dry_run: bool = False
    report: Optional[str] = None
    leakgate: Optional[Union[str, Sequence[str]]] = None
    gate_timeout: float = GATE_TIMEOUT_SECONDS
    work_dir: Optional[str] = None  # where temporary directories go; None = system temp


@dataclass
class Outcome:
    options: Options
    exit_code: int = EXIT_USAGE
    errors: List[str] = field(default_factory=list)
    manifest: Optional[Manifest] = None
    files: List[FileResult] = field(default_factory=list)
    unlisted: "OrderedDict[str, int]" = field(default_factory=OrderedDict)
    gate: GateResult = field(default_factory=lambda: GateResult("not-run"))
    canary: CanaryResult = field(default_factory=lambda: CanaryResult("skipped"))
    gate_display: str = ""
    written: bool = False
    #: Plugin files generated from the manifest's plugins section.
    generated: List[FileResult] = field(default_factory=list)

    @property
    def flags(self) -> List[Tuple[FileResult, Flag]]:
        return [(f, flag) for f in self.files for flag in sorted(f.flags, key=lambda x: (x.line, x.col, x.flag, x.text))]

    @property
    def warnings(self) -> List[str]:
        out = [w for f in self.files for w in f.warnings]
        if self.canary.status == "skipped" and self.manifest is not None:
            out.append("gate canary skipped: %s" % self.canary.reason)
        return out


def check_paths(opts: Options) -> List[str]:
    """Upfront checks on --source, --manifest, --out and --report."""
    errors = []
    source = Path(opts.source)
    if not source.is_dir():
        errors.append("--source %s is not a directory" % opts.source)
    if not Path(opts.manifest).is_file():
        errors.append("--manifest %s is not a file" % opts.manifest)
    out = Path(opts.out)
    if out.exists():
        if not out.is_dir():
            errors.append("--out %s exists and is not a directory" % opts.out)
        elif any(out.iterdir()):
            errors.append("--out %s already exists and is not empty; remove it or pick a new path" % opts.out)
    real_out = _resolve(out)
    if source.is_dir():
        real_source = _resolve(source)
        if _is_inside(real_out, real_source):
            errors.append("--out must not be inside --source")
        if _is_inside(real_source, real_out):
            errors.append("--source must not be inside --out")
    if opts.report:
        report = _resolve(opts.report)
        if Path(opts.report).is_dir():
            errors.append("--report %s is a directory" % opts.report)
        if _is_inside(report, real_out):
            errors.append("--report must not be inside --out (the report quotes private text)")
        if source.is_dir() and _is_inside(report, _resolve(source)):
            errors.append("--report must not be inside --source")
    return errors


def unlisted_files(source: Path, manifest: Manifest) -> "OrderedDict[str, int]":
    """Count source files no entry lists, grouped by top-level folder.

    ``.git`` folders are skipped. Symlinked folders are not followed.
    """
    listed = {entry.source for entry in manifest.entries}
    counts = {}  # type: Dict[str, int]
    for folder, dirs, files in os.walk(str(source)):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        rel_folder = os.path.relpath(folder, str(source)).replace(os.sep, "/")
        for name in files:
            rel = name if rel_folder == "." else rel_folder + "/" + name
            if rel in listed:
                continue
            group = rel.split("/")[0] + "/" if "/" in rel else "(top level)"
            counts[group] = counts.get(group, 0) + 1
    return OrderedDict(sorted(counts.items(), key=lambda kv: (kv[0] != "(top level)", kv[0].lower(), kv[0])))


def _gate_command(opts: Options) -> Tuple[Optional[List[str]], str, Optional[Path], str]:
    """Resolve the gate: ``(argv prefix, display text, cwd, problem)``."""
    if opts.leakgate is None:
        if not DEFAULT_GATE_SCRIPT.is_file():
            return None, DEFAULT_GATE_DISPLAY, None, "the leak gate is not installed at tools/repo/leakgate/leakgate.py; pass --leakgate CMD"
        return [sys.executable, str(DEFAULT_GATE_SCRIPT)], DEFAULT_GATE_DISPLAY, REPO_ROOT, ""
    if isinstance(opts.leakgate, str):
        argv = shlex.split(opts.leakgate, posix=os.name != "nt")
    else:
        argv = list(opts.leakgate)
    if not argv:
        return None, "", None, "--leakgate is empty"
    return argv, " ".join(shlex.quote(part) for part in argv), None, ""


def _write_tree(root: Path, files: Sequence[FileResult]) -> None:
    for result in files:
        path = root.joinpath(*result.entry.target.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result.data)
        os.chmod(str(path), result.mode)


def _install(build: Path, out: Path) -> None:
    """Copy the checked tree next to ``out``, then rename it into place."""
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(tempfile.mkdtemp(prefix=".%s.partial-" % out.name, dir=str(out.parent)))
    try:
        shutil.copytree(str(build), str(partial), dirs_exist_ok=True)
        os.chmod(str(partial), 0o755)
        if out.exists():
            out.rmdir()  # checked empty upfront; never replace a non-empty tree
        os.rename(str(partial), str(out))
    except BaseException:
        shutil.rmtree(str(partial), ignore_errors=True)
        raise


def run_extraction(opts: Options) -> Outcome:
    """Validate, transform, gate and (unless refused or dry) write. Never raises
    for user errors: they come back as ``exit_code`` and ``errors``."""
    outcome = Outcome(opts)
    errors = check_paths(opts)
    source = Path(opts.source)
    if source.is_dir() and Path(opts.manifest).is_file():
        manifest, manifest_errors = load_manifest(opts.manifest, source)
        errors.extend(manifest_errors)
        outcome.manifest = manifest
    if errors:
        outcome.errors = errors
        outcome.exit_code = EXIT_USAGE
        return outcome
    manifest = outcome.manifest
    assert manifest is not None
    compiled = Compiled.build(manifest)
    outcome.unlisted = unlisted_files(source, manifest)
    for entry in sorted(manifest.entries, key=lambda e: e.target):
        path = source.joinpath(*entry.source.split("/"))
        try:
            raw = path.read_bytes()
            mode = path.stat().st_mode
        except OSError as exc:
            outcome.errors.append("%s: can't read %s: %s" % (entry.label, entry.source, exc.strerror or exc))
            continue
        outcome.files.append(transform_entry(compiled, entry, raw, mode))
    outcome.generated = generated_files(manifest)
    if outcome.errors:
        outcome.exit_code = EXIT_USAGE
        return outcome

    command, outcome.gate_display, cwd, problem = _gate_command(opts)
    parent = _resolve(opts.work_dir or tempfile.gettempdir())
    enclosing = find_enclosing_git(parent)
    if not problem and enclosing is not None:
        problem = (
            "the temporary directory %s is inside the git work tree at %s, where a leak gate "
            "may scan only tracked files; set TMPDIR to a folder outside any repository" % (parent, enclosing)
        )
    try:
        build = Path(tempfile.mkdtemp(prefix="extract-build-", dir=str(parent)))
    except OSError as exc:
        outcome.errors.append("can't create a temporary directory in %s: %s" % (parent, exc.strerror or exc))
        outcome.exit_code = EXIT_USAGE
        return outcome
    try:
        _write_tree(build, outcome.files + outcome.generated)
        if problem or command is None:
            outcome.gate = GateResult("unavailable", problem)
            outcome.canary = CanaryResult("error", reason="not run: the leak gate is unavailable")
        else:
            terms = [("persona_map", t) for t in manifest.persona_map] + [("owner_terms", t) for t in manifest.owner_terms]
            outcome.canary = run_canary(command, terms, parent, opts.gate_timeout, cwd)
            if outcome.canary.status in ("passed", "skipped"):
                outcome.gate = invoke_gate(command, build, opts.gate_timeout, cwd)
                outcome.gate.output_tail = _scrub(outcome.gate.output_tail, build, "<output>")
                outcome.gate.findings = sorted(
                    (_relative_finding(f, build) for f in outcome.gate.findings),
                    key=lambda f: (f.path, f.line or 0, f.col or 0, f.rule_id),
                )
            else:
                outcome.gate = GateResult(
                    "unavailable",
                    "the gate canary %s: %s" % (outcome.canary.status, outcome.canary.reason),
                    output_tail=outcome.canary.output_tail,
                )
        if outcome.flags or outcome.gate.status == "findings":
            outcome.exit_code = EXIT_REFUSED
        elif outcome.gate.status != "clean":
            outcome.exit_code = EXIT_GATE_UNAVAILABLE
        else:
            outcome.exit_code = EXIT_OK
            if not opts.dry_run:
                try:
                    _install(build, Path(opts.out))
                except OSError as exc:
                    outcome.errors.append("can't write --out %s: %s" % (opts.out, exc.strerror or exc))
                    outcome.exit_code = EXIT_USAGE
                else:
                    outcome.written = True
    finally:
        shutil.rmtree(str(build), ignore_errors=True)
    return outcome


def _scrub(text: str, folder: Path, placeholder: str) -> str:
    """Replace a temporary folder's path in gate output (it differs every run)."""
    for root in sorted({str(folder), str(_resolve(folder))}, key=len, reverse=True):
        text = text.replace(root, placeholder).replace(root.replace("\\", "/"), placeholder)
    return text


def _relative_finding(finding: Finding, build: Path) -> Finding:
    """Show a finding's path relative to the output root (temp paths vary)."""
    raw = finding.path.replace("\\", "/")
    for root in (str(build).replace("\\", "/"), str(_resolve(build)).replace("\\", "/")):
        if raw.startswith(root.rstrip("/") + "/"):
            raw = raw[len(root.rstrip("/")) + 1 :]
            break
    return Finding(raw, finding.line, finding.col, finding.rule_id, finding.excerpt)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _code(text: str) -> str:
    """Markdown inline code that survives backticks and table pipes."""
    text = " ".join(text.split())
    if not text:
        return "(empty)"
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return (fence + pad + text + pad + fence).replace("|", "\\|")


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _result_line(outcome: Outcome) -> str:
    opts = outcome.options
    dry = " (dry run)" if opts.dry_run else ""
    if outcome.exit_code == EXIT_USAGE:
        return "**ERROR**%s, exit 2: the run stopped before writing anything. See Errors." % dry
    if outcome.exit_code == EXIT_REFUSED:
        return "**REFUSED**%s, exit 1: %s and %s. Nothing was written." % (
            dry,
            _plural(len(outcome.flags), "flag"),
            _plural(len(outcome.gate.findings), "leak-gate finding"),
        )
    if outcome.exit_code == EXIT_GATE_UNAVAILABLE:
        return "**GATE UNAVAILABLE**%s, exit 3: %s. The tool fails closed, so nothing was written." % (dry, outcome.gate.reason)
    if opts.dry_run:
        return "**CLEAN** (dry run), exit 0: a real run would write %s. Nothing was written." % _plural(len(outcome.files), "file")
    return "**WRITTEN**, exit 0: %s written to %s. Review the diff by hand before anything leaves staging." % (
        _plural(len(outcome.files), "file"),
        _code(opts.out),
    )


def _gate_line(outcome: Outcome) -> str:
    gate = outcome.gate
    if gate.status == "clean":
        return "ran: 0 findings"
    if gate.status == "findings":
        return "ran: %s" % _plural(len(gate.findings), "finding")
    if gate.status == "unavailable":
        return "unavailable: %s" % gate.reason
    return "not run"


def _canary_line(outcome: Outcome) -> str:
    canary = outcome.canary
    if canary.status == "passed":
        return "passed: the gate caught all %d manifest terms" % canary.total
    if canary.status == "failed":
        return "FAILED: %s" % canary.reason
    if canary.status == "skipped":
        return "skipped: %s" % canary.reason
    return "not completed: %s" % canary.reason


def render_report(outcome: Outcome) -> str:
    """The markdown report. Deterministic: sorted, no timestamps, no temp paths."""
    opts = outcome.options
    manifest = outcome.manifest
    lines = [
        "# Extraction report",
        "",
        "> **Private.** This report quotes source text, so it can hold private names and paths. "
        "Keep it out of every repository and never paste it anywhere public.",
        "",
        "## Result",
        "",
        _result_line(outcome),
        "",
        "| Item | Value |",
        "|---|---|",
        "| Mode | %s |" % ("dry run (never writes)" if opts.dry_run else "real run"),
        "| Source | %s |" % _code(opts.source),
        "| Manifest | %s%s |" % (_code(opts.manifest), " (sha256 `%s`)" % manifest.sha256[:16] if manifest else ""),
        "| Output | %s%s |" % (_code(opts.out), " (written)" if outcome.written else " (not written)"),
    ]
    if outcome.exit_code == EXIT_USAGE and not outcome.files:
        lines += ["", "## Errors", ""] + ["- %s" % _cell(error) for error in outcome.errors] + [""]
        return "\n".join(lines)
    flags = outcome.flags
    total_events = sum(event.count for f in outcome.files for event in f.events)
    unlisted_total = sum(outcome.unlisted.values())
    lines += [
        "| Files exported | %d |" % len(outcome.files),
        "| Source files not in the manifest | %d (counted, never copied) |" % unlisted_total,
        "| Rewrites applied | %d |" % total_events,
        "| Flags | %d |" % len(flags),
        "| Leak gate | %s |" % _cell(_gate_line(outcome)),
        "| Gate command | %s |" % (_code(outcome.gate_display + " check <output> --json") if outcome.gate_display else "(none)"),
        "| Gate canary | %s |" % _cell(_canary_line(outcome)),
    ]
    if outcome.errors:
        lines += ["", "## Errors", ""] + ["- %s" % _cell(error) for error in outcome.errors]

    lines += ["", "## Flags", ""]
    if flags:
        lines += [
            "Each flag blocks the run until the manifest handles it: rewrite the text with an entry `replace` rule, "
            "drop the line or block with an entry `drop` rule, keep only part of the file with `select`, "
            "or leave the file out. Line numbers are source lines.",
            "",
            "| Source file | Line | Flag | Matched text | Why |",
            "|---|---|---|---|---|",
        ]
        for result, flag in sorted(flags, key=lambda fr: (fr[0].entry.source, fr[1].line, fr[1].col, fr[1].flag)):
            lines.append(
                "| %s | %d | `%s` | %s | %s |"
                % (_code(result.entry.source), flag.line, flag.flag, _code(_shorten(flag.text)), _cell(flag.message))
            )
    else:
        lines.append("None.")

    lines += ["", "## Leak gate", "", "- Command: %s" % (_code(outcome.gate_display) if outcome.gate_display else "(none)")]
    lines.append("- Canary: %s" % _cell(_canary_line(outcome)))
    for missed in outcome.canary.missed:
        lines.append("  - missed %s; add it to the gate's private denylist" % missed)
    lines.append("- Scan of the output: %s" % _cell(_gate_line(outcome)))
    if outcome.gate.output_tail and outcome.gate.status == "unavailable":
        lines += ["", "Gate output (last lines):", "", "```text", outcome.gate.output_tail.replace("```", "'''"), "```"]
    if outcome.gate.findings:
        by_target = {f.entry.target: f.entry.source for f in outcome.files}
        lines += ["", "| Output file | Line | Rule | Excerpt (redacted by the gate) | Source file |", "|---|---|---|---|---|"]
        for finding in outcome.gate.findings:
            lines.append(
                "| %s | %s | %s | %s | %s |"
                % (
                    _code(finding.path),
                    finding.line if finding.line is not None else "",
                    _code(finding.rule_id),
                    _code(_shorten(finding.excerpt)),
                    _code(by_target.get(finding.path, "")) if by_target.get(finding.path) else "",
                )
            )

    warnings_list = outcome.warnings
    if warnings_list:
        lines += ["", "## Warnings", ""] + ["- %s" % _cell(w) for w in sorted(warnings_list)]

    lines += ["", "## Rewrites by rule", ""]
    totals = OrderedDict()  # type: OrderedDict[Tuple[int, str, str], List[Any]]
    for result in outcome.files:
        for event in result.events:
            key = (_kind_rank(event.kind), event.kind, event.summary)
            bucket = totals.setdefault(key, [0, set()])
            bucket[0] += event.count
            bucket[1].add(result.entry.target)
    if totals:
        lines += ["| Rule set | Change | Count | Files |", "|---|---|---|---|"]
        for (_, kind, summary), (count, targets) in sorted(totals.items(), key=lambda kv: kv[0]):
            lines.append("| `%s` | %s | %d | %d |" % (kind, _cell(summary), count, len(targets)))
    else:
        lines.append("None.")

    lines += ["", "## Files", ""]
    for result in outcome.files:
        lines += _file_section(result)

    lines += ["## Source files not in the manifest", ""]
    if unlisted_total:
        lines += [
            "%s never copied. Counts only; nothing outside the manifest leaves the source." % _plural(unlisted_total, "file was", "files were"),
            "",
            "| Location | Files |",
            "|---|---|",
        ]
        lines += ["| %s | %d |" % (_code(group) if group != "(top level)" else group, count) for group, count in outcome.unlisted.items()]
    else:
        lines.append("None: the manifest lists every file in the source.")
    lines.append("")
    return "\n".join(lines)


def _kind_rank(kind: str) -> int:
    order = ("select", "drop", "drop_patterns", "replace") + RULE_SET_ORDER[1:] + ("binary",)
    return order.index(kind) if kind in order else len(order)


def _file_section(result: FileResult) -> List[str]:
    entry = result.entry
    applied = ", ".join(entry.rewrites) if entry.rewrites else "none"
    head = "From %s, %s in, %s out. Rewrites: %s." % (
        _code(entry.source),
        _plural(result.source_lines, "line"),
        _plural(result.output_lines, "line"),
        applied,
    )
    lines = ["### %s" % _code(entry.target), "", head]
    if entry.note:
        lines += ["", "Note: %s" % _cell(entry.note)]
    groups = OrderedDict()  # type: OrderedDict[Tuple[int, str, str, str], List[Any]]
    for event in result.events:
        key = (_kind_rank(event.kind), event.kind, event.rule, event.change)
        bucket = groups.setdefault(key, [0, []])
        bucket[0] += event.count
        bucket[1].append(event.line)
    if groups:
        lines += ["", "| Rule set | Change | Count | Source lines |", "|---|---|---|---|"]
        for (_, kind, _, change), (count, numbers) in sorted(groups.items(), key=lambda kv: kv[0]):
            lines.append("| `%s` | %s | %d | %s |" % (kind, _cell(change), count, _ranges(n for n in numbers if n)))
    else:
        lines += ["", "No rewrites applied: copied as is."]
    if result.flags:
        lines += ["", "Flags: %d, on source lines %s (listed under Flags)." % (len(result.flags), _ranges(f.line for f in result.flags))]
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _summary(outcome: Outcome) -> str:
    plain = re.sub(r"\*\*|`", "", _result_line(outcome))
    return "extract: " + plain


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract.py",
        description=(
            "Copy the files a manifest allowlists from a private source tree, apply its safe rewrites, "
            "flag what can't be rewritten safely, run the leak gate, and write --out only when nothing "
            "was flagged or found. Exit 0 written clean, 1 refused, 2 usage error, 3 leak gate unavailable."
        ),
    )
    parser.add_argument("--source", required=True, metavar="DIR", help="the private source tree (read only)")
    parser.add_argument("--manifest", required=True, metavar="FILE", help="the allowlist manifest (JSON)")
    parser.add_argument("--out", required=True, metavar="DIR", help="where the staged tree goes; must not exist or be empty")
    parser.add_argument("--dry-run", action="store_true", help="do everything except write --out")
    parser.add_argument("--report", metavar="FILE", help="write the markdown report here (default: print it)")
    parser.add_argument(
        "--leakgate",
        metavar="CMD",
        help="leak gate command prefix; the tool appends 'check <dir> --json' "
        "(default: python3 tools/repo/leakgate/leakgate.py)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.leakgate is not None and not args.leakgate.strip():
        print("extract: error: --leakgate is empty", file=sys.stderr)
        return EXIT_USAGE
    opts = Options(
        source=args.source,
        manifest=args.manifest,
        out=args.out,
        dry_run=args.dry_run,
        report=args.report,
        leakgate=args.leakgate,
    )
    outcome = run_extraction(opts)
    report = render_report(outcome)
    if opts.report:
        try:
            path = Path(opts.report)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(report, encoding="utf-8")
            os.replace(str(temporary), str(path))
        except OSError as exc:
            print("extract: error: can't write --report %s: %s" % (opts.report, exc.strerror or exc), file=sys.stderr)
            return EXIT_USAGE
        print(_summary(outcome))
        print("extract: report written to %s" % opts.report)
    else:
        sys.stdout.write(report)
    for error in outcome.errors:
        print("extract: error: %s" % error, file=sys.stderr)
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
