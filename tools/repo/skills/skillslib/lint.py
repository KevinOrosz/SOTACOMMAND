"""The rules behind ``skills.py lint``.

Every rule has a stable code, a fixed severity and a one-line summary in
:data:`RULES`. ``CONTRACT.md`` lists them, ``skills.py lint --list-rules``
prints them, and ``tests/skills`` holds one failing fixture per rule.
Errors make ``lint`` exit 1. Warnings are reported but only fail the run
under ``--strict``.
"""

import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence

from . import summary as summary_mod
from .model import (
    CaseInfo,
    ParsedMarkdown,
    PluginInfo,
    Problem,
    SkillInfo,
    load_plugins,
    read_text,
)
from .yamlsubset import Map, Node, Scalar, Seq

ERROR = "error"
WARNING = "warning"

RULES = OrderedDict(
    [
        # Plugins
        ("PLG001", (ERROR, "every directory under packs/ and labs/ is a plugin with .claude-plugin/plugin.json")),
        ("PLG002", (ERROR, "plugin.json is valid JSON holding an object")),
        ("PLG003", (ERROR, "the plugin name is present and kebab-case")),
        ("PLG004", (ERROR, "the plugin name contains neither 'claude' nor 'anthropic'")),
        ("PLG005", (ERROR, "the plugin has a README.md")),
        ("PLG006", (ERROR, "plugin.json has a semver version")),
        ("PLG007", (ERROR, "plugin.json has a description")),
        ("PLG008", (ERROR, "plugin.json has an author with a name")),
        ("PLG009", (ERROR, "no top-level bin/ directory (claude.ai and Cowork refuse to install it)")),
        ("PLG010", (ERROR, "no CLAUDE.md at the plugin root (plugins never load it; use a skill)")),
        ("PLG011", (ERROR, "manifest component paths start with ./ and stay inside the plugin")),
        ("PLG012", (ERROR, "no local stdio MCP servers (they block claude.ai and Cowork installs)")),
        ("PLG013", (WARNING, "settings.json holds only agent and subagentStatusLine")),
        ("PLG014", (ERROR, "every SKILL.md sits in a skill folder the plugin loads")),
        # Skills
        ("SKL001", (ERROR, "SKILL.md opens with '---' at byte 0, uses LF line endings and closes its frontmatter")),
        ("SKL002", (ERROR, "the frontmatter stays inside the supported YAML subset")),
        ("SKL003", (ERROR, "only the six portable top-level keys are present")),
        ("SKL004", (ERROR, "name is lowercase letters, digits and single hyphens, at most 64 characters")),
        ("SKL005", (ERROR, "name matches the skill folder name")),
        ("SKL006", (ERROR, "name contains neither 'claude' nor 'anthropic'")),
        ("SKL007", (ERROR, "description is a non-empty string of at most 1024 characters without < or >")),
        ("SKL008", (ERROR, "compatibility, when present, is a string of at most 500 characters")),
        ("SKL009", (ERROR, "license and allowed-tools, when present, are strings")),
        ("SKL010", (ERROR, "metadata is a block mapping whose values are all strings")),
        ("SKL011", (ERROR, "metadata has version, status, pack, min-tier, roles and gate")),
        ("SKL012", (ERROR, "metadata values are valid (semver, enums, role ids, pack matches the folder)")),
        ("SKL013", (ERROR, "retired skills, and only retired skills, carry metadata.replaces")),
        ("SKL014", (ERROR, "stable skills have at least 3 eval cases")),
        ("SKL015", (ERROR, "stable skills meet the stable rule in their newest complete two-arm run")),
        ("SKL016", (WARNING, "no scaffold placeholder (TODO) left in the description")),
        ("SKL017", (WARNING, "SKILL.md stays under 500 lines")),
        ("SKL018", (WARNING, "metadata.replaces names a skill that exists")),
        ("SKL019", (ERROR, "skill names are unique across all packs and labs")),
        ("SKL020", (ERROR, "stable skills qualify on a run that pinned the model under test (suite.modelPinned)")),
        # Eval cases
        ("EVL001", (ERROR, "eval cases live at evals/<skill>/<case>/ for a skill in the same plugin")),
        ("EVL002", (ERROR, "case directory names are valid claude plugin eval case names")),
        ("EVL003", (ERROR, "case names are unique within a plugin")),
        ("EVL004", (ERROR, "every case has a non-empty prompt")),
        ("EVL005", (ERROR, "prompt.md has valid frontmatter with known keys and typed values")),
        ("EVL006", (ERROR, "no case pins a model (models come from CI variables only)")),
        ("EVL007", (ERROR, "the case's tags include its skill name")),
        ("EVL008", (ERROR, "case.yaml is valid: schema_version \"1.1\", name, known keys")),
        ("EVL009", (ERROR, "files that case.yaml names exist inside the case directory")),
        ("EVL010", (ERROR, "every case has at least one grader in graders/")),
        ("EVL011", (ERROR, "graders have a known type and valid options")),
        ("EVL012", (WARNING, "llm graders state concrete PASS and FAIL conditions")),
        ("EVL013", (WARNING, "every case has a tool_used grader for the Skill tool")),
        ("EVL014", (WARNING, "no scaffold placeholder (TODO) left in a case")),
        # Result summaries
        ("RES001", (ERROR, "committed summary.json files follow the redacted summary format")),
        ("RES002", (WARNING, "no raw eval output (aggregate-result.json, report.html, mock-recordings/)")),
        ("RES003", (WARNING, "summaries name this plugin and its skills")),
        ("RES004", (WARNING, "every case in a summary is a current eval case (no drift after a rename)")),
    ]
)

