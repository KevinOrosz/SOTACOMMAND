"""Role source contract: load and validate ``roles/<role-id>/`` folders.

The contract is documented in adapters/ROLES.md. In short, each role is a
folder holding ``soul.md`` (frontmatter plus body) and, optionally,
``program.md``, ``prepare.md``, ``protocols.md``, ``rubric.md`` and ``checks.md``, which are
appended to the body in that fixed order. Nothing else may live in a role
folder, so a stray file stops the generator instead of being silently dropped.
"""

import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from . import frontmatter

SOUL_FILE = "soul.md"
OPTIONAL_FILES = ("program.md", "prepare.md", "protocols.md", "rubric.md", "checks.md")

REQUIRED_KEYS = ("id", "description", "tier")
OPTIONAL_KEYS = ("allowed-tools", "disallowed-tools", "isolation", "main", "write-paths")
TIERS = ("low", "mid", "high")
ISOLATION_VALUES = ("worktree", "none")

MAX_ID_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
# Built-in tools are PascalCase, optionally with a parenthesized specifier.
BUILTIN_TOOL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?:\(([^()]*)\))?$")
# MCP entries: mcp__*, mcp__<server>, mcp__<server>__* or mcp__<server>__<tool>.
MCP_TOOL_RE = re.compile(r"^mcp__(?:\*|[A-Za-z0-9_.-]+(?:__(?:\*|[A-Za-z0-9_.-]+))?)$")
DELEGATION_TOOL = "Agent"


class RoleError(ValueError):
    """A role source that breaks the contract."""


class Role(object):
    """One validated role, ready for the adapters."""

    __slots__ = (
        "id", "description", "tier", "allowed_tools", "disallowed_tools",
        "isolation", "main", "write_paths", "body", "files",
    )

    def __init__(
        self,
        id: str,
        description: str,
        tier: str,
        allowed_tools: Optional[Tuple[str, ...]],
        disallowed_tools: Tuple[str, ...],
        isolation: Optional[str],
        main: bool,
        write_paths: Optional[Tuple[str, ...]],
        body: str,
        files: Tuple[str, ...],
    ) -> None:
        self.id = id
        self.description = description
        self.tier = tier
        # None means the role inherits every tool the harness offers.
        self.allowed_tools = allowed_tools
        self.disallowed_tools = disallowed_tools
        # "worktree" or None.
        self.isolation = isolation
        self.main = main
        # None means no role-specific write scope.
        self.write_paths = write_paths
        self.body = body
        self.files = files

    def delegates_to(self) -> Tuple[str, ...]:
        """Role ids named inside an ``Agent(...)`` entry of the allowlist."""
        for tool in self.allowed_tools or ():
            name, spec = split_tool(tool)
            if name == DELEGATION_TOOL and spec is not None:
                return tuple(part.strip() for part in spec.split(",") if part.strip())
        return ()

    def __repr__(self) -> str:
        return "Role(%r)" % self.id


def split_tool(entry: str) -> Tuple[str, Optional[str]]:
    """Split ``Name(spec)`` into ``("Name", "spec")``; plain names give ``None``."""
    if entry.endswith(")") and "(" in entry:
        name, _, rest = entry.partition("(")
        return name, rest[:-1]
    return entry, None


def split_tool_list(value: str) -> List[str]:
    """Split a comma-separated tool string, keeping commas inside parentheses.

    ``"Agent(a, b), Read"`` becomes ``["Agent(a, b)", "Read"]``, the same way
    Claude Code reads a ``tools`` string.
    """
    items: List[str] = []
    depth = 0
    current: List[str] = []
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise RoleError("unbalanced ')' in tool list %r" % value)
        if char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if depth != 0:
        raise RoleError("unbalanced '(' in tool list %r" % value)
    items.append("".join(current).strip())
    if items == [""]:
        return []
    return items


def _tool_list(role_id: str, key: str, value: frontmatter.Value) -> Tuple[str, ...]:
    if isinstance(value, bool):
        raise RoleError("%s: %s must be a list of tool names" % (role_id, key))
    if isinstance(value, str):
        items = split_tool_list(value)
    elif isinstance(value, list):
        items = [item.strip() for item in value]
    else:
        items = []
    seen = set()
    for item in items:
        if not item:
            raise RoleError("%s: %s has an empty entry" % (role_id, key))
        if not (BUILTIN_TOOL_RE.match(item) or MCP_TOOL_RE.match(item)):
            hint = ""
            if item[:1].islower() and not item.startswith("mcp__"):
                hint = " (tool names are case-sensitive, for example Read, not read)"
            raise RoleError("%s: %s entry %r is not a tool name%s" % (role_id, key, item, hint))
        if item in seen:
            raise RoleError("%s: %s lists %r twice" % (role_id, key, item))
        seen.add(item)
    return tuple(items)


