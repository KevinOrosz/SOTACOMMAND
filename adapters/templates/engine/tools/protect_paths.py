#!/usr/bin/env python3
"""PreToolUse hook: deny file writes to protected paths.

Source: adapters/templates/engine/tools/protect_paths.py, copied into
engine/tools/ by adapters/generate.py. Edit the template, then regenerate.

Claude Code runs this before every Write or Edit call (hooks/hooks.json) and
sends the documented PreToolUse JSON on stdin. Two rules apply:

1. ``protected_paths`` in the project's ``os.config.json``: path globs that no
   agent may write.
2. Role write scopes: ``write_paths`` in ``role_policy.json`` (generated from
   each role's ``write-paths``), plus any ``role_write_paths`` additions in
   ``os.config.json``. A role that has a scope may write only inside it. The
   role comes from the hook input's ``agent_type``, with any plugin prefix
   removed (``sotacommand-engine:owner-auditor`` is ``owner-auditor``).
3. With a ``second_brain`` block in ``os.config.json``, nothing under that
   folder's ``KB/`` may be written directly. Agents propose through
   ``bin/kb write put``, and only the owner lands proposals.

Glob rules: patterns are relative to the project root and anchored there.
``*`` and ``?`` stay inside one path segment, ``**`` spans any number of
segments, and a pattern also covers everything below a matching directory, so
``memory``, ``memory/`` and ``memory/**`` protect the same files. Protected
paths match case-insensitively (to hold on case-insensitive filesystems); write
scopes match case-sensitively. Paths are checked both as written and with
symlinks resolved, and relative to the nearest git checkout as well, so the
rules also hold inside ``.claude/worktrees/<name>/``.

Output follows the hooks reference: to allow, exit 0 with no output, which
leaves the normal permission flow in charge; to deny, exit 0 and print
``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
"permissionDecision": "deny", "permissionDecisionReason": ...}}``. When the
call cannot be evaluated (input that is not JSON, an unreadable
os.config.json or role policy), the hook denies or exits 2, so it fails closed.

This hook guards the file-editing tools. ``protect_shell.py`` applies the same
rules to Bash and PowerShell commands, as a guard and not a sandbox, so roles
that must not write elsewhere also go without Bash in their tool policy.
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
import ledger  # noqa: E402

POLICY_NAME = "role_policy.json"
PATH_KEYS = ("file_path", "notebook_path")
READ_ONLY_TOOLS = ("Read", "Glob", "Grep", "LS", "NotebookRead")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")

Pattern = Tuple[str, "re.Pattern[str]"]


class PolicyError(Exception):
    """A rule source (os.config.json or role_policy.json) cannot be used."""


# --- Globs -----------------------------------------------------------------

def glob_to_regex(pattern: str) -> str:
    """Translate a project-relative glob into a regular expression body."""
    text = pattern.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    if text.endswith("/"):
        text += "**"
    segments = [segment for segment in text.split("/") if segment]
    if not segments:
        raise ValueError("empty path pattern %r" % pattern)
    out: List[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
        else:
            out.append(_segment_regex(segment) + ("" if last else "/"))
    return "".join(out)


def _segment_regex(segment: str) -> str:
    out: List[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            while index + 1 < len(segment) and segment[index + 1] == "*":
                index += 1
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            close = segment.find("]", index + 2)
            if close == -1:
                out.append(re.escape(char))
            else:
                body = segment[index + 1:close].replace("\\", "\\\\")
                if body.startswith("!") or body.startswith("^"):
                    out.append("[^" + body[1:] + "/]")
                else:
                    out.append("[" + body + "]")
                index = close
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


def compile_patterns(patterns: Sequence[str], ignore_case: bool) -> List[Pattern]:
    flags = re.IGNORECASE if ignore_case else 0
    return [(pattern, re.compile(glob_to_regex(pattern), flags)) for pattern in patterns]


def match(rel_path: str, patterns: Sequence[Pattern]) -> Optional[str]:
    """Return the first pattern that covers ``rel_path`` or one of its parents."""
    if not rel_path:
        return None
    parts = rel_path.split("/")
    candidates = [rel_path] + ["/".join(parts[:end]) for end in range(1, len(parts))]
    for pattern, regex in patterns:
        for candidate in candidates:
            if regex.fullmatch(candidate):
                return pattern
    return None


# --- Paths -----------------------------------------------------------------

def normalize(path: str) -> str:
    if os.name == "nt" or _DRIVE.match(path):
        path = path.replace("\\", "/")
    return os.path.normpath(path)


def is_absolute(path: str) -> bool:
    return os.path.isabs(path) or bool(_DRIVE.match(path))


def relative(path: str, root: str) -> Optional[str]:
    """``path`` relative to ``root`` as a POSIX path, or None when outside it."""
    try:
        common = os.path.commonpath([os.path.normcase(path), os.path.normcase(root)])
    except ValueError:
        return None
    if not common or common != os.path.normcase(root):
        return None
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return None
    if rel == ".":
        return ""
    return rel.replace(os.sep, "/").replace("\\", "/")


def checkout_root(path: str) -> Optional[str]:
    """Nearest ancestor holding ``.git`` (a repository or a worktree)."""
    current = os.path.dirname(path)
    while current:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def views(target: str, project: str) -> List[Tuple[str, List[str]]]:
    """The target as written and with symlinks resolved, each with its roots."""
    lexical = normalize(target)
    lexical_roots = [normalize(project)]
    found = checkout_root(lexical)
    if found:
        lexical_roots.append(found)
    resolved = os.path.realpath(lexical)
    resolved_roots = [os.path.realpath(root) for root in lexical_roots]
    found = checkout_root(resolved)
    if found:
        resolved_roots.append(found)
    return [(lexical, _unique(lexical_roots)), (resolved, _unique(resolved_roots))]


def _unique(items: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


# --- Rules -----------------------------------------------------------------

def role_of(agent_type: Any) -> Optional[str]:
    """``plugin:role`` and ``role`` both name ``role``.

    GAP-6 (ROADMAP.md): the main session's scope relies on Claude Code sending
    ``agent_type`` for it; confirm in a live session.
    """
    if not isinstance(agent_type, str) or not agent_type.strip():
        return None
    return agent_type.strip().rsplit(":", 1)[-1]


def _string_list(value: Any, where: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise PolicyError("%s must be a list of non-empty path globs" % where)
    return list(value)


def load_policy(path: Optional[str] = None) -> Dict[str, List[str]]:
    """Role write scopes generated from the role sources."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), POLICY_NAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PolicyError("%s cannot be read: %s" % (POLICY_NAME, exc))
    scopes = data.get("write_paths", {}) if isinstance(data, dict) else None
    if not isinstance(scopes, dict):
        raise PolicyError("%s must hold a write_paths object" % POLICY_NAME)
    return {
        str(role): _string_list(globs, "%s write_paths.%s" % (POLICY_NAME, role))
        for role, globs in scopes.items()
        if not str(role).startswith("$")
    }


