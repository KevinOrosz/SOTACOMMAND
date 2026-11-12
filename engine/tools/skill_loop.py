#!/usr/bin/env python3
"""The learning loop: a repeated problem becomes a skill, and the skill has to earn "stable".

Source: adapters/templates/engine/tools/skill_loop.py, copied into
engine/tools/ by adapters/generate.py. Edit the template, then regenerate.

The loop, in the owner's project:

1. **Propose.** The owner-auditor sees the same refusal in the ledger more
   than once, or the same correction in ``memory/lessons.md``. It writes a
   proposal to ``memory/skill-proposals/<name>.md``::

       ---
       name: release-checklist
       description: Use before any release to run the checklist in order.
       match: shell_guard.ask \\bgit\\s+push
       ---
       1. Run the tests.
       2. ...

   ``match`` is optional: text that appears in the ledger's refusals this
   skill is meant to end. Without it the skill can't be measured.

2. **Approve.** The owner says yes. ``skill_loop.py draft <name>`` turns the
   proposal into ``.claude/skills/<name>/SKILL.md`` (and
   ``.agents/skills/<name>/`` when the project has that folder), marked
   ``experimental``, with the ledger's count of matching refusals over the
   last 28 days as its baseline.

3. **Measure.** ``skill_loop.py status`` counts, from the ledger, the sessions
   since the skill was adopted and the matching refusals since. Session start
   reports the same line.

4. **Earn it.** ``skill_loop.py promote <name>`` marks the skill ``stable``
   only when the ledger shows at least ``min_sessions`` sessions since
   adoption (default 5) with no matching refusal, against a baseline of at
   least one. Otherwise it refuses and says why. A skill with no ``match``
   stays experimental: there is nothing to measure it by.

The verdict always comes from the ledger, which agents can't edit, never
from the status a SKILL.md claims. A skill that says ``stable`` without
having qualified is reported as such.

``os.config.json`` may set ``"skill_loop": {"min_sessions": 5}``.

Exit codes: 0 ok, 1 refused (the reason is printed), 2 usage error.
Standard library only; no network calls, no model calls.
"""

import argparse
import datetime
import os
import re
import sys
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import os_config  # noqa: E402  (sibling module, found via the line above)
import ledger  # noqa: E402

PROPOSALS = os.path.join("memory", "skill-proposals")
SKILL_DIRS = (os.path.join(".claude", "skills"), os.path.join(".agents", "skills"))
ORIGIN = "owner-auditor proposal"
BASELINE_DAYS = 28
DEFAULT_MIN_SESSIONS = 5
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_META = re.compile(r'^  ([a-z-]+): "((?:[^"\\]|\\.)*)"\s*$')


class LoopError(Exception):
    """The step can't go ahead; the message says why."""


