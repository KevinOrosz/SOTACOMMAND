"""Shared helpers for the site tests: load build.py and make throwaway repo roots."""

import copy
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, Optional

REPO = Path(__file__).resolve().parents[2]
BUILD_PY = REPO / "tools" / "repo" / "site" / "build.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE_PATH = FIXTURES / "summary.sample.json"
SAMPLE_REL = "packs/sample-pack/evals/results/2026-11-01/summary.json"
SAMPLE_SKILLS = ("inbox-triage", "release-notes", "report-digest")

_spec = importlib.util.spec_from_file_location("site_build", str(BUILD_PY))
build = importlib.util.module_from_spec(_spec)
sys.modules["site_build"] = build  # dataclasses look their module up by name
_spec.loader.exec_module(build)


def load_sample() -> dict:
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


def sample_without_skill_fields() -> dict:
    data = load_sample()
    for case in data["cases"]:
        case.pop("skill", None)
    return data


def make_root(
    root: Path,
    summaries: Optional[Dict[str, dict]] = None,
    skills: Optional[Dict[str, Iterable[str]]] = None,
) -> Path:
    """Copy the real site sources and README into `root`, then add test inputs.

    summaries maps a repo-relative path to summary data; skills maps a pack
    folder such as "packs/sample-pack" to the skill folders to create.
    """
    shutil.copytree(
        str(REPO / "site"),
        str(root / "site"),
        ignore=shutil.ignore_patterns("evals", "llms.txt", "llms-full.txt"),
    )
    shutil.copy2(str(REPO / "README.md"), str(root / "README.md"))
    for pack, names in (skills or {}).items():
        for name in names:
            folder = root / pack / "skills" / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text("---\nname: {}\ndescription: test\n---\n".format(name), encoding="utf-8")
    for rel, data in (summaries or {}).items():
        write_json(root / rel, data)
    return root


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sample_root(root: Path, data: Optional[dict] = None) -> Path:
    return make_root(
        root,
        summaries={SAMPLE_REL: copy.deepcopy(data) if data is not None else load_sample()},
        skills={"packs/sample-pack": SAMPLE_SKILLS},
    )


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BUILD_PY)] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
    )


def element(html: str, anchor: str) -> str:
    """Return the <tr> with the given id."""
    match = re.search(r'<tr id="{}"[^>]*>.*?</tr>'.format(re.escape(anchor)), html, re.DOTALL)
    if not match:
        raise AssertionError("no row with id {}".format(anchor))
    return match.group(0)
