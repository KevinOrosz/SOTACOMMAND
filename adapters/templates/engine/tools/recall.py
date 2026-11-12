#!/usr/bin/env python3
"""Recall: keep every session's handoff, and search the team's memory.

Source: adapters/templates/engine/tools/recall.py, copied into engine/tools/
by adapters/generate.py. Edit the template, then regenerate.

The team's day-to-day memory is ``memory/HANDOFF.md``, rewritten every
session, so yesterday's note used to be gone the day after. Two things fix
that:

**History.** At session start, when the handoff differs from the last one
kept, a copy goes to ``.sotacommand/history/handoff-<UTC time>.md``. The
newest ``keep`` copies stay (default 200); older ones are removed.
``.sotacommand/**`` is a protected path in the config template, so agents
can read the history but not rewrite it.

**Search.** ``recall.py "what did we decide about billing"`` ranks passages
from the history, ``memory/``, ``profile/owner.md`` and ``TEAM.md``. It
builds a full-text index in memory on each call (SQLite FTS5, which ships
with Python), so there is no index file to go stale. Where FTS5 is missing
it falls back to counting matching words. Each hit shows the file, the line
and the passage.

``os.config.json`` may set ``"recall": {"keep": 200}``. History is kept only
in a project that has an ``os.config.json``.

Local only: no network calls, no model calls. For memory that lasts months
and is curated by the owner, connect a second brain (``second_brain`` in
os.config.json); this tool is the short, automatic layer under it.

Exit codes: 0 hits printed, 1 nothing found, 2 usage error.
"""

import argparse
import datetime
import os
import re
import sqlite3
import sys
from typing import Any, Dict, Iterator, List, Optional, Sequence, TextIO, Tuple

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import os_config  # noqa: E402  (sibling module, found via the line above)

HISTORY = os.path.join(".sotacommand", "history")
HANDOFF = os.path.join("memory", "HANDOFF.md")
DEFAULT_KEEP = 200
SOURCES = ("memory", HISTORY)
SINGLE_FILES = (os.path.join("profile", "owner.md"), "TEAM.md")
MAX_FILE_BYTES = 512 * 1024
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_'-]*")
_SNAPSHOT = re.compile(r"^handoff-\d{8}T\d{6}Z\.md$")


def _config(root: str) -> Optional[Dict[str, Any]]:
    try:
        config, _ = os_config.load_config(root)
    except os_config.ConfigError:
        return None
    return config