def _path_list(role_id: str, value: frontmatter.Value) -> Tuple[str, ...]:
    if isinstance(value, bool) or value is None:
        raise RoleError("%s: write-paths must be a list of path globs" % role_id)
    items = [value] if isinstance(value, str) else list(value)
    paths: List[str] = []
    for item in items:
        pattern = item.strip()
        if pattern.startswith("./"):
            pattern = pattern[2:]
        if not pattern:
            raise RoleError("%s: write-paths has an empty entry" % role_id)
        if "\\" in pattern:
            raise RoleError("%s: write-paths entry %r must use '/' separators" % (role_id, item))
        if pattern.startswith("/") or pattern.startswith("~") or re.match(r"^[A-Za-z]:", pattern):
            raise RoleError(
                "%s: write-paths entry %r must be relative to the project root" % (role_id, item)
            )
        if ".." in pattern.split("/"):
            raise RoleError("%s: write-paths entry %r must not contain '..'" % (role_id, item))
        if pattern in paths:
            raise RoleError("%s: write-paths lists %r twice" % (role_id, item))
        paths.append(pattern)
    return tuple(paths)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if text.startswith("﻿"):
        text = text[1:]
    return text


def load_role(role_dir: str) -> Role:
    """Load and validate one role folder.

    Extracted role sources get this frontmatter from the extraction manifest's
    ``roles`` section (tools/repo/extract/README.md).
    """
    folder = os.path.basename(os.path.normpath(role_dir))
    soul_path = os.path.join(role_dir, SOUL_FILE)
    if not os.path.isfile(soul_path):
        raise RoleError("%s: missing %s" % (folder, SOUL_FILE))

    allowed_names = (SOUL_FILE,) + OPTIONAL_FILES
    for entry in sorted(os.listdir(role_dir)):
        if entry.startswith("."):
            continue
        if entry not in allowed_names:
            raise RoleError(
                "%s: %s is not part of the role source contract (allowed files: %s)"
                % (folder, entry, ", ".join(allowed_names))
            )
        if not os.path.isfile(os.path.join(role_dir, entry)):
            raise RoleError("%s: %s must be a file" % (folder, entry))

    try:
        fields, soul_body = frontmatter.parse(_read_text(soul_path))
    except frontmatter.FrontmatterError as exc:
        raise RoleError("%s/%s: %s" % (folder, SOUL_FILE, exc))

    unknown = sorted(set(fields) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS))
    if unknown:
        raise RoleError(
            "%s: unknown frontmatter key(s) %s (allowed: %s)"
            % (folder, ", ".join(unknown), ", ".join(REQUIRED_KEYS + OPTIONAL_KEYS))
        )
    for key in REQUIRED_KEYS:
        if not isinstance(fields.get(key), str) or not fields[key].strip():
            raise RoleError("%s: %s is required and must be a string" % (folder, key))

    role_id = fields["id"].strip()
    if not ID_RE.match(role_id) or len(role_id) > MAX_ID_LENGTH:
        raise RoleError(
            "%s: id %r must be lowercase kebab-case, at most %d characters"
            % (folder, role_id, MAX_ID_LENGTH)
        )
    if role_id != folder:
        raise RoleError("%s: id %r must match the folder name" % (folder, role_id))

    description = " ".join(fields["description"].split())
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise RoleError(
            "%s: description is %d characters; the limit is %d"
            % (role_id, len(description), MAX_DESCRIPTION_LENGTH)
        )

    tier = fields["tier"].strip()
    if tier not in TIERS:
        raise RoleError(
            "%s: tier must be one of %s, never a model name (got %r)"
            % (role_id, "|".join(TIERS), tier)
        )

    allowed_tools: Optional[Tuple[str, ...]] = None
    if fields.get("allowed-tools") is not None:
        allowed_tools = _tool_list(role_id, "allowed-tools", fields["allowed-tools"])
        if not allowed_tools:
            raise RoleError(
                "%s: allowed-tools is empty; omit it to inherit tools, "
                "or list at least one tool" % role_id
            )
    disallowed_tools: Tuple[str, ...] = ()
    if fields.get("disallowed-tools") is not None:
        disallowed_tools = _tool_list(role_id, "disallowed-tools", fields["disallowed-tools"])
    overlap = sorted(set(allowed_tools or ()) & set(disallowed_tools))
    if overlap:
        raise RoleError(
            "%s: %s appear(s) in both allowed-tools and disallowed-tools"
            % (role_id, ", ".join(overlap))
        )
    agent_entries = [t for t in (allowed_tools or ()) if split_tool(t)[0] == DELEGATION_TOOL]
    if len(agent_entries) > 1:
        raise RoleError("%s: allowed-tools may hold at most one Agent entry" % role_id)
    for entry in agent_entries:
        _, spec = split_tool(entry)
        if spec is not None:
            targets = [part.strip() for part in spec.split(",")]
            if not all(targets):
                raise RoleError("%s: %s has an empty role id" % (role_id, entry))
            for target in targets:
                if not ID_RE.match(target):
                    raise RoleError(
                        "%s: %s must list role ids such as Agent(builder), not %r"
                        % (role_id, entry, target)
                    )

    isolation_value = fields.get("isolation")
    isolation: Optional[str] = None
    if isolation_value is not None:
        if not isinstance(isolation_value, str) or isolation_value not in ISOLATION_VALUES:
            raise RoleError(
                "%s: isolation must be 'worktree' or 'none' (got %r)" % (role_id, isolation_value)
            )
        isolation = None if isolation_value == "none" else isolation_value

    main_value = fields.get("main", False)
    if main_value is None:
        main_value = False
    if not isinstance(main_value, bool):
        raise RoleError("%s: main must be true or false" % role_id)

    write_paths: Optional[Tuple[str, ...]] = None
    if "write-paths" in fields:
        write_paths = _path_list(role_id, fields["write-paths"])

    parts = [soul_body.strip()]
    if not parts[0]:
        raise RoleError("%s: %s needs a body with the role's instructions" % (role_id, SOUL_FILE))
    included = [SOUL_FILE]
    for name in OPTIONAL_FILES:
        path = os.path.join(role_dir, name)
        if not os.path.isfile(path):
            continue
        text = _read_text(path)
        if text.lstrip().startswith("---"):
            raise RoleError("%s/%s: only soul.md carries frontmatter" % (role_id, name))
        if not text.strip():
            raise RoleError("%s/%s is empty; delete it or add content" % (role_id, name))
        parts.append(text.strip())
        included.append(name)
    body = "\n\n".join(parts) + "\n"

    return Role(
        id=role_id,
        description=description,
        tier=tier,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        isolation=isolation,
        main=main_value,
        write_paths=write_paths,
        body=body,
        files=tuple(included),
    )


