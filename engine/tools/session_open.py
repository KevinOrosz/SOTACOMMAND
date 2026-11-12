#!/usr/bin/env python3
"""SessionStart hook: report memory-file sizes against their caps.

Source: adapters/templates/engine/tools/session_open.py, copied into
engine/tools/ by adapters/generate.py. Edit the template, then regenerate.

Reads ``memory_caps`` from the project's ``os.config.json``::

    "memory_caps": [
      {"path": "memory/HANDOFF.md", "max_bytes": 900, "max_lines": 15}
    ]

Each entry names a project-relative file and at least one of ``max_bytes`` and
``max_lines``. Every file is reported as OK, OVER, MISSING or INVALID. The
report states facts; it never tells the agent what to do.

Hook mode (the default) prints one JSON object, as the hooks reference
describes for SessionStart: ``hookSpecificOutput.additionalContext`` carries
the report into Claude's context, and ``systemMessage`` tells the user when a
file is over its cap or missing. It always exits 0, because SessionStart
cannot block a session.

``--text`` prints the report as plain lines for other harnesses and scripts,
and exits 1 when any file is over its cap, missing or invalid.

Before the caps it checks boot parity in the installed plugin: the role
folder (``agents/*.md``), the registry (``roles`` in ``tools/role_policy.json``)
and the router (the main agent's ``Agent(...)`` list, ``routes``) must agree.
Any drift is reported as ``BOOT_INVALID``, and the boot skill stops instead
of guessing which copy is right. A plugin without ``tools/role_policy.json``
(another harness, or an older install) skips the check and says so.

In hook mode it keeps a copy of the handoff when it changed since the last
session (``recall.py``), so earlier sessions stay searchable. It records the session in the local ledger (``ledger.py``),
refreshes ``.sotacommand/ledger-summary.md`` and adds one line of counts for
the last seven days.

It also states which helpers the owner switched on and off in the interview
(``profile/answers.json``); ``protect_team.py`` refuses work for the ones that
are off.

With a ``second_brain`` block in ``os.config.json`` it adds one line about the
connected SOTASECONDBRAIN folder: its wiki page count and how many proposals
wait in ``KB/inbox`` for the owner. It reads files only and runs nothing there.
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple

# The plugin directory is not a place for state, so never write bytecode there.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import os_config  # noqa: E402  (sibling module, found via the line above)
import protect_team  # noqa: E402
import ledger  # noqa: E402
import skill_loop  # noqa: E402
import recall  # noqa: E402

TEMPLATE = os.path.join("templates", "os.config.json")
POLICY = os.path.join("tools", "role_policy.json")
_NAME_RE = re.compile(r"^name:\s*(\S+)\s*$", re.M)
_TOOLS_RE = re.compile(r"^tools:\s*(.+?)\s*$", re.M)
_AGENT_RE = re.compile(r"Agent\(([^)]*)\)")


def _frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    return text[4:end] if end != -1 else ""


def version_note(root: str) -> List[str]:
    """One line when the project was set up with a different version than this plugin."""
    try:
        with open(os.path.join(os_config.plugin_root(), ".claude-plugin", "plugin.json"), "r", encoding="utf-8") as handle:
            plugin = json.load(handle).get("version")
        with open(os.path.join(root, ".sotacommand", "install.json"), "r", encoding="utf-8") as handle:
            project = json.load(handle).get("version")
    except (OSError, ValueError, AttributeError):
        return []
    if not isinstance(plugin, str) or not isinstance(project, str) or plugin == project:
        return []
    return ["session-open: this project was set up with version %s and the engine is %s; the owner can run "
            "`wizard.py update` from their SOTACOMMAND folder to bring its files and settings up to date." % (project, plugin)]


def boot_parity(plugin_dir: str) -> Tuple[Optional[List[str]], List[str]]:
    """Folder, registry and router parity of an installed engine.

    Returns ``(problems, notes)``; ``problems`` is None when there is no
    registry to check against.
    """
    policy_path = os.path.join(plugin_dir, POLICY)
    if not os.path.isfile(policy_path):
        return None, ["session-open: no %s in the plugin; boot parity was not checked." % POLICY.replace(os.sep, "/")]
    problems: List[str] = []
    try:
        with open(policy_path, "r", encoding="utf-8") as handle:
            policy = json.load(handle)
    except (OSError, ValueError) as exc:
        return ["%s is unreadable (%s)" % (POLICY.replace(os.sep, "/"), exc)], []
    registry = policy.get("roles") if isinstance(policy, dict) else None
    main = policy.get("main_role") if isinstance(policy, dict) else None
    routes = policy.get("routes", []) if isinstance(policy, dict) else []
    if not isinstance(registry, list) or not all(isinstance(r, str) for r in registry) or not isinstance(main, str):
        return ["%s has no roles list or main_role; regenerate the engine" % POLICY.replace(os.sep, "/")], []
    agents_dir = os.path.join(plugin_dir, "agents")
    folder: Dict[str, str] = {}
    if os.path.isdir(agents_dir):
        for entry in sorted(os.listdir(agents_dir)):
            if not entry.endswith(".md"):
                continue
            try:
                with open(os.path.join(agents_dir, entry), "r", encoding="utf-8") as handle:
                    head = _frontmatter(handle.read())
            except (OSError, UnicodeDecodeError):
                problems.append("agents/%s is unreadable" % entry)
                continue
            match = _NAME_RE.search(head)
            name = match.group(1).strip('"') if match else None
            if name != entry[:-3]:
                problems.append("agents/%s names agent %r, not %r" % (entry, name, entry[:-3]))
            folder[entry[:-3]] = head
    for role in sorted(set(registry) - set(folder)):
        problems.append("role %s is registered but agents/%s.md is missing" % (role, role))
    for role in sorted(set(folder) - set(registry)):
        problems.append("agents/%s.md is not in the registry" % role)
    if main not in registry:
        problems.append("main role %s is not in the registry" % main)
    elif main in folder:
        tools = _TOOLS_RE.search(folder[main])
        found = _AGENT_RE.search(tools.group(1)) if tools else None
        named = [part.strip().split(":")[-1] for part in found.group(1).split(",") if part.strip()] if found else []
        if sorted(named) != sorted(routes):
            problems.append(
                "the router in agents/%s.md names %s, but the registry routes to %s"
                % (main, ", ".join(sorted(named)) or "no roles", ", ".join(sorted(routes)) or "no roles")
            )
    for role in routes:
        if role not in registry:
            problems.append("route to %s, which is not a registered role" % role)
    return problems, []


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def measure(root: str, index: int, entry: Any) -> Dict[str, Any]:
    """Check one memory_caps entry. Returns a result dict with a ``status``."""
    result: Dict[str, Any] = {"index": index, "path": None, "status": "INVALID", "detail": ""}
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"].strip():
        result["detail"] = "entry %d needs a 'path' string" % index
        return result
    rel = entry["path"].strip().replace("\\", "/")
    result["path"] = rel
    max_bytes, max_lines = entry.get("max_bytes"), entry.get("max_lines")
    if max_bytes is None and max_lines is None:
        result["detail"] = "needs max_bytes or max_lines"
        return result
    for name, value in (("max_bytes", max_bytes), ("max_lines", max_lines)):
        if value is not None and not _positive_int(value):
            result["detail"] = "%s must be a positive integer" % name
            return result
    if rel.startswith("/") or ".." in rel.split("/"):
        result["detail"] = "path must stay inside the project"
        return result
    full = os.path.join(root, *rel.split("/"))
    result.update(max_bytes=max_bytes, max_lines=max_lines)
    if not os.path.exists(full):
        result["status"] = "MISSING"
        return result
    if not os.path.isfile(full):
        result["detail"] = "not a file"
        return result
    with open(full, "rb") as handle:
        content = handle.read()
    size = len(content)
    lines = len(content.decode("utf-8", errors="replace").splitlines())
    result.update(bytes=size, lines=lines)
    over = (max_bytes is not None and size > max_bytes) or (max_lines is not None and lines > max_lines)
    result["status"] = "OVER" if over else "OK"
    return result


def describe(result: Dict[str, Any]) -> str:
    status, path = result["status"], result["path"] or "entry %d" % result["index"]
    if status == "INVALID":
        return "INVALID %s: %s" % (path, result["detail"])
    caps = []
    if result.get("max_bytes") is not None:
        caps.append("cap %d bytes" % result["max_bytes"])
    if result.get("max_lines") is not None:
        caps.append("cap %d lines" % result["max_lines"])
    if status == "MISSING":
        return "MISSING %s (%s)" % (path, ", ".join(caps))
    return "%s %s: %d bytes, %d lines (%s)" % (status, path, result["bytes"], result["lines"], ", ".join(caps))


def build_report(hook_input: Dict[str, Any], root: str) -> Tuple[List[str], bool, Optional[str]]:
    """Return (lines, problems_found, message_for_user)."""
    lines: List[str] = []
    agent = hook_input.get("agent_type")
    source = hook_input.get("source")
    if isinstance(source, str) and source:
        lines.append(
            "session-open: session source %s; main agent %s."
            % (source, agent if isinstance(agent, str) and agent else "not set")
        )
    lines.extend(version_note(root))
    parity, notes = boot_parity(os_config.plugin_root())
    lines.extend(notes)
    if parity:
        lines.append("session-open: BOOT_INVALID: the role folder, registry and router disagree (%d problem(s)):" % len(parity))
        lines.extend("BOOT_INVALID %s" % problem for problem in parity)
        lines.append("session-open: memory caps were not checked; regenerate the engine (python3 adapters/generate.py) or reinstall it.")
        return lines, True, "session-open: BOOT_INVALID: the engine's roles are out of sync; regenerate or reinstall it."
    if parity is not None:
        lines.append("session-open: boot parity OK (role folder, registry and router agree).")
    try:
        team = protect_team.team_status(root)
    except protect_team.TeamError as exc:
        lines.append("session-open: team: %s; no work can go to the helpers until it is fixed." % exc)
        team = None
    else:
        if team is None:
            lines.append("session-open: team: the owner hasn't done the interview (no profile/answers.json); every helper is on.")
        else:
            lines.append("session-open: team: on: %s; off: %s. Work for a helper that is off is refused." % (
                ", ".join(team[0]) or "none", ", ".join(team[1]) or "none"))
    try:
        config, config_path = os_config.load_config(root)
    except os_config.ConfigError as exc:
        lines.append("session-open: %s; memory caps were not checked." % exc)
        return lines, True, "session-open: os.config.json is invalid; memory caps were not checked."
    if config is None:
        template = os.path.join(os_config.plugin_root(), TEMPLATE)
        lines.append(
            "session-open: no os.config.json in %s; memory caps were not checked. "
            "A template is at %s." % (root, template)
        )
        return lines, False, None
    try:
        brain = os_config.second_brain(config, root)
    except os_config.ConfigError as exc:
        lines.append("session-open: %s; the second brain was not checked." % exc)
        return lines, True, "session-open: second_brain in os.config.json is invalid."
    brain_problem = False
    if brain is not None:
        line, brain_problem = describe_brain(brain)
        lines.append(line)
    lines_, problems, message = _caps_report(config, root)
    lines.extend(lines_)
    if brain_problem and not problems:
        message = "session-open: the second brain at %s was not found." % brain["path"]
    return lines, problems or brain_problem, message


def describe_brain(brain: Dict[str, Any]) -> Tuple[str, bool]:
    """One fact line about the connected SOTASECONDBRAIN instance. Reads files only."""
    root = brain["root"]
    if not (os.path.isfile(os.path.join(root, "bin", "kb")) and os.path.isdir(os.path.join(root, "KB"))):
        return ("session-open: second brain: MISSING at %s (no bin/kb and KB/ there)." % brain["path"], True)
    pages = 0
    for folder, dirs, files in os.walk(os.path.join(root, "KB", "wiki")):
        dirs[:] = [d for d in dirs if not d.startswith((".", "_"))]
        pages += sum(1 for name in files if name.endswith(".md") and not name.startswith("_"))
    proposals = 0
    inbox = os.path.join(root, "KB", "inbox")
    if os.path.isdir(inbox):
        for agent in os.listdir(inbox):
            folder = os.path.join(inbox, agent)
            if agent.startswith(("_", ".")) or not os.path.isdir(folder):
                continue
            proposals += sum(1 for name in os.listdir(folder) if name.endswith(".md"))
    return (
        "session-open: second brain: OK at %s: %d wiki page(s), %d proposal file(s) in KB/inbox for the owner to review%s."
        % (brain["path"], pages, proposals, "; lint runs at close" if brain["lint_on_close"] else ""),
        False,
    )


def _caps_report(config: Dict[str, Any], root: str) -> Tuple[List[str], bool, Optional[str]]:
    lines: List[str] = []
    caps = config.get("memory_caps")
    if caps is None or caps == []:
        lines.append("session-open: os.config.json sets no memory_caps; nothing to check.")
        return lines, False, None
    if not isinstance(caps, list):
        lines.append("session-open: memory_caps in os.config.json must be a list; nothing was checked.")
        return lines, True, "session-open: memory_caps in os.config.json is invalid."
    results = [measure(root, index, entry) for index, entry in enumerate(caps, 1)]
    counts = {status: sum(1 for r in results if r["status"] == status)
              for status in ("OK", "OVER", "MISSING", "INVALID")}
    lines.append(
        "session-open: memory caps: %d OK, %d OVER, %d MISSING, %d INVALID."
        % (counts["OK"], counts["OVER"], counts["MISSING"], counts["INVALID"])
    )
    lines.extend(describe(result) for result in results)
    flagged = [r["path"] or "entry %d" % r["index"] for r in results if r["status"] != "OK"]
    message = None
    if flagged:
        message = "session-open: %d memory file(s) need attention: %s." % (len(flagged), ", ".join(flagged))
    return lines, bool(flagged), message


def main(
    argv: Optional[Sequence[str]] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> int:
    stdout = stdout or sys.stdout
    parser = argparse.ArgumentParser(description="Report memory-file sizes against the caps in os.config.json.")
    parser.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, then the input cwd)")
    parser.add_argument("--text", action="store_true", help="print plain lines; exit 1 when a file needs attention")
    args = parser.parse_args(argv)

    hook_input: Dict[str, Any] = {}
    if not args.text:
        try:
            hook_input = os_config.read_hook_input(stdin)
        except os_config.InputError:
            hook_input = {}
    root = os_config.project_root(hook_input, args.root)
    lines, problems, message = build_report(hook_input, root)
    if not args.text:
        recall.archive_handoff(root)
        kept = len(recall.snapshots(root))
        if kept:
            lines.append("session-open: history: %d past handoff(s) kept in .sotacommand/history/; search them with Grep, or recall.py." % kept)
        ledger.record(root, "session_start", hook_input, source=hook_input.get("source"))
        if any("BOOT_INVALID" in line for line in lines):
            ledger.record(root, "boot_invalid", hook_input)
        for line in lines:
            if line.startswith("OVER "):
                ledger.record(root, "memory_over", hook_input, detail=line.split(":", 1)[0][5:])
    try:
        lines.extend("session-open: %s" % text for text in skill_loop.status_lines(root))
    except Exception:  # a broken skill file must not break the report
        pass
    summary = ledger.write_summary(root)
    if summary is not None and summary["events"]:
        lines.append("session-open: %s Full counts: .sotacommand/ledger-summary.md." % ledger.line(ledger.summarize(root, 7)))

    if args.text:
        stdout.write("\n".join(lines) + "\n")
        return 1 if problems else 0
    payload: Dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }
    if message:
        payload["systemMessage"] = message
    os_config.emit_json(payload, stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
