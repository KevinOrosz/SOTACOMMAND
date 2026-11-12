"""Scanning: turn files and rules into redacted findings.

For each file leakgate checks, in order:

1. **Path rules.** A file whose relative path matches a deny glob is one
   finding at line 0, column 0 (the whole file).
2. **The file path itself.** Content rules also run over the relative path,
   because a file named after a person leaks as surely as its content does.
   Those findings sit at line 0 with the column inside the path.
3. **Content.** Regex rules run on the text as written. Term, env-denylist
   and hashed-term rules run on a normalized view (no accents, fullwidth
   forms or zero-width characters) mapped back to real positions. Binary
   files skip this step; path rules still apply to them.

Overlapping matches of one rule merge into one finding. Allowlist entries
mark findings as allowed instead of deleting them, so ``--show-allowed`` can
audit what each exception hides. Only blocking findings decide the exit code.

Redaction: an excerpt never contains matched text. By default it names only
the rule and position. With ``context > 0`` it adds surrounding characters,
with every detected span on that line masked.
"""

import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .config import Config, load_config
from .discovery import FileTarget, discover
from .errors import ScanError
from .textutil import LineIndex, NormalizedView, decode_bytes, sanitize_for_display

MASK = "«…»"  # «…»
ELLIPSIS = "…"
MODES = ("check", "bench")


class Finding(object):
    """One finding. ``line`` and ``col`` are 1-based; ``line == 0`` means the
    file path (``col == 0``: the whole file, from a path rule).

    ``excerpt`` is redacted: it shows the rule and position, never the
    matched text. ``allowed_by`` lists the allowlist entries that cover the
    finding; an empty tuple means it blocks.
    """

    __slots__ = ("path", "line", "col", "rule_id", "excerpt", "rule_type", "allowed_by")

    def __init__(self, path, line, col, rule_id, excerpt, rule_type, allowed_by=()):
        # type: (str, int, int, str, str, str, Tuple[str, ...]) -> None
        self.path = path
        self.line = line
        self.col = col
        self.rule_id = rule_id
        self.excerpt = excerpt
        self.rule_type = rule_type
        self.allowed_by = tuple(allowed_by)

    @property
    def blocking(self) -> bool:
        return not self.allowed_by

    def key(self) -> Tuple[str, int, int, str]:
        return (self.path, self.line, self.col, self.rule_id)

    def to_dict(self, with_allowed_by: bool = False) -> Dict[str, Any]:
        data = {
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "excerpt": self.excerpt,
        }  # type: Dict[str, Any]
        if with_allowed_by:
            data["allowed_by"] = list(self.allowed_by)
        return data

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Finding):
            return NotImplemented
        return self.to_dict(True) == other.to_dict(True)

    def __hash__(self) -> int:
        return hash(self.key())

    def __repr__(self) -> str:
        return "Finding(%s:%d:%d %s%s)" % (
            self.path,
            self.line,
            self.col,
            self.rule_id,
            " allowed" if self.allowed_by else "",
        )


