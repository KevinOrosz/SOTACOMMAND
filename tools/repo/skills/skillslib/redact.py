"""``skills.py redact-results``: turn ``aggregate-result.json`` into ``summary.json``.

The redactor copies an allowlist of fields; nothing else can reach the
output. It keeps the source document's field names and paths, so the
summary stays a strict subset of ``aggregate-result.json``:

* ``schemaVersion``, ``claudeVersion``, ``startedAt``, ``durationSeconds``,
  ``costUsd``, ``partial`` and ``partialReason``;
* ``suite.ablation``, ``suite.threshold`` and each plugin's ``name`` and
  ``version``;
* ``aggregates.casesTotal``, ``casesPassed``, ``overallScore``,
  ``overallPassRate`` and ``meanDelta``;
* per case: ``name``, ``runsPerCase``, ``aggregates.score``, ``passRate``,
  ``scoreWithout``, ``passRateWithout`` and ``delta``, and per run
  ``score``, ``passed`` and ``skippedPaidGraders``.

It adds ``summaryFormat``, the run ``date``, each case's ``skill`` (the
case's parent directory under ``evals/``), ``suite.modelPinned`` and
``suite.judgeModelPinned`` (booleans, never the model names) and a per-run
``errored`` flag in place of the error text.

It drops model identifiers, absolute paths, prompts, transcripts, trace
paths, grader definitions, explanations, judge votes and evidence. As a
final check it collects every value the source holds under a key that names
a model and refuses to write if any of them appears in the output.
"""

import datetime
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import PluginInfo
from .summary import (
    CASE_NAME_RE,
    PARTIAL_REASONS,
    SKILL_NAME_RE,
    SUMMARY_FORMAT,
    is_int,
    is_number,
    validate_summary,
)

_MODEL_KEY_RE = re.compile(r"model", re.IGNORECASE)
_MIN_LEAK_LENGTH = 3


class RedactError(Exception):
    """The input can't be redacted safely, so nothing was written."""


def _require(obj: Any, key: str, where: str) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        raise RedactError("{}.{} is missing; is this an aggregate-result.json from claude plugin eval?".format(where, key))
    return obj[key]


def _number(obj: dict, key: str, where: str) -> float:
    value = _require(obj, key, where)
    if not is_number(value):
        raise RedactError("{}.{} must be a number".format(where, key))
    return value


def _optional_number(obj: dict, key: str, where: str) -> Optional[float]:
    if key not in obj or obj[key] is None:
        return None
    return _number(obj, key, where)


def _path_parts(path: str) -> List[str]:
    return [part for part in re.split(r"[\\/]+", path) if part and part != "."]


def skill_from_case_dir(case_dir: Any) -> Optional[str]:
    """The skill a case belongs to: the name of the case directory's parent.

    Cases live at ``evals/<skill>/<case>/``. The source records each case's
    directory as a path (absolute, or relative to the plugin or eval root),
    so its parent component names the skill.
    """
    if not isinstance(case_dir, str):
        return None
    parts = _path_parts(case_dir)
    if len(parts) < 2:
        return None
    parent = parts[-2]
    if parent in ("evals", "results", "mocks") or not SKILL_NAME_RE.fullmatch(parent):
        return None
    return parent


def _run(run: Any, where: str) -> Dict[str, Any]:
    if not isinstance(run, dict):
        raise RedactError("{} must be an object".format(where))
    passed = run.get("passed")
    return {
        "score": _number(run, "score", where),
        "passed": bool(passed) if isinstance(passed, bool) else _number(run, "score", where) >= 1,
        "errored": run.get("error") not in (None, "") or bool(run.get("aborted")),
        "skippedPaidGraders": bool(run.get("skippedPaidGraders", False)),
    }


