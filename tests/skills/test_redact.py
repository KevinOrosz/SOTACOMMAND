"""skills.py redact-results: only allowlisted fields survive, and no model identifier does.

The fixture mirrors the shape of a real ``aggregate-result.json`` from
``claude plugin eval`` (schemaVersion 1). Its model identifiers are fake
placeholders, so no real model name is committed.
"""

import copy
import json
import subprocess
import sys

from .helpers import SKILLS_PY, TreeTestCase

from skillslib.redact import RedactError, markdown_table, redact, redact_file, skill_from_case_dir  # noqa: E402
from skillslib.summary import validate_summary  # noqa: E402

AGENT_MODEL = "fake-agent-model-1234"
JUDGE_MODEL = "fake-judge-model-5678"
CASE_MODEL = "fake-case-model-9012"
SECRET_PATH = "/srv/ci-runner/private-checkout"


def grader_result(name: str, passed: bool) -> dict:
    return {
        "name": name,
        "passed": passed,
        "weight": 1,
        "explanation": "judged by {} in {}".format(JUDGE_MODEL, SECRET_PATH),
        "withOnly": False,
        "scored": True,
        "judgeVotes": [True, True, passed],
        "evidence": "file contents written under " + SECRET_PATH,
    }


def run(score: float, error=None, aborted=None) -> dict:
    out = {
        "score": score,
        "passed": score >= 1,
        "turns": 6,
        "costUsd": 0.04,
        "judgeCostUsd": 0.0002,
        "durationSeconds": 12,
        "startedAt": "2026-11-05T02:29:10.000Z",
        "error": error,
        "tracePath": "/tmp/claude-eval-abc/out/trace.jsonl",
        "skippedPaidGraders": False,
        "graders": [grader_result("20-output", score >= 1)],
    }
    if aborted:
        out["aborted"] = aborted
    return out


def aggregate() -> dict:
    return {
        "schemaVersion": 1,
        "claudeVersion": "2.1.318",
        "startedAt": "2026-11-05T02:29:08.012Z",
        "durationSeconds": 159,
        "costUsd": 1.4078061,
        "partial": False,
        "suite": {
            "root": SECRET_PATH + "/packs/demo",
            "ablation": "with-without",
            "modelOverride": AGENT_MODEL,
            "judgeModel": JUDGE_MODEL,
            "caseFilter": "alpha-*",
            "threshold": 0.8,
            "concurrency": 3,
            "plugins": [{"name": "demo-pack", "version": "1.0.0", "path": SECRET_PATH + "/packs/demo"}],
        },
        "cases": [
            {
                "name": "alpha-basic",
                "dir": "evals/alpha/alpha-basic",
                "source": "mixed",
                "promptMarkdown": "Please create a greeting file.",
                "model": CASE_MODEL,
                "runsPerCase": 3,
                "timeoutSeconds": 120,
                "maxTurns": 5,
                "graders": [{"name": "20-output", "type": "file_exists", "weight": 1, "config": {"path": "hello.txt"}}],
                "arms": {"with": [run(1), run(1), run(1)], "without": [run(0.5), run(0.5, error="timed out after 120s"), run(0.0, aborted={"server": "s", "tool": "t", "reason": "r"})]},
                "advisories": ["advice mentioning " + AGENT_MODEL],
                "aggregates": {"score": 1, "passRate": 1, "scoreWithout": 0.3333, "passRateWithout": 0, "delta": 0.6667},
            },
            {
                "name": "beta-basic",
                "dir": SECRET_PATH + "/packs/demo/evals/beta/beta-basic",
                "source": "prompt.md",
                "promptMarkdown": "Another prompt.",
                "runsPerCase": 3,
                "timeoutSeconds": 120,
                "maxTurns": 5,
                "graders": [],
                "arms": {"with": [run(0.5)]},
                "aggregates": {"score": 0.5, "passRate": 0},
            },
        ],
        "aggregates": {"casesTotal": 2, "casesPassed": 1, "overallScore": 0.75, "overallPassRate": 0.5, "meanDelta": 0.6667},
    }


def all_strings(node) -> list:
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in [k] + all_strings(v)]
    if isinstance(node, list):
        return [s for item in node for s in all_strings(item)]
    return [node] if isinstance(node, str) else []


