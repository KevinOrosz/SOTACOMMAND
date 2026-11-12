"""Shared helpers for the engine and adapter tests."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Dict, Iterable, Optional, Sequence

sys.dont_write_bytecode = True

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ADAPTERS_DIR = os.path.join(REPO_ROOT, "adapters")
GENERATE = os.path.join(ADAPTERS_DIR, "generate.py")
SAMPLE_ROLES = os.path.join(REPO_ROOT, "fixtures", "roles-sample")
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
TOOLS_DIR = os.path.join(ENGINE_DIR, "tools")

if ADAPTERS_DIR not in sys.path:
    sys.path.insert(0, ADAPTERS_DIR)

# Variables Claude Code sets for hooks; tests control them explicitly.
HOOK_ENV_VARS = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA")


def clean_env(**extra: str) -> Dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in HOOK_ENV_VARS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def run(
    args: Sequence[str],
    stdin: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        input=stdin if stdin is not None else "",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=env if env is not None else clean_env(),
        cwd=cwd or REPO_ROOT,
        timeout=120,
    )


def run_generate(*args: str, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return run([sys.executable, "-B", GENERATE] + list(args), cwd=cwd)


def run_tool(
    name: str,
    payload: Any = None,
    raw: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    args: Iterable[str] = (),
    tools_dir: str = TOOLS_DIR,
) -> subprocess.CompletedProcess:
    """Run an engine hook tool with ``payload`` as its JSON stdin."""
    stdin = raw if raw is not None else ("" if payload is None else json.dumps(payload))
    return run(
        [sys.executable, "-B", os.path.join(tools_dir, name)] + list(args),
        stdin=stdin,
        env=env,
    )


def load_tool_module(name: str, tools_dir: str = TOOLS_DIR):
    """Import an engine tool by path, for unit tests of its functions."""
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    spec = importlib.util.spec_from_file_location("engine_tool_" + name, os.path.join(tools_dir, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def frontmatter_block(text: str) -> str:
    """The text between the opening and closing ``---`` lines."""
    assert text.startswith("---\n"), "missing frontmatter"
    end = text.index("\n---\n", 4)
    return text[4:end]


def make_role(
    roles_dir: str,
    role_id: str,
    fields: Sequence[str],
    body: str = "# Role\n\nInstructions.\n",
    files: Optional[Dict[str, str]] = None,
    folder: Optional[str] = None,
) -> str:
    """Write ``roles_dir/<folder>/soul.md`` with the given frontmatter lines."""
    role_dir = os.path.join(roles_dir, folder or role_id)
    text = "---\n" + "\n".join(fields) + "\n---\n\n" + body
    write(os.path.join(role_dir, "soul.md"), text)
    for name, content in (files or {}).items():
        write(os.path.join(role_dir, name), content)
    return role_dir


def minimal_roles(roles_dir: str) -> None:
    """Two valid roles: a main role and one delegate."""
    make_role(roles_dir, "lead", [
        "id: lead",
        "description: Main role for tests.",
        "tier: high",
        "main: true",
        "allowed-tools: Agent(worker), Read, Write",
        "write-paths: [notes/**]",
    ])
    make_role(roles_dir, "worker", [
        "id: worker",
        "description: Delegate role for tests.",
        "tier: low",
        "allowed-tools: Read, Grep",
        "disallowed-tools: Bash",
        "isolation: worktree",
    ])


class TempDirTestCase(unittest.TestCase):
    """A test case with a fresh temporary directory in ``self.tmp``."""

    def setUp(self) -> None:
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="engine-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, *parts: str) -> str:
        return os.path.join(self.tmp, *parts)
