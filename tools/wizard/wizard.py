#!/usr/bin/env python3
"""WIZARD: set SOTACOMMAND up in a project, for any agent harness.

This is the deterministic half of the one-word ``WIZARD`` boot. An agent that
reads ``WIZARD.md`` runs these commands; a person can run them by hand.

Usage (from a clone of this repository)::

    python3 tools/wizard/wizard.py doctor
    python3 tools/wizard/wizard.py install --target PROJECT_DIR [--create] [--harness HARNESS ...] [--include-labs] [--dry-run]
    python3 tools/wizard/wizard.py check --target PROJECT_DIR
    python3 tools/wizard/wizard.py update --target PROJECT_DIR [--dry-run]
    python3 tools/wizard/wizard.py uninstall --target PROJECT_DIR [--dry-run]
    python3 tools/wizard/wizard.py questions [--json]
    python3 tools/wizard/wizard.py interview --target PROJECT_DIR [--answers FILE] [--redo] [--dry-run]

``interview`` asks who the owner is and which helpers they want, in plain
words: on the terminal, or from an agent's ``--answers`` JSON file. It writes
``profile/owner.md``, ``profile/answers.json`` and ``TEAM.md`` (the owner's
team card), and never replaces them without ``--redo``. ``questions`` prints
the interview for an agent to ask in chat.

``HARNESS`` is ``claude``, ``codex``, ``copilot``, ``cursor``, ``gemini``,
``hermes``, ``flat`` or ``generic`` (repeatable; default ``generic``). ``generic`` is the
harness-neutral install: ``AGENTS.md`` plus skills in ``.agents/skills/``,
which Codex, Cursor, Gemini CLI and any harness that reads ``AGENTS.md`` pick
up.

What ``install`` writes into ``PROJECT_DIR``, never overwriting a file that is
already there:

* ``os.config.json``, ``memory/HANDOFF.md`` and ``memory/lessons.md`` from the
  engine templates (every harness);
* ``generic`` and ``codex``: ``AGENTS.md`` (only when absent) and the pack
  skills in ``.agents/skills/``;
* ``copilot``: one ``.github/agents/<role>.agent.md`` per role;
* ``cursor``: the generic install plus ``.cursor/rules/sotacommand.mdc``;
* ``gemini``: the generic install plus ``GEMINI.md``, which imports
  ``AGENTS.md``;
* ``hermes``: the generic install. Hermes Agent reads ``AGENTS.md`` and, once
  the project is trusted, the skills in ``.agents/skills/``; ``install`` prints
  the one command that trusts it and warns about files that would hide
  ``AGENTS.md`` from Hermes;
* ``flat``: ``SOTACOMMAND.prompt.md``, one self-contained prompt with the
  roles, boot, close and every skill inlined, for any chat assistant or wrapper;
* ``claude``: nothing extra. The plugin installs through Claude Code itself;
  ``install`` prints the two commands to run.

``update`` brings an installed project up to this copy's version. A file the
wizard wrote and the owner hasn't changed is replaced; one the owner edited is
kept, with the new version written beside it as ``<name>.new``. ``TEAM.md`` is
rebuilt from the saved interview answers. ``os.config.json`` is only added to:
settings and protected paths the template has and the project lacks, after a
backup to ``os.config.json.bak``. ``profile/`` and ``memory/`` are never
touched.

Every file ``install`` writes is recorded, with its SHA-256, in
``.sotacommand/install.json``. ``uninstall`` removes only recorded files that
are unchanged since the install, so a memory file or config the owner has
edited is always kept, and nothing the owner wrote is ever removed.

Exit codes: 0 ok, 1 a check failed or something was skipped or kept, 2 usage error.

Standard library only, Python 3.9+. No network calls, no model calls, and
nothing is written outside ``PROJECT_DIR``.

Windows, macOS and Linux all run it: CI runs its tests on each.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from typing import Dict, List, Optional, Sequence, Tuple

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import interview as interview_mod  # noqa: E402  (sibling module, found via the line above)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
TEMPLATES = os.path.join(ENGINE_DIR, "templates")
ADAPTERS_DIR = os.path.join(REPO_ROOT, "adapters")
COPILOT_AGENTS = os.path.join(REPO_ROOT, ".github", "agents")

HARNESSES = ("claude", "codex", "copilot", "cursor", "gemini", "hermes", "flat", "generic")
#: Harnesses that read AGENTS.md and .agents/skills/, so they get the generic install too.
AGENTS_MD_HARNESSES = ("generic", "codex", "cursor", "gemini", "hermes")
#: Files Hermes Agent reads before AGENTS.md (its docs: first match wins), so they would hide the team.
HERMES_OVERRIDES = (".hermes.md", "HERMES.md", "AGENTS.override.md")
RECORD = ".sotacommand/install.json"
#: Files a tool rewrites on every install; uninstall removes them even when changed.
TOOL_OWNED = (".agents/skills/.sotacommand-install.json", RECORD)
PROJECT_FILES = (
    ("os.config.json", os.path.join(TEMPLATES, "os.config.json")),
    ("memory/HANDOFF.md", os.path.join(TEMPLATES, "memory", "HANDOFF.template.md")),
    ("memory/lessons.md", os.path.join(TEMPLATES, "memory", "lessons.template.md")),
)
REQUIRED_PYTHON = (3, 9)

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2


def current_version() -> str:
    """This copy's version: the engine plugin's, so there is one number to bump."""
    with open(os.path.join(ENGINE_DIR, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
        return str(json.load(handle).get("version", "0.0.0"))


def installed_version(target: str) -> Optional[str]:
    """The version stamped into the project's install record, or None."""
    try:
        with open(_native(target, RECORD), encoding="utf-8") as handle:
            value = json.load(handle).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _plugin_names() -> Tuple[str, str]:
    """(engine plugin name, marketplace name) from the committed files."""
    with open(os.path.join(ENGINE_DIR, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
        plugin = json.load(handle)["name"]
    with open(os.path.join(REPO_ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as handle:
        market = json.load(handle)["name"]
    return plugin, market


# --------------------------------------------------------------------------- doctor


def doctor() -> Tuple[List[str], bool]:
    """Facts about this machine. Returns (lines, ok)."""
    lines = []
    ok = sys.version_info[:2] >= REQUIRED_PYTHON
    lines.append(
        "%s python %d.%d (needs %d.%d or later)"
        % ("OK  " if ok else "FAIL", sys.version_info[0], sys.version_info[1], REQUIRED_PYTHON[0], REQUIRED_PYTHON[1])
    )
    if shutil.which("git"):
        lines.append("OK   git found")
    else:
        lines.append("INFO git not found (only needed to clone and update)")
    for name, harness in (("claude", "Claude Code"), ("codex", "Codex"), ("hermes", "Hermes Agent"), ("gh", "GitHub CLI (Copilot)")):
        found = shutil.which(name)
        lines.append("%s %s %s" % ("OK  " if found else "INFO", harness, "found" if found else "not found"))
    lines.append("INFO any other harness works through AGENTS.md (use --harness generic)")
    return lines, ok


# --------------------------------------------------------------------------- record


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _native(target: str, relative: str) -> str:
    return os.path.join(target, *relative.split("/"))


def read_record(target: str) -> dict:
    path = _native(target, RECORD)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError("%s is unreadable (%s); remove it to start a fresh record" % (RECORD, exc))
    files = data.get("files") if isinstance(data, dict) else None
    return dict(files) if isinstance(files, dict) else {}


def write_record(target: str, written: Sequence[str]) -> None:
    """Add the files this install wrote, with their current hashes."""
    files = read_record(target)
    for relative in written:
        path = _native(target, relative)
        if os.path.isfile(path):
            files[relative] = _sha256(path)
    _save_record(target, files)


def _save_record(target: str, files: dict) -> None:
    path = _native(target, RECORD)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"installer": "tools/wizard/wizard.py", "version": current_version(),
                   "files": dict(sorted(files.items()))}, handle, indent=2)
        handle.write("\n")


def _files_under(target: str, relative_dir: str) -> List[str]:
    found = []
    for folder, dirs, files in os.walk(_native(target, relative_dir)):
        dirs.sort()
        for name in sorted(files):
            found.append(os.path.relpath(os.path.join(folder, name), target).replace(os.sep, "/"))
    return found


# --------------------------------------------------------------------------- install


def _copy_if_absent(source: str, destination: str, dry_run: bool) -> bool:
    """Copy a file unless the destination exists. Returns True when written."""
    if os.path.exists(destination):
        return False
    if not dry_run:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(source, destination)
    return True


def _install_project_files(target: str, dry_run: bool, report: List[str], written: List[str]) -> None:
    for relative, source in PROJECT_FILES:
        destination = os.path.join(target, *relative.split("/"))
        if _copy_if_absent(source, destination, dry_run):
            report.append("wrote %s" % relative)
            written.append(relative)
        else:
            report.append("kept existing %s" % relative)


def _osgen():
    if ADAPTERS_DIR not in sys.path:
        sys.path.insert(0, ADAPTERS_DIR)
    from osgen import codex, harnesses, roles  # noqa: E402  (adapters/ is put on the path above)

    roles_dir = os.path.join(ENGINE_DIR, "roles")
    if not os.path.isdir(roles_dir):
        roles_dir = os.path.join(REPO_ROOT, "fixtures", "roles-sample")
    return codex, harnesses, roles.load_roles(roles_dir)


def _install_agents_md_and_skills(
    target: str, include_labs: bool, dry_run: bool, report: List[str], written: List[str]
) -> List[str]:
    """The harness-neutral install, through the Codex adapter. Returns skipped skills."""
    if dry_run:
        report.append("would write AGENTS.md (if absent) and pack skills into .agents/skills/")
        return []
    codex, _, roles = _osgen()
    lines, skipped = codex.install(REPO_ROOT, roles, target, include_labs=include_labs)
    report.extend(lines)
    if "wrote AGENTS.md" in lines:
        written.append("AGENTS.md")
    for line in lines:
        if line.startswith("installed skill "):
            written.extend(_files_under(target, ".agents/skills/" + line[len("installed skill "):]))
    written.append(TOOL_OWNED[0])
    return skipped


def _install_copilot(target: str, dry_run: bool, report: List[str], written: List[str]) -> None:
    for name in sorted(os.listdir(COPILOT_AGENTS)):
        if not name.endswith(".agent.md"):
            continue
        destination = os.path.join(target, ".github", "agents", name)
        if _copy_if_absent(os.path.join(COPILOT_AGENTS, name), destination, dry_run):
            report.append("wrote .github/agents/%s" % name)
            written.append(".github/agents/" + name)
        else:
            report.append("kept existing .github/agents/%s" % name)


def _hermes_notes(target: str) -> List[str]:
    """What a Hermes Agent user still has to do, and what would get in the way."""
    lines = [
        "Hermes Agent: it reads AGENTS.md in this folder on its own. To load the team's skills too, run once:",
        "  hermes skills trust \"%s\"" % target,
    ]
    if not interview_mod.is_git_project(target):
        lines.append("  Hermes finds project skills from the nearest git project, and this folder isn't one yet: run `git init` in it first.")
    hiding = [name for name in HERMES_OVERRIDES if os.path.isfile(_native(target, name))]
    if hiding:
        lines.append("  Heads up: %s is in this folder, and Hermes reads it INSTEAD of AGENTS.md. Your team won't load until it is "
                     "removed or says to follow AGENTS.md." % " and ".join(hiding))
    lines.append("  On Hermes the helpers' limits are rules the Lead follows; Hermes's own command approval and sandbox are the hard boundary.")
    return lines


def _claude_commands() -> List[str]:
    plugin, market = _plugin_names()
    return [
        "claude plugin marketplace add %s" % REPO_ROOT,
        "claude plugin install %s@%s" % (plugin, market),
    ]


def install(
    target: str, harnesses: Sequence[str], include_labs: bool = False, dry_run: bool = False
) -> Tuple[List[str], List[str]]:
    """Install SOTACOMMAND into ``target``. Returns (report lines, skipped items)."""
    if not os.path.isdir(target):
        raise ValueError("target folder does not exist: %s" % target)
    if os.path.realpath(target) == os.path.realpath(REPO_ROOT):
        raise ValueError("install into your own project folder, not into the SOTACOMMAND clone itself")
    report = []  # type: List[str]
    skipped = []  # type: List[str]
    written = []  # type: List[str]
    _install_project_files(target, dry_run, report, written)
    if any(h in harnesses for h in AGENTS_MD_HARNESSES):
        skipped += _install_agents_md_and_skills(target, include_labs, dry_run, report, written)
    if "copilot" in harnesses:
        _install_copilot(target, dry_run, report, written)
    for harness in ("cursor", "gemini", "flat"):
        if harness in harnesses:
            _, adapters, roles = _osgen()
            lines, files = adapters.install(harness, REPO_ROOT, roles, target, include_labs, dry_run)
            report.extend(lines)
            written.extend(files)
    if not dry_run:
        write_record(target, written)
    if "hermes" in harnesses:
        report.extend(_hermes_notes(target))
    if "claude" in harnesses:
        report.append("Claude Code: run these two commands, then start a new session in the project:")
        report.extend("  " + command for command in _claude_commands())
    return report, skipped


# --------------------------------------------------------------------------- update

SETTING_NOTES = {
    "shell_guard": "commands that are always blocked, and commands that need your OK each time",
    "ledger": "a local count of sessions, refusals and failed closes for the Time-Keeper",
    "close_gate": "a check that must pass before a session can finish",
    "memory_caps": "size limits for the team's daily notes",
    "role_write_paths": "extra folders a helper may write to",
    "protected_paths": "files no helper may change",
}


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def merge_config(current: "Dict[str, object]", template: "Dict[str, object]") -> List[str]:
    """Add to ``current`` what the template has and it lacks. Returns one line per addition."""
    added = []
    for key, value in template.items():
        if key.startswith("$") or key in current:
            continue
        current[key] = value
        added.append("added setting %s: %s" % (key, SETTING_NOTES.get(key, "new in this version")))
    paths, wanted = current.get("protected_paths"), template.get("protected_paths")
    if isinstance(paths, list) and isinstance(wanted, list):
        for path in wanted:
            if path not in paths:
                paths.append(path)
                added.append("added protected path %s" % path)
    return added


def _managed_files(target: str, files: dict) -> "Dict[str, str]":
    """The current text of every file the wizard manages in this project, by relative path."""
    codex, adapters, roles = _osgen()
    from osgen.text import read_template  # noqa: E402  (adapters/ is on the path after _osgen)

    fresh = {}  # type: Dict[str, str]
    skills = codex.find_skills(REPO_ROOT, include_labs=False)
    has_skills = any(rel.startswith(".agents/skills/") for rel in files)
    if "AGENTS.md" in files or has_skills:
        if "AGENTS.md" in files:
            fresh["AGENTS.md"] = codex.render_agents_md(roles, [name for name, _ in skills])
        for name, source in skills:
            for folder, dirs, names in os.walk(source):
                dirs[:] = sorted(d for d in dirs if not (folder == source and d in codex.SKIPPED_SKILL_DIRS) and not d.startswith("."))
                for filename in sorted(names):
                    text = _read(os.path.join(folder, filename))
                    if text is not None and not filename.startswith("."):
                        relative = os.path.relpath(os.path.join(folder, filename), source).replace(os.sep, "/")
                        fresh[".agents/skills/%s/%s" % (name, relative)] = text
    if adapters.GEMINI_FILE in files:
        fresh[adapters.GEMINI_FILE] = read_template("harnesses", "GEMINI.md")
    if adapters.CURSOR_RULE in files:
        fresh[adapters.CURSOR_RULE] = read_template("harnesses", "sotacommand.mdc")
    if adapters.FLAT_FILE in files:
        fresh[adapters.FLAT_FILE] = adapters.render_flat(REPO_ROOT, roles)
    if any(rel.startswith(".github/agents/") for rel in files):
        for name in sorted(os.listdir(COPILOT_AGENTS)):
            text = _read(os.path.join(COPILOT_AGENTS, name))
            if name.endswith(".agent.md") and text is not None:
                fresh[".github/agents/" + name] = text
    answers_text = _read(_native(target, "profile/answers.json"))
    if "TEAM.md" in files and answers_text is not None:
        try:
            script = interview_mod.load_script()
            answers = interview_mod.normalize(script, {k: v for k, v in json.loads(answers_text).items()})
            fresh["TEAM.md"] = interview_mod.render_team(script, answers, interview_mod.display_folder(target))
        except (ValueError, KeyError):
            pass  # answers from an older interview that no longer validate: leave the card alone
    return fresh


def _skill_hashes(record: "Dict[str, str]") -> "Dict[str, str]":
    """The skills installer's per-file hashes, from the wizard's own record."""
    prefix = ".agents/skills/"
    return dict(sorted((rel[len(prefix):], sha) for rel, sha in record.items()
                       if rel.startswith(prefix) and rel.count("/") >= 3 and not rel.endswith(".new")))


def update(target: str, dry_run: bool = False) -> Tuple[List[str], List[str]]:
    """Bring ``target`` up to this copy's version. Returns (report lines, files kept with a .new beside them)."""
    if not os.path.isdir(target):
        raise ValueError("target folder does not exist: %s" % target)
    if not os.path.isfile(_native(target, RECORD)):
        raise ValueError("no install record in %s; run install first" % target)
    files = read_record(target)
    before = installed_version(target)
    report = []  # type: List[str]
    kept = []  # type: List[str]
    writes = []  # type: List[Tuple[str, str]]
    record = dict(files)

    # The settings file first: if it is broken, stop before anything is written.
    config_path = _native(target, "os.config.json")
    config_text = _read(config_path)
    config_plan = None
    if config_text is not None:
        from collections import OrderedDict
        try:
            config = json.loads(config_text, object_pairs_hook=OrderedDict)
            if not isinstance(config, dict):
                raise ValueError("it must hold a JSON object")
        except ValueError as exc:
            raise ValueError("os.config.json can't be read (%s); fix it, then run update again" % exc)
        with open(os.path.join(TEMPLATES, "os.config.json"), encoding="utf-8") as handle:
            template = json.load(handle, object_pairs_hook=OrderedDict)
        added = merge_config(config, template)
        if added:
            config_plan = (json.dumps(config, indent=2, ensure_ascii=False) + "\n", config_text)
            backup = "os.config.json.bak"
            stamp = 1
            while os.path.lexists(_native(target, backup)):
                stamp += 1
                backup = "os.config.json.bak%d" % stamp  # never replace an earlier backup
            report.append("os.config.json: %d addition(s); your existing settings are unchanged%s" % (
                len(added), "" if dry_run else "; backup in %s" % backup))
            report += ["  " + line for line in added]
        else:
            report.append("os.config.json: nothing to add")

    for relative, text in sorted(_managed_files(target, files).items()):
        path = _native(target, relative)
        current = _read(path)
        new_hash = _text_hash(text)
        if current is None and os.path.lexists(path):
            current = ""  # present but not readable as text: never treat it as missing
        if current is None:
            if relative in files:
                report.append("left alone: %s (you removed it)" % relative)
            elif relative.startswith(".agents/skills/"):
                writes.append((relative, text))
                record[relative] = new_hash
                report.append("%s %s (new)" % ("would add" if dry_run else "added", relative))
            continue
        if _text_hash(current) == new_hash:
            record[relative] = new_hash
            continue
        if relative in files and _sha256(path) == files[relative]:
            writes.append((relative, text))
            record[relative] = new_hash
            report.append("%s %s" % ("would update" if dry_run else "updated", relative))
        else:
            beside = relative + ".new"
            number = 1
            while os.path.lexists(_native(target, beside)) and beside not in files and _read(_native(target, beside)) != text:
                number += 1
                beside = "%s.new%d" % (relative, number)  # a file of the owner's already has that name
            if _read(_native(target, beside)) != text:
                writes.append((beside, text))
            record[beside] = new_hash
            kept.append(relative)
            report.append("kept your %s (you changed it); the new version is beside it as %s" % (relative, beside))

    if not dry_run:
        if config_plan is not None:
            unchanged = files.get("os.config.json") == _sha256(config_path)
            _write(_native(target, backup), config_plan[1])
            _write(config_path, config_plan[0])
            if unchanged:
                record["os.config.json"] = _sha256(config_path)
        for relative, text in writes:
            _write(_native(target, relative), text)
        state_path = _native(target, TOOL_OWNED[0])
        if os.path.isfile(state_path):
            names = sorted({rel.split("/")[2] for rel in record if rel.startswith(".agents/skills/") and rel.count("/") >= 3})
            try:
                state = json.loads(_read(state_path) or "{}")
                listed, listed_hashes = sorted(state.get("managed", [])), state.get("hashes", {})
            except (ValueError, AttributeError):
                listed, listed_hashes = [], {}
            if names != listed or listed_hashes != _skill_hashes(record):
                _write(state_path, json.dumps({"installer": "tools/wizard/wizard.py update", "managed": names,
                                               "hashes": _skill_hashes(record)}, indent=2) + "\n")
                record[TOOL_OWNED[0]] = _sha256(state_path)
        if record != files or before != current_version() or config_plan is not None:
            _save_record(target, record)
    now = current_version()
    if len(report) == 1 and report[0].endswith("nothing to add") and before == now:
        report.append("everything is already up to date")
    report.append("version: %s -> %s" % (before or "not recorded", now))
    plugin, market = _plugin_names()
    report.append("Claude Code users: also run `claude plugin marketplace update %s` and `claude plugin update %s@%s`." % (market, plugin, market))
    return report, kept


# --------------------------------------------------------------------------- uninstall


def uninstall(target: str, dry_run: bool = False) -> Tuple[List[str], List[str]]:
    """Remove what ``install`` wrote and the owner hasn't changed. Returns (report, kept)."""
    if not os.path.isdir(target):
        raise ValueError("target folder does not exist: %s" % target)
    files = read_record(target)
    if not files and not os.path.isfile(_native(target, RECORD)):
        return ["nothing to uninstall: no %s in %s" % (RECORD, target)], []
    report = []  # type: List[str]
    kept = []  # type: List[str]
    removed_dirs = set()
    verb = "would remove" if dry_run else "removed"
    for relative in sorted(files) + [RECORD]:
        path = _native(target, relative)
        if not os.path.isfile(path):
            removed_dirs.add(os.path.dirname(path))  # already gone; its folder may now be empty
            continue
        if relative not in TOOL_OWNED and _sha256(path) != files.get(relative):
            report.append("kept %s (changed since install)" % relative)
            kept.append(relative)
            continue
        if not dry_run:
            os.remove(path)
        report.append("%s %s" % (verb, relative))
        removed_dirs.add(os.path.dirname(path))
    if not dry_run:
        for folder in sorted(removed_dirs, key=len, reverse=True):
            while os.path.realpath(folder) != os.path.realpath(target) and os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
                folder = os.path.dirname(folder)
    if kept and not dry_run:
        # The kept files stay on the record with their install hashes, so a
        # rerun after the owner reverts or deletes them finishes the job.
        remaining = {relative: files[relative] for relative in kept}
        skills = sorted({rel.split("/")[2] for rel in kept if rel.startswith(".agents/skills/") and rel.count("/") >= 3})
        if skills:
            # Keep the skills list too, so a later install recognizes these folders as its own.
            state_path = _native(target, TOOL_OWNED[0])
            _write(state_path, json.dumps({"installer": "tools/wizard/wizard.py uninstall", "managed": skills,
                                           "hashes": _skill_hashes(remaining)}, indent=2) + "\n")
            remaining[TOOL_OWNED[0]] = _sha256(state_path)
        _save_record(target, remaining)
    return report, kept


# --------------------------------------------------------------------------- check


def check(target: str) -> Tuple[List[str], bool]:
    """Is ``target`` set up? Returns (lines, ok)."""
    lines = []
    ok = True
    for relative, _ in PROJECT_FILES:
        present = os.path.isfile(os.path.join(target, *relative.split("/")))
        lines.append("%s %s" % ("OK     " if present else "MISSING", relative))
        ok = ok and present
    if os.path.isfile(_native(target, RECORD)):
        stamped, now = installed_version(target), current_version()
        if stamped == now:
            lines.append("OK      version %s" % now)
        else:
            lines.append("INFO    installed with version %s; this copy is %s. Run: python3 tools/wizard/wizard.py update --target \"%s\"" % (
                stamped or "(not recorded)", now, target))
    for relative, label in (("profile/owner.md", "who you are and your rules"), ("TEAM.md", "your team card")):
        present = os.path.isfile(_native(target, relative))
        lines.append("%s %s (%s%s)" % ("OK     " if present else "INFO   ", relative, label,
                                       "" if present else "; not written yet: run the interview"))
    answers_path = _native(target, "profile/answers.json")
    if os.path.isfile(answers_path):
        try:
            with open(answers_path, encoding="utf-8") as handle:
                helpers = json.load(handle).get("helpers", [])
        except (OSError, ValueError):
            helpers = []
        if "builder" in helpers:
            git = interview_mod.is_git_project(target)
            lines.append("%s git project (the Builder needs one%s)" % (
                "OK     " if git else "INFO   ", "" if git else "; run: git init, git add -A, git commit -m \"Start\""))
    hiding = [name for name in HERMES_OVERRIDES if os.path.isfile(_native(target, name))]
    if hiding:
        lines.append("INFO    %s present: Hermes Agent reads it instead of AGENTS.md, so the team won't load there" % " and ".join(hiding))
    agents_md = os.path.isfile(os.path.join(target, "AGENTS.md"))
    lines.append("%s AGENTS.md (harness-neutral entry point)" % ("OK     " if agents_md else "INFO   "))
    tools_dir = os.path.join(ENGINE_DIR, "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import session_open  # noqa: E402  (engine/tools is put on the path above)

    report, problems, _ = session_open.build_report({}, target)
    lines.extend(report)
    return lines, ok and not problems


# --------------------------------------------------------------------------- interview

GIT_HINT = (
    "wizard: note: the Builder works in its own copy of your project, which it makes with git. This folder isn't a\n"
    "wizard: git project yet. To make it one, run these in the folder: git init, then git add -A, then\n"
    "wizard: git commit -m \"Start\". Until then, ask the Builder for small jobs only, or leave it for later."
)



def run_interview(target: str, answers_file: Optional[str], redo: bool, dry_run: bool) -> int:
    if not os.path.isdir(target):
        sys.stderr.write("wizard: error: target folder does not exist: %s\n" % target)
        return EXIT_USAGE
    if os.path.realpath(target) == os.path.realpath(REPO_ROOT):
        sys.stderr.write("wizard: error: run the interview for your own project folder, not the SOTACOMMAND clone itself\n")
        return EXIT_USAGE
    script = interview_mod.load_script()
    try:
        existing = [rel for rel in interview_mod.OUTPUTS if os.path.exists(_native(target, rel))]
        if existing and not redo:
            raise interview_mod.InterviewError(
                "%s already exist(s). Your answers are safe. To replace them with new answers, add --redo" % ", ".join(existing))
        if answers_file:
            try:
                with open(os.path.expanduser(answers_file), encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError) as exc:
                sys.stderr.write("wizard: error: can't read the answers file: %s\n" % exc)
                return EXIT_USAGE
        else:
            try:
                raw = interview_mod.ask_in_terminal(script, input, print)
            except (EOFError, KeyboardInterrupt):
                sys.stderr.write("\nwizard: interview stopped; nothing was written\n")
                return EXIT_PROBLEMS
        answers = interview_mod.normalize(script, raw)
        lines, written = interview_mod.write_outputs(script, answers, target, redo, dry_run)
    except interview_mod.InterviewError as exc:
        sys.stderr.write("wizard: %s\n" % exc)
        return EXIT_PROBLEMS
    if written:
        write_record(target, written)
        # An agent's temporary answers file: profile/answers.json now holds the copy.
        scratch = _native(target, ".sotacommand/answers.json")
        if answers_file and os.path.isfile(scratch) and os.path.realpath(os.path.expanduser(answers_file)) == os.path.realpath(scratch):
            os.remove(scratch)
            lines.append("removed the temporary .sotacommand/answers.json (your answers are in profile/answers.json)")
    print("\n".join(lines))
    names = ", ".join(answers["helpers"]) or "none"
    print("wizard: %s for %s (%s). Helpers on: %s." % (
        "dry run, nothing written" if dry_run else "interview saved", answers["project"], answers["name"], names))
    if not dry_run:
        print("wizard: open TEAM.md in your project: it is your team card.")
        if "builder" in answers["helpers"] and not interview_mod.is_git_project(target):
            print(GIT_HINT)
        harness = interview_mod.HARNESS_FOR_TOOL[answers["tool"]]
        print("wizard: your tool uses --harness %s. If you haven't installed for it yet: "
              "python3 tools/wizard/wizard.py install --target \"%s\" --harness %s" % (harness, target, harness))
    return EXIT_OK


# --------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wizard.py",
        description="Set SOTACOMMAND up in a project, for any agent harness. The agent-facing procedure is WIZARD.md.",
        epilog="Exit codes: 0 ok, 1 a check failed or something was skipped, 2 usage error.",
    )
    sub = parser.add_subparsers(dest="command", metavar="{doctor,install,interview,questions,check,update,uninstall}")
    sub.required = True
    sub.add_parser("doctor", help="check this machine")
    inst = sub.add_parser("install", help="install SOTACOMMAND into a project folder")
    inst.add_argument("--target", required=True, help="your project folder")
    inst.add_argument("--harness", action="append", choices=HARNESSES,
                      help="claude, codex, copilot, cursor, gemini, hermes, flat or generic (repeatable; default generic)")
    inst.add_argument("--create", action="store_true", help="make the project folder if it doesn't exist yet")
    inst.add_argument("--include-labs", action="store_true", help="also install experimental skills from labs/")
    inst.add_argument("--dry-run", action="store_true", help="say what would be written, write nothing")
    qst = sub.add_parser("questions", help="print the interview questions, for an agent to ask in chat")
    qst.add_argument("--json", action="store_true", help="print the raw question script as JSON")
    itv = sub.add_parser("interview", help="answer a few plain questions; writes your profile and team card")
    itv.add_argument("--target", required=True, help="your project folder")
    itv.add_argument("--answers", help="a JSON file of answers (from an agent); without it, you are asked on the terminal")
    itv.add_argument("--redo", action="store_true", help="replace the profile and team card from an earlier interview")
    itv.add_argument("--dry-run", action="store_true", help="check the answers and say what would be written")
    chk = sub.add_parser("check", help="check that a project is set up")
    chk.add_argument("--target", required=True, help="your project folder")
    upd = sub.add_parser("update", help="bring an installed project up to this version, keeping every file you changed")
    upd.add_argument("--target", required=True, help="your project folder")
    upd.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    uni = sub.add_parser("uninstall", help="remove what install wrote, keeping every file you changed")
    uni.add_argument("--target", required=True, help="your project folder")
    uni.add_argument("--dry-run", action="store_true", help="say what would be removed, remove nothing")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        lines, ok = doctor()
        print("\n".join(lines))
        return EXIT_OK if ok else EXIT_PROBLEMS
    if args.command == "questions":
        script = interview_mod.load_script()
        print(json.dumps(script, indent=2, ensure_ascii=False) if args.json else interview_mod.render_questions(script), end="" if not args.json else "\n")
        return EXIT_OK
    target = os.path.abspath(os.path.expanduser(args.target))
    if args.command == "interview":
        return run_interview(target, args.answers, args.redo, args.dry_run)
    if args.command == "install":
        if args.create and not os.path.exists(target):
            if args.dry_run:
                import tempfile
                print("would create the folder %s" % target)
                preview = tempfile.mkdtemp(prefix="wizard-preview-")
                try:
                    lines, _ = install(preview, args.harness or ["generic"], args.include_labs, True)
                finally:
                    shutil.rmtree(preview, ignore_errors=True)
                print("\n".join(line.replace("wrote ", "would write ", 1) if line.startswith("wrote ") else line for line in lines))
                print("wizard: dry run, nothing written")
                return EXIT_OK
            os.makedirs(target)
            print("created the folder %s" % target)
        try:
            lines, skipped = install(target, args.harness or ["generic"], args.include_labs, args.dry_run)
        except ValueError as exc:
            sys.stderr.write("wizard: error: %s\n" % exc)
            return EXIT_USAGE
        print("\n".join(lines))
        print("wizard: %s" % ("dry run, nothing written" if args.dry_run else "install finished in %s" % target))
        return EXIT_PROBLEMS if skipped else EXIT_OK
    if args.command == "update":
        try:
            lines, kept = update(target, args.dry_run)
        except ValueError as exc:
            sys.stderr.write("wizard: error: %s\n" % exc)
            return EXIT_USAGE
        print("\n".join(lines))
        print("wizard: %s" % ("dry run, nothing changed" if args.dry_run else "update finished in %s%s" % (
            target, "; %d file(s) of yours kept, new versions beside them" % len(kept) if kept else "")))
        return EXIT_PROBLEMS if kept else EXIT_OK
    if args.command == "uninstall":
        try:
            lines, kept = uninstall(target, args.dry_run)
        except ValueError as exc:
            sys.stderr.write("wizard: error: %s\n" % exc)
            return EXIT_USAGE
        print("\n".join(lines))
        if args.dry_run:
            print("wizard: dry run, nothing removed")
        else:
            print("wizard: uninstalled from %s%s" % (target, "; kept %d changed file(s)" % len(kept) if kept else ""))
        return EXIT_PROBLEMS if kept else EXIT_OK
    if not os.path.isdir(target):
        sys.stderr.write("wizard: error: target folder does not exist: %s\n" % target)
        return EXIT_USAGE
    lines, ok = check(target)
    print("\n".join(lines))
    print("wizard: %s" % ("ready" if ok else "needs attention (see the lines above)"))
    return EXIT_OK if ok else EXIT_PROBLEMS


if __name__ == "__main__":
    sys.exit(main())
