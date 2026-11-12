"""Adapters for harnesses that build on the harness-neutral AGENTS.md install.

* ``gemini``: ``GEMINI.md``, which imports ``AGENTS.md`` (Gemini CLI's
  ``@file`` import), so Gemini CLI follows the same instructions.
* ``cursor``: ``.cursor/rules/sotacommand.mdc``, an always-applied project
  rule that points to ``AGENTS.md`` (Cursor also reads ``AGENTS.md`` itself).
* ``flat``: ``SOTACOMMAND.prompt.md``, one self-contained file with the
  roles, the boot and close steps and every installed skill inlined, for chat
  assistants and wrappers that can't read project files.

Each file is written only when absent. Standard library only.
"""

import os
from typing import List, Sequence, Tuple

from .codex import find_skills, render_agents_md
from .roles import Role
from .text import read_template

GEMINI_FILE = "GEMINI.md"
CURSOR_RULE = ".cursor/rules/sotacommand.mdc"
FLAT_FILE = "SOTACOMMAND.prompt.md"


def _strip_frontmatter(text: str) -> Tuple[str, str]:
    """``(description, body)`` of a SKILL.md, without its frontmatter block."""
    description = ""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                if line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip('"')
            text = text[end + 5:]
    return description, text.strip() + "\n"


def demote_headings(text: str, levels: int) -> str:
    """Add ``levels`` to every Markdown heading outside fenced code."""
    out = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            line = "#" * levels + line
        out.append(line)
    return "\n".join(out) + "\n"


def render_flat(repo_root: str, roles: Sequence[Role], include_labs: bool = False) -> str:
    skills = find_skills(repo_root, include_labs)
    agents = render_agents_md(roles, [name for name, _ in skills])
    parts = [read_template("harnesses", "flat-header.md"), demote_headings(agents, 1).replace("## AGENTS.md\n", "## Project instructions\n", 1)]
    for name, source in skills:
        with open(os.path.join(source, "SKILL.md"), "r", encoding="utf-8") as handle:
            description, body = _strip_frontmatter(handle.read())
        parts.append("## Skill: %s\n\nUse when: %s\n\n%s" % (name, description or "(no description)", demote_headings(body, 2)))
    return "\n".join(part.rstrip("\n") + "\n" for part in parts)


def _write_if_absent(target: str, relative: str, text: str, dry_run: bool) -> bool:
    path = os.path.join(target, *relative.split("/"))
    if os.path.exists(path):
        return False
    if not dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    return True


def install(
    harness: str, repo_root: str, roles: Sequence[Role], target: str, include_labs: bool = False, dry_run: bool = False
) -> Tuple[List[str], List[str]]:
    """Write one harness's file. Returns (report lines, written relative paths)."""
    if harness == "gemini":
        relative, text = GEMINI_FILE, read_template("harnesses", "GEMINI.md")
    elif harness == "cursor":
        relative, text = CURSOR_RULE, read_template("harnesses", "sotacommand.mdc")
    elif harness == "flat":
        relative, text = FLAT_FILE, render_flat(repo_root, roles, include_labs)
    else:
        raise ValueError("unknown harness %r" % harness)
    if _write_if_absent(target, relative, text, dry_run):
        return ["%s %s" % ("would write" if dry_run else "wrote", relative)], ([] if dry_run else [relative])
    return ["kept existing %s" % relative], []
