#!/usr/bin/env python3
"""The ledger: a local, counted record of what the team cost its owner.

Source: adapters/templates/engine/tools/ledger.py, copied into engine/tools/
by adapters/generate.py. Edit the template, then regenerate.

The owner-auditor's report used to rest on what a model remembered. The
engine's hooks now write one line per event to ``.sotacommand/ledger.jsonl``
in the project, so the report can start from counts:

==================  =========================================================
kind                written when
==================  =========================================================
``session_start``   a session opens (SessionStart)
``owner_message``   the owner sends a message (UserPromptSubmit)
``denied``          a guard refuses a write, a shell command or a hand-off
``asked``           a shell command needs the owner's OK (shell_guard.ask)
``close_failed``    the close gate's verify fails
``boot_invalid``    the role folder, registry and router disagree at boot
``memory_over``     a memory file is over its cap at boot
==================  =========================================================

What is and isn't recorded:

* Local only. Nothing is sent anywhere; this module makes no network calls.
* Never the owner's words. An ``owner_message`` line holds the message's
  length, not its text.
* Never a full shell command, which could hold a secret. A ``denied`` line
  holds the guard's reason, cut to 200 characters.
* Nothing at all unless the project has an ``os.config.json``, and nothing
  when it sets ``"ledger": {"enabled": false}``.

The file rotates at 2 MB (one older file is kept). ``.sotacommand/**`` is a
protected path in the config template, so agents can read the ledger but not
edit it. Writing the ledger never changes a guard's decision: every failure
here is swallowed.

Commands::

    ledger.py --event owner_message      hook mode: reads hook JSON on stdin
    ledger.py report [--root DIR] [--days N]
"""

import argparse
import datetime
import json
import os
import sys
from collections import OrderedDict
from typing import Any, Dict, Iterator, List, Optional, Sequence, TextIO

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import os_config  # noqa: E402  (sibling module, found via the line above)

DIR = ".sotacommand"
LEDGER = os.path.join(DIR, "ledger.jsonl")
OLDER = os.path.join(DIR, "ledger.1.jsonl")
SUMMARY = os.path.join(DIR, "ledger-summary.md")
MAX_BYTES = 2 * 1024 * 1024
MAX_DETAIL = 200
KINDS = ("session_start", "owner_message", "denied", "asked", "close_failed", "boot_invalid", "memory_over")
GUARDS = ("write", "shell", "team")


def enabled(root: str) -> bool:
    """True when the project is set up and hasn't switched the ledger off."""
    try:
        config, _ = os_config.load_config(root)
    except os_config.ConfigError:
        return False
    if config is None:
        return False
    block = config.get("ledger")
    return not (isinstance(block, dict) and block.get("enabled") is False)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def record(root: str, kind: str, hook_input: Optional[Dict[str, Any]] = None, now: Optional[datetime.datetime] = None,
           **fields: Any) -> bool:
    """Append one event. Returns True when a line was written. Never raises."""
    try:
        if kind not in KINDS or not enabled(root):
            return False
        entry: "OrderedDict[str, Any]" = OrderedDict()
        entry["ts"] = (now or _now()).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry["kind"] = kind
        hook_input = hook_input or {}
        session = hook_input.get("session_id")
        if isinstance(session, str) and session:
            entry["session"] = session[:8]
        role = hook_input.get("agent_type")
        if isinstance(role, str) and role:
            entry["role"] = role.rpartition(":")[2]
        for key, value in fields.items():
            if value is None:
                continue
            entry[key] = " ".join(value.split())[:MAX_DETAIL] if isinstance(value, str) else value
        path = os.path.join(root, LEDGER)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path) and os.path.getsize(path) > MAX_BYTES:
            os.replace(path, os.path.join(root, OLDER))
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception:  # the ledger must never break a hook
        return False


def events(root: str) -> Iterator[Dict[str, Any]]:
    """Every readable event, oldest file first. Bad lines are skipped."""
    for relative in (OLDER, LEDGER):
        path = os.path.join(root, relative)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(entry, dict) and isinstance(entry.get("ts"), str) and entry.get("kind") in KINDS:
                        yield entry
        except OSError:
            continue


