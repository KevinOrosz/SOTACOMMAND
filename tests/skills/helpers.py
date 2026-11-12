"""Shared fixtures for the skills toolchain tests.

``make_tree`` writes a small, valid repository (one plugin, ``packs/demo``,
with one skill and one eval case) into a temporary directory and applies
per-test overrides, so each lint rule can be exercised by one small mutation.
"""

import copy
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Iterable, List, Optional

REPO = Path(__file__).resolve().parents[2]
TOOL_DIR = REPO / "tools" / "repo" / "skills"
SKILLS_PY = TOOL_DIR / "skills.py"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

PLACE_FILES = """\
#!/usr/bin/env bash
set -euo pipefail
case_dir="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$case_dir/files" ]; then
  cp -R "$case_dir/files/." .
fi
"""

MANIFEST = {
    "name": "demo-pack",
    "version": "1.0.0",
    "description": "A demo pack used by the skills toolchain tests.",
    "author": {"name": "Owner"},
    "license": "Apache-2.0",
}


def skill_md(
    name: str = "alpha",
    pack: str = "demo",
    status: str = "experimental",
    extra_meta: str = "",
    description: str = "Write a greeting to hello.txt. Use when the user asks for a greeting file.",
    body: str = "# Alpha\n\nWrite `hello.txt` containing `hello`.\n",
) -> str:
    return (
        "---\n"
        "name: {name}\n"
        "description: {description}\n"
        "license: Apache-2.0\n"
        "metadata:\n"
        '  version: "1.0.0"\n'
        "  status: {status}\n"
        "  pack: {pack}\n"
        "  min-tier: low\n"
        "  roles: builder\n"
        "  gate: none\n"
        "{extra_meta}"
        "---\n\n{body}"
    ).format(name=name, description=description, status=status, pack=pack, extra_meta=extra_meta, body=body)


def prompt_md(skill: str = "alpha", body: str = "Please create a greeting file.", extra: str = "") -> str:
    return (
        "---\n"
        "tags: [{skill}]\n"
        "max_turns: 5\n"
        "timeout_seconds: 120\n"
        "allowed_tools: [Read, Write, Skill]\n"
        "{extra}"
        "---\n\n{body}\n"
    ).format(skill=skill, extra=extra, body=body)


def case_yaml(name: str = "alpha-basic", schema: str = '"1.1"', script: str = "place-files.sh") -> str:
    return (
        "schema_version: {schema}\n"
        "name: {name}\n"
        'expected_outcome: "hello.txt holds a greeting"\n'
        "context:\n"
        "  scaffold_script: {script}\n"
    ).format(schema=schema, name=name, script=script)


def skill_fired(skill: str = "alpha") -> str:
    return (
        "---\n"
        "type: tool_used\n"
        "tool: Skill\n"
        "input_match: '\"skill\"\\s*:\\s*\"(?:[\\w-]+:)?" + skill + "\"'\n"
        "arm: with-only\n"
        "---\n"
    )


FILE_EXISTS = "---\ntype: file_exists\npath: hello.txt\n---\n"
JUDGE = (
    "---\ntype: llm\nfocus:\n  source: file\n  path: hello.txt\n---\n\n"
    "PASS if hello.txt contains a greeting.\nFAIL if hello.txt is missing or empty.\n"
)


def case_files(skill: str = "alpha", case: str = "alpha-basic", pack: str = "demo") -> Dict[str, str]:
    base = "packs/{}/evals/{}/{}/".format(pack, skill, case)
    return {
        base + "prompt.md": prompt_md(skill),
        base + "case.yaml": case_yaml(case),
        base + "place-files.sh": PLACE_FILES,
        base + "files/input.txt": "hello\n",
        base + "graders/10-skill-fired.md": skill_fired(skill),
        base + "graders/20-output.md": FILE_EXISTS,
        base + "graders/30-judge.md": JUDGE,
    }


def base_files() -> Dict[str, str]:
    files = {
        "packs/demo/.claude-plugin/plugin.json": json.dumps(MANIFEST, indent=2) + "\n",
        "packs/demo/README.md": "# demo-pack\n\nA demo pack.\n",
        "packs/demo/skills/alpha/SKILL.md": skill_md(),
        "labs/README.md": "# labs\n",
    }
    files.update(case_files())
    return files