def _case(case: Any, index: int, case_skills: Dict[str, str]) -> Dict[str, Any]:
    where = "cases[{}]".format(index)
    if not isinstance(case, dict):
        raise RedactError("{} must be an object".format(where))
    name = _require(case, "name", where)
    if not isinstance(name, str) or not CASE_NAME_RE.fullmatch(name):
        raise RedactError("{}.name {!r} isn't a valid case name".format(where, name))
    aggregates = _require(case, "aggregates", where)
    agg_where = where + ".aggregates"
    out_agg = {"score": _number(aggregates, "score", agg_where)}  # type: Dict[str, Any]
    pass_rate = _optional_number(aggregates, "passRate", agg_where)
    out_agg["passRate"] = pass_rate if pass_rate is not None else out_agg["score"]
    for key in ("scoreWithout", "passRateWithout", "delta"):
        value = _optional_number(aggregates, key, agg_where)
        if value is not None:
            out_agg[key] = value
    arms = _require(case, "arms", where)
    with_runs = _require(arms, "with", where + ".arms")
    if not isinstance(with_runs, list):
        raise RedactError("{}.arms.with must be a list".format(where))
    out_arms = {"with": [_run(r, "{}.arms.with[{}]".format(where, i)) for i, r in enumerate(with_runs)]}
    without = arms.get("without")
    if isinstance(without, list):
        out_arms["without"] = [_run(r, "{}.arms.without[{}]".format(where, i)) for i, r in enumerate(without)]
    runs_per_case = case.get("runsPerCase")
    if not is_int(runs_per_case) or runs_per_case < 1:
        runs_per_case = max(len(with_runs), 1)
    skill = skill_from_case_dir(case.get("dir")) or case_skills.get(name)
    return {
        "name": name,
        "skill": skill,
        "runsPerCase": runs_per_case,
        "aggregates": out_agg,
        "arms": out_arms,
    }


def _run_date(started_at: Optional[str], override: Optional[str]) -> str:
    if override is not None:
        try:
            datetime.datetime.strptime(override, "%Y-%m-%d")
        except ValueError:
            raise RedactError("--date {!r} must be YYYY-MM-DD".format(override))
        return override
    if started_at and re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", started_at):
        return started_at[:10]
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _model_values(node: Any, under_model_key: bool = False) -> List[str]:
    """Every string in the source that sits under a key naming a model."""
    found = []  # type: List[str]
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_model_values(value, under_model_key or bool(_MODEL_KEY_RE.search(str(key)))))
    elif isinstance(node, list):
        for item in node:
            found.extend(_model_values(item, under_model_key))
    elif isinstance(node, str) and under_model_key and len(node) >= _MIN_LEAK_LENGTH:
        found.append(node)
    return found


def _strings(node: Any) -> List[str]:
    """Every string value in ``node`` (keys are fixed by the format, so they are skipped)."""
    if isinstance(node, dict):
        return [s for value in node.values() for s in _strings(value)]
    if isinstance(node, list):
        return [s for item in node for s in _strings(item)]
    return [node] if isinstance(node, str) else []


