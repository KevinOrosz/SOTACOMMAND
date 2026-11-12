"""The redacted eval summary (``summary.json``) and the evidence read from it.

``claude plugin eval --json`` writes ``aggregate-result.json``, which holds
model identifiers, absolute paths, prompts and per-run grader evidence. It
must never be committed. ``skills.py redact-results`` turns it into a
``summary.json`` that keeps only the numbers. The summary is a strict subset
of the source document, with the same field names at the same paths, plus a
few additive fields (``summaryFormat``, ``date``, ``cases[].skill``,
``suite.modelPinned``, ``suite.judgeModelPinned`` and per-run ``errored``).
A reader of one can therefore read the other.

The format is documented in ``tools/repo/skills/CONTRACT.md``.
:func:`validate_summary` enforces it strictly: an unknown key anywhere is an
error, so a committed summary can only carry the allowlisted fields.
"""

import datetime
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

SUMMARY_FORMAT = 1
SUMMARY_FILENAME = "summary.json"

# The stable rule (see CONTRACT.md). A skill is stable only when its newest
# complete two-arm run meets every threshold.
STABLE_MIN_SCORE = 0.8
STABLE_MIN_DELTA = 0.2
STABLE_MIN_RUNS = 3
STABLE_MIN_CASES = 3
_EPSILON = 1e-9

PARTIAL_REASONS = ("cost_ceiling", "interrupted", "auth_failed")
ABLATIONS = ("none", "with-without")

CASE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SKILL_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PLUGIN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}")
_CLAUDE_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_ ()-]{0,63}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)

_TOP_KEYS = (
    "summaryFormat",
    "schemaVersion",
    "claudeVersion",
    "startedAt",
    "date",
    "durationSeconds",
    "costUsd",
    "partial",
    "partialReason",
    "suite",
    "cases",
    "aggregates",
)
_TOP_REQUIRED = tuple(k for k in _TOP_KEYS if k != "partialReason")
_SUITE_KEYS = ("ablation", "threshold", "modelPinned", "judgeModelPinned", "plugins")
_PLUGIN_KEYS = ("name", "version")
_CASE_KEYS = ("name", "skill", "runsPerCase", "aggregates", "arms")
_CASE_AGG_KEYS = ("score", "passRate", "scoreWithout", "passRateWithout", "delta")
_CASE_AGG_REQUIRED = ("score", "passRate")
_RUN_KEYS = ("score", "passed", "errored", "skippedPaidGraders")
_AGG_KEYS = ("casesTotal", "casesPassed", "overallScore", "overallPassRate", "meanDelta")
_AGG_REQUIRED = ("casesTotal", "casesPassed", "overallScore", "overallPassRate")


