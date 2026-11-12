"""Gitignore-style globs over POSIX paths relative to a scan root.

leakgate uses globs in three places: path deny rules, allowlist scopes and
``--exclude``. They all share these semantics, a documented subset of
gitignore:

* Paths are relative to the scan root and always use ``/``.
* A pattern that contains ``/`` (other than a trailing one) is anchored at the
  root. A leading ``/`` or ``./`` anchors explicitly. A pattern without ``/``
  matches a name at any depth, so ``*.pem`` matches ``a/b/key.pem``.
* ``*`` matches any run of characters except ``/``. ``?`` matches one
  character except ``/``. ``[abc]``, ``[a-z]`` and ``[!abc]`` are classes.
* ``**`` as a whole segment matches zero or more directories:
  ``**/KB/**`` matches ``KB/x`` and ``a/KB/b/c``. A trailing ``/**`` matches
  everything below that directory, but not the directory name itself.
* A trailing ``/`` means everything below a directory: ``docs/`` is the same
  as ``**/docs/**`` and ``/docs/`` the same as ``docs/**``.
* Matching is case-sensitive, as git is on Linux.

Backslashes are rejected rather than guessed at: write ``/`` on every OS.
"""

import re
from typing import Iterable, Optional, Pattern, Tuple


def _translate_segment(segment: str) -> str:
    """Translate one path segment (no ``/``) into a regular expression."""
    out = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if ch == "*":
            while i < n and segment[i] == "*":
                i += 1
            out.append("[^/]*")
            continue
        if ch == "?":
            out.append("[^/]")
        elif ch == "[":
            j = i + 1
            if j < n and segment[j] in "!^":
                j += 1
            if j < n and segment[j] == "]":
                j += 1
            while j < n and segment[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(ch))
            else:
                body = segment[i + 1 : j]
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                body = body.replace("\\", "\\\\")
                # A negated class could otherwise match the separator.
                out.append("(?!/)[" + body + "]")
                i = j
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def translate(pattern: str) -> str:
    """Return an anchored regular expression equivalent to ``pattern``.

    Raises ``ValueError`` for empty patterns, surrounding whitespace,
    backslashes and empty segments (``a//b``).
    """
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("glob is empty")
    if pattern != pattern.strip():
        raise ValueError("glob %r has leading or trailing whitespace" % pattern)
    if "\\" in pattern:
        raise ValueError("glob %r contains a backslash; use / on every OS" % pattern)

    body = pattern
    anchored = False
    if body.startswith("./"):
        body = body[2:]
        anchored = True
    elif body.startswith("/"):
        body = body.lstrip("/")
        anchored = True
    dir_only = body.endswith("/")
    body = body.rstrip("/")
    if not body:
        raise ValueError("glob %r matches nothing" % pattern)

    segments = body.split("/")
    if any(segment == "" for segment in segments):
        raise ValueError("glob %r has an empty path segment" % pattern)
    if not anchored and len(segments) == 1 and segments[0] != "**":
        segments = ["**"] + segments
    if dir_only:
        segments = segments + ["**"]

    parts = []
    last = len(segments) - 1
    for index, segment in enumerate(segments):
        if segment == "**":
            if index == last:
                parts.append(".+" if index > 0 else ".*")
            else:
                parts.append("(?:[^/]+/)*")
            continue
        parts.append(_translate_segment(segment))
        if index != last:
            parts.append("/")
    return r"\A" + "".join(parts) + r"\Z"


class Glob(object):
    """One compiled glob. ``Glob("internal/**").matches("internal/a.md")``."""

    __slots__ = ("pattern", "_regex")

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self._regex = re.compile(translate(pattern))  # type: Pattern[str]

    def matches(self, path: str) -> bool:
        return self._regex.match(path) is not None

    def __repr__(self) -> str:
        return "Glob(%r)" % self.pattern


class GlobSet(object):
    """An ordered set of globs; reports which one matched first."""

    __slots__ = ("globs",)

    def __init__(self, patterns: Iterable[str]) -> None:
        self.globs = tuple(Glob(p) for p in patterns)  # type: Tuple[Glob, ...]

    def first_match(self, path: str) -> Optional[str]:
        for glob in self.globs:
            if glob.matches(path):
                return glob.pattern
        return None

    def __bool__(self) -> bool:
        return bool(self.globs)

    def __repr__(self) -> str:
        return "GlobSet(%r)" % [g.pattern for g in self.globs]
