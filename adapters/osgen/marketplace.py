"""The root ``.claude-plugin/marketplace.json``.

Built from the plugin directories present: ``engine/`` (generated in the same
run), and every ``packs/<name>/`` and ``labs/<name>/`` that holds a
``.claude-plugin/plugin.json``. Directories without a manifest are skipped
and reported, so packs that are still being written appear on the next run
once their manifest exists.

``defaultEnabled`` is written on every entry. The default marketplace enables
proven work only:

* ``engine``: always ``true``.
* ``packs/<name>``: ``true`` only when the pack has at least one
  ``skills/<skill>/SKILL.md`` and every one of them declares
  ``metadata.status: stable``. A skill whose frontmatter cannot be read
  counts as not stable.
* ``labs/<name>``: always ``false``.

Format, from the Claude Code marketplace reference: ``name``, ``owner`` and
``plugins`` are required; ``description`` avoids a validation warning;
relative sources start with ``./``. Entries leave ``version`` and the display
fields to each plugin's own ``plugin.json``: it takes precedence for
``version``, and Claude Code reads it for relative-path entries.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import ENGINE_DIR, MARKETPLACE_DESCRIPTION, MARKETPLACE_NAME, OWNER_NAME
from .text import json_text

MARKETPLACE_PATH = ".claude-plugin/marketplace.json"
# (directory, category, stable rule applies). Labs never start enabled.
PLUGIN_GROUPS = (("packs", "skills", True), ("labs", "labs", False))
STABLE = "stable"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RESERVED_WORDS = ("claude", "anthropic")

Plugin = Tuple[str, Dict[str, object], str, bool]


class MarketplaceError(ValueError):
    pass


def check_name(name: str, where: str) -> None:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise MarketplaceError(
            "%s: plugin name %r must use letters, digits, '.', '_' and '-' and start with a "
            "letter or digit" % (where, name)
        )
    lowered = name.lower()
    for word in RESERVED_WORDS:
        if word in lowered:
            raise MarketplaceError("%s: plugin name %r must not contain %r" % (where, name, word))


def read_manifest(plugin_dir: str) -> Optional[Dict[str, object]]:
    path = os.path.join(plugin_dir, ".claude-plugin", "plugin.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise MarketplaceError("%s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise MarketplaceError("%s must hold a JSON object" % path)
    return data


#: tools/repo/skills, whose parser skills.py lint uses on every SKILL.md.
SKILLS_TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools", "repo", "skills")


def _skillslib():
    """The skills toolchain's model module, so lint and the marketplace read a skill the same way."""
    if SKILLS_TOOL_DIR not in sys.path:
        sys.path.insert(0, SKILLS_TOOL_DIR)
    from skillslib import model  # noqa: E402

    return model


def skill_status(skill_file: str) -> Optional[str]:
    """``metadata.status`` of one SKILL.md, or None when it cannot be read.

    Reads with the same YAML-subset parser as ``skills.py lint``, so a skill
    lint accepts as stable is stable here too.
    """
    model = _skillslib()
    parsed = model.parse_markdown(Path(skill_file))
    if parsed.frontmatter is None:
        return None
    metadata = parsed.frontmatter.items.get("metadata")
    status = model.scalar_string(metadata if isinstance(metadata, model.Map) else None, "status")
    return status.strip() if status is not None else None


def skill_statuses(plugin_dir: str) -> List[Tuple[str, Optional[str]]]:
    """``(skill folder, status)`` for each ``skills/<skill>/SKILL.md``."""
    root = os.path.join(plugin_dir, "skills")
    if not os.path.isdir(root):
        return []
    found = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, "SKILL.md")
        if not name.startswith(".") and os.path.isfile(path):
            found.append((name, skill_status(path)))
    return found


def stable_pack(plugin_dir: str) -> Tuple[bool, str]:
    """Whether every skill in the pack is stable, and why not when it is not."""
    statuses = skill_statuses(plugin_dir)
    if not statuses:
        return False, "no skills/<skill>/SKILL.md found"
    unstable = [
        "%s=%s" % (name, status or "no readable status") for name, status in statuses if status != STABLE
    ]
    if unstable:
        return False, "not every skill is stable: " + ", ".join(unstable)
    return True, ""


def discover(repo_root: str) -> Tuple[List[Plugin], List[str], List[str]]:
    """Find pack and lab plugins.

    Returns ``(plugins, skipped directories, notes)``. Each plugin is
    ``(relative dir, manifest, category, enabled by default)``; each note says
    why a pack starts disabled.
    """
    found: List[Plugin] = []
    skipped: List[str] = []
    notes: List[str] = []
    for group, category, stable_rule in PLUGIN_GROUPS:
        base = os.path.join(repo_root, group)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if name.startswith(".") or not os.path.isdir(path):
                continue
            relative = "%s/%s" % (group, name)
            manifest = read_manifest(path)
            if manifest is None:
                skipped.append(relative)
                continue
            enabled = False
            if stable_rule:
                enabled, reason = stable_pack(path)
                if not enabled:
                    notes.append("%s starts disabled (%s)" % (relative, reason))
            found.append((relative, manifest, category, enabled))
    return found, skipped, notes


def entry(relative: str, manifest: Dict[str, object], category: str, enabled: bool) -> Dict[str, object]:
    """One plugin entry. Display fields stay in the plugin's own plugin.json,
    which Claude Code reads for relative-path entries, so editing a pack's
    description never makes this file stale."""
    name = manifest.get("name")
    check_name(name, relative)  # type: ignore[arg-type]
    return {"name": name, "source": "./" + relative, "category": category, "defaultEnabled": enabled}


def render(engine_manifest: Dict[str, object], repo_root: str) -> Tuple[str, List[str], List[str]]:
    """Return (marketplace.json text, skipped plugin directories, notes)."""
    plugins = [entry(ENGINE_DIR, engine_manifest, "engine", True)]
    found, skipped, notes = discover(repo_root)
    for relative, manifest, category, enabled in found:
        plugins.append(entry(relative, manifest, category, enabled))
    names: Dict[str, str] = {}
    for item in plugins:
        name = str(item["name"])
        if name in names:
            raise MarketplaceError(
                "duplicate plugin name %r in %s and %s" % (name, names[name], item["source"])
            )
        names[name] = str(item["source"])
    data = {
        "name": MARKETPLACE_NAME,
        "owner": {"name": OWNER_NAME},
        "description": MARKETPLACE_DESCRIPTION,
        "plugins": plugins,
    }
    return json_text(data), skipped, notes


def add_marketplace(outputs, engine_manifest: Dict[str, object], repo_root: str) -> Tuple[List[str], List[str]]:
    """Add the marketplace file. Returns (skipped plugin directories, notes)."""
    text, skipped, notes = render(engine_manifest, repo_root)
    outputs.add(MARKETPLACE_PATH, text)
    return skipped, notes