def keep_count(config: Optional[Dict[str, Any]]) -> int:
    block = (config or {}).get("recall")
    value = block.get("keep") if isinstance(block, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else DEFAULT_KEEP


def snapshots(root: str) -> List[str]:
    """History file names, oldest first."""
    folder = os.path.join(root, HISTORY)
    if not os.path.isdir(folder):
        return []
    return sorted(name for name in os.listdir(folder) if _SNAPSHOT.match(name))


def archive_handoff(root: str, now: Optional[datetime.datetime] = None) -> Optional[str]:
    """Keep a copy of the handoff when it changed. Returns the new file's name, or None. Never raises."""
    try:
        config = _config(root)
        source = os.path.join(root, HANDOFF)
        if config is None or not os.path.isfile(source) or os.path.getsize(source) > MAX_FILE_BYTES:
            return None
        with open(source, "rb") as handle:
            data = handle.read()
        if not data.strip():
            return None
        kept = snapshots(root)
        folder = os.path.join(root, HISTORY)
        if kept:
            with open(os.path.join(folder, kept[-1]), "rb") as handle:
                if handle.read() == data:
                    return None
        os.makedirs(folder, exist_ok=True)
        now = now or datetime.datetime.now(datetime.timezone.utc)
        name = "handoff-%s.md" % now.strftime("%Y%m%dT%H%M%SZ")
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(data)
        for old in snapshots(root)[:-keep_count(config)]:
            os.remove(os.path.join(folder, old))
        return name
    except Exception:  # history must never break session start
        return None


def _files(root: str) -> Iterator[str]:
    for relative in SINGLE_FILES:
        if os.path.isfile(os.path.join(root, relative)):
            yield relative
    for source in SOURCES:
        base = os.path.join(root, source)
        for folder, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                if name.endswith((".md", ".txt")):
                    yield os.path.relpath(os.path.join(folder, name), root)


def passages(root: str) -> List[Tuple[str, int, str]]:
    """``(file, first line, text)`` for every blank-line-separated passage in the team's memory."""
    found = []
    newest = snapshots(root)[-1:]
    current = os.path.join(root, HANDOFF)
    for relative in _files(root):
        path = os.path.join(root, relative)
        if newest and os.path.basename(relative) == newest[0] and os.path.isfile(current):
            try:  # the newest copy is usually today's handoff: don't show the same passage twice
                with open(path, "rb") as one, open(current, "rb") as two:
                    if one.read() == two.read():
                        continue
            except OSError:
                pass
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES:
                continue
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        start, block = 1, []  # type: int, List[str]
        for number, line in enumerate(lines + [""], 1):
            if line.strip():
                if not block:
                    start = number
                block.append(line)
            elif block:
                found.append((relative.replace(os.sep, "/"), start, "\n".join(block)))
                block = []
    return found


def words(query: str) -> List[str]:
    seen = []  # type: List[str]
    for word in _WORD.findall(query.lower()):
        if len(word) > 1 and word not in seen:
            seen.append(word)
    return seen


def _fts(rows: Sequence[Tuple[str, int, str]], terms: Sequence[str], limit: int) -> List[Tuple[str, int, str]]:
    db = sqlite3.connect(":memory:")
    try:
        db.execute("CREATE VIRTUAL TABLE passages USING fts5(file UNINDEXED, line UNINDEXED, text, tokenize='porter unicode61')")
        db.executemany("INSERT INTO passages VALUES (?, ?, ?)", rows)
        match = " OR ".join('"%s"' % term.replace('"', "") for term in terms)
        hits = db.execute(
            "SELECT file, line, text FROM passages WHERE passages MATCH ? ORDER BY bm25(passages), file DESC, line LIMIT ?",
            (match, limit),
        ).fetchall()
    finally:
        db.close()
    return [(file, int(line), text) for file, line, text in hits]


def _fallback(rows: Sequence[Tuple[str, int, str]], terms: Sequence[str], limit: int) -> List[Tuple[str, int, str]]:
    scored = []
    for file, line, text in rows:
        lowered = text.lower()
        distinct = sum(1 for term in terms if term in lowered)
        if distinct:
            scored.append((-distinct, -sum(lowered.count(term) for term in terms), file, line, text))
    scored.sort()
    return [(file, line, text) for _, _, file, line, text in scored[:limit]]


def search(root: str, query: str, limit: int = 5, use_fts: bool = True) -> List[Tuple[str, int, str]]:
    """The best-matching passages for ``query``, best first."""
    terms = words(query)
    rows = passages(root)
    if not terms or not rows:
        return []
    if use_fts:
        try:
            return _fts(rows, terms, limit)
        except sqlite3.Error:
            pass
    return _fallback(rows, terms, limit)


def render(hits: Sequence[Tuple[str, int, str]]) -> str:
    out = []
    for file, line, text in hits:
        out.append("%s:%d" % (file, line))
        out += ["    " + row for row in text.splitlines()[:12]]
        out.append("")
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None, stdout: Optional[TextIO] = None, stderr: Optional[TextIO] = None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = argparse.ArgumentParser(description="Search the team's memory: past handoffs, memory/, the owner profile and the team card.")
    parser.add_argument("query", nargs="+", help="what to look for, in plain words")
    parser.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, then the current folder)")
    parser.add_argument("--limit", type=int, default=5, help="how many passages to show (default 5)")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    root = os_config.project_root({}, args.root)
    hits = search(root, " ".join(args.query), args.limit)
    if not hits:
        stderr.write("recall: nothing in this project's memory matches %r (%d past handoff(s) kept).\n" % (
            " ".join(args.query), len(snapshots(root))))
        return 1
    stdout.write(render(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