def redact(source: Any, date: Optional[str] = None, plugin_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Build the redacted summary for a parsed ``aggregate-result.json``."""
    if not isinstance(source, dict):
        raise RedactError("the input must be a JSON object")
    schema_version = _require(source, "schemaVersion", "result")
    if not is_int(schema_version) or schema_version < 1:
        raise RedactError("result.schemaVersion must be a positive integer")
    claude_version = _require(source, "claudeVersion", "result")
    if not isinstance(claude_version, str):
        raise RedactError("result.claudeVersion must be a string")
    started_at = source.get("startedAt")
    if started_at is not None and not isinstance(started_at, str):
        raise RedactError("result.startedAt must be a string")
    suite = _require(source, "suite", "result")
    ablation = _require(suite, "ablation", "result.suite")
    threshold = _number(suite, "threshold", "result.suite")
    plugins = []
    for index, plugin in enumerate(suite.get("plugins") or []):
        if not isinstance(plugin, dict) or not isinstance(plugin.get("name"), str):
            raise RedactError("result.suite.plugins[{}].name must be a string".format(index))
        entry = {"name": plugin["name"]}
        if isinstance(plugin.get("version"), str):
            entry["version"] = plugin["version"]
        plugins.append(entry)

    case_skills = {}  # type: Dict[str, str]
    if plugin_dir is not None:
        info = PluginInfo(plugin_dir)
        case_skills = {case.name: case.skill_group for case in info.cases if case.skill_group}

    cases = _require(source, "cases", "result")
    if not isinstance(cases, list):
        raise RedactError("result.cases must be a list")
    out_cases = [_case(case, index, case_skills) for index, case in enumerate(cases)]

    aggregates = _require(source, "aggregates", "result")
    out_agg = {
        "casesTotal": _require(aggregates, "casesTotal", "result.aggregates"),
        "casesPassed": _require(aggregates, "casesPassed", "result.aggregates"),
        "overallScore": _number(aggregates, "overallScore", "result.aggregates"),
        "overallPassRate": _optional_number(aggregates, "overallPassRate", "result.aggregates"),
    }  # type: Dict[str, Any]
    if out_agg["overallPassRate"] is None:
        out_agg["overallPassRate"] = out_agg["overallScore"]
    mean_delta = _optional_number(aggregates, "meanDelta", "result.aggregates")
    if mean_delta is not None:
        out_agg["meanDelta"] = mean_delta

    partial = bool(source.get("partial", False))
    summary = {
        "summaryFormat": SUMMARY_FORMAT,
        "schemaVersion": schema_version,
        "claudeVersion": claude_version,
        "startedAt": started_at,
        "date": _run_date(started_at, date),
        "durationSeconds": _number(source, "durationSeconds", "result"),
        "costUsd": _number(source, "costUsd", "result"),
        "partial": partial,
    }  # type: Dict[str, Any]
    reason = source.get("partialReason")
    if partial and reason in PARTIAL_REASONS:
        summary["partialReason"] = reason
    summary["suite"] = {
        "ablation": ablation,
        "threshold": threshold,
        "modelPinned": bool(suite.get("modelOverride")),
        "judgeModelPinned": bool(suite.get("judgeModel")),
        "plugins": plugins,
    }
    summary["cases"] = out_cases
    summary["aggregates"] = out_agg

    errors = validate_summary(summary)
    if errors:
        raise RedactError("the redacted summary would be invalid: {}".format("; ".join(errors)))
    output_strings = _strings(summary)
    for value in _model_values(source):
        if any(value in text for text in output_strings):
            raise RedactError("a value from a model field would survive redaction; nothing was written")
    return summary


def render_json(summary: Dict[str, Any]) -> str:
    return json.dumps(summary, indent=2, ensure_ascii=False) + "\n"


def markdown_table(summary: Dict[str, Any]) -> str:
    """A short Markdown report of a summary, for CI job summaries and the terminal."""

    def fmt(value: Optional[float], signed: bool = False) -> str:
        if value is None:
            return "—"
        return "{:+.2f}".format(value) if signed else "{:.2f}".format(value)

    plugins = ", ".join(p["name"] for p in summary["suite"]["plugins"]) or "unknown plugin"
    lines = [
        "| Case | Skill | Score | W/OUT | Δ | Runs |",
        "|---|---|---|---|---|---|",
    ]
    for case in summary["cases"]:
        agg = case["aggregates"]
        runs = len(case["arms"]["with"]) + len(case["arms"].get("without", []))
        lines.append(
            "| {} | {} | {} | {} | {} | {} |".format(
                case["name"], case["skill"] or "—", fmt(agg["score"]), fmt(agg.get("scoreWithout")),
                fmt(agg.get("delta"), signed=True), runs,
            )
        )
    agg = summary["aggregates"]
    footer = "{}: score {} · mean Δ {} · {}/{} cases passed at threshold {} · ${:.2f} · {:.0f}s · ablation {}".format(
        plugins, fmt(agg["overallScore"]), fmt(agg.get("meanDelta"), signed=True), agg["casesPassed"],
        agg["casesTotal"], summary["suite"]["threshold"], summary["costUsd"], summary["durationSeconds"],
        summary["suite"]["ablation"],
    )
    if summary["partial"]:
        footer += " · PARTIAL ({})".format(summary.get("partialReason", "unknown reason"))
    return "\n".join(lines + ["", footer, ""])


def redact_file(source_path: Path, out_path: Path, date: Optional[str] = None, plugin_dir: Optional[Path] = None) -> Tuple[Dict[str, Any], str]:
    """Redact ``source_path`` into ``out_path``. Returns the summary and its JSON text."""
    if source_path.resolve() == out_path.resolve():
        raise RedactError("--out must differ from the input; the raw result is never overwritten in place")
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise RedactError("can't read {}: {}".format(source_path, exc))
    except ValueError as exc:
        raise RedactError("{} is not valid JSON: {}".format(source_path, exc))
    summary = redact(source, date=date, plugin_dir=plugin_dir)
    text = render_json(summary)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(out_path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return summary, text