def _quote(text: str) -> str:
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def _unquote(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def min_sessions(root: str) -> int:
    try:
        config, _ = os_config.load_config(root)
    except os_config.ConfigError:
        return DEFAULT_MIN_SESSIONS
    block = (config or {}).get("skill_loop")
    value = block.get("min_sessions") if isinstance(block, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else DEFAULT_MIN_SESSIONS


def read_proposal(root: str, name: str) -> "OrderedDict[str, str]":
    """``name``, ``description``, ``match`` and ``body`` of a proposal. Raises LoopError."""
    if not _NAME.match(name) or len(name) > 64:
        raise LoopError("a skill name is lowercase words joined by dashes, like release-checklist")
    path = os.path.join(root, PROPOSALS, name + ".md")
    if not os.path.isfile(path):
        raise LoopError("no proposal at %s" % os.path.join(PROPOSALS, name + ".md").replace(os.sep, "/"))
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise LoopError("the proposal must start with a --- block holding name and description")
    head, body = text[4:].split("\n---\n", 1)
    fields: "OrderedDict[str, str]" = OrderedDict()
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep or key.strip() not in ("name", "description", "match"):
            raise LoopError("the proposal's --- block takes only name, description and match (got %r)" % line.strip())
        fields[key.strip()] = value.strip().strip('"')
    if fields.get("name") != name:
        raise LoopError("the proposal's name must be %r, the same as its file name" % name)
    description = fields.get("description", "")
    if not description or len(description) > 1024 or "<" in description or ">" in description:
        raise LoopError("the proposal needs a description (when to use the skill) of at most 1024 characters, without < or >")
    if not body.strip():
        raise LoopError("the proposal has no steps under its --- block")
    return OrderedDict([("name", name), ("description", description), ("match", fields.get("match", "")), ("body", body.strip() + "\n")])


def _count(root: str, match: str, since: str = "", until: str = "9999") -> int:
    """Refusals and approvals asked whose detail contains ``match``, in a time window."""
    if not match:
        return 0
    return sum(
        1 for event in ledger.events(root)
        if event["kind"] in ("denied", "asked") and since <= event["ts"] <= until and match in str(event.get("detail", ""))
    )


def _sessions(root: str, since: str) -> int:
    return sum(1 for event in ledger.events(root) if event["kind"] == "session_start" and event["ts"] > since)


def render_skill(proposal: Dict[str, str], status: str, extra: Sequence[Tuple[str, str]]) -> str:
    meta = [("status", status), ("origin", ORIGIN)] + list(extra)
    lines = ["---", "name: %s" % proposal["name"], "description: %s" % _quote(proposal["description"]), "metadata:"]
    lines += ["  %s: %s" % (key, _quote(value)) for key, value in meta]
    return "\n".join(lines + ["---", "", proposal["body"]])


def draft(root: str, name: str, now: Optional[datetime.datetime] = None) -> List[str]:
    """Turn an approved proposal into an experimental skill. Raises LoopError."""
    proposal = read_proposal(root, name)
    primary = os.path.join(root, SKILL_DIRS[0], name, "SKILL.md")
    if os.path.exists(primary):
        raise LoopError("%s already exists; the skill was drafted before" % os.path.join(SKILL_DIRS[0], name, "SKILL.md").replace(os.sep, "/"))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    since = (now - datetime.timedelta(days=BASELINE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    baseline = _count(root, proposal["match"], since, stamp)
    extra = [("adopted", stamp), ("match", proposal["match"]), ("baseline-repeats", str(baseline)), ("baseline-days", str(BASELINE_DAYS))]
    text = render_skill(proposal, "experimental", extra)
    written = []
    for index, folder in enumerate(SKILL_DIRS):
        if index and not os.path.isdir(os.path.join(root, folder)):
            continue
        path = os.path.join(root, folder, name, "SKILL.md")
        if os.path.exists(path):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        written.append("wrote %s" % os.path.relpath(path, root).replace(os.sep, "/"))
    note = ("baseline: %d matching refusal(s) in the last %d days" % (baseline, BASELINE_DAYS)) if proposal["match"] else \
        "no match text, so this skill can't be measured and will stay experimental"
    return written + ["skill %s is experimental; %s" % (name, note)]


def read_skill(path: str) -> Optional[Dict[str, str]]:
    """The metadata of a skill this loop drafted, or None for any other skill."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return None
    meta = {key: _unquote(value) for key, value in (m.groups() for m in map(_META.match, text[4:].split("\n---\n", 1)[0].splitlines()) if m)}
    return meta if meta.get("origin") == ORIGIN and "adopted" in meta else None


def verdicts(root: str) -> List["OrderedDict[str, Any]"]:
    """One measured verdict per skill this loop drafted, by name."""
    base = os.path.join(root, SKILL_DIRS[0])
    need = min_sessions(root)
    out = []
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        meta = read_skill(os.path.join(base, name, "SKILL.md"))
        if meta is None:
            continue
        sessions = _sessions(root, meta["adopted"])
        repeats = _count(root, meta.get("match", ""), meta["adopted"])
        baseline = int(meta["baseline-repeats"]) if meta.get("baseline-repeats", "").isdigit() else 0
        if not meta.get("match"):
            verdict, why = "unmeasured", "no match text to count against"
        elif baseline < 1:
            verdict, why = "unmeasured", "the ledger showed no matching refusal before it was adopted"
        elif repeats:
            verdict, why = "not helping yet", "%d matching refusal(s) since it was adopted (baseline %d in %s days)" % (
                repeats, baseline, meta.get("baseline-days", "?"))
        elif sessions < need:
            verdict, why = "measuring", "%d of %d sessions, no matching refusal so far" % (sessions, need)
        else:
            verdict, why = "qualifies", "no matching refusal in %d sessions (baseline %d in %s days)" % (
                sessions, baseline, meta.get("baseline-days", "?"))
        claimed = meta.get("status", "experimental")
        if claimed == "stable" and "qualified" not in meta:
            why += "; its file says stable, but it was not promoted by this tool"
        out.append(OrderedDict([("name", name), ("claimed", claimed), ("verdict", verdict), ("why", why),
                                ("sessions", sessions), ("repeats", repeats), ("baseline", baseline)]))
    return out


def status_lines(root: str) -> List[str]:
    return ["skill %s (%s): %s: %s." % (v["name"], v["claimed"], v["verdict"], v["why"]) for v in verdicts(root)]


def promote(root: str, name: str, now: Optional[datetime.datetime] = None) -> List[str]:
    """Mark a skill stable when the ledger says it qualifies. Raises LoopError."""
    found = [v for v in verdicts(root) if v["name"] == name]
    if not found:
        raise LoopError("%s is not a skill this loop drafted" % name)
    verdict = found[0]
    if verdict["claimed"] == "stable" and "not promoted" not in verdict["why"]:
        raise LoopError("%s is already stable" % name)
    if verdict["verdict"] != "qualifies":
        raise LoopError("%s stays experimental: %s: %s" % (name, verdict["verdict"], verdict["why"]))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    evidence = "%d sessions without a matching refusal; baseline %d" % (verdict["sessions"], verdict["baseline"])
    done = []
    for folder in SKILL_DIRS:
        path = os.path.join(root, folder, name, "SKILL.md")
        if read_skill(path) is None:
            continue
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        text = re.sub(r'^  status: ".*"$', '  status: "stable"', text, count=1, flags=re.M)
        text = re.sub(r'^(  origin: .*)$', '\\1\n  qualified: %s\n  evidence: %s' % (_quote(now.strftime("%Y-%m-%d")), _quote(evidence)),
                      text, count=1, flags=re.M)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        done.append("promoted %s" % os.path.relpath(path, root).replace(os.sep, "/"))
    return done + ["skill %s is stable: %s" % (name, evidence)]


def main(argv: Optional[Sequence[str]] = None, stdout: Optional[TextIO] = None, stderr: Optional[TextIO] = None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = argparse.ArgumentParser(description="The learning loop: propose, draft, measure and promote project skills.")
    parser.add_argument("command", choices=("draft", "status", "promote"))
    parser.add_argument("name", nargs="?", help="the skill's name (draft and promote)")
    parser.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, then the current folder)")
    args = parser.parse_args(argv)
    root = os_config.project_root({}, args.root)
    try:
        if args.command == "status":
            lines = status_lines(root) or ["no skills drafted from proposals yet."]
        elif not args.name:
            parser.error("%s needs the skill's name" % args.command)
        else:
            lines = draft(root, args.name) if args.command == "draft" else promote(root, args.name)
    except LoopError as exc:
        stderr.write("skill_loop: %s\n" % exc)
        return 1
    stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