def is_number(value: Any) -> bool:
    """A finite JSON number (``bool`` excluded)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class _Checker(object):
    def __init__(self) -> None:
        self.errors = []  # type: List[str]

    def fail(self, path: str, message: str) -> None:
        self.errors.append("{}: {}".format(path, message))

    def object(self, value: Any, path: str, allowed: Sequence[str], required: Sequence[str]) -> bool:
        if not isinstance(value, dict):
            self.fail(path, "must be an object")
            return False
        for key in value:
            if key not in allowed:
                self.fail("{}.{}".format(path, key), "is not part of the summary format")
        for key in required:
            if key not in value:
                self.fail("{}.{}".format(path, key), "is required")
        return True

    def number(self, obj: dict, key: str, path: str, low: float, high: Optional[float]) -> None:
        if key not in obj:
            return
        value = obj[key]
        where = "{}.{}".format(path, key)
        if not is_number(value):
            self.fail(where, "must be a number")
        elif value < low - _EPSILON or (high is not None and value > high + _EPSILON):
            bound = "[{}, {}]".format(low, high) if high is not None else ">= {}".format(low)
            self.fail(where, "must be in {}".format(bound))

    def integer(self, obj: dict, key: str, path: str, low: int) -> None:
        if key not in obj:
            return
        value = obj[key]
        if not is_int(value) or value < low:
            self.fail("{}.{}".format(path, key), "must be an integer >= {}".format(low))

    def boolean(self, obj: dict, key: str, path: str) -> None:
        if key in obj and not isinstance(obj[key], bool):
            self.fail("{}.{}".format(path, key), "must be true or false")

    def string(self, obj: dict, key: str, path: str, pattern: "re.Pattern", what: str) -> None:
        if key not in obj:
            return
        value = obj[key]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            self.fail("{}.{}".format(path, key), "must be {}".format(what))


def _valid_date(text: str) -> bool:
    try:
        datetime.date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        return False
    return True


def validate_summary(doc: Any) -> List[str]:
    """Return every way ``doc`` departs from the summary format (empty when valid)."""
    check = _Checker()
    if not check.object(doc, "summary", _TOP_KEYS, _TOP_REQUIRED):
        return check.errors
    if "summaryFormat" in doc and (not is_int(doc["summaryFormat"]) or doc["summaryFormat"] != SUMMARY_FORMAT):
        check.fail("summary.summaryFormat", "must be {}".format(SUMMARY_FORMAT))
    check.integer(doc, "schemaVersion", "summary", 1)
    check.string(doc, "claudeVersion", "summary", _CLAUDE_VERSION_RE, "a Claude Code version string")
    if "startedAt" in doc and doc["startedAt"] is not None:
        check.string(doc, "startedAt", "summary", _TIMESTAMP_RE, "an ISO 8601 UTC timestamp or null")
    if "date" in doc:
        date = doc["date"]
        if not isinstance(date, str) or not _DATE_RE.fullmatch(date) or not _valid_date(date):
            check.fail("summary.date", "must be a YYYY-MM-DD date")
    check.number(doc, "durationSeconds", "summary", 0, None)
    check.number(doc, "costUsd", "summary", 0, None)
    check.boolean(doc, "partial", "summary")
    if "partialReason" in doc:
        if doc.get("partial") is not True:
            check.fail("summary.partialReason", "is only allowed when partial is true")
        elif doc["partialReason"] not in PARTIAL_REASONS:
            check.fail("summary.partialReason", "must be one of {}".format(", ".join(PARTIAL_REASONS)))

    suite = doc.get("suite")
    if "suite" in doc and check.object(suite, "summary.suite", _SUITE_KEYS, _SUITE_KEYS):
        if "ablation" in suite and suite["ablation"] not in ABLATIONS:
            check.fail("summary.suite.ablation", "must be one of {}".format(", ".join(ABLATIONS)))
        check.number(suite, "threshold", "summary.suite", 0, 1)
        check.boolean(suite, "modelPinned", "summary.suite")
        check.boolean(suite, "judgeModelPinned", "summary.suite")
        plugins = suite.get("plugins")
        if "plugins" in suite:
            if not isinstance(plugins, list):
                check.fail("summary.suite.plugins", "must be a list")
            else:
                for index, plugin in enumerate(plugins):
                    where = "summary.suite.plugins[{}]".format(index)
                    if check.object(plugin, where, _PLUGIN_KEYS, ("name",)):
                        check.string(plugin, "name", where, _PLUGIN_NAME_RE, "a plugin name")
                        check.string(plugin, "version", where, _VERSION_RE, "a version string")

    cases = doc.get("cases")
    if "cases" in doc:
        if not isinstance(cases, list):
            check.fail("summary.cases", "must be a list")
        else:
            seen = set()
            for index, case in enumerate(cases):
                _validate_case(check, case, "summary.cases[{}]".format(index), seen)

    aggregates = doc.get("aggregates")
    if "aggregates" in doc and check.object(aggregates, "summary.aggregates", _AGG_KEYS, _AGG_REQUIRED):
        check.integer(aggregates, "casesTotal", "summary.aggregates", 0)
        check.integer(aggregates, "casesPassed", "summary.aggregates", 0)
        check.number(aggregates, "overallScore", "summary.aggregates", 0, 1)
        check.number(aggregates, "overallPassRate", "summary.aggregates", 0, 1)
        check.number(aggregates, "meanDelta", "summary.aggregates", -1, 1)
        total = aggregates.get("casesTotal")
        passed = aggregates.get("casesPassed")
        if is_int(total) and is_int(passed) and passed > total:
            check.fail("summary.aggregates.casesPassed", "can't exceed casesTotal")
        if is_int(total) and isinstance(cases, list) and total != len(cases):
            check.fail("summary.aggregates.casesTotal", "must equal the number of cases ({})".format(len(cases)))
    return check.errors


def _validate_case(check: _Checker, case: Any, where: str, seen: set) -> None:
    if not check.object(case, where, _CASE_KEYS, _CASE_KEYS):
        return
    name = case.get("name")
    check.string(case, "name", where, CASE_NAME_RE, "a case name")
    if isinstance(name, str):
        if name in seen:
            check.fail(where + ".name", "duplicates another case name")
        seen.add(name)
    if case.get("skill") is not None:
        check.string(case, "skill", where, SKILL_NAME_RE, "a skill name or null")
    check.integer(case, "runsPerCase", where, 1)
    aggregates = case.get("aggregates")
    if "aggregates" in case and check.object(
        aggregates, where + ".aggregates", _CASE_AGG_KEYS, _CASE_AGG_REQUIRED
    ):
        for key in ("score", "passRate", "scoreWithout", "passRateWithout"):
            check.number(aggregates, key, where + ".aggregates", 0, 1)
        check.number(aggregates, "delta", where + ".aggregates", -1, 1)
    arms = case.get("arms")
    if "arms" in case and check.object(arms, where + ".arms", ("with", "without"), ("with",)):
        for arm in ("with", "without"):
            if arm not in arms:
                continue
            runs = arms[arm]
            arm_where = "{}.arms.{}".format(where, arm)
            if not isinstance(runs, list):
                check.fail(arm_where, "must be a list")
                continue
            for index, run in enumerate(runs):
                run_where = "{}[{}]".format(arm_where, index)
                if check.object(run, run_where, _RUN_KEYS, _RUN_KEYS):
                    check.number(run, "score", run_where, 0, 1)
                    check.boolean(run, "passed", run_where)
                    check.boolean(run, "errored", run_where)
                    check.boolean(run, "skippedPaidGraders", run_where)


class LoadedSummary(NamedTuple):
    """A summary file on disk: its parsed document, or the reasons it can't be used."""

    path: Path
    doc: Optional[Dict[str, Any]]
    errors: List[str]

    @property
    def sort_key(self) -> Tuple[str, str]:
        """Newest last: ``startedAt`` when present, else the run date."""
        doc = self.doc or {}
        stamp = doc.get("startedAt") or "{}T00:00:00Z".format(doc.get("date", "0000-00-00"))
        return (_to_utc(stamp), str(self.path))


