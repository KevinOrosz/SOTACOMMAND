"""Codex adapter: install pack skills and an AGENTS.md into a project.

Codex reads ``AGENTS.md`` and skills under ``.agents/skills/``. The install:

* copies every skill folder ``packs/<pack>/skills/<skill>/`` (and
  ``labs/...`` with ``include_labs``) to ``<target>/.agents/skills/<skill>/``
  as real files, never symlinks, so it works on Windows. Each skill's
  ``evals/`` folder is left out: it is test material, not runtime content;
* writes ``<target>/AGENTS.md`` only when the file does not exist;
* records the skill folders it installed in
  ``.agents/skills/.sotacommand-install.json``, with a SHA-256 of every file.
  A later install replaces a folder, or removes one whose skill is gone, only
  while every file in it still matches those hashes. A folder the owner
  edited is kept and reported; ``wizard.py update`` offers the new version
  beside it. A folder it did not install is never touched unless ``force``
  is set.
"""

import hashlib
import json
import os
import shutil
import tempfile
from typing import Dict, List, Sequence, Tuple

from . import PLUGIN_NAME
from .docs import role_table
from .outputs import copy_tree
from .roles import Role, main_role
from .text import read_template, render

SKILLS_DIR = os.path.join(".agents", "skills")
STATE_FILE = ".sotacommand-install.json"
SKIPPED_SKILL_DIRS = ("evals",)


class CodexError(ValueError):
    pass


def find_skills(repo_root: str, include_labs: bool) -> List[Tuple[str, str]]:
    """Return sorted ``(skill name, source dir)`` pairs from packs (and labs)."""
    groups = ("packs", "labs") if include_labs else ("packs",)
    skills: Dict[str, str] = {}
    for group in groups:
        base = os.path.join(repo_root, group)
        if not os.path.isdir(base):
            continue
        for plugin in sorted(os.listdir(base)):
            skills_root = os.path.join(base, plugin, "skills")
            if plugin.startswith(".") or not os.path.isdir(skills_root):
                continue
            for name in sorted(os.listdir(skills_root)):
                source = os.path.join(skills_root, name)
                if name.startswith(".") or not os.path.isfile(os.path.join(source, "SKILL.md")):
                    continue
                if name in skills:
                    raise CodexError(
                        "skill %r exists in both %s and %s; skill names must be unique"
                        % (name, skills[name], source)
                    )
                skills[name] = source
    return sorted(skills.items())


def render_agents_md(roles: Sequence[Role], skill_names: Sequence[str]) -> str:
    if skill_names:
        skills = "\n".join("- `%s`" % name for name in skill_names)
    else:
        skills = "- (none installed yet)"
    return render(
        read_template("codex", "AGENTS.md"),
        {
            "MAIN_ROLE": main_role(roles).id,
            "ROLE_TABLE": role_table(roles, "Boundary"),
            "SKILL_LIST": skills,
            "PLUGIN_NAME": PLUGIN_NAME,
        },
    )


