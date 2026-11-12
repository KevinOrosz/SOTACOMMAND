"""Discovery and loading of plugins, skills, eval cases and result summaries.

Layout (see ``CONTRACT.md``)::

    <root>/                       packs/ or labs/
      <pack>/                     one Claude Code plugin per directory
        .claude-plugin/plugin.json
        README.md
        skills/<skill>/SKILL.md
        evals/<skill>/<case>/     prompt.md, case.yaml, graders/*.md, files/...
        evals/results/<run>/summary.json

Loading never raises for bad content. Parse failures are recorded on the
loaded objects so ``lint`` can report all of them in one pass.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, NamedTuple, Optional, Tuple

from .summary import SUMMARY_FILENAME, load_summary
from .yamlsubset import (
    FrontmatterError,
    Map,
    Scalar,
    YamlSubsetError,
    parse_document,
    split_frontmatter,
)

MANIFEST = Path(".claude-plugin") / "plugin.json"
DEFAULT_EVAL_DIR = "evals"
# Directories under the eval directory that hold run output or MCP mocks, never cases.
RESERVED_EVAL_DIRS = ("results", "mocks")
RAW_RESULT_FILES = ("aggregate-result.json", "report.html")
RAW_RESULT_DIRS = ("mock-recordings",)
_SKIP_DIRS = ("__pycache__", "node_modules")


class Problem(NamedTuple):
    """One lint finding. ``line`` is 1-based, or 0 when it concerns the whole file."""

    path: Path
    line: int
    severity: str
    code: str
    message: str


class ParsedYaml(NamedTuple):
    """A YAML-subset document: the root mapping, or why it couldn't be read."""

    root: Optional[Map]
    error_line: int
    error: Optional[str]


class ParsedMarkdown(NamedTuple):
    """A Markdown file with frontmatter, split and parsed."""

    path: Path
    text: Optional[str]
    frontmatter: Optional[Map]
    yaml_text: str
    body: str
    body_first_line: int
    has_bom: bool
    has_crlf: bool
    error_line: int
    error: Optional[str]
    # "read", "frontmatter" or "yaml": which stage failed, when one did.
    error_stage: Optional[str]