def rules_from_config(config: Optional[Dict[str, Any]]) -> Tuple[List[str], Dict[str, List[str]]]:
    """``protected_paths`` and ``role_write_paths`` from os.config.json."""
    if not config:
        return [], {}
    raw_protected = config.get("protected_paths")
    protected = [] if raw_protected is None else _string_list(
        raw_protected, "os.config.json protected_paths"
    )
    extra = config.get("role_write_paths")
    if extra is None:
        extra = {}
    if not isinstance(extra, dict):
        raise PolicyError("os.config.json role_write_paths must map role ids to path globs")
    additions = {
        str(role): _string_list(globs, "os.config.json role_write_paths.%s" % role)
        for role, globs in extra.items()
        if not str(role).startswith("$")
    }
    return protected, additions


def evaluate(
    target: str,
    project: str,
    role: Optional[str],
    protected: Sequence[str],
    scopes: Dict[str, List[str]],
) -> Optional[str]:
    """Return a deny reason, or None when the write may go ahead."""
    try:
        protected_patterns = compile_patterns(protected, ignore_case=True)
        scope_globs = scopes.get(role, []) if role else []
        scoped = bool(role) and role in scopes
        scope_patterns = compile_patterns(scope_globs, ignore_case=False)
    except ValueError as exc:
        return "protect_paths: a path rule is invalid (%s); file writes are blocked until it is fixed." % exc

    for path, roots in views(target, project):
        for root in roots:
            rel = relative(path, root)
            if rel is None:
                continue
            hit = match(rel, protected_patterns)
            if hit:
                return (
                    "protect_paths: %s is protected by %r in os.config.json (protected_paths). "
                    "Protected files are changed by the owner, not by agents." % (rel, hit)
                )
    if not scoped:
        return None
    allowed = ", ".join(scope_globs) if scope_globs else "nothing"
    for path, roots in views(target, project):
        containing = [root for root in roots if relative(path, root) is not None]
        if not containing:
            return (
                "protect_paths: the %s role may write only %s inside the project; "
                "%s is outside it." % (role, allowed, path)
            )
        rel = relative(path, max(containing, key=len))
        if rel is None or not match(rel, scope_patterns):
            return (
                "protect_paths: the %s role may write only %s (role policy); "
                "%s is outside that scope." % (role, allowed, rel or path)
            )
    return None