PORTABLE_KEYS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")
REQUIRED_METADATA = ("version", "status", "pack", "min-tier", "roles", "gate")
STATUSES = ("stable", "experimental", "retired")
TIERS = ("low", "mid", "high")
GATES = ("none", "mandatory")
RESERVED_WORDS = ("claude", "anthropic")
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
MAX_SKILL_LINES = 500
SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
PLACEHOLDER = "TODO"

COMPONENT_PATH_KEYS = (
    "skills", "commands", "agents", "hooks", "mcpServers", "lspServers", "outputStyles", "workflows", "types",
)
EXPERIMENTAL_PATH_KEYS = ("themes", "monitors")
PLUGIN_SETTINGS_KEYS = ("agent", "subagentStatusLine")

PROMPT_KEYS = (
    "schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome", "model",
    "max_turns", "timeout_seconds", "allowed_tools", "disallowed_tools", "append_system_prompt", "env",
)
CASE_KEYS = (
    "schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome", "context",
    "execution", "graders",
)
CONTEXT_KEYS = ("scaffold_script", "history_file", "add_dirs")
EXECUTION_KEYS = (
    "prompt", "model", "max_turns", "timeout_seconds", "allowed_tools", "disallowed_tools", "append_system_prompt", "env",
)
CASE_SCHEMA_VERSION = "1.1"
CASE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
WINDOWS_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"] + ["com{}".format(i) for i in range(1, 10)] + ["lpt{}".format(i) for i in range(1, 10)]
)
ENV_KEY_RE = re.compile(r"EVAL_[A-Z0-9_]*")

GRADER_COMMON_KEYS = ("type", "name", "weight", "arm")
GRADER_OPTIONS = OrderedDict(
    [
        ("regex", (("pattern", "flags", "match", "target"), ("pattern",))),
        ("tool_used", (("tool", "input_match", "min", "max"), ("tool",))),
        ("tool_order", (("before", "after"), ("before", "after"))),
        ("file_exists", (("path", "exists"), ("path",))),
        ("llm", (("criteria", "focus"), ())),
        ("baseline", (("baseline_file", "criteria"), ("baseline_file",))),
    ]
)
GRADER_ARMS = ("with-only", "both")
GRADER_VIEWS = ("last_message", "trace", "files", "mock_calls")
REGEX_FLAGS = frozenset("dgimsuyv")
REGEX_MATCH_RE = re.compile(r"contains|not_contains|count:[0-9]+")
INLINE_FLAGS_RE = re.compile(r"\(\?[a-zA-Z]+[:)]")


class LintResult(NamedTuple):
    problems: List[Problem]
    plugins: int
    skills: int
    cases: int
    summaries: int

    @property
    def errors(self) -> int:
        return sum(1 for p in self.problems if p.severity == ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for p in self.problems if p.severity == WARNING)


class _Reporter(object):
    def __init__(self) -> None:
        self.problems = []  # type: List[Problem]

    def add(self, code: str, path: Path, line: int, message: str) -> None:
        severity = RULES[code][0]
        self.problems.append(Problem(path, line, severity, code, message))


# ---------------------------------------------------------------------------
# Small helpers over parsed YAML nodes
# ---------------------------------------------------------------------------


def _line(mapping: Optional[Map], key: str, default: int = 0) -> int:
    if mapping is None:
        return default
    return mapping.key_lines.get(key, mapping.line or default)


def _str_value(node: Optional[Node]) -> Optional[str]:
    """A string as the eval runner (YAML 1.2) reads it."""
    if isinstance(node, Scalar) and node.tag == "str":
        return node.value
    return None


def _str_list(node: Optional[Node]) -> Optional[List[str]]:
    if not isinstance(node, Seq):
        return None
    values = [_str_value(item) for item in node.items]
    if any(v is None for v in values):
        return None
    return [v for v in values if v is not None]


def _int_value(node: Optional[Node]) -> Optional[int]:
    if isinstance(node, Scalar) and node.tag == "int":
        return node.value
    return None


def _number_value(node: Optional[Node]) -> Optional[float]:
    if isinstance(node, Scalar) and node.tag in ("int", "float"):
        return node.value
    return None


def _describe(node: Optional[Node]) -> str:
    if isinstance(node, Scalar):
        if node.tag == "null":
            return "empty"
        if node.tag != "str":
            return "a YAML {}".format(node.tag)
        if node.tag11 != "str":
            return "a YAML 1.1 {} (PyYAML reads it that way)".format(node.tag11)
        return "a string" if node.value.strip() else "an empty string"
    if isinstance(node, Seq):
        return "a list"
    if isinstance(node, Map):
        return "a mapping"
    return "missing"


