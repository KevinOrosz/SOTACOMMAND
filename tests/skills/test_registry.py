"""skills.py registry: generation from frontmatter and summaries, and the --check drift gate."""

import json
import subprocess
import sys

from .helpers import REPO, SKILLS_PY, TreeTestCase, case_files, skill_md, summary_doc

from skillslib import registry  # noqa: E402

RESULTS = "packs/demo/evals/results/"


def rows(text: str) -> list:
    return [line for line in text.splitlines() if line.startswith("| [")]


class RegistryTests(TreeTestCase):
    def generate(self) -> str:
        _, text = registry.generate(self.root)
        return text

    def test_rows_come_from_frontmatter(self) -> None:
        self.tree(
            {
                "packs/demo/skills/beta/SKILL.md": skill_md(name="beta", status="retired", extra_meta="  replaces: alpha\n"),
            }
        )
        lines = rows(self.generate())
        self.assertEqual(
            lines,
            [
                "| [alpha](packs/demo/skills/alpha/SKILL.md) | demo | experimental | low | builder | none | — | — | — | — |",
                "| [beta](packs/demo/skills/beta/SKILL.md) | demo | retired | low | builder | none | — | — | — | — |",
            ],
        )
        self.assertIn("2 skill(s): 0 stable, 1 experimental, 1 retired.", self.generate())

    def test_scores_come_from_the_newest_complete_summary(self) -> None:
        older = summary_doc([("alpha-basic", "alpha", 0.5, 0.1, 3, 3)], started_at="2026-10-01T09:00:00.000Z")
        newer = summary_doc([("alpha-basic", "alpha", 0.9, 0.35, 3, 3)], started_at="2026-11-02T09:00:00.000Z")
        partial = summary_doc(
            [("alpha-basic", "alpha", 0.2, -0.5, 1, 1)], started_at="2026-11-04T09:00:00.000Z", partial=True
        )
        self.tree(
            {
                RESULTS + "2026-10-01/summary.json": json.dumps(older),
                RESULTS + "2026-11-02/summary.json": json.dumps(newer),
                RESULTS + "2026-11-04/summary.json": json.dumps(partial),
            }
        )
        line = rows(self.generate())[0]
        self.assertIn("| 0.90 | +0.35 | 3 | [2026-11-02](packs/demo/evals/results/2026-11-02/summary.json) |", line)

    def test_partial_and_one_arm_results_are_marked(self) -> None:
        partial = summary_doc([("alpha-basic", "alpha", 0.75, 0.25, 2, 2)], partial=True)
        self.tree({RESULTS + "p/summary.json": json.dumps(partial)})
        self.assertIn("| 0.75 | +0.25 | 2 | [2026-11-01](packs/demo/evals/results/p/summary.json) (partial) |", rows(self.generate())[0])
        one_arm = summary_doc([("alpha-basic", "alpha", 1.0, 0.0, 3, 0)], ablation="none")
        self.tree({RESULTS + "p/summary.json": None, RESULTS + "q/summary.json": json.dumps(one_arm)})
        self.assertIn("| 1.00 | — | 3 | [2026-11-01](packs/demo/evals/results/q/summary.json) (no baseline) |", rows(self.generate())[0])

    def test_skill_score_is_the_mean_over_its_cases(self) -> None:
        overrides = dict(case_files(case="alpha-two"))
        doc = summary_doc([("alpha-basic", "alpha", 1.0, 0.6, 3, 3), ("alpha-two", "alpha", 0.5, 0.0, 3, 2)])
        overrides[RESULTS + "r/summary.json"] = json.dumps(doc)
        self.tree(overrides)
        self.assertIn("| 0.75 | +0.30 | 3 |", rows(self.generate())[0])

    def test_output_is_deterministic_and_sorted(self) -> None:
        self.tree(
            {
                "packs/zeta/.claude-plugin/plugin.json": json.dumps(
                    {"name": "zeta-pack", "version": "1.0.0", "description": "z", "author": {"name": "Owner"}}
                ),
                "packs/zeta/README.md": "# zeta\n",
                "packs/zeta/skills/aaa/SKILL.md": skill_md(name="aaa", pack="zeta"),
                "labs/lab/.claude-plugin/plugin.json": json.dumps(
                    {"name": "lab-pack", "version": "0.1.0", "description": "l", "author": {"name": "Owner"}}
                ),
                "labs/lab/README.md": "# lab\n",
                "labs/lab/skills/mmm/SKILL.md": skill_md(name="mmm", pack="lab"),
            }
        )
        first, second = self.generate(), self.generate()
        self.assertEqual(first, second)
        self.assertEqual([line.split("]")[0] for line in rows(first)], ["| [alpha", "| [mmm", "| [aaa"])
        self.assertIn("(labs/lab/skills/mmm/SKILL.md)", first)

    def test_empty_tree_renders_a_placeholder_row(self) -> None:
        self.root.joinpath("packs").mkdir()
        text = self.generate()
        self.assertIn("| — | — | — | — | — | — | — | — | — | — |", text)
        self.assertIn("0 skill(s)", text)

    def test_unparseable_skill_stops_generation(self) -> None:
        self.tree({"packs/demo/skills/alpha/SKILL.md": "# no frontmatter\n"})
        with self.assertRaises(registry.RegistryError):
            self.generate()

    def test_links_follow_the_output_location(self) -> None:
        self.tree()
        _, text = registry.generate(self.root, self.root / "docs" / "REGISTRY.md")
        self.assertIn("(../packs/demo/skills/alpha/SKILL.md)", text)
        self.assertIn("(../tools/repo/skills/CONTRACT.md)", text)


class RegistryCliTests(TreeTestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SKILLS_PY), "registry", "--root", str(self.root)] + list(args),
            capture_output=True,
            text=True,
        )

    def test_check_fails_when_missing_or_stale_and_passes_after_writing(self) -> None:
        self.tree()
        missing = self.run_cli("--check")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("is missing", missing.stderr)

        written = self.run_cli()
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertTrue((self.root / "REGISTRY.md").is_file())
        self.assertEqual(self.run_cli("--check").returncode, 0)

        self.tree({"packs/demo/skills/alpha/SKILL.md": skill_md(status="retired", extra_meta="  replaces: alpha\n")})
        stale = self.run_cli("--check")
        self.assertEqual(stale.returncode, 1)
        self.assertIn("is stale", stale.stderr)
        self.assertIn("-| [alpha]", stale.stderr)
        self.assertIn("+| [alpha]", stale.stderr)

        hand_edit = (self.root / "REGISTRY.md").read_text(encoding="utf-8") + "extra line\n"
        self.assertEqual(self.run_cli().returncode, 0)
        (self.root / "REGISTRY.md").write_text(hand_edit, encoding="utf-8")
        self.assertEqual(self.run_cli("--check").returncode, 1)

    def test_committed_registry_is_current(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SKILLS_PY), "registry", "--check"], cwd=str(REPO), capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