def read_text(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Read a UTF-8 file with its line endings intact. Returns ``(text, None)`` or ``(None, reason)``."""
    try:
        with open(str(path), encoding="utf-8", newline="") as handle:
            return handle.read(), None
    except UnicodeDecodeError:
        return None, "is not valid UTF-8"
    except OSError as exc:
        return None, "can't be read ({})".format(exc.strerror or exc)


def parse_markdown(path: Path) -> ParsedMarkdown:
    """Read ``path`` and parse its ``---`` frontmatter with the YAML subset."""
    text, read_error = read_text(path)
    if text is None:
        return ParsedMarkdown(path, None, None, "", "", 0, False, False, 0, read_error, "read")
    try:
        split = split_frontmatter(text)
    except FrontmatterError as exc:
        return ParsedMarkdown(path, text, None, "", "", 0, False, False, exc.line, exc.message, "frontmatter")
    try:
        root = parse_document(split.yaml_text, split.yaml_first_line)
    except YamlSubsetError as exc:
        return ParsedMarkdown(
            path, text, None, split.yaml_text, split.body, split.body_first_line,
            split.has_bom, split.has_crlf, exc.line, exc.message, "yaml",
        )
    return ParsedMarkdown(
        path, text, root, split.yaml_text, split.body, split.body_first_line,
        split.has_bom, split.has_crlf, 0, None, None,
    )


def parse_yaml_file(path: Path) -> ParsedYaml:
    """Parse a whole YAML-subset file such as ``case.yaml``."""
    text, read_error = read_text(path)
    if text is None:
        return ParsedYaml(None, 0, read_error)
    lines = text.replace("\r\n", "\n").split("\n")
    # A leading document-start marker is legal YAML; drop it and keep line numbers.
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---":
            lines[index] = ""
        break
    try:
        return ParsedYaml(parse_document("\n".join(lines)), 0, None)
    except YamlSubsetError as exc:
        return ParsedYaml(None, exc.line, exc.message)


def scalar_string(mapping: Optional[Map], key: str) -> Optional[str]:
    """The value of ``key`` when it is a portable string scalar, else ``None``."""
    if mapping is None:
        return None
    node = mapping.items.get(key)
    if isinstance(node, Scalar) and node.is_portable_string:
        return node.value
    return None


class SkillInfo(object):
    """A skill folder and its parsed ``SKILL.md``."""

    def __init__(self, plugin: "PluginInfo", directory: Path) -> None:
        self.plugin = plugin
        self.dir = directory
        self.folder = directory.name
        self.skill_md = directory / "SKILL.md"
        self.parsed = parse_markdown(self.skill_md)

    @property
    def frontmatter(self) -> Optional[Map]:
        return self.parsed.frontmatter

    @property
    def name(self) -> str:
        """The frontmatter name when it is a string, else the folder name."""
        return scalar_string(self.frontmatter, "name") or self.folder

    @property
    def metadata(self) -> Optional[Map]:
        if self.frontmatter is None:
            return None
        node = self.frontmatter.items.get("metadata")
        return node if isinstance(node, Map) else None

    def meta(self, key: str) -> Optional[str]:
        return scalar_string(self.metadata, key)


class CaseInfo(object):
    """One eval case directory: its prompt, optional ``case.yaml`` and graders."""

    def __init__(self, plugin: "PluginInfo", directory: Path) -> None:
        self.plugin = plugin
        self.dir = directory
        self.name = directory.name
        relative = directory.relative_to(plugin.eval_dir).parts
        # Cases live exactly at <eval dir>/<skill>/<case>/.
        self.skill_group = relative[0] if len(relative) == 2 else None
        self.relative_parts = relative
        prompt_path = directory / "prompt.md"
        case_yaml_path = directory / "case.yaml"
        self.prompt = parse_markdown(prompt_path) if prompt_path.is_file() else None
        self.case_yaml_path = case_yaml_path
        self.case_yaml = parse_yaml_file(case_yaml_path) if case_yaml_path.is_file() else None
        graders_dir = directory / "graders"
        self.graders = []  # type: List[ParsedMarkdown]
        if graders_dir.is_dir():
            self.graders = [parse_markdown(p) for p in sorted(graders_dir.glob("*.md")) if p.is_file()]

    @property
    def case_yaml_root(self) -> Optional[Map]:
        return self.case_yaml.root if self.case_yaml is not None else None


class PluginInfo(object):
    """A plugin directory under ``packs/`` or ``labs/`` and everything in it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir_name = root.name
        self.manifest_path = root / MANIFEST
        self.manifest = None  # type: Optional[Dict[str, Any]]
        self.manifest_error = None  # type: Optional[str]
        if not self.manifest_path.is_file():
            self.manifest_error = "missing"
        else:
            text, read_error = read_text(self.manifest_path)
            if text is None:
                self.manifest_error = read_error
            else:
                try:
                    loaded = json.loads(text)
                except ValueError as exc:
                    self.manifest_error = "is not valid JSON ({})".format(exc)
                else:
                    if isinstance(loaded, dict):
                        self.manifest = loaded
                    else:
                        self.manifest_error = "must be a JSON object"
        self.eval_dir = root / self._eval_dir_name()
        self.skills = [SkillInfo(self, d) for d in self._skill_dirs()]
        known = {s.skill_md.resolve() for s in self.skills}
        self.stray_skill_files = [p for p in self._all_skill_files() if p.resolve() not in known]
        self.cases = [CaseInfo(self, d) for d in self._case_dirs()]
        self.summaries = [load_summary(p) for p in self._summary_files()]
        self.raw_results = self._raw_result_paths()

    @property
    def name(self) -> Optional[str]:
        if self.manifest is None:
            return None
        value = self.manifest.get("name")
        return value if isinstance(value, str) else None

    def _eval_dir_name(self) -> str:
        experimental = (self.manifest or {}).get("experimental")
        value = experimental.get("evals") if isinstance(experimental, dict) else None
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, str):
            cleaned = value[2:] if value.startswith("./") else value
            parts = Path(cleaned).parts
            if cleaned and not Path(cleaned).is_absolute() and ".." not in parts:
                return cleaned.rstrip("/")
        return DEFAULT_EVAL_DIR

    def skill_roots(self) -> List[Path]:
        """``skills/`` plus every directory the manifest's ``skills`` key adds."""
        roots = [self.root / "skills"]
        declared = (self.manifest or {}).get("skills")
        entries = declared if isinstance(declared, list) else [declared] if declared is not None else []
        for entry in entries:
            if not isinstance(entry, str):
                continue
            cleaned = entry[2:] if entry.startswith("./") else entry
            if cleaned in ("", "."):
                candidate = self.root
            elif ".." in Path(cleaned).parts or Path(cleaned).is_absolute():
                continue
            else:
                candidate = self.root / cleaned
            if candidate not in roots:
                roots.append(candidate)
        return roots

    def _skill_dirs(self) -> List[Path]:
        found = []  # type: List[Path]
        for base in self.skill_roots():
            if not base.is_dir():
                continue
            if base != self.root / "skills" and (base / "SKILL.md").is_file():
                found.append(base)
                continue
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / "SKILL.md").is_file() and child not in found:
                    found.append(child)
        return found

    def _all_skill_files(self) -> Iterator[Path]:
        eval_dir = self.eval_dir.resolve()
        for dirpath, dirnames, filenames in os.walk(str(self.root)):
            current = Path(dirpath)
            dirnames[:] = sorted(
                d
                for d in dirnames
                if not d.startswith(".") and d not in _SKIP_DIRS and (current / d).resolve() != eval_dir
            )
            if "SKILL.md" in filenames:
                yield current / "SKILL.md"

    def _case_dirs(self) -> List[Path]:
        if not self.eval_dir.is_dir():
            return []
        cases = []
        for dirpath, dirnames, filenames in os.walk(str(self.eval_dir)):
            current = Path(dirpath)
            if current == self.eval_dir:
                dirnames[:] = [d for d in dirnames if d not in RESERVED_EVAL_DIRS]
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS)
            if current != self.eval_dir and ("prompt.md" in filenames or "case.yaml" in filenames):
                cases.append(current)
                dirnames[:] = []  # everything inside a case directory belongs to that case
        return sorted(cases)

    def _summary_files(self) -> List[Path]:
        results = self.eval_dir / "results"
        if not results.is_dir():
            return []
        return sorted(p for p in results.rglob(SUMMARY_FILENAME) if p.is_file())

    def _raw_result_paths(self) -> List[Path]:
        results = self.eval_dir / "results"
        if not results.is_dir():
            return []
        found = []
        for path in sorted(results.rglob("*")):
            if (path.is_file() and path.name in RAW_RESULT_FILES) or (
                path.is_dir() and path.name in RAW_RESULT_DIRS
            ):
                found.append(path)
        return found

    def cases_for(self, skill: str) -> List[CaseInfo]:
        return [case for case in self.cases if case.skill_group == skill]


def discover_plugin_dirs(root: Path) -> List[Path]:
    """Plugin directories in a root: the root itself if it is a plugin, else its subdirectories.

    Every non-hidden subdirectory of ``packs/`` or ``labs/`` is expected to be
    a plugin, so one without a manifest is still returned (lint reports it).
    """
    if (root / MANIFEST).is_file():
        return [root]
    return [
        child
        for child in sorted(root.iterdir())
        if child.is_dir() and not child.name.startswith(".") and child.name not in _SKIP_DIRS
    ]


def load_plugins(roots: List[Path]) -> List[PluginInfo]:
    """Load every plugin under the given roots, in a stable order."""
    plugins = []
    for root in roots:
        plugins.extend(PluginInfo(d) for d in discover_plugin_dirs(root))
    return plugins