class RedactTests(TreeTestCase):
    def test_no_model_identifier_path_or_transcript_survives(self) -> None:
        summary = redact(aggregate())
        text = json.dumps(summary)
        for forbidden in (AGENT_MODEL, JUDGE_MODEL, CASE_MODEL, SECRET_PATH, "trace.jsonl", "/tmp/", "Please create",
                          "timed out", "judged by", "evidence", "explanation", "promptMarkdown", "tracePath",
                          "modelOverride", "judgeModel\"", "advisories", "caseFilter", "root"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_output_is_valid_and_keeps_the_numbers(self) -> None:
        summary = redact(aggregate())
        self.assertEqual(validate_summary(summary), [])
        self.assertEqual(summary["schemaVersion"], 1)
        self.assertEqual(summary["claudeVersion"], "2.1.318")
        self.assertEqual(summary["date"], "2026-11-05")
        self.assertEqual((summary["costUsd"], summary["durationSeconds"]), (1.4078061, 159))
        self.assertEqual(
            summary["aggregates"],
            {"casesTotal": 2, "casesPassed": 1, "overallScore": 0.75, "overallPassRate": 0.5, "meanDelta": 0.6667},
        )
        first, second = summary["cases"]
        self.assertEqual(first["name"], "alpha-basic")
        self.assertEqual(first["aggregates"]["score"], 1)
        self.assertEqual(first["aggregates"]["delta"], 0.6667)
        self.assertNotIn("delta", second["aggregates"])
        self.assertEqual(len(first["arms"]["with"]), 3)
        self.assertEqual([r["errored"] for r in first["arms"]["without"]], [False, True, True])
        self.assertEqual(
            summary["suite"],
            {
                "ablation": "with-without",
                "threshold": 0.8,
                "modelPinned": True,
                "judgeModelPinned": True,
                "plugins": [{"name": "demo-pack", "version": "1.0.0"}],
            },
        )

    def test_only_allowlisted_keys_appear(self) -> None:
        summary = redact(aggregate())
        allowed = {
            "summaryFormat", "schemaVersion", "claudeVersion", "startedAt", "date", "durationSeconds", "costUsd",
            "partial", "partialReason", "suite", "ablation", "threshold", "modelPinned", "judgeModelPinned",
            "plugins", "name", "version", "cases", "skill", "runsPerCase", "aggregates", "score", "passRate",
            "scoreWithout", "passRateWithout", "delta", "arms", "with", "without", "passed", "errored",
            "skippedPaidGraders", "casesTotal", "casesPassed", "overallScore", "overallPassRate", "meanDelta",
        }

        def keys(node):
            if isinstance(node, dict):
                return set(node) | set().union(*(keys(v) for v in node.values()))
            if isinstance(node, list):
                return set().union(*(keys(v) for v in node)) if node else set()
            return set()

        self.assertLessEqual(keys(summary), allowed)

    def test_skill_attribution_comes_from_the_case_directory(self) -> None:
        summary = redact(aggregate())
        self.assertEqual([c["skill"] for c in summary["cases"]], ["alpha", "beta"])
        self.assertEqual(skill_from_case_dir("evals/csv-summary/csv-sales-totals"), "csv-summary")
        self.assertEqual(skill_from_case_dir("C:\\work\\packs\\x\\evals\\csv-summary\\case-a"), "csv-summary")
        self.assertIsNone(skill_from_case_dir("evals/loose-case"))
        self.assertIsNone(skill_from_case_dir("."))
        self.assertIsNone(skill_from_case_dir(None))
        self.assertIsNone(skill_from_case_dir("evals/Not_A_Skill/case"))

    def test_plugin_dir_attributes_cases_the_path_cannot(self) -> None:
        self.tree()
        source = aggregate()
        source["cases"][0]["dir"] = "."
        self.assertIsNone(redact(source)["cases"][0]["skill"])
        summary = redact(source, plugin_dir=self.root / "packs" / "demo")
        self.assertEqual(summary["cases"][0]["skill"], "alpha")

    def test_unpinned_runs_and_partial_runs(self) -> None:
        source = aggregate()
        del source["suite"]["modelOverride"]
        del source["suite"]["judgeModel"]
        del source["cases"][0]["model"]
        source["partial"] = True
        source["partialReason"] = "cost_ceiling"
        summary = redact(source)
        self.assertEqual((summary["suite"]["modelPinned"], summary["suite"]["judgeModelPinned"]), (False, False))
        self.assertEqual((summary["partial"], summary["partialReason"]), (True, "cost_ceiling"))
        source["partialReason"] = "a-reason-from-the-future"
        self.assertNotIn("partialReason", redact(source))

    def test_date_override_and_validation(self) -> None:
        self.assertEqual(redact(aggregate(), date="2026-01-02")["date"], "2026-01-02")
        with self.assertRaises(RedactError):
            redact(aggregate(), date="Jan 2")

    def test_refuses_when_a_model_value_would_survive(self) -> None:
        source = aggregate()
        source["suite"]["modelOverride"] = "alpha-basic"  # the same text as a case name, which is kept
        with self.assertRaises(RedactError) as caught:
            redact(source)
        self.assertIn("model field", str(caught.exception))

    def test_refuses_input_that_isnt_a_result_document(self) -> None:
        broken = []
        for mutate in (
            lambda d: d.pop("cases"),
            lambda d: d.pop("aggregates"),
            lambda d: d["suite"].pop("ablation"),
            lambda d: d.__setitem__("schemaVersion", "one"),
            lambda d: d["cases"][0]["aggregates"].__setitem__("score", "high"),
            lambda d: d["cases"][0].__setitem__("name", "../escape"),
            lambda d: d["suite"].__setitem__("ablation", "sometimes"),
        ):
            source = copy.deepcopy(aggregate())
            mutate(source)
            try:
                redact(source)
            except RedactError:
                continue
            broken.append(source)
        self.assertEqual(broken, [])
        with self.assertRaises(RedactError):
            redact([1, 2, 3])

    def test_markdown_table(self) -> None:
        table = markdown_table(redact(aggregate()))
        self.assertIn("| alpha-basic | alpha | 1.00 | 0.33 | +0.67 | 6 |", table)
        self.assertIn("| beta-basic | beta | 0.50 | — | — | 1 |", table)
        self.assertIn("demo-pack: score 0.75 · mean Δ +0.67 · 1/2 cases passed", table)


class RedactCliTests(TreeTestCase):
    def test_cli_writes_the_summary_and_never_touches_the_input(self) -> None:
        self.tree()
        source = self.root / "aggregate-result.json"
        source.write_text(json.dumps(aggregate()), encoding="utf-8")
        before = source.read_text(encoding="utf-8")
        out = self.root / "packs/demo/evals/results/2026-11-05/summary.json"
        result = subprocess.run(
            [sys.executable, str(SKILLS_PY), "redact-results", str(source), "--out", str(out)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("| alpha-basic |", result.stdout)
        self.assertEqual(source.read_text(encoding="utf-8"), before)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(validate_summary(written), [])
        for value in all_strings(written):
            for model in (AGENT_MODEL, JUDGE_MODEL, CASE_MODEL):
                self.assertNotIn(model, value)

    def test_cli_refuses_to_overwrite_the_input_and_bad_json(self) -> None:
        self.tree()
        source = self.root / "aggregate-result.json"
        source.write_text(json.dumps(aggregate()), encoding="utf-8")
        same = subprocess.run(
            [sys.executable, str(SKILLS_PY), "redact-results", str(source), "--out", str(source)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(same.returncode, 1)
        self.assertIn("--out must differ", same.stderr)
        bad = self.root / "bad.json"
        bad.write_text("{nope", encoding="utf-8")
        with self.assertRaises(RedactError):
            redact_file(bad, self.root / "out.json")
        self.assertFalse((self.root / "out.json").exists())

    def test_redacted_summary_lints_clean_in_its_pack(self) -> None:
        self.tree()
        source = self.root / "aggregate-result.json"
        source.write_text(json.dumps(aggregate()), encoding="utf-8")
        redact_file(source, self.root / "packs/demo/evals/results/2026-11-05/summary.json")
        from skillslib.lint import lint_roots

        problems = lint_roots([self.root / "packs"]).problems
        # beta-basic is attributed to a skill this tree doesn't have, and has no case
        # folder here: those are the only findings.
        self.assertEqual(sorted(p.code for p in problems), ["RES003", "RES004"])
