"""Human-readable role summaries shared by generated docs."""

from typing import List, Sequence

from .roles import Role
from .text import code_list, markdown_cell


def ordered(roles: Sequence[Role]) -> List[Role]:
    """Main role first, then the others by id."""
    return sorted(roles, key=lambda role: (not role.main, role.id))


def boundary(role: Role) -> str:
    """One-line summary of a role's tool policy and scopes."""
    parts = []
    if role.allowed_tools is None:
        parts.append("all tools")
    else:
        parts.append("tools: " + code_list(role.allowed_tools))
    if role.disallowed_tools:
        parts.append("denied: " + code_list(role.disallowed_tools))
    if role.write_paths is not None:
        parts.append("writes only " + (code_list(role.write_paths) or "nothing"))
    if role.isolation == "worktree":
        parts.append("works in a git worktree")
    return "; ".join(parts)


def role_table(roles: Sequence[Role], boundary_header: str) -> str:
    """Markdown table of roles: id, when to delegate, boundary."""
    lines = [
        "| Role | Delegate when | %s |" % boundary_header,
        "|---|---|---|",
    ]
    for role in ordered(roles):
        name = "`%s`%s" % (role.id, " (main)" if role.main else "")
        lines.append(
            "| %s | %s | %s |"
            % (name, markdown_cell(role.description), markdown_cell(boundary(role)))
        )
    return "\n".join(lines)
