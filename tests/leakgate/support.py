"""Shared helpers for the leak-gate tests.

Leak-shaped strings never appear in these test sources: they come from
``fixtures/leak-corpus/`` (where synthetic private-looking material is allowed),
or they are invented terms that only a test-local config treats as private.
That keeps ``leakgate check tests/leakgate`` clean, which test_self asserts.
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Iterator, List, Mapping, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL_DIR = os.path.join(REPO_ROOT, "tools", "repo", "leakgate")
TOOL = os.path.join(TOOL_DIR, "leakgate.py")
RULES = os.path.join(TOOL_DIR, "rules.json")
CORPUS = os.path.join(REPO_ROOT, "fixtures", "leak-corpus")
PLANTED = os.path.join(CORPUS, "planted")
CLEAN = os.path.join(CORPUS, "clean")
BENCH_CONFIG = os.path.join(CORPUS, "bench.json")
SAMPLES = os.path.join(CORPUS, "samples", "rule-samples.json")

if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)

import leakgate  # noqa: E402  (imported after the path tweak on purpose)

#: Environment variables that would make a run non-hermetic.
_LEAKGATE_VARS = ("LEAKGATE_DENYLIST", "LEAKGATE_SALT", "LEAKGATE_DEBUG")


def hermetic_env(extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _LEAKGATE_VARS}
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def run_cli(*args: str, cwd: str = REPO_ROOT, env: Optional[Mapping[str, str]] = None) -> subprocess.CompletedProcess:
    """Run ``leakgate.py`` with this interpreter and a hermetic environment."""
    return subprocess.run(
        [sys.executable, TOOL] + list(args),
        cwd=cwd,
        env=hermetic_env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        check=False,
    )


def run_json(*args: str, **kwargs) -> dict:
    completed = run_cli(*args, **kwargs)
    try:
        document = json.loads(completed.stdout)
    except ValueError:
        raise AssertionError("not JSON (exit %d):\n%s\n%s" % (completed.returncode, completed.stdout, completed.stderr))
    document["_returncode"] = completed.returncode
    return document


def load_samples() -> Dict[str, Dict[str, List[str]]]:
    """The rule samples, with sealed tokens unsealed (see the bench module's docstring)."""
    with open(SAMPLES, encoding="utf-8") as handle:
        return json.loads(handle.read().replace("{{seal}}", ""))["rules"]


def production_config(**kwargs) -> "leakgate.Config":
    """The shipped rules with an empty environment (no CI secrets)."""
    kwargs.setdefault("env", {})
    return leakgate.load_config(RULES, **kwargs)


def bench_config() -> "leakgate.Config":
    return leakgate.load_config(BENCH_CONFIG, bench=True)


def write_files(root: str, files: Mapping[str, object]) -> None:
    """Write ``{relpath: str | bytes}`` under ``root``."""
    for relpath, content in files.items():
        full = os.path.join(root, *relpath.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8", "newline": ""}
        with open(full, mode, **kwargs) as handle:
            handle.write(content)


def write_json(path: str, data: object) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    return path


def write_rules(directory: str, name: str = "rules.json", **sections: object) -> str:
    """Write a rules file. Pass ``extends=RULES`` to layer on the real rules."""
    data = {"version": 1}
    data.update(sections)
    return write_json(os.path.join(directory, name), data)


@contextlib.contextmanager
def temp_dir() -> Iterator[str]:
    path = tempfile.mkdtemp(prefix="leakgate-test-")
    try:
        yield os.path.realpath(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def git_available() -> bool:
    return shutil.which("git") is not None


def _git(root: str, *args: str) -> None:
    env = hermetic_env({"GIT_CONFIG_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0"})
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    subprocess.run(["git", "-c", "init.defaultBranch=main"] + list(args), cwd=root, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def init_git_repo(root: str, tracked: List[str], gitignore: str = "") -> None:
    """Make a throwaway repository inside a temp dir and stage ``tracked``.

    Only ever called on directories the test created; it never touches the
    repository under test.
    """
    assert root.startswith(os.path.realpath(tempfile.gettempdir())), root
    if gitignore:
        write_files(root, {".gitignore": gitignore})
    _git(root, "init", "-q")
    if tracked:
        _git(root, "add", "--", *tracked)


def lone_sample(rule_id: str, config: "leakgate.Config") -> str:
    """A flag sample that yields exactly one finding, of ``rule_id``, under ``config``."""
    for sample in load_samples()[rule_id]["flag"]:
        findings = leakgate.scan_text(sample, config)
        if [f.rule_id for f in findings] == [rule_id]:
            return sample
    raise AssertionError("no lone %s sample for this config" % rule_id)


def finding_keys(findings) -> List[tuple]:
    return [(f.path, f.line, f.rule_id) for f in findings]