class ScanReport(object):
    """Everything a check found. ``findings`` block; ``allowed`` don't."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.findings = []  # type: List[Finding]
        self.allowed = []  # type: List[Finding]
        self.files_scanned = 0
        self.files_binary = 0
        self.warnings = []  # type: List[str]
        self.errors = []  # type: List[str]

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 2
        return 1 if self.findings else 0

    @property
    def files_with_findings(self) -> int:
        return len({finding.path for finding in self.findings})


class _Match(object):
    """A raw match before positions, redaction and allowlisting."""

    __slots__ = ("rule_id", "rule_type", "start", "end")

    def __init__(self, rule_id: str, rule_type: str, start: int, end: int) -> None:
        self.rule_id = rule_id
        self.rule_type = rule_type
        self.start = start
        self.end = end


def _merge_overlaps(matches: List[_Match]) -> List[_Match]:
    """Merge overlapping spans of the same rule into one match."""
    by_rule = {}  # type: Dict[str, List[_Match]]
    for match in matches:
        by_rule.setdefault(match.rule_id, []).append(match)
    merged = []
    for rule_matches in by_rule.values():
        rule_matches.sort(key=lambda m: (m.start, -m.end))
        current = rule_matches[0]
        for match in rule_matches[1:]:
            if match.start < current.end:
                current = _Match(current.rule_id, current.rule_type, current.start, max(current.end, match.end))
            else:
                merged.append(current)
                current = match
        merged.append(current)
    merged.sort(key=lambda m: (m.start, m.rule_id))
    return merged


def find_matches(text: str, config: Config) -> List[_Match]:
    """Every content-rule match in ``text``, overlaps merged."""
    matches = []  # type: List[_Match]
    for rule in config.regex_rules:
        for start, end in rule.finditer(text):
            matches.append(_Match(rule.id, rule.type, start, end))
    name_rules = list(config.term_rules)
    if config.hashed_rule is not None:
        name_rules.append(config.hashed_rule)
    if name_rules:
        view = NormalizedView(text)
        for rule in name_rules:
            for start, end in rule.finditer(view.text):
                original_start, original_end = view.to_original(start, end)
                matches.append(_Match(rule.id, rule.type, original_start, original_end))
    if not matches:
        return matches
    return _merge_overlaps(matches)


def _snippet(line_text: str, start: int, end: int, masks: Sequence[Tuple[int, int]], context: int) -> str:
    """``context`` characters around ``[start, end)`` with every mask applied."""
    low = max(0, start - context)
    high = min(len(line_text), max(end, start + 1) + context)
    pieces = []
    position = low
    for mask_start, mask_end in sorted(masks):
        if mask_end <= low or mask_start >= high:
            continue
        mask_start = max(mask_start, low)
        mask_end = min(mask_end, high)
        if mask_start > position:
            pieces.append(sanitize_for_display(line_text[position:mask_start]))
        if mask_end > position:
            pieces.append(MASK)
            position = mask_end
    if position < high:
        pieces.append(sanitize_for_display(line_text[position:high]))
    prefix = ELLIPSIS if low > 0 else ""
    suffix = ELLIPSIS if high < len(line_text) else ""
    return prefix + "".join(pieces) + suffix


def content_excerpt(rule_id: str, line: int, col: int, snippet: Optional[str] = None) -> str:
    excerpt = "[%s @ %d:%d]" % (rule_id, line, col)
    return excerpt + " " + snippet if snippet else excerpt


def _path_rule_excerpt(rule_id: str, glob: str) -> str:
    return "[%s @ file] path matches denied glob '%s'" % (rule_id, glob)


def _path_name_excerpt(rule_id: str, col: int) -> str:
    return "[%s @ path:%d] the file path itself matches" % (rule_id, col)


def _findings_from_text(text, relpath, config, mode, context):
    # type: (str, str, Config, str, int) -> List[Finding]
    matches = find_matches(text, config)
    if not matches:
        return []
    index = LineIndex(text)
    spans_by_line = {}  # type: Dict[int, List[Tuple[int, int]]]
    located = []
    for match in matches:
        line, col = index.position(match.start)
        line_start, line_end = index.bounds(line)
        span = (match.start - line_start, min(match.end, line_end) - line_start)
        spans_by_line.setdefault(line, []).append(span)
        located.append((match, line, col, line_start, line_end, span))
    findings = []
    for match, line, col, line_start, line_end, span in located:
        snippet = None
        if context > 0:
            snippet = _snippet(text[line_start:line_end], span[0], span[1], spans_by_line[line], context)
        allowed_by = config.allowed_by(relpath, match.rule_id, match.rule_type, text[match.start : match.end], mode)
        findings.append(
            Finding(relpath, line, col, match.rule_id, content_excerpt(match.rule_id, line, col, snippet), match.rule_type, allowed_by)
        )
    return findings


def _findings_for_path(relpath: str, config: Config, mode: str) -> List[Finding]:
    findings = []
    for rule in config.path_rules:
        glob = rule.globs.first_match(relpath)
        if glob is not None:
            allowed_by = config.allowed_by(relpath, rule.id, rule.type, relpath, mode)
            findings.append(Finding(relpath, 0, 0, rule.id, _path_rule_excerpt(rule.id, glob), rule.type, allowed_by))
    for match in find_matches(relpath, config):
        col = match.start + 1
        allowed_by = config.allowed_by(relpath, match.rule_id, match.rule_type, relpath[match.start : match.end], mode)
        findings.append(Finding(relpath, 0, col, match.rule_id, _path_name_excerpt(match.rule_id, col), match.rule_type, allowed_by))
    return findings


def _read_target(target: FileTarget) -> Tuple[Optional[str], bool]:
    """Return ``(text, is_binary)``. Raises ``OSError`` when unreadable."""
    if target.is_symlink:
        return os.readlink(target.abspath), False
    with open(target.abspath, "rb") as handle:
        data = handle.read()
    text = decode_bytes(data)
    return text, text is None


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError("mode must be one of %s, not %r" % (", ".join(MODES), mode))


def _coerce_config(config: Union[Config, str, None]) -> Config:
    if isinstance(config, Config):
        return config
    return load_config(config)


def _file_findings(target: FileTarget, config: Config, mode: str, context: int, report: ScanReport) -> List[Finding]:
    findings = _findings_for_path(target.relpath, config, mode)
    try:
        text, binary = _read_target(target)
    except OSError as exc:
        report.errors.append("can't read %s: %s" % (target.relpath, exc.strerror or exc))
        return findings
    report.files_scanned += 1
    if binary:
        report.files_binary += 1
    elif text:
        findings.extend(_findings_from_text(text, target.relpath, config, mode, context))
    return findings


def scan_report(
    paths: Iterable[str],
    config: Union[Config, str, None] = None,
    root: Optional[str] = None,
    mode: str = "check",
    all_files: bool = False,
    exclude: Iterable[str] = (),
    context: int = 0,
    cwd: Optional[str] = None,
) -> ScanReport:
    """Scan ``paths`` and return a :class:`ScanReport`.

    ``config`` is a :class:`Config`, a rules-file path, or ``None`` for the
    default ``rules.json``. ``mode`` is ``"check"`` or ``"bench"`` and selects
    which allowlist entries apply. ``root``, ``all_files`` and ``exclude``
    work as the CLI flags do (see ``discovery``). ``context`` adds that many
    characters of masked context to each excerpt.
    """
    _check_mode(mode)
    compiled = _coerce_config(config)
    report = ScanReport(mode)
    report.warnings.extend(compiled.warnings)
    found = discover(paths, root=root, all_files=all_files, exclude=exclude, cwd=cwd)
    report.warnings.extend(found.warnings)
    report.errors.extend(found.errors)
    seen = set()
    for target in found.files:
        for finding in _file_findings(target, compiled, mode, max(0, int(context)), report):
            if finding.key() in seen:
                continue
            seen.add(finding.key())
            (report.findings if finding.blocking else report.allowed).append(finding)
    report.findings.sort(key=Finding.key)
    report.allowed.sort(key=Finding.key)
    return report


def scan(paths: Iterable[str], config: Union[Config, str, None] = None, include_allowed: bool = False, **options: Any) -> List[Finding]:
    """Scan files and return findings, sorted by path, line, column and rule.

    Returns blocking findings only, unless ``include_allowed`` is true.
    Accepts the keyword options of :func:`scan_report`. Raises
    :class:`ScanError` when a file couldn't be read: the gate fails closed.
    """
    report = scan_report(paths, config, **options)
    if report.errors:
        raise ScanError("; ".join(report.errors))
    if include_allowed:
        return sorted(report.findings + report.allowed, key=Finding.key)
    return list(report.findings)


def scan_text(text: str, config: Union[Config, str, None] = None, path: str = "<text>", mode: str = "check", context: int = 0) -> List[Finding]:
    """Scan a string as if it were the content of a file at ``path``.

    Path rules and file-name matching don't apply; allowlist entries match
    against ``path``. Returns blocking and allowed findings, sorted.
    """
    _check_mode(mode)
    compiled = _coerce_config(config)
    findings = _findings_from_text(text, path, compiled, mode, max(0, int(context)))
    return sorted(findings, key=Finding.key)