def deny(reason: str, stdout: TextIO) -> None:
    os_config.emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        stdout,
    )


def main(
    argv: Optional[Sequence[str]] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = argparse.ArgumentParser(description="PreToolUse hook that denies writes to protected paths.")
    parser.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, then the input cwd)")
    parser.add_argument("--policy", help="role policy file (default: role_policy.json beside this script)")
    args = parser.parse_args(argv)

    try:
        hook_input = os_config.read_hook_input(stdin)
    except os_config.InputError as exc:
        stderr.write("protect_paths: %s; blocking the call.\n" % exc)
        return 2
    if hook_input.get("tool_name") in READ_ONLY_TOOLS:
        return 0
    tool_input = hook_input.get("tool_input", {})
    if not isinstance(tool_input, dict):
        stderr.write("protect_paths: tool_input is not an object; blocking the call.\n")
        return 2
    target = next(
        (tool_input[key] for key in PATH_KEYS if isinstance(tool_input.get(key), str) and tool_input[key]),
        None,
    )
    if target is None:
        return 0

    project = os_config.project_root(hook_input, args.root)
    if not is_absolute(target):
        # Claude Code always sends absolute paths; other callers may not.
        base = hook_input.get("cwd") if isinstance(hook_input.get("cwd"), str) else project
        target = os.path.join(base, target)

    try:
        config, _ = os_config.load_config(project)
        protected, additions = rules_from_config(config)
        scopes = load_policy(args.policy)
        brain = os_config.second_brain(config, project)
    except (os_config.ConfigError, PolicyError) as exc:
        deny("protect_paths: %s; file writes are blocked until it is fixed." % exc, stdout)
        return 0
    for role, globs in additions.items():
        scopes[role] = list(scopes.get(role, [])) + [g for g in globs if g not in scopes.get(role, [])]

    if brain is not None:
        kb = os.path.join(brain["root"], "KB")
        for path, _ in views(target, project):
            for root in _unique([kb, os.path.realpath(kb)]):
                if relative(path, root) is not None:
                    deny(
                        "protect_paths: %s is inside the second brain's KB/. Agents propose changes with "
                        "bin/kb write put, and only the owner lands them." % path,
                        stdout,
                    )
                    ledger.record(project, "denied", hook_input, guard="write", detail="a direct write into the second brain's KB/")
                    return 0
    reason = evaluate(target, project, role_of(hook_input.get("agent_type")), protected, scopes)
    if reason:
        deny(reason, stdout)
        ledger.record(project, "denied", hook_input, guard="write", detail=reason)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail closed: a crash must not let the write through
        sys.stderr.write("protect_paths: internal error (%s: %s); blocking the call.\n"
                         % (type(exc).__name__, exc))
        sys.exit(2)
