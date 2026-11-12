"""The redacted summary format and the stable rule computed from it."""

import copy
import unittest
from pathlib import Path

from .helpers import summary_doc

from skillslib.summary import (  # noqa: E402
    LoadedSummary,
    evidence_for,
    latest_evidence,
    qualifying_evidence,
    stable_rule_failures,
    validate_summary,
)

GOOD = summary_doc([("alpha-basic", "alpha", 0.9, 0.3, 3, 3), ("alpha-two", "alpha", 1.0, 0.5, 3, 3)])


def loaded(doc: dict, name: str = "s") -> LoadedSummary:
    return LoadedSummary(Path(name + "/summary.json"), doc, [])


class ValidateTests(unittest.TestCase):
    def test_valid_summary(self) -> None:
        self.assertEqual(validate_summary(GOOD), [])

    def test_rejections(self) -> None:
        def mutate(change):
            doc = copy.deepcopy(GOOD)
            change(doc)
            return validate_summary(doc)

        cases = {
            "unknown top-level key": lambda d: d.__setitem__("model", "x"),
            "unknown suite key": lambda d: d["suite"].__setitem__("modelOverride", "x"),
            "unknown case key": lambda d: d["cases"][0].__setitem__("promptMarkdown", "x"),
            "unknown run key": lambda d: d["cases"][0]["arms"]["with"][0].__setitem__("tracePath", "x"),
            "missing required key": lambda d: d.pop("costUsd"),
            "wrong format version": lambda d: d.__setitem__("summaryFormat", 2),
            "bool as number": lambda d: d.__setitem__("costUsd", True),
            "score above 1": lambda d: d["cases"][0]["aggregates"].__setitem__("score", 1.5),
            "bad date": lambda d: d.__setitem__("date", "2026-02-30"),
            "bad timestamp": lambda d: d.__setitem__("startedAt", "yesterday"),
            "partial reason without partial": lambda d: d.__setitem__("partialReason", "cost_ceiling"),
            "unknown ablation": lambda d: d["suite"].__setitem__("ablation", "sometimes"),
            "duplicate case": lambda d: d["cases"].append(copy.deepcopy(d["cases"][0])),
            "case name with a path": lambda d: d["cases"][0].__setitem__("name", "../x"),
            "skill name with capitals": lambda d: d["cases"][0].__setitem__("skill", "Alpha"),
            "casesTotal mismatch": lambda d: d["aggregates"].__setitem__("casesTotal", 5),
            "casesPassed above total": lambda d: d["aggregates"].__setitem__("casesPassed", 3),
            "not an object": lambda d: d["cases"].__setitem__(0, "case"),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.assertTrue(mutate(change), label)
        self.assertTrue(validate_summary([]))

    def test_null_skill_and_started_at_are_allowed(self) -> None:
        doc = copy.deepcopy(GOOD)
        doc["cases"][0]["skill"] = None
        doc["startedAt"] = None
        self.assertEqual(validate_summary(doc), [])


class EvidenceTests(unittest.TestCase):
    def test_evidence_averages_the_skill_cases(self) -> None:
        evidence = evidence_for(loaded(GOOD), "alpha")
        self.assertAlmostEqual(evidence.score, 0.95)
        self.assertAlmostEqual(evidence.delta, 0.4)
        self.assertEqual((evidence.runs, evidence.runs_without, evidence.cases), (3, 3, 2))
        self.assertIsNone(evidence_for(loaded(GOOD), "beta"))

    def test_latest_prefers_complete_runs_and_orders_by_time(self) -> None:
        old = summary_doc([("a", "alpha", 0.5, 0.1, 3, 3)], started_at="2026-01-01T00:00:00.000Z")
        new = summary_doc([("a", "alpha", 0.9, 0.3, 3, 3)], started_at="2026-02-01T00:00:00.000Z")
        part = summary_doc([("a", "alpha", 0.1, 0.0, 1, 1)], started_at="2026-03-01T00:00:00.000Z", partial=True)
        summaries = [loaded(old, "old"), loaded(part, "part"), loaded(new, "new")]
        self.assertEqual(latest_evidence(summaries, "alpha").score, 0.9)
        self.assertEqual(latest_evidence([loaded(part, "part")], "alpha").score, 0.1)
        self.assertIsNone(latest_evidence([], "alpha"))

    def test_timezone_offsets_sort_by_utc(self) -> None:
        early = summary_doc([("a", "alpha", 0.5, 0.1, 3, 3)], started_at="2026-01-01T10:00:00+05:00")
        late = summary_doc([("a", "alpha", 0.9, 0.3, 3, 3)], started_at="2026-01-01T06:00:00.000Z")
        self.assertEqual(latest_evidence([loaded(late, "late"), loaded(early, "early")], "alpha").score, 0.9)

    def test_stable_rule(self) -> None:
        three = [("c{}".format(i), "alpha", 0.9, 0.3, 3, 3) for i in range(3)]
        self.assertEqual(stable_rule_failures(qualifying_evidence([loaded(summary_doc(three))], "alpha")), [])
        edge = [("c{}".format(i), "alpha", 0.8, 0.2, 3, 3) for i in range(3)]
        self.assertEqual(stable_rule_failures(qualifying_evidence([loaded(summary_doc(edge))], "alpha")), [])
        self.assertEqual(len(stable_rule_failures(None)), 1)
        two_cases = summary_doc(three[:2])
        self.assertTrue(any("2 case(s)" in r for r in stable_rule_failures(evidence_for(loaded(two_cases), "alpha"))))
        no_delta = summary_doc(three, ablation="none")
        self.assertIsNone(qualifying_evidence([loaded(no_delta)], "alpha"))
        self.assertTrue(stable_rule_failures(evidence_for(loaded(no_delta), "alpha")))


if __name__ == "__main__":
    unittest.main()