def summarize(root: str, days: int = 7, now: Optional[datetime.datetime] = None) -> "OrderedDict[str, Any]":
    """Counts for the last ``days`` days (today included), in UTC."""
    now = now or _now()
    first_day = (now - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    last_day = now.strftime("%Y-%m-%d")
    totals: "OrderedDict[str, int]" = OrderedDict((kind, 0) for kind in KINDS)
    denied_by: "OrderedDict[str, int]" = OrderedDict((guard, 0) for guard in GUARDS)
    by_day: Dict[str, Dict[str, int]] = {}
    reasons: Dict[str, int] = {}
    for entry in events(root):
        day = entry["ts"][:10]
        if not first_day <= day <= last_day:
            continue
        kind = entry["kind"]
        totals[kind] += 1
        by_day.setdefault(day, {k: 0 for k in KINDS})[kind] += 1
        if kind == "denied":
            guard = entry.get("guard")
            if guard in denied_by:
                denied_by[guard] += 1
            detail = entry.get("detail")
            if isinstance(detail, str) and detail:
                reasons[detail] = reasons.get(detail, 0) + 1
    repeated = sorted(((count, text) for text, count in reasons.items() if count > 1), reverse=True)[:5]
    return OrderedDict([
        ("days", days), ("from", first_day), ("to", last_day), ("totals", totals), ("denied_by", denied_by),
        ("by_day", OrderedDict(sorted(by_day.items()))), ("repeated_denials", [(text, count) for count, text in repeated]),
        ("events", sum(totals.values())),
    ])


def line(summary: Dict[str, Any]) -> str:
    """One fact line for the session-start report."""
    t, d = summary["totals"], summary["denied_by"]
    return (
        "ledger, last %d days (counted, not estimated): %d session(s), %d owner message(s), "
        "%d refusal(s) (%d write, %d shell, %d team), %d approval(s) asked, %d failed close(s)."
        % (summary["days"], t["session_start"], t["owner_message"], t["denied"], d["write"], d["shell"], d["team"],
           t["asked"], t["close_failed"])
    )


def render(summary: Dict[str, Any]) -> str:
    """The summary as Markdown, for the owner-auditor to read."""
    t, d = summary["totals"], summary["denied_by"]
    out = [
        "# Ledger summary",
        "",
        "Counted by the engine's hooks from `.sotacommand/ledger.jsonl`, %s to %s (UTC). These are counts, not estimates." % (summary["from"], summary["to"]),
        "The ledger doesn't know minutes or causes: those are the owner-auditor's judgment, and must be labelled as estimates.",
        "",
        "| Measure | Count |",
        "|---|--:|",
        "| Sessions started | %d |" % t["session_start"],
        "| Messages the owner sent | %d |" % t["owner_message"],
        "| Refusals by a guard | %d |" % t["denied"],
        "| ...file writes refused | %d |" % d["write"],
        "| ...shell commands refused | %d |" % d["shell"],
        "| ...hand-offs to a helper that is off | %d |" % d["team"],
        "| Approvals the owner was asked for | %d |" % t["asked"],
        "| Failed closes | %d |" % t["close_failed"],
        "| Boots with a broken role registry | %d |" % t["boot_invalid"],
        "| Memory files over their cap at boot | %d |" % t["memory_over"],
        "",
    ]
    if summary["by_day"]:
        out += ["## By day", "", "| Day | Sessions | Owner messages | Refusals | Approvals asked | Failed closes |", "|---|--:|--:|--:|--:|--:|"]
        for day, counts in summary["by_day"].items():
            out.append("| %s | %d | %d | %d | %d | %d |" % (day, counts["session_start"], counts["owner_message"],
                                                              counts["denied"], counts["asked"], counts["close_failed"]))
        out.append("")
    if summary["repeated_denials"]:
        out += ["## The same refusal, more than once", "", "A refusal that repeats is a rule the team keeps running into: a candidate for the top fix.", ""]
        out += ["- %d times: %s" % (count, text) for text, count in summary["repeated_denials"]]
        out.append("")
    if not summary["events"]:
        out += ["No events in this period.", ""]
    return "\n".join(out)


def write_summary(root: str, days: int = 28, now: Optional[datetime.datetime] = None) -> Optional["OrderedDict[str, Any]"]:
    """Refresh ``.sotacommand/ledger-summary.md``. Returns the summary, or None when the ledger is off. Never raises."""
    try:
        if not enabled(root):
            return None
        summary = summarize(root, days, now)
        path = os.path.join(root, SUMMARY)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render(summary))
        return summary
    except Exception:
        return None


def main(argv: Optional[Sequence[str]] = None, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    stdout = stdout or sys.stdout
    parser = argparse.ArgumentParser(description="The local ledger of what the team cost its owner.")
    parser.add_argument("command", nargs="?", choices=("report",), help="print the summary")
    parser.add_argument("--event", choices=("owner_message",), help="hook mode: record this event from hook JSON on stdin")
    parser.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, then the input cwd)")
    parser.add_argument("--days", type=int, default=7, help="days to summarize (default 7)")
    args = parser.parse_args(argv)
    if args.event:
        # A hook on the owner's own message: stay silent and never block it.
        try:
            hook_input = os_config.read_hook_input(stdin)
        except os_config.InputError:
            return 0
        prompt = hook_input.get("prompt")
        record(os_config.project_root(hook_input, args.root), args.event, hook_input,
               chars=len(prompt) if isinstance(prompt, str) else None)
        return 0
    if args.command != "report" or args.days < 1:
        parser.error("say `report`, with --days of at least 1, or use --event")
    root = os_config.project_root({}, args.root)
    if not enabled(root):
        stdout.write("ledger: off for %s (no os.config.json, or ledger.enabled is false).\n" % root)
        return 1
    stdout.write(render(summarize(root, args.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
