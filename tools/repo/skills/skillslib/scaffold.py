"""``skills.py new <pack> <skill>``: scaffold a skill and its first eval case.

The skill folder follows the portable frontmatter contract, and the eval case
uses the exact ``claude plugin eval`` layout::

    <pack>/skills/<skill>/SKILL.md
    <pack>/evals/<skill>/<skill>-basic/prompt.md
    <pack>/evals/<skill>/<skill>-basic/case.yaml
    <pack>/evals/<skill>/<skill>-basic/place-files.sh
    <pack>/evals/<skill>/<skill>-basic/graders/10-skill-fired.md
    <pack>/evals/<skill>/<skill>-basic/graders/20-result.md

Placeholders start with ``TODO`` so ``lint`` warns until they are replaced.
The new skill starts as ``experimental``; only a qualifying eval run makes it
``stable``.
"""

import os
import stat
from pathlib import Path
from typing import List, Sequence

from .lint import NAME_RE, RESERVED_WORDS, TIERS, MAX_NAME
from .model import MANIFEST, discover_plugin_dirs

SKILL_TEMPLATE = """\
---
name: {skill}
description: TODO say what this skill does and when to use it, in one or two sentences with the words a user would type.
license: Apache-2.0
metadata:
  version: "0.1.0"
  status: experimental
  pack: {pack}
  min-tier: {min_tier}
  roles: {roles}
  gate: none
---

# {title}

TODO write the instructions Claude follows when this skill runs: the input it reads, the exact output it
writes, and the rules that settle edge cases. Keep this file under 500 lines and move long reference
material to `references/`.
"""

PROMPT_TEMPLATE = """\
---
tags: [{skill}]
max_turns: 10
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit]
---

TODO write the request a user would type that this skill should handle, without naming the skill.
"""

CASE_TEMPLATE = """\
schema_version: "1.1"
name: {case}
expected_outcome: TODO describe what a correct run produces.
context:
  scaffold_script: place-files.sh
"""

PLACE_FILES_SCRIPT = """\
#!/usr/bin/env bash
# Seeds the eval workspace with this case's input files.
#
# claude plugin eval runs this script only under --scaffold, as
# `bash <case dir>/place-files.sh`, with the empty run workspace as the
# working directory. It copies everything in the case's files/ directory into
# that workspace. A case without files/ gets an empty workspace.
set -euo pipefail

case_dir="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$case_dir/files" ]; then
  cp -R "$case_dir/files/." .
fi
"""

SKILL_FIRED_GRADER = """\
---
# Indicator: did Claude invoke the skill? Two-arm runs report it without scoring it.
type: tool_used
tool: Skill
input_match: '"skill"\\s*:\\s*"(?:[\\w-]+:)?{skill}"'
arm: with-only
---
"""

RESULT_GRADER = """\
---
type: llm
---

TODO PASS if the result contains the concrete thing a correct run produces.
FAIL if that is missing, wrong, or written somewhere else.
"""


class ScaffoldError(Exception):
    """The skill can't be scaffolded (bad name, missing pack, or it already exists)."""


def _title(skill: str) -> str:
    return " ".join(part.capitalize() for part in skill.split("-"))


def _write(path: Path, content: str, created: List[Path], executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    if executable:
        mode = os.stat(str(path)).st_mode
        os.chmod(str(path), mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    created.append(path)


def scaffold(
    repo_root: Path,
    pack: str,
    skill: str,
    labs: bool = False,
    roles: str = "builder",
    min_tier: str = "mid",
    plugin_roots: Sequence[str] = ("packs", "labs"),
) -> List[Path]:
    """Create the skill and one eval case. Returns the files written, in order."""
    if len(skill) > MAX_NAME or not NAME_RE.fullmatch(skill):
        raise ScaffoldError(
            "skill name {!r} must be lowercase letters, digits and single hyphens, at most {} characters".format(skill, MAX_NAME)
        )
    if any(word in skill for word in RESERVED_WORDS):
        raise ScaffoldError("skill name {!r} must not contain 'claude' or 'anthropic'".format(skill))
    if min_tier not in TIERS:
        raise ScaffoldError("min-tier {!r} must be one of {}".format(min_tier, ", ".join(TIERS)))
    role_ids = [r.strip() for r in roles.split(",")]
    if not role_ids or any(not NAME_RE.fullmatch(r) for r in role_ids):
        raise ScaffoldError("roles {!r} must be comma-separated role ids such as 'builder, orchestrator'".format(roles))

    pack_dir = repo_root / ("labs" if labs else "packs") / pack
    if not (pack_dir / MANIFEST).is_file():
        raise ScaffoldError(
            "{} is not a plugin; create its .claude-plugin/plugin.json and README.md first (see packs/example)".format(pack_dir)
        )
    for root_name in plugin_roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for plugin_dir in discover_plugin_dirs(root):
            if (plugin_dir / "skills" / skill).exists():
                raise ScaffoldError("skill {!r} already exists at {}".format(skill, plugin_dir / "skills" / skill))
    case = "{}-basic".format(skill)
    case_dir = pack_dir / "evals" / skill / case
    if case_dir.exists():
        raise ScaffoldError("eval case {} already exists".format(case_dir))

    values = {
        "skill": skill,
        "pack": pack,
        "case": case,
        "roles": ", ".join(role_ids),
        "min_tier": min_tier,
        "title": _title(skill),
    }
    created = []  # type: List[Path]
    _write(pack_dir / "skills" / skill / "SKILL.md", SKILL_TEMPLATE.format(**values), created)
    _write(case_dir / "prompt.md", PROMPT_TEMPLATE.format(**values), created)
    _write(case_dir / "case.yaml", CASE_TEMPLATE.format(**values), created)
    _write(case_dir / "place-files.sh", PLACE_FILES_SCRIPT, created, executable=True)
    _write(case_dir / "graders" / "10-skill-fired.md", SKILL_FIRED_GRADER.format(**values), created)
    _write(case_dir / "graders" / "20-result.md", RESULT_GRADER, created)
    return created