def _display_path(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def _inside(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


def _check_component_paths(rep: _Reporter, plugin: PluginInfo, key: str, value) -> None:
    items = value if isinstance(value, list) else [value]
    for item in items:
        if not isinstance(item, str):
            continue  # inline configuration
        if key == "skills" and item in (".", "./"):
            continue
        if key == "mcpServers" and item.startswith("https://"):
            continue
        if not item.startswith("./"):
            rep.add("PLG011", plugin.manifest_path, 0, "'{}' path {!r} must start with ./".format(key, item))
        elif ".." in Path(item).parts:
            rep.add("PLG011", plugin.manifest_path, 0, "'{}' path {!r} must not contain '..'".format(key, item))


def _stdio_servers(config) -> List[str]:
    """Names of MCP servers in a config object that launch a local process."""
    if not isinstance(config, dict):
        return []
    servers = config.get("mcpServers", config)
    if not isinstance(servers, dict):
        return []
    names = []
    for name, server in servers.items():
        if isinstance(server, dict) and ("command" in server or server.get("type") == "stdio"):
            names.append(str(name))
    return names


def _check_plugin(rep: _Reporter, plugin: PluginInfo) -> None:
    manifest_path = plugin.manifest_path
    if plugin.manifest_error == "missing":
        rep.add(
            "PLG001", manifest_path, 0,
            "missing; every directory under packs/ and labs/ is a plugin and needs .claude-plugin/plugin.json",
        )
    elif plugin.manifest_error:
        rep.add("PLG002", manifest_path, 0, "plugin.json {}".format(plugin.manifest_error))
    manifest = plugin.manifest
    if manifest is not None:
        name = manifest.get("name")
        if not isinstance(name, str) or not name:
            rep.add("PLG003", manifest_path, 0, "'name' is required")
        else:
            if not NAME_RE.fullmatch(name):
                rep.add("PLG003", manifest_path, 0, "name {!r} must be kebab-case (a-z, 0-9, single hyphens)".format(name))
            if any(word in name.lower() for word in RESERVED_WORDS):
                rep.add("PLG004", manifest_path, 0, "name {!r} must not contain 'claude' or 'anthropic'".format(name))
        version = manifest.get("version")
        if version is None:
            rep.add("PLG006", manifest_path, 0, "'version' is required (claude plugin validate --strict fails without it)")
        elif not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
            rep.add("PLG006", manifest_path, 0, "version {!r} must be a semver string such as 1.0.0".format(version))
        description = manifest.get("description")
        if not isinstance(description, str) or not description.strip():
            rep.add("PLG007", manifest_path, 0, "'description' is required (claude plugin validate --strict fails without it)")
        author = manifest.get("author")
        if not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author["name"].strip():
            rep.add("PLG008", manifest_path, 0, "'author' must be an object with a 'name' (claude plugin validate --strict fails without it)")
        for key in COMPONENT_PATH_KEYS:
            if key in manifest:
                _check_component_paths(rep, plugin, key, manifest[key])
        experimental = manifest.get("experimental")
        if isinstance(experimental, dict):
            for key in EXPERIMENTAL_PATH_KEYS:
                if key in experimental:
                    _check_component_paths(rep, plugin, key, experimental[key])
        declared = manifest.get("mcpServers")
        servers = []  # type: List[str]
        for entry in declared if isinstance(declared, list) else [declared]:
            if isinstance(entry, dict):  # inline server configs keyed by name
                servers.extend(_stdio_servers({"mcpServers": entry}))
            elif (
                isinstance(entry, str)
                and entry.startswith("./")
                and entry.endswith(".json")
                and ".." not in Path(entry).parts
            ):
                servers.extend(_stdio_servers(_load_json(plugin.root / entry[2:])))
        for server in servers:
            rep.add("PLG012", manifest_path, 0, "MCP server {!r} runs a local command; packs can't ship stdio servers".format(server))

    if not (plugin.root / "README.md").is_file():
        rep.add("PLG005", plugin.root / "README.md", 0, "missing; every plugin needs a README.md")
    if (plugin.root / "bin").exists():
        rep.add("PLG009", plugin.root / "bin", 0, "a top-level bin/ directory blocks claude.ai and Cowork installs; move scripts under the skill")
    if (plugin.root / "CLAUDE.md").exists():
        rep.add("PLG010", plugin.root / "CLAUDE.md", 0, "plugins never load CLAUDE.md; put the instructions in a skill")
    mcp_file = plugin.root / ".mcp.json"
    if mcp_file.is_file():
        for server in _stdio_servers(_load_json(mcp_file)):
            rep.add("PLG012", mcp_file, 0, "MCP server {!r} runs a local command; packs can't ship stdio servers".format(server))
    settings = plugin.root / "settings.json"
    if settings.is_file():
        loaded = _load_json(settings)
        if isinstance(loaded, dict):
            extra = sorted(k for k in loaded if k not in PLUGIN_SETTINGS_KEYS)
            if extra:
                rep.add("PLG013", settings, 0, "keys {} are dropped; a plugin settings.json honors only agent and subagentStatusLine".format(", ".join(extra)))
    for stray in plugin.stray_skill_files:
        rep.add("PLG014", stray, 0, "this SKILL.md isn't in a folder the plugin loads; skills live at skills/<name>/SKILL.md")


def _load_json(path: Path):
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


def _check_skill(rep: _Reporter, skill: SkillInfo, all_names: Dict[str, List[SkillInfo]]) -> None:
    path = skill.skill_md
    parsed = skill.parsed
    if parsed.error_stage in ("read", "frontmatter"):
        rep.add("SKL001", path, parsed.error_line, parsed.error or "can't be read")
        return
    if parsed.has_bom:
        rep.add("SKL001", path, 1, "starts with a byte-order mark; '---' must be the first bytes of the file")
    if parsed.has_crlf:
        rep.add("SKL001", path, 1, "uses CRLF line endings; skill packaging tools expect LF")
    if "---" in parsed.yaml_text:
        rep.add("SKL001", path, 1, "the frontmatter contains '---'; the reference validator splits the file on it")
    if parsed.error_stage == "yaml":
        rep.add("SKL002", path, parsed.error_line, parsed.error or "unsupported YAML")
        return
    fm = parsed.frontmatter
    assert fm is not None
    for key in fm.items:
        if key not in PORTABLE_KEYS:
            rep.add(
                "SKL003", path, _line(fm, key),
                "'{}' isn't a portable key (allowed: {}); put custom fields under metadata".format(key, ", ".join(PORTABLE_KEYS)),
            )

    name_node = fm.items.get("name")
    if name_node is None:
        rep.add("SKL004", path, fm.line, "'name' is required")
    elif not isinstance(name_node, Scalar) or not name_node.is_portable_string:
        rep.add("SKL004", path, _line(fm, "name"), "'name' must be a string, found {}".format(_describe(name_node)))
    else:
        name = name_node.value
        if len(name) > MAX_NAME:
            rep.add("SKL004", path, name_node.line, "name is {} characters; the limit is {}".format(len(name), MAX_NAME))
        if not NAME_RE.fullmatch(name):
            rep.add(
                "SKL004", path, name_node.line,
                "name {!r} must be lowercase letters, digits and single hyphens, with no leading or trailing hyphen".format(name),
            )
        if name != skill.folder:
            rep.add("SKL005", path, name_node.line, "name {!r} must match the folder name {!r}".format(name, skill.folder))
        if any(word in name for word in RESERVED_WORDS):
            rep.add("SKL006", path, name_node.line, "name {!r} must not contain 'claude' or 'anthropic'".format(name))
        if len(all_names.get(name, [])) > 1:
            others = sorted(str(s.skill_md) for s in all_names[name] if s is not skill)
            rep.add("SKL019", path, name_node.line, "skill name {!r} is also used by {}".format(name, ", ".join(others)))

    desc_node = fm.items.get("description")
    if desc_node is None:
        rep.add("SKL007", path, fm.line, "'description' is required")
    elif not isinstance(desc_node, Scalar) or not desc_node.is_portable_string:
        rep.add("SKL007", path, _line(fm, "description"), "'description' must be a string, found {}".format(_describe(desc_node)))
    else:
        description = desc_node.value
        if not description.strip():
            rep.add("SKL007", path, desc_node.line, "description must not be empty")
        if len(description) > MAX_DESCRIPTION:
            rep.add("SKL007", path, desc_node.line, "description is {} characters; the limit is {}".format(len(description), MAX_DESCRIPTION))
        if "<" in description or ">" in description:
            rep.add("SKL007", path, desc_node.line, "description must not contain < or > (XML tags are rejected on upload)")
        if PLACEHOLDER in description:
            rep.add("SKL016", path, desc_node.line, "description still holds the scaffold placeholder")

    for key in ("license", "allowed-tools"):
        node = fm.items.get(key)
        if node is None:
            continue
        if not isinstance(node, Scalar) or not node.is_portable_string or not node.value.strip():
            hint = " (the portable form is a space-separated string)" if key == "allowed-tools" else ""
            rep.add("SKL009", path, _line(fm, key), "'{}' must be a non-empty string, found {}{}".format(key, _describe(node), hint))

    compat = fm.items.get("compatibility")
    if compat is not None:
        if not isinstance(compat, Scalar) or not compat.is_portable_string or not compat.value.strip():
            rep.add("SKL008", path, _line(fm, "compatibility"), "'compatibility' must be a non-empty string, found {}".format(_describe(compat)))
        elif len(compat.value) > MAX_COMPATIBILITY:
            rep.add("SKL008", path, compat.line, "compatibility is {} characters; the limit is {}".format(len(compat.value), MAX_COMPATIBILITY))

    _check_metadata(rep, skill, fm, all_names)

    text = parsed.text or ""
    line_count = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    if line_count > MAX_SKILL_LINES:
        rep.add("SKL017", path, 0, "SKILL.md has {} lines; keep it under {} and move detail to references/".format(line_count, MAX_SKILL_LINES))


def _check_metadata(rep: _Reporter, skill: SkillInfo, fm: Map, all_names: Dict[str, List[SkillInfo]]) -> None:
    path = skill.skill_md
    node = fm.items.get("metadata")
    if node is None:
        rep.add("SKL011", path, fm.line, "'metadata' is required, with {}".format(", ".join(REQUIRED_METADATA)))
        return
    if not isinstance(node, Map):
        rep.add("SKL010", path, _line(fm, "metadata"), "'metadata' must be a mapping, found {}".format(_describe(node)))
        return
    if node.style == "flow":
        rep.add("SKL010", path, node.line, "write metadata as an indented block; the reference validator rejects flow style")
    values = {}  # type: Dict[str, str]
    for key, value in node.items.items():
        if not isinstance(value, Scalar) or not value.is_portable_string:
            rep.add("SKL010", path, _line(node, key), "metadata.{} must be a string, found {}; quote it".format(key, _describe(value)))
        else:
            values[key] = value.value
    for key in REQUIRED_METADATA:
        if key not in node.items:
            rep.add("SKL011", path, node.line, "metadata.{} is required".format(key))

    def bad(key: str, message: str) -> None:
        rep.add("SKL012", path, _line(node, key), "metadata.{} {}".format(key, message))

    if "version" in values and not SEMVER_RE.fullmatch(values["version"]):
        bad("version", "{!r} must be semver, such as 1.0.0".format(values["version"]))
    status = values.get("status")
    if status is not None and status not in STATUSES:
        bad("status", "{!r} must be one of {}".format(status, ", ".join(STATUSES)))
    if "pack" in values and values["pack"] != skill.plugin.dir_name:
        bad("pack", "{!r} must match the pack folder {!r}".format(values["pack"], skill.plugin.dir_name))
    if "min-tier" in values and values["min-tier"] not in TIERS:
        bad("min-tier", "{!r} must be one of {}".format(values["min-tier"], ", ".join(TIERS)))
    if "gate" in values and values["gate"] not in GATES:
        bad("gate", "{!r} must be one of {}".format(values["gate"], ", ".join(GATES)))
    if "roles" in values:
        roles = [r.strip() for r in values["roles"].split(",")]
        if not roles or any(not NAME_RE.fullmatch(r) for r in roles):
            bad("roles", "{!r} must be comma-separated role ids such as 'builder, orchestrator'".format(values["roles"]))
        elif len(set(roles)) != len(roles):
            bad("roles", "{!r} lists a role twice".format(values["roles"]))

    replaces = values.get("replaces")
    if status == "retired":
        if replaces is None:
            rep.add("SKL013", path, node.line, "retired skills must name their replacement in metadata.replaces")
        elif not NAME_RE.fullmatch(replaces):
            rep.add("SKL013", path, _line(node, "replaces"), "metadata.replaces {!r} must be a skill name".format(replaces))
        elif replaces not in all_names:
            rep.add("SKL018", path, _line(node, "replaces"), "metadata.replaces names {!r}, which isn't a skill in the linted packs".format(replaces))
    elif "replaces" in node.items:
        rep.add("SKL013", path, _line(node, "replaces"), "only retired skills carry metadata.replaces")

    if status == "stable":
        cases = skill.plugin.cases_for(skill.folder)
        if len(cases) < summary_mod.STABLE_MIN_CASES:
            rep.add(
                "SKL014", path, _line(node, "status"),
                "stable skills need at least {} eval cases under {}/{}/; found {}".format(
                    summary_mod.STABLE_MIN_CASES, skill.plugin.eval_dir.name, skill.folder, len(cases)
                ),
            )
        evidence = summary_mod.qualifying_evidence(skill.plugin.summaries, skill.folder)
        failures = summary_mod.stable_rule_failures(evidence)
        if failures:
            rep.add("SKL015", path, _line(node, "status"), "stable rule not met: {}".format("; ".join(failures)))
        if evidence is not None and not evidence.model_pinned:
            rep.add(
                "SKL020", path, _line(node, "status"),
                "the newest complete two-arm run ({}) didn't pin the model under test; rerun with --model "
                "(the evals workflow passes vars.EVAL_MODEL)".format(_display_path(evidence.summary_path, skill.plugin.root)),
            )


# ---------------------------------------------------------------------------
# Eval cases
# ---------------------------------------------------------------------------


def _check_view(rep: _Reporter, path: Path, mapping: Map, key: str) -> None:
    node = mapping.items.get(key)
    if node is None:
        return
    if isinstance(node, Scalar) and _str_value(node) in GRADER_VIEWS:
        return
    if isinstance(node, Map) and set(node.items) == {"source", "path"}:
        if _str_value(node.items["source"]) == "file" and (_str_value(node.items["path"]) or "").strip():
            return
    rep.add(
        "EVL011", path, _line(mapping, key),
        "'{}' must be one of {} or {{source: file, path: <path>}}".format(key, ", ".join(GRADER_VIEWS)),
    )


def _check_grader(rep: _Reporter, case: CaseInfo, grader: ParsedMarkdown) -> Optional[str]:
    """Check one grader file. Returns its type when readable."""
    path = grader.path
    if grader.error_stage is not None:
        rep.add("EVL011", path, grader.error_line, grader.error or "can't be read")
        return None
    fm = grader.frontmatter
    assert fm is not None
    grader_type = _str_value(fm.items.get("type"))
    if grader_type is None:
        rep.add("EVL011", path, fm.line, "'type' is required ({})".format(", ".join(GRADER_OPTIONS)))
        return None
    if grader_type not in GRADER_OPTIONS:
        rep.add("EVL011", path, _line(fm, "type"), "unknown grader type {!r} ({})".format(grader_type, ", ".join(GRADER_OPTIONS)))
        return None
    options, required = GRADER_OPTIONS[grader_type]
    for key in fm.items:
        if key not in GRADER_COMMON_KEYS and key not in options:
            rep.add("EVL011", path, _line(fm, key), "'{}' isn't an option of {} graders".format(key, grader_type))
    for key in required:
        if key not in fm.items:
            rep.add("EVL011", path, fm.line, "{} graders need '{}'".format(grader_type, key))
    if "weight" in fm.items:
        weight = _number_value(fm.items["weight"])
        if weight is None or weight <= 0:
            rep.add("EVL011", path, _line(fm, "weight"), "'weight' must be a positive number")
    if "arm" in fm.items and _str_value(fm.items["arm"]) not in GRADER_ARMS:
        rep.add("EVL011", path, _line(fm, "arm"), "'arm' must be one of {}".format(", ".join(GRADER_ARMS)))

    criteria = _str_value(fm.items.get("criteria")) or grader.body.strip()
    if grader_type == "regex":
        pattern = _str_value(fm.items.get("pattern"))
        if "pattern" in fm.items and not pattern:
            rep.add("EVL011", path, _line(fm, "pattern"), "'pattern' must be a non-empty string")
        elif pattern and INLINE_FLAGS_RE.search(pattern):
            rep.add("EVL011", path, _line(fm, "pattern"), "inline flags such as (?i) aren't supported; use 'flags: i'")
        if "flags" in fm.items:
            flags = _str_value(fm.items["flags"])
            if flags is None or any(f not in REGEX_FLAGS for f in flags) or len(set(flags)) != len(flags):
                rep.add("EVL011", path, _line(fm, "flags"), "'flags' must be JavaScript regex flags such as 'im'")
        if "match" in fm.items and not REGEX_MATCH_RE.fullmatch(_str_value(fm.items["match"]) or ""):
            rep.add("EVL011", path, _line(fm, "match"), "'match' must be contains, not_contains or count:N")
        _check_view(rep, path, fm, "target")
    elif grader_type in ("llm", "baseline"):
        if not criteria:
            rep.add("EVL011", path, fm.line, "{} graders need criteria: write the rubric as the file body".format(grader_type))
        else:
            if "PASS" not in criteria or "FAIL" not in criteria:
                rep.add("EVL012", path, grader.body_first_line or fm.line, "state concrete PASS and FAIL conditions in the rubric")
            if PLACEHOLDER in criteria:
                rep.add("EVL014", path, grader.body_first_line or fm.line, "the rubric still holds the scaffold placeholder")
        if grader_type == "llm":
            _check_view(rep, path, fm, "focus")
        else:
            baseline_file = _str_value(fm.items.get("baseline_file"))
            if baseline_file is not None:
                target = case.dir / baseline_file
                if not _inside(case.dir, target) or not target.is_file():
                    rep.add("EVL011", path, _line(fm, "baseline_file"), "baseline_file {!r} must be a file in the case directory".format(baseline_file))
    elif grader_type == "tool_used":
        if "tool" in fm.items and not _str_value(fm.items["tool"]):
            rep.add("EVL011", path, _line(fm, "tool"), "'tool' must be a tool name")
        if "input_match" in fm.items and not _str_value(fm.items["input_match"]):
            rep.add("EVL011", path, _line(fm, "input_match"), "'input_match' must be a regex string")
        bounds = {}
        for key in ("min", "max"):
            if key in fm.items:
                value = _int_value(fm.items[key])
                if value is None or value < 0:
                    rep.add("EVL011", path, _line(fm, key), "'{}' must be a whole number >= 0".format(key))
                else:
                    bounds[key] = value
        if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
            rep.add("EVL011", path, _line(fm, "min"), "'min' can't exceed 'max'")
    elif grader_type == "tool_order":
        for key in ("before", "after"):
            node = fm.items.get(key)
            if node is None:
                continue
            ok = bool(_str_value(node)) or (
                isinstance(node, Map)
                and set(node.items) <= {"tool", "input_match"}
                and bool(_str_value(node.items.get("tool")))
            )
            if not ok:
                rep.add("EVL011", path, _line(fm, key), "'{}' must be a tool name or {{tool: <name>, input_match: <regex>}}".format(key))
    elif grader_type == "file_exists":
        if "path" in fm.items and not (_str_value(fm.items["path"]) or "").strip():
            rep.add("EVL011", path, _line(fm, "path"), "'path' must be a glob string")
        if "exists" in fm.items:
            node = fm.items["exists"]
            if not (isinstance(node, Scalar) and node.tag == "bool"):
                rep.add("EVL011", path, _line(fm, "exists"), "'exists' must be true or false")
    return grader_type


def _check_typed_fields(rep: _Reporter, code: str, path: Path, mapping: Map) -> None:
    """Shared type checks for prompt.md frontmatter and case.yaml (and its execution block)."""
    limits = (("max_turns", 1, 200), ("timeout_seconds", 1, 3600), ("runs", 1, 50))
    for key, low, high in limits:
        if key in mapping.items:
            value = _int_value(mapping.items[key])
            if value is None or not low <= value <= high:
                rep.add(code, path, _line(mapping, key), "'{}' must be a whole number from {} to {}".format(key, low, high))
    for key in ("tags", "allowed_tools", "disallowed_tools", "plugins"):
        if key in mapping.items and _str_list(mapping.items[key]) is None:
            rep.add(code, path, _line(mapping, key), "'{}' must be a list of strings".format(key))
    for key in ("description", "expected_outcome", "append_system_prompt", "prompt"):
        if key in mapping.items and _str_value(mapping.items[key]) is None:
            rep.add(code, path, _line(mapping, key), "'{}' must be a string".format(key))
    if "env" in mapping.items:
        env = mapping.items["env"]
        if not isinstance(env, Map) or any(
            not ENV_KEY_RE.fullmatch(k) or _str_value(v) is None for k, v in env.items.items()
        ):
            rep.add(code, path, _line(mapping, "env"), "'env' must map EVAL_* names to strings")


def _check_case(rep: _Reporter, case: CaseInfo, skill_folders: Sequence[str], duplicates: Dict[str, List[CaseInfo]]) -> None:
    where = case.prompt.path if case.prompt is not None else case.case_yaml_path
    eval_dir_name = case.plugin.eval_dir.name
    if case.skill_group is None:
        rep.add("EVL001", where, 0, "cases live at {}/<skill>/<case>/; this one is at {}".format(eval_dir_name, "/".join(case.relative_parts)))
    elif case.skill_group not in skill_folders:
        rep.add("EVL001", where, 0, "{!r} isn't a skill in this plugin; cases live at {}/<skill>/<case>/".format(case.skill_group, eval_dir_name))
    if not CASE_NAME_RE.fullmatch(case.name) or case.name.endswith(".") or case.name.split(".")[0].lower() in WINDOWS_RESERVED:
        rep.add("EVL002", where, 0, "case name {!r} must start with a letter or digit, use only letters, digits, '.', '_' and '-', and be a valid Windows file name".format(case.name))
    if len(duplicates.get(case.name, [])) > 1:
        rep.add("EVL003", where, 0, "another case in this plugin is also named {!r}; reports can't tell them apart".format(case.name))

    tags = None  # type: Optional[List[str]]
    prompt_text = ""
    prompt_unreadable = False
    if case.prompt is not None:
        prompt = case.prompt
        prompt_unreadable = prompt.error_stage is not None
        if prompt.error_stage == "frontmatter":
            rep.add("EVL005", prompt.path, prompt.error_line, "prompt.md needs a '---' frontmatter block with tags, max_turns, timeout_seconds and allowed_tools")
        elif prompt.error_stage is not None:
            rep.add("EVL005", prompt.path, prompt.error_line, prompt.error or "can't be read")
        else:
            fm = prompt.frontmatter
            assert fm is not None
            for key in fm.items:
                if key not in PROMPT_KEYS:
                    rep.add("EVL005", prompt.path, _line(fm, key), "'{}' isn't a prompt.md field; claude plugin eval rejects unknown keys".format(key))
            if "model" in fm.items:
                rep.add("EVL006", prompt.path, _line(fm, "model"), "don't pin a model in a case; CI passes --model from a repository variable")
            if "name" in fm.items and _str_value(fm.items["name"]) != case.name:
                rep.add("EVL005", prompt.path, _line(fm, "name"), "'name' must match the case directory {!r}".format(case.name))
            if "schema_version" in fm.items and _str_value(fm.items["schema_version"]) != CASE_SCHEMA_VERSION:
                rep.add("EVL005", prompt.path, _line(fm, "schema_version"), "'schema_version' must be the string \"1.1\"")
            _check_typed_fields(rep, "EVL005", prompt.path, fm)
            if "tags" in fm.items:
                tags = _str_list(fm.items["tags"]) or []
            prompt_text = prompt.body.strip()
            if PLACEHOLDER in prompt_text:
                rep.add("EVL014", prompt.path, prompt.body_first_line, "the prompt still holds the scaffold placeholder")

    root = case.case_yaml_root
    if case.case_yaml is not None:
        cy_path = case.case_yaml_path
        if root is None:
            rep.add("EVL008", cy_path, case.case_yaml.error_line, case.case_yaml.error or "can't be read")
        else:
            for key in root.items:
                if key not in CASE_KEYS:
                    rep.add("EVL008", cy_path, _line(root, key), "'{}' isn't a case.yaml field".format(key))
            version = root.items.get("schema_version")
            if version is None:
                rep.add("EVL008", cy_path, root.line, "'schema_version: \"1.1\"' is required")
            elif _str_value(version) != CASE_SCHEMA_VERSION:
                rep.add("EVL008", cy_path, _line(root, "schema_version"), "schema_version must be the quoted string \"1.1\", found {}".format(_describe(version)))
            name = _str_value(root.items.get("name"))
            if "name" not in root.items:
                rep.add("EVL008", cy_path, root.line, "'name' is required")
            elif name != case.name:
                rep.add("EVL008", cy_path, _line(root, "name"), "'name' must match the case directory {!r}".format(case.name))
            if "graders" in root.items:
                rep.add("EVL008", cy_path, _line(root, "graders"), "write graders as graders/NN-name.md files, not a case.yaml list")
            _check_typed_fields(rep, "EVL008", cy_path, root)
            outcome = _str_value(root.items.get("expected_outcome"))
            if outcome and PLACEHOLDER in outcome:
                rep.add("EVL014", cy_path, _line(root, "expected_outcome"), "expected_outcome still holds the scaffold placeholder")
            context = root.items.get("context")
            if context is not None:
                if not isinstance(context, Map):
                    rep.add("EVL008", cy_path, _line(root, "context"), "'context' must be a mapping")
                else:
                    for key in context.items:
                        if key not in CONTEXT_KEYS:
                            rep.add("EVL008", cy_path, _line(context, key), "'context.{}' isn't a case.yaml field ({})".format(key, ", ".join(CONTEXT_KEYS)))
                    for key in ("scaffold_script", "history_file"):
                        if key in context.items:
                            value = _str_value(context.items[key])
                            target = case.dir / value if value else None
                            if target is None or not _inside(case.dir, target) or not target.is_file():
                                rep.add("EVL009", cy_path, _line(context, key), "context.{} must name a file in the case directory".format(key))
                    if "add_dirs" in context.items:
                        dirs = _str_list(context.items["add_dirs"])
                        if dirs is None:
                            rep.add("EVL008", cy_path, _line(context, "add_dirs"), "'context.add_dirs' must be a list of strings")
                        else:
                            for value in dirs:
                                target = case.dir / value
                                if not _inside(case.dir, target) or not target.is_dir():
                                    rep.add("EVL009", cy_path, _line(context, "add_dirs"), "context.add_dirs entry {!r} must be a directory in the case directory".format(value))
            execution = root.items.get("execution")
            if execution is not None:
                if not isinstance(execution, Map):
                    rep.add("EVL008", cy_path, _line(root, "execution"), "'execution' must be a mapping")
                else:
                    for key in execution.items:
                        if key not in EXECUTION_KEYS:
                            rep.add("EVL008", cy_path, _line(execution, key), "'execution.{}' isn't a case.yaml field".format(key))
                    if "model" in execution.items:
                        rep.add("EVL006", cy_path, _line(execution, "model"), "don't pin a model in a case; CI passes --model from a repository variable")
                    _check_typed_fields(rep, "EVL008", cy_path, execution)
                    if not prompt_text:
                        prompt_text = (_str_value(execution.items.get("prompt")) or "").strip()
            if tags is None and "tags" in root.items:
                tags = _str_list(root.items["tags"]) or []

    if not prompt_text and not prompt_unreadable:
        rep.add("EVL004", where, 0, "the case has no prompt; write it as the body of prompt.md")
    if (
        not prompt_unreadable
        and case.skill_group is not None
        and case.skill_group in skill_folders
        and case.skill_group not in (tags or [])
    ):
        rep.add("EVL007", where, 0, "add the skill name to the case's tags (tags: [{}, ...]) so --tag {} selects it".format(case.skill_group, case.skill_group))

    if not case.graders:
        rep.add("EVL010", where, 0, "the case has no graders; add graders/NN-name.md files (claude plugin eval refuses a case without one)")
        return
    skill_fired = False
    for grader in case.graders:
        grader_type = _check_grader(rep, case, grader)
        if grader_type == "tool_used" and grader.frontmatter is not None:
            if _str_value(grader.frontmatter.items.get("tool")) == "Skill":
                skill_fired = True
    if not skill_fired:
        rep.add("EVL013", where, 0, "add a tool_used grader with 'tool: Skill' so the report shows whether the skill fired")


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def _check_results(rep: _Reporter, plugin: PluginInfo) -> None:
    skill_folders = {s.folder for s in plugin.skills}
    case_names = {c.name for c in plugin.cases}
    for loaded in plugin.summaries:
        for error in loaded.errors:
            rep.add("RES001", loaded.path, 0, error)
        doc = loaded.doc
        if doc is None:
            continue
        names = [p.get("name") for p in doc["suite"]["plugins"]]
        if plugin.name is not None and names and plugin.name not in names:
            rep.add("RES003", loaded.path, 0, "the summary is for plugin(s) {}, not {!r}".format(", ".join(map(repr, names)), plugin.name))
        for case in doc["cases"]:
            skill = case.get("skill")
            if skill is not None and skill not in skill_folders:
                rep.add("RES003", loaded.path, 0, "case {!r} is attributed to {!r}, which isn't a skill in this plugin".format(case["name"], skill))
            if case.get("name") not in case_names:
                rep.add(
                    "RES004", loaded.path, 0,
                    "case {!r} has no eval case folder any more (renamed or removed); re-run the eval so the numbers "
                    "describe the current cases, then delete this summary".format(case.get("name")),
                )
    for raw in plugin.raw_results:
        rep.add("RES002", raw, 0, "raw eval output holds model identifiers and transcripts; never commit it (keep only the redacted summary.json)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def lint_plugins(plugins: Sequence[PluginInfo]) -> LintResult:
    """Run every rule over already-loaded plugins."""
    rep = _Reporter()
    all_names = {}  # type: Dict[str, List[SkillInfo]]
    for plugin in plugins:
        for skill in plugin.skills:
            all_names.setdefault(skill.name, []).append(skill)
    case_count = 0
    summary_count = 0
    for plugin in plugins:
        _check_plugin(rep, plugin)
        for skill in plugin.skills:
            _check_skill(rep, skill, all_names)
        skill_folders = [s.folder for s in plugin.skills]
        by_name = {}  # type: Dict[str, List[CaseInfo]]
        for case in plugin.cases:
            by_name.setdefault(case.name, []).append(case)
        for case in plugin.cases:
            _check_case(rep, case, skill_folders, by_name)
        _check_results(rep, plugin)
        case_count += len(plugin.cases)
        summary_count += len(plugin.summaries)
    problems = sorted(rep.problems, key=lambda p: (str(p.path), p.line, p.code, p.message))
    return LintResult(
        problems=problems,
        plugins=len(plugins),
        skills=sum(len(p.skills) for p in plugins),
        cases=case_count,
        summaries=summary_count,
    )


def lint_roots(roots: Iterable[Path]) -> LintResult:
    """Load every plugin under ``roots`` and lint it."""
    return lint_plugins(load_plugins(list(roots)))


__all__ = ["RULES", "LintResult", "lint_plugins", "lint_roots"]
