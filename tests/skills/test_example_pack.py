"""packs/example: its free graders pass a correct output and catch a wrong one, offline.

``tests/skills/fixtures/example-golden/<case>/expected/`` holds the output a
skill-following run should produce, and ``counter/`` a plausible no-skill
answer. Every regex and file_exists grader must pass the expected output, and
every case must have at least one free grader that fails the counter-example.
That proves the graders measure the skill's format before a single model call
is spent. The JavaScript regexes the graders use stay inside the syntax
Python's ``re`` reads the same way.
"""

import json
import re
import unittest
from typing import Dict, Optional

from .helpers import REPO

from skillslib.model import PluginInfo  # noqa: E402
from skillslib.summary import load_summary  # noqa: E402
from skillslib.yamlsubset import to_python  # noqa: E402

PACK = REPO / "packs" / "example"
GOLDEN = REPO / "tests" / "skills" / "fixtures" / "example-golden"
JS_FLAGS = {"m": re.MULTILINE, "i": re.IGNORECASE, "s": re.DOTALL}


def grade(grader: dict, files: Dict[str, str]) -> Optional[bool]:
    """Apply a free grader to the files a run produced. None for graders this can't run offline."""
    kind = grader["type"]
    if kind == "file_exists":
        found = any(re.fullmatch(_glob_to_regex(grader["path"]), name) for name in files)
        return found if grader.get("exists", True) else not found
    if kind != "regex":
        return None
    target = grader.get("target", "last_message")
    if not isinstance(target, dict):
        return None
    text = files.get(target["path"])
    if text is None:
        return False
    flags = 0
    for flag in grader.get("flags", ""):
        flags |= JS_FLAGS[flag]
    count = len(re.findall(grader["pattern"], text, flags))
    match = grader.get("match", "contains")
    if match == "contains":
        return count > 0
    if match == "not_contains":
        return count == 0
    return count == int(match.split(":", 1)[1])


def _glob_to_regex(glob: str) -> str:
    return "".join(".*" if ch == "*" else re.escape(ch) for ch in glob)


def outputs(case: str, kind: str) -> Dict[str, str]:
    directory = GOLDEN / case / kind
    return {p.name: p.read_text(encoding="utf-8") for p in directory.iterdir() if p.is_file()}


class ExamplePackGraderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plugin = PluginInfo(PACK)

    def test_six_cases_three_per_skill(self) -> None:
        self.assertEqual(sorted(s.folder for s in self.plugin.skills), ["changelog-entry", "csv-summary"])
        for skill in ("csv-summary", "changelog-entry"):
            self.assertEqual(len(self.plugin.cases_for(skill)), 3, skill)

    def test_every_case_has_a_golden_pair(self) -> None:
        self.assertEqual(sorted(c.name for c in self.plugin.cases), sorted(p.name for p in GOLDEN.iterdir()))

    def test_free_graders_pass_the_expected_output(self) -> None:
        for case in self.plugin.cases:
            files = outputs(case.name, "expected")
            for grader in case.graders:
                result = grade(to_python(grader.frontmatter), files)
                if result is not None:
                    with self.subTest(case=case.name, grader=grader.path.name):
                        self.assertTrue(result)

    def test_free_graders_catch_the_counter_example(self) -> None:
        for case in self.plugin.cases:
            files = outputs(case.name, "counter")
            results = [grade(to_python(g.frontmatter), files) for g in case.graders]
            with self.subTest(case=case.name):
                self.assertIn(False, results)

    def test_case_mix_matches_the_brief(self) -> None:
        for case in self.plugin.cases:
            types = [to_python(g.frontmatter)["type"] for g in case.graders]
            with self.subTest(case=case.name):
                self.assertEqual(types.count("llm"), 1)
                self.assertIn("regex", types)
                fired = [to_python(g.frontmatter) for g in case.graders if to_python(g.frontmatter).get("tool") == "Skill"]
                self.assertEqual(len(fired), 1)
                self.assertEqual(fired[0]["arm"], "with-only")
                context = to_python(case.case_yaml_root)["context"]
                self.assertEqual(context["scaffold_script"], "place-files.sh")
                self.assertTrue((case.dir / "files").is_dir())

    def test_file_exists_only_where_the_run_creates_the_file(self) -> None:
        # file_exists only sees files created during the run, never ones place-files.sh copied in.
        for case in self.plugin.cases:
            seeded = {p.name for p in (case.dir / "files").iterdir()}
            for grader in case.graders:
                values = to_python(grader.frontmatter)
                if values["type"] == "file_exists":
                    with self.subTest(case=case.name):
                        self.assertNotIn(values["path"], seeded)


class ExamplePackResultTests(unittest.TestCase):
    def test_committed_summaries_are_valid_and_cover_both_skills(self) -> None:
        paths = sorted((PACK / "evals" / "results").rglob("summary.json"))
        for path in paths:
            loaded = load_summary(path)
            with self.subTest(path=str(path)):
                self.assertEqual(loaded.errors, [])
                self.assertEqual(loaded.doc["suite"]["plugins"][0]["name"], "example-pack")
                self.assertEqual({c["skill"] for c in loaded.doc["cases"]}, {"csv-summary", "changelog-entry"})

    def test_no_raw_eval_output_is_committed(self) -> None:
        raw = [p for p in (PACK / "evals").rglob("*") if p.name in ("aggregate-result.json", "report.html")]
        self.assertEqual(raw, [])

    def test_manifest(self) -> None:
        manifest = json.loads((PACK / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "example-pack")
        self.assertEqual(manifest["license"], "Apache-2.0")
        self.assertNotIn("homepage", manifest)
        self.assertTrue((PACK / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()