_TIMESTAMP_PARTS = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?"
    r"(?:Z|([+-])([0-9]{2}):([0-9]{2}))"
)


def _to_utc(stamp: str) -> str:
    """Normalize an ISO 8601 timestamp to a sortable UTC string."""
    match = _TIMESTAMP_PARTS.fullmatch(stamp)
    if not match:
        return stamp
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    micro = int((match.group(7) or "0")[:6].ljust(6, "0"))
    try:
        moment = datetime.datetime(year, month, day, hour, minute, second, micro)
    except ValueError:
        return stamp
    if match.group(8):
        offset = datetime.timedelta(hours=int(match.group(9)), minutes=int(match.group(10)))
        moment = moment - offset if match.group(8) == "+" else moment + offset
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")


def load_summary(path: Path) -> LoadedSummary:
    """Read and validate one ``summary.json``."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return LoadedSummary(path, None, ["can't be read: {}".format(exc)])
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return LoadedSummary(path, None, ["is not valid JSON: {}".format(exc)])
    errors = validate_summary(doc)
    return LoadedSummary(path, doc if not errors else None, errors)


class SkillEvidence(NamedTuple):
    """One skill's numbers in one summary."""

    summary_path: Path
    date: str
    started_at: Optional[str]
    score: float
    delta: Optional[float]
    delta_cases: int
    runs: int
    runs_without: int
    cases: int
    partial: bool
    ablation: str
    model_pinned: bool