def _read_state(path: str) -> Tuple[List[str], Dict[str, str]]:
    """``(managed skill names, {"<skill>/<file>": sha256})``; the hashes are empty for an older state file."""
    if not os.path.isfile(path):
        return [], {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CodexError("%s is unreadable: %s" % (path, exc))
    managed = data.get("managed", []) if isinstance(data, dict) else []
    hashes = data.get("hashes", {}) if isinstance(data, dict) else {}
    hashes = {k: v for k, v in hashes.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(hashes, dict) else {}
    return [name for name in managed if isinstance(name, str)], hashes


def tree_hashes(folder: str, prefix: str = "") -> Dict[str, str]:
    """``{"<prefix>/<relative path>": sha256}`` for every file under ``folder``; symlinks count as changed."""
    found = {}  # type: Dict[str, str]
    for current, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(current, name)
            relative = os.path.relpath(path, folder).replace(os.sep, "/")
            key = "%s/%s" % (prefix, relative) if prefix else relative
            if os.path.islink(path):
                found[key] = "symlink"
                continue
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(65536), b""):
                    digest.update(block)
            found[key] = digest.hexdigest()
    return found


def _unchanged(destination: str, name: str, recorded: Dict[str, str], source: str = "") -> bool:
    """True when the installed folder is exactly what this tool put there (or exactly the new source)."""
    if os.path.islink(destination) or not os.path.isdir(destination):
        return False
    current = tree_hashes(destination, name)
    mine = {k: v for k, v in recorded.items() if k.startswith(name + "/")}
    if mine and set(current) <= set(mine) and all(current[k] == mine[k] for k in current):
        return True  # every file is as installed (a file the owner deleted is simply restored)
    if source:
        fresh = {k: v for k, v in tree_hashes(source, name).items() if not k.split("/")[1:2] or k.split("/")[1] not in SKIPPED_SKILL_DIRS}
        return current == fresh
    return False


def install(
    repo_root: str,
    roles: Sequence[Role],
    target: str,
    include_labs: bool = False,
    force: bool = False,
) -> Tuple[List[str], List[str]]:
    """Install into ``target``. Returns (report lines, skipped skill names)."""
    if not os.path.isdir(target):
        raise CodexError("target directory does not exist: %s" % target)
    report: List[str] = []
    skipped: List[str] = []
    skills = find_skills(repo_root, include_labs)
    skills_dir = os.path.join(target, SKILLS_DIR)
    os.makedirs(skills_dir, exist_ok=True)
    state_path = os.path.join(skills_dir, STATE_FILE)
    previous_list, recorded = _read_state(state_path)
    previous = set(previous_list)
    installed: List[str] = []
    kept: List[str] = []
    hashes: Dict[str, str] = {}

    for name, source in skills:
        destination = os.path.join(skills_dir, name)
        if os.path.lexists(destination) and name not in previous and not force:
            report.append(
                "skipped %s: %s exists and was not installed by this tool (use --force to replace it)"
                % (name, os.path.join(SKILLS_DIR, name))
            )
            skipped.append(name)
            continue
        if os.path.lexists(destination) and name in previous and not force and not _unchanged(destination, name, recorded, source):
            report.append(
                "kept your changed skill %s: %s differs from what was installed. Run wizard.py update to get the new "
                "version beside it, or --force to replace it" % (name, os.path.join(SKILLS_DIR, name))
            )
            kept.append(name)
            hashes.update({k: v for k, v in recorded.items() if k.startswith(name + "/")})
            continue
        staging = tempfile.mkdtemp(prefix=".%s-" % name, dir=skills_dir)
        try:
            copy = os.path.join(staging, name)
            copy_tree(source, copy, skip_dirs=SKIPPED_SKILL_DIRS)
            if os.path.islink(destination) or os.path.isfile(destination):
                os.remove(destination)
            elif os.path.isdir(destination):
                shutil.rmtree(destination)
            os.replace(copy, destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        installed.append(name)
        hashes.update(tree_hashes(destination, name))
        report.append("installed skill %s" % name)

    # Skills this tool installed earlier whose source is gone.
    for name in sorted(previous - {skill for skill, _ in skills}):
        stale = os.path.join(skills_dir, name)
        if not os.path.isdir(stale) or os.path.islink(stale):
            continue
        if force or _unchanged(stale, name, recorded):
            shutil.rmtree(stale)
            report.append("removed skill %s (no longer in any pack)" % name)
        else:
            report.append("kept your changed skill %s: it is no longer in any pack, and you edited it" % name)
            kept.append(name)
            hashes.update({k: v for k, v in recorded.items() if k.startswith(name + "/")})

    with open(state_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            {"installer": "adapters/generate.py --harness codex", "managed": sorted(installed + kept),
             "hashes": dict(sorted(hashes.items()))},
            handle,
            indent=2,
        )
        handle.write("\n")

    agents_md = os.path.join(target, "AGENTS.md")
    if os.path.exists(agents_md):
        report.append("kept existing AGENTS.md")
    else:
        with open(agents_md, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_agents_md(roles, installed))
        report.append("wrote AGENTS.md")
    return report, skipped