def load_roles(roles_dir: str) -> List[Role]:
    """Load every role under ``roles_dir`` and validate the set as a whole."""
    if not os.path.isdir(roles_dir):
        raise RoleError("roles directory not found: %s" % roles_dir)
    roles: List[Role] = []
    for entry in sorted(os.listdir(roles_dir)):
        path = os.path.join(roles_dir, entry)
        if entry.startswith(".") or not os.path.isdir(path):
            continue
        roles.append(load_role(path))
    validate_set(roles)
    return roles


def validate_set(roles: Sequence[Role]) -> None:
    """Checks that need every role: one main role, known delegation targets,
    and every delegate reachable from the main role.

    This is the build-time half of folder, registry and router parity; the
    engine's session_open.py checks the installed copy at boot (BOOT_INVALID).
    """
    if not roles:
        raise RoleError("no roles found (expected roles/<role-id>/soul.md folders)")
    ids = [role.id for role in roles]
    main_roles = [role.id for role in roles if role.main]
    if len(main_roles) != 1:
        raise RoleError(
            "exactly one role must set main: true (found %d: %s)"
            % (len(main_roles), ", ".join(main_roles) or "none")
        )
    main = by_id(roles)[main_roles[0]]
    if main.isolation is not None:
        raise RoleError("%s: the main role runs the session and cannot set isolation" % main.id)
    known = set(ids)
    for role in roles:
        for target in role.delegates_to():
            if target not in known:
                raise RoleError("%s: Agent(...) names unknown role %r" % (role.id, target))
            if target == role.id:
                raise RoleError("%s: Agent(...) cannot name the role itself" % role.id)
    routed = set(main.delegates_to())
    if routed:
        unreachable = [role.id for role in roles if not role.main and role.id not in routed]
        if unreachable:
            raise RoleError(
                "%s: Agent(...) must name every delegate role, or the router can't reach it (missing: %s)"
                % (main.id, ", ".join(sorted(unreachable)))
            )


def by_id(roles: Sequence[Role]) -> Dict[str, Role]:
    return {role.id: role for role in roles}


def main_role(roles: Sequence[Role]) -> Role:
    for role in roles:
        if role.main:
            return role
    raise RoleError("no main role")