def summary_doc(
    cases: Iterable[tuple] = (),
    started_at: str = "2026-11-01T10:00:00.000Z",
    partial: bool = False,
    ablation: str = "with-without",
    plugin: str = "demo-pack",
    model_pinned: bool = True,
) -> dict:
    """A valid redacted summary. Each case is (name, skill, score, delta, runs_with, runs_without)."""
    out_cases = []
    for name, skill, score, delta, runs_with, runs_without in cases:
        aggregates = {"score": score, "passRate": 1.0 if score >= 1 else 0.0}
        arms = {"with": [_run(score) for _ in range(runs_with)]}
        if ablation == "with-without":
            without = round(score - delta, 6)
            aggregates.update({"scoreWithout": without, "passRateWithout": 0.0, "delta": delta})
            arms["without"] = [_run(without) for _ in range(runs_without)]
        out_cases.append(
            {"name": name, "skill": skill, "runsPerCase": max(runs_with, 1), "aggregates": aggregates, "arms": arms}
        )
    deltas = [c["aggregates"]["delta"] for c in out_cases if "delta" in c["aggregates"]]
    scores = [c["aggregates"]["score"] for c in out_cases]
    doc = {
        "summaryFormat": 1,
        "schemaVersion": 1,
        "claudeVersion": "2.1.318",
        "startedAt": started_at,
        "date": started_at[:10],
        "durationSeconds": 120,
        "costUsd": 0.5,
        "partial": partial,
    }
    if partial:
        doc["partialReason"] = "cost_ceiling"
    doc["suite"] = {
        "ablation": ablation,
        "threshold": 0.8,
        "modelPinned": model_pinned,
        "judgeModelPinned": model_pinned,
        "plugins": [{"name": plugin, "version": "1.0.0"}],
    }
    doc["cases"] = out_cases
    doc["aggregates"] = {
        "casesTotal": len(out_cases),
        "casesPassed": sum(1 for s in scores if s >= 0.8),
        "overallScore": sum(scores) / len(scores) if scores else 0,
        "overallPassRate": 0.5,
    }
    if deltas:
        doc["aggregates"]["meanDelta"] = sum(deltas) / len(deltas)
    return doc


def _run(score: float) -> dict:
    return {"score": score, "passed": score >= 1, "errored": False, "skippedPaidGraders": False}


def stable_files(
    delta: float = 0.3, score: float = 0.9, runs: int = 3, cases: int = 3, model_pinned: bool = True
) -> Dict[str, str]:
    """A tree whose skill ``alpha`` is stable, with ``cases`` eval cases and a qualifying summary."""
    files = base_files()
    files["packs/demo/skills/alpha/SKILL.md"] = skill_md(status="stable")
    names = ["alpha-basic", "alpha-two", "alpha-three"][:cases]
    for name in names[1:]:
        files.update(case_files(case=name))
    doc = summary_doc(
        [(n, "alpha", score, delta, runs, runs) for n in ["alpha-basic", "alpha-two", "alpha-three"]],
        model_pinned=model_pinned,
    )
    files["packs/demo/evals/results/2026-11-01/summary.json"] = json.dumps(doc, indent=2) + "\n"
    return files


def write_tree(root: Path, files: Dict[str, Optional[str]]) -> None:
    for rel, content in files.items():
        path = root / rel
        if content is None:
            if path.is_dir():
                shutil.rmtree(str(path))
            elif path.exists():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        if path.name.endswith(".sh"):
            os.chmod(str(path), os.stat(str(path)).st_mode | stat.S_IXUSR)


class TreeTestCase(unittest.TestCase):
    """A test case with a fresh temporary directory per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def tree(self, overrides: Optional[Dict[str, Optional[str]]] = None, base: Optional[Dict[str, str]] = None) -> Path:
        files = copy.deepcopy(base if base is not None else base_files())  # type: Dict[str, Optional[str]]
        write_tree(self.root, files)
        if overrides:
            write_tree(self.root, overrides)
        return self.root

    def roots(self) -> List[Path]:
        return [self.root / "packs", self.root / "labs"]
