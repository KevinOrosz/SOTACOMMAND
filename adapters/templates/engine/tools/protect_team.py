#!/usr/bin/env python3
"""PreToolUse hook: no work goes to a helper the owner turned off.

Source: adapters/templates/engine/tools/protect_team.py, copied into
engine/tools/ by adapters/generate.py. Edit the template, then regenerate.

The WIZARD interview asks the owner which helpers they want and saves the
answer in the project's ``profile/answers.json`` (``"helpers": [...]``).
``profile/`` is a protected path, so agents can read the choice but not change
it. Claude Code runs this before every Agent call (the tool that hands work to
a subagent) and sends the documented PreToolUse JSON on stdin. The hook denies
the call when its ``subagent_type`` is one of this plugin's roles and the
owner left that role off.

* The main role is never off: it is the session itself.
* Agents that are not this plugin's roles (another plugin's, or a built-in
  one) are not this hook's business and pass.
* No ``profile/answers.json`` means the interview hasn't been done: every
  helper is on.
* An answers file that can't be read denies the plugin's roles until it is
  fixed, so a broken file never switches a helper back on by accident.

Output follows the hooks reference: silence allows; a ``permissionDecision``
of ``deny`` with a reason otherwise.
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import os_config  # noqa: E402  (sibling module, found via the line above)
import ledger  # noqa: E402

ANSWERS = os.path.join("profile", "answers.json")
POLICY = "role_policy.json"
AGENT_TOOLS = ("Agent", "Task")


class TeamError(Exception):
    """The owner's team choice can't be read."""


def load_registry(policy_path: Optional[str] = None) -> Tuple[str, List[str], str]:
    """``(plugin name, role ids, main role)`` from role_policy.json. Raises TeamError."""
    path = policy_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), POLICY)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise TeamError("%s is unreadable (%s)" % (POLICY, exc))
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise TeamError("%s has no roles list; regenerate the engine" % POLICY)
    return str(data.get("plugin", "")), roles, str(data.get("main_role", ""))


def helpers_on(root: str) -> Optional[List[str]]:
    """The helpers the owner chose, or None when there is no answers file. Raises TeamError."""
    path = os.path.join(root, ANSWERS)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise TeamError("profile/answers.json is unreadable (%s)" % exc)
    helpers = data.get("helpers") if isinstance(data, dict) else None
    if not isinstance(helpers, list) or not all(isinstance(helper, str) for helper in helpers):
        raise TeamError("profile/answers.json has no helpers list")
    return helpers


def role_named(agent_type: Any, plugin: str, roles: Sequence[str]) -> Optional[str]:
    """The plugin role an agent type names, or None when it is someone else's agent."""
    if not isinstance(agent_type, str) or not agent_type:
        return None
    prefix, _, name = agent_type.rpartition(":")
    if prefix and prefix != plugin:
        return None
    return name if name in roles else None


def team_status(root: str, policy_path: Optional[str] = None) -> Optional[Tuple[List[str], List[str]]]:
    """``(on, off)`` delegate roles, or None before the interview. Raises TeamError."""
    _, roles, main = load_registry(policy_path)
    chosen = helpers_on(root)
    if chosen is None:
        return None
    delegates = [role for role in roles if role != main]
    return [r for r in delegates if r in chosen], [r for r in delegates if r not in chosen]


def deny(reason: str, stdout: TextIO) -> None:
    os_config.emit_json(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                "permissionDecisionReason": reason}},
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
    parser = argparse.ArgumentParser(description="PreToolUse hook that keeps work away from helpers the owner turned off.")
    parser.add_argument("--root", help="project root (default: CLAUDE_PROJECT_DIR, then the input cwd)")
    parser.add_argument("--policy", help="role policy file (default: role_policy.json beside this script)")
    args = parser.parse_args(argv)

    try:
        hook_input = os_config.read_hook_input(stdin)
    except os_config.InputError as exc:
        stderr.write("protect_team: %s; blocking the call.\n" % exc)
        return 2
    if hook_input.get("tool_name") not in AGENT_TOOLS:
        return 0
    tool_input = hook_input.get("tool_input", {})
    agent_type = tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
    try:
        plugin, roles, main_role = load_registry(args.policy)
        role = role_named(agent_type, plugin, roles)
        if role is None or role == main_role:
            return 0
        project = os_config.project_root(hook_input, args.root)
        chosen = helpers_on(project)
    except TeamError as exc:
        deny("protect_team: %s; work can't be handed to the team's helpers until it is fixed." % exc, stdout)
        return 0
    if chosen is None or role in chosen:
        return 0
    deny(
        "protect_team: the owner turned the %s helper off (profile/answers.json; TEAM.md lists it under "
        "\"Helpers that are off\"). Don't route around it with another helper. Tell the owner this job needs "
        "the %s helper, and that redoing the interview with --redo turns it on." % (role, role),
        stdout,
    )
    ledger.record(project, "denied", hook_input, guard="team", detail="work for the %s helper, which is off" % role)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail closed
        sys.stderr.write("protect_team: internal error (%s: %s); blocking the call.\n" % (type(exc).__name__, exc))
        sys.exit(2)
