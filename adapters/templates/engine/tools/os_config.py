"""Shared helpers for the engine's hook tools.

Source: adapters/templates/engine/tools/os_config.py, copied into
engine/tools/ by adapters/generate.py. Edit the template, then regenerate.

The tools read the documented Claude Code hook input from stdin, find the
project root, and read the owner's ``os.config.json`` there. Standard library
only, Python 3.9+, no network access.
"""

import json
import os
import sys
from typing import Any, Dict, Optional, TextIO, Tuple

CONFIG_NAME = "os.config.json"


class ConfigError(Exception):
    """os.config.json exists but cannot be used."""


class InputError(Exception):
    """The hook input on stdin is not a JSON object."""


def read_hook_input(stream: Optional[TextIO] = None) -> Dict[str, Any]:
    """Read the hook's JSON input. Empty input (a manual run) gives ``{}``."""
    data = (stream or sys.stdin).read()
    if not data.strip():
        return {}
    try:
        value = json.loads(data)
    except ValueError as exc:
        raise InputError("hook input is not valid JSON: %s" % exc)
    if not isinstance(value, dict):
        raise InputError("hook input must be a JSON object")
    return value


def project_root(hook_input: Dict[str, Any], override: Optional[str] = None) -> str:
    """The directory that holds the project's os.config.json.

    Order: ``--root``, then ``CLAUDE_PROJECT_DIR`` (set for every Claude Code
    hook), then the nearest ancestor of the input ``cwd`` (or the process cwd)
    that contains os.config.json, then that cwd itself.
    """
    if override:
        return os.path.abspath(override)
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return os.path.abspath(env_root)
    start = hook_input.get("cwd")
    if not isinstance(start, str) or not start:
        start = os.getcwd()
    start = os.path.abspath(start)
    current = start
    while True:
        if os.path.isfile(os.path.join(current, CONFIG_NAME)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return start
        current = parent


def load_config(root: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return ``(config, path)``. ``config`` is None when the file is absent.

    Raises ConfigError when the file exists but is not a JSON object.
    """
    path = os.path.join(root, CONFIG_NAME)
    if not os.path.isfile(path):
        return None, path
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError("%s is not valid JSON: %s" % (CONFIG_NAME, exc))
    if not isinstance(value, dict):
        raise ConfigError("%s must hold a JSON object" % CONFIG_NAME)
    return value, path


def second_brain(config: Optional[Dict[str, Any]], root: str) -> Optional[Dict[str, Any]]:
    """The optional ``second_brain`` block: ``{"root": abs path, "lint_on_close": bool}``.

    Returns None when the key is absent. Raises ConfigError when it is malformed.
    ``path`` is the SOTASECONDBRAIN instance root (the folder that holds
    ``bin/kb`` and ``KB/``), absolute or relative to the project root.
    """
    if not config or config.get("second_brain") is None:
        return None
    block = config["second_brain"]
    if not isinstance(block, dict):
        raise ConfigError("second_brain must be an object")
    path = block.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ConfigError("second_brain.path must be a non-empty path to the second brain's folder")
    lint_on_close = block.get("lint_on_close", False)
    if not isinstance(lint_on_close, bool):
        raise ConfigError("second_brain.lint_on_close must be true or false")
    resolved = os.path.normpath(os.path.join(root, os.path.expanduser(path)))
    return {"root": resolved, "path": path, "lint_on_close": lint_on_close}


def plugin_root() -> str:
    """The engine plugin directory (the parent of this tools/ folder)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def emit_json(payload: Dict[str, Any], stream: Optional[TextIO] = None) -> None:
    """Print one JSON object and nothing else, as hook output requires."""
    out = stream or sys.stdout
    out.write(json.dumps(payload, ensure_ascii=False))
    out.write("\n")
    out.flush()
