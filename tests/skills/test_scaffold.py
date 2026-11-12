"""skills.py new: the scaffold uses the portable frontmatter and the exact claude plugin eval layout."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .helpers import SKILLS_PY, TreeTestCase

from skillslib.lint import lint_roots  # noqa: E402
from skillslib.model import PluginInfo, parse_markdown, parse_yaml_file  # noqa: E402
from skillslib.scaffold import ScaffoldError, scaffold  # noqa: E402
from skillslib.yamlsubset import to_python  # noqa: E402

EXPECTED = [
    "packs/demo/skills/beta/SKILL.md",
    "packs/demo/evals/beta/beta-basic/prompt.md",
    "packs/demo/evals/beta/beta-basic/case.yaml",
    "packs/demo/evals/beta/beta-basic/place-files.sh",
    "packs/demo/evals/beta/beta-basic/graders/10-skill-fired.md",
    "packs/demo/evals/beta/beta-basic/graders/20-result.md",
]


class ScaffoldTests(TreeTestCase):
    def test_creates_the_skill_and_one_case(self) -> None:
        self.tree()
        created = scaffold(self.root, "demo", "beta")
        self.assertEqual([p.relative_to(self.root).as_posix() for p in created], EXPECTED)

    def test_frontmatter_is_portable_and_lints_with_placeholder_warnings_only(self) -> None:
        self.tree()
        scaffold(self.root, "demo", "beta", roles="builder, orchestrator", min_tier="high")
        skill = parse_markdown(self.root / EXPECTED[0])
        values = to_python(skill.frontmatter)
        self.assertEqual(list(values), ["name", "description", "license", "metadata"])
        self.assertEqual(values["name"], "beta")
        self.assertEqual(
            values["metadata"],
            {
                "version": "0.1.0",
                "status": "experimental",
                "pack": "demo",
                "min-tier": "high",
                "roles": "builder, orchestrator",
                "gate": "none",
            },
        )
        result = lint_roots(self.roots())
        self.assertEqual(result.errors, 0, [p.message for p in result.problems])
        self.assertEqual(sorted({p.code for p in result.problems}), ["EVL014", "SKL016"])

    def test_case_matches_the_claude_plugin_eval_layout(self) -> None:
        self.tree()
        scaffold(self.root, "demo", "beta")
        case_dir = self.root / "packs/demo/evals/beta/beta-basic"
        prompt = parse_markdown(case_dir / "prompt.md")
        self.assertEqual(
            to_python(prompt.frontmatter),
            {
                "tags": ["beta"],
                "max_turns": 10,
                "timeout_seconds": 300,
                "allowed_tools": ["Read", "Glob", "Grep", "Skill", "Write", "Edit"],
            },
        )
        self.assertTrue(prompt.body.strip())
        case = to_python(parse_yaml_file(case_dir / "case.yaml").root)
        self.assertEqual(case["schema_version"], "1.1")
        self.assertEqual(case["name"], "beta-basic")
        self.assertEqual(case["context"], {"scaffold_script": "place-files.sh"})
        fired = to_python(parse_markdown(case_dir / "graders/10-skill-fired.md").frontmatter)
        self.assertEqual(fired["type"], "tool_used")
        self.assertEqual(fired["tool"], "Skill")
        self.assertEqual(fired["arm"], "with-only")
        self.assertEqual(fired["input_match"], '"skill"\\s*:\\s*"(?:[\\w-]+:)?beta"')
        judge = parse_markdown(case_dir / "graders/20-result.md")
        self.assertEqual(to_python(judge.frontmatter), {"type": "llm"})
        self.assertIn("PASS if", judge.body)
        self.assertIn("FAIL if", judge.body)
        self.assertTrue(os.access(str(case_dir / "place-files.sh"), os.X_OK))
        plugin = PluginInfo(self.root / "packs/demo")
        self.assertEqual([c.name for c in plugin.cases_for("beta")], ["beta-basic"])

    @unittest.skipIf(shutil.which("bash") is None, "bash is not installed")
    def test_place_files_copies_inputs_into_the_workspace(self) -> None:
        self.tree()
        scaffold(self.root, "demo", "beta")
        case_dir = self.root / "packs/demo/evals/beta/beta-basic"
        (case_dir / "files" / "data").mkdir(parents=True)
        (case_dir / "files" / "input.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (case_dir / "files" / "data" / "nested.txt").write_text("x\n", encoding="utf-8")
        with tempfile.TemporaryDirectory() as workspace:
            subprocess.run(["bash", str(case_dir / "place-files.sh")], cwd=workspace, check=True)
            self.assertEqual((Path(workspace) / "input.csv").read_text(encoding="utf-8"), "a,b\n1,2\n")
            self.assertTrue((Path(workspace) / "data" / "nested.txt").is_file())
        shutil.rmtree(str(case_dir / "files"))
        with tempfile.TemporaryDirectory() as workspace:
            subprocess.run(["bash", str(case_dir / "place-files.sh")], cwd=workspace, check=True)
            self.assertEqual(os.listdir(workspace), [])

    def test_labs_flag(self) -> None:
        self.tree(
            {
                "labs/lab/.claude-plugin/plugin.json": '{"name": "lab-pack", "version": "0.1.0", "description": "l", "author": {"name": "Owner"}}',
                "labs/lab/README.md": "# lab\n",
            }
        )
        created = scaffold(self.root, "lab", "gamma", labs=True)
        self.assertEqual(created[0].relative_to(self.root).as_posix(), "labs/lab/skills/gamma/SKILL.md")
        self.assertEqual(lint_roots(self.roots()).errors, 0)

    def test_refusals(self) -> None:
        self.tree()
        refusals = {
            "uppercase": dict(pack="demo", skill="Beta"),
            "double hyphen": dict(pack="demo", skill="be--ta"),
            "reserved word": dict(pack="demo", skill="claude-helper"),
            "too long": dict(pack="demo", skill="b" * 65),
            "missing pack": dict(pack="nope", skill="beta"),
            "existing skill": dict(pack="demo", skill="alpha"),
            "bad tier": dict(pack="demo", skill="beta", min_tier="top"),
            "bad roles": dict(pack="demo", skill="beta", roles="Builder Person"),
        }
        for label, kwargs in refusals.items():
            with self.subTest(label):
                with self.assertRaises(ScaffoldError):
                    scaffold(self.root, **kwargs)
        self.assertFalse((self.root / "packs/demo/skills/beta").exists())

    def test_existing_skill_in_another_pack_is_refused(self) -> None:
        self.tree(
            {
                "packs/other/.claude-plugin/plugin.json": '{"name": "other-pack", "version": "1.0.0", "description": "o", "author": {"name": "Owner"}}',
                "packs/other/README.md": "# other\n",
            }
        )
        with self.assertRaises(ScaffoldError):
            scaffold(self.root, "other", "alpha")


class ScaffoldCliTests(TreeTestCase):
    def test_cli(self) -> None:
        self.tree()
        created = subprocess.run(
            [sys.executable, str(SKILLS_PY), "new", "demo", "beta", "--root", str(self.root)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertIn("created packs/demo/skills/beta/SKILL.md", created.stdout)
        again = subprocess.run(
            [sys.executable, str(SKILLS_PY), "new", "demo", "beta", "--root", str(self.root)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(again.returncode, 1)
        self.assertIn("already exists", again.stderr)