def evidence_for(summary: LoadedSummary, skill: str) -> Optional[SkillEvidence]:
    """The skill's mean score, mean Δ and run counts in ``summary``, if it has cases there."""
    doc = summary.doc
    if doc is None:
        return None
    cases = [case for case in doc["cases"] if case.get("skill") == skill]
    if not cases:
        return None
    scores = [case["aggregates"]["score"] for case in cases]
    deltas = [case["aggregates"]["delta"] for case in cases if "delta" in case["aggregates"]]
    runs = min(len(case["arms"]["with"]) for case in cases)
    runs_without = min(len(case["arms"].get("without", [])) for case in cases)
    return SkillEvidence(
        summary_path=summary.path,
        date=doc["date"],
        started_at=doc.get("startedAt"),
        score=sum(scores) / len(scores),
        delta=(sum(deltas) / len(deltas)) if deltas else None,
        delta_cases=len(deltas),
        runs=runs,
        runs_without=runs_without,
        cases=len(cases),
        partial=bool(doc["partial"]),
        ablation=doc["suite"]["ablation"],
        model_pinned=bool(doc["suite"]["modelPinned"]),
    )


def latest_evidence(summaries: Sequence[LoadedSummary], skill: str) -> Optional[SkillEvidence]:
    """Evidence from the newest summary covering ``skill``; complete runs win over partial ones."""
    ordered = sorted((s for s in summaries if s.doc is not None), key=lambda s: s.sort_key, reverse=True)
    found = [e for e in (evidence_for(s, skill) for s in ordered) if e is not None]
    complete = [e for e in found if not e.partial]
    if complete:
        return complete[0]
    return found[0] if found else None


def qualifying_evidence(summaries: Sequence[LoadedSummary], skill: str) -> Optional[SkillEvidence]:
    """Evidence from the newest complete two-arm run covering ``skill``."""
    ordered = sorted((s for s in summaries if s.doc is not None), key=lambda s: s.sort_key, reverse=True)
    for summary in ordered:
        evidence = evidence_for(summary, skill)
        if evidence is not None and not evidence.partial and evidence.ablation == "with-without":
            return evidence
    return None


def stable_rule_failures(evidence: Optional[SkillEvidence]) -> List[str]:
    """Why ``evidence`` doesn't meet the stable rule (empty when it does)."""
    if evidence is None:
        return ["no complete two-arm eval run (--ablation with-without) covers this skill"]
    reasons = []
    if evidence.cases < STABLE_MIN_CASES:
        reasons.append("the run covers {} case(s); at least {} are required".format(evidence.cases, STABLE_MIN_CASES))
    if evidence.runs < STABLE_MIN_RUNS or evidence.runs_without < STABLE_MIN_RUNS:
        reasons.append(
            "the run has {} with-skill and {} baseline run(s) per case; at least {} of each are required".format(
                evidence.runs, evidence.runs_without, STABLE_MIN_RUNS
            )
        )
    if evidence.score < STABLE_MIN_SCORE - _EPSILON:
        reasons.append("with-skill score {:.2f} is below {:.2f}".format(evidence.score, STABLE_MIN_SCORE))
    if evidence.delta is None or evidence.delta_cases < evidence.cases:
        reasons.append("the run has no baseline delta for every case")
    elif evidence.delta < STABLE_MIN_DELTA - _EPSILON:
        reasons.append("delta {:+.2f} is below {:+.2f}".format(evidence.delta, STABLE_MIN_DELTA))
    return reasons
