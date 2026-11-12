"""Reading redacted eval summaries: layout variants, attribution, partial runs and bad input."""

import tempfile
import unittest
from pathlib import Path

from tests.site import support
from tests.site.support import build


def rows_for(root: Path):
    warnings = []
    runs = [build.load_run(path, root, warnings) for path in build.find_summaries(root)]
    return runs, build.build_skill_rows(runs)


def by_skill(rows):
    return {row.skill: row for row in rows}


class TempRootTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class LayoutTests(TempRootTest):
    def test_nested_layout_matches_the_eval_result_format(self):
        support.sample_root(self.root)
        runs, rows = rows_for(self.root)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual((run.kind, run.pack, run.date), ("pack", "sample-pack", "2026-11-01"))
        self.assertEqual((run.cases_passed, run.cases_total, run.claude_version), (4, 9, "2.1.318"))
        self.assertEqual((run.threshold, run.model_pinned), (0.8, True))
        self.assertAlmostEqual(run.overall_score, 0.7033333333333334)
        self.assertEqual(sorted(by_skill(rows)), ["inbox-triage", "release-notes", "report-digest"])

    def test_flat_layout_reads_the_same(self):
        data = support.load_sample()
        aggregates = data.pop("aggregates")
        data.update(aggregates)
        for case in data["cases"]:
            case.update(case.pop("aggregates"))
        support.sample_root(self.root, data)
        flat_runs, flat_rows = rows_for(self.root)

        with tempfile.TemporaryDirectory() as other:
            nested_runs, nested_rows = rows_for(support.sample_root(Path(other)))
        self.assertEqual(
            [(r.skill, r.score, r.delta, r.runs, r.status) for r in flat_rows],
            [(r.skill, r.score, r.delta, r.runs, r.status) for r in nested_rows],
        )
        self.assertEqual(flat_runs[0].overall_score, nested_runs[0].overall_score)

    def test_extra_fields_are_ignored(self):
        data = support.load_sample()
        data["futureField"] = {"anything": [1, 2, 3]}
        data["cases"][0]["notes"] = "extra"
        data["cases"][0]["aggregates"]["passRate"] = 0.67
        support.sample_root(self.root, data)
        _runs, rows = rows_for(self.root)
        self.assertEqual(len(rows), 3)

    def test_missing_aggregates_fall_back_to_their_definitions(self):
        data = support.load_sample()
        del data["aggregates"]
        support.sample_root(self.root, data)
        run = rows_for(self.root)[0][0]
        scores = [c["aggregates"]["score"] for c in data["cases"]]
        deltas = [c["aggregates"]["delta"] for c in data["cases"]]
        self.assertAlmostEqual(run.overall_score, sum(scores) / 9)
        self.assertAlmostEqual(run.mean_delta, sum(deltas) / 9)
        self.assertEqual((run.cases_passed, run.cases_total), (4, 9), "passed is counted at suite.threshold")


class DateTests(TempRootTest):
    def date_for(self, data, rel=support.SAMPLE_REL):
        support.make_root(self.root, summaries={rel: data})
        return rows_for(self.root)[0][0].date

    def test_started_at(self):
        self.assertEqual(self.date_for(support.load_sample()), "2026-11-01")

    def test_the_summary_date_wins_over_the_timestamp(self):
        data = support.load_sample()
        data["date"] = "2026-10-30"  # a redactor's --date override
        self.assertEqual(self.date_for(data), "2026-10-30")

    def test_started_at_without_a_date(self):
        data = support.load_sample()
        del data["date"]
        data["startedAt"] = "2026-10-29T23:59:00.000Z"
        self.assertEqual(self.date_for(data), "2026-10-29")

    def test_null_started_at(self):
        data = support.load_sample()
        data["startedAt"] = None
        self.assertEqual(self.date_for(data), "2026-11-01")

    def test_results_folder_name(self):
        data = support.load_sample()
        del data["startedAt"], data["date"]
        rel = "packs/sample-pack/evals/results/2026-09-10T17-02-11-482Z/summary.json"
        self.assertEqual(self.date_for(data, rel), "2026-09-10")

    def test_epoch_milliseconds(self):
        data = support.load_sample()
        del data["date"]
        data["startedAt"] = 1793000000000
        self.assertEqual(self.date_for(data), "2026-10-26")

    def test_no_date_at_all(self):
        data = support.load_sample()
        del data["startedAt"], data["date"]
        self.assertIsNone(self.date_for(data, "packs/sample-pack/evals/results/latest/summary.json"))


class RunsTests(TempRootTest):
    def test_runs_from_the_with_arm(self):
        data = support.load_sample()
        for case in data["cases"]:
            del case["runsPerCase"]
            case["arms"] = {"with": [{"score": 1}, {"score": 1}, {"score": 1}], "without": [{"score": 0}] * 3}
        support.sample_root(self.root, data)
        self.assertEqual({row.runs for row in rows_for(self.root)[1]}, {3})

    def test_runs_that_happened_beat_the_configured_count(self):
        data = support.load_sample()
        data["cases"][0]["arms"] = {"with": [{"score": 1}, {"score": 1}], "without": [{"score": 0}] * 2}
        support.sample_root(self.root, data)
        row = by_skill(rows_for(self.root)[1])["release-notes"]
        self.assertEqual(row.runs, 2)
        self.assertIn("2 runs per arm, needs 3", row.reasons)

    def test_top_level_runs_per_case(self):
        data = support.load_sample()
        for case in data["cases"]:
            del case["runsPerCase"]
        data["runsPerCase"] = 5
        support.sample_root(self.root, data)
        self.assertEqual({row.runs for row in rows_for(self.root)[1]}, {5})

    def test_a_bare_runs_count_is_not_trusted(self):
        # The eval summary table's RUNS column counts both arms, so "runs": 6
        # could mean 3 per arm or 6. It must never qualify a skill.
        data = support.load_sample()
        for case in data["cases"]:
            del case["runsPerCase"]
            case["runs"] = 6
        support.sample_root(self.root, data)
        row = by_skill(rows_for(self.root)[1])["release-notes"]
        self.assertIsNone(row.runs)
        self.assertEqual(row.status, build.BELOW)
        self.assertIn("runs not recorded", row.reasons)

    def test_a_skill_takes_the_fewest_runs_of_its_cases(self):
        data = support.load_sample()
        data["cases"][1]["runsPerCase"] = 2
        support.sample_root(self.root, data)
        row = by_skill(rows_for(self.root)[1])["release-notes"]
        self.assertEqual(row.runs, 2)
        self.assertEqual(row.status, build.BELOW)
        self.assertIn("2 runs per arm, needs 3", row.reasons)


class AttributionTests(TempRootTest):
    def test_case_names_match_skill_folders(self):
        support.sample_root(self.root, support.sample_without_skill_fields())
        rows = by_skill(rows_for(self.root)[1])
        self.assertEqual(sorted(rows), ["inbox-triage", "release-notes", "report-digest"])
        self.assertEqual(
            [c.name for c in rows["release-notes"].cases],
            ["release-notes-basic", "release-notes-breaking-change", "release-notes-empty-history"],
        )

    def test_the_longest_matching_folder_wins(self):
        data = support.sample_without_skill_fields()
        support.make_root(
            self.root,
            summaries={support.SAMPLE_REL: data},
            skills={"packs/sample-pack": ["release", "release-notes", "report-digest", "inbox-triage"]},
        )
        rows = by_skill(rows_for(self.root)[1])
        self.assertNotIn("release", rows)
        self.assertEqual(len(rows["release-notes"].cases), 3)

    def test_a_pack_with_one_skill_owns_every_case(self):
        support.make_root(
            self.root,
            summaries={support.SAMPLE_REL: support.sample_without_skill_fields()},
            skills={"packs/sample-pack": ["only-skill"]},
        )
        rows = rows_for(self.root)[1]
        self.assertEqual([(row.skill, len(row.cases)) for row in rows], [("only-skill", 9)])

    def test_engine_summaries_are_listed_as_the_engine_pack(self):
        rel = "engine/evals/results/2026-11-01/summary.json"
        support.make_root(self.root, summaries={rel: support.sample_without_skill_fields()}, skills={"engine": ["only-skill"]})
        rows = rows_for(self.root)[1]
        self.assertEqual([(row.pack, row.skill, len(row.cases)) for row in rows], [("engine", "only-skill", 9)])

    def test_skill_level_results_folders(self):
        rel = "packs/sample-pack/skills/release-notes/evals/results/2026-11-01/summary.json"
        support.make_root(self.root, summaries={rel: support.sample_without_skill_fields()})
        rows = rows_for(self.root)[1]
        self.assertEqual([(row.skill, len(row.cases)) for row in rows], [("release-notes", 9)])

    def test_unmatched_cases_are_grouped_not_dropped(self):
        support.make_root(
            self.root,
            summaries={support.SAMPLE_REL: support.sample_without_skill_fields()},
            skills={"packs/sample-pack": ["alpha", "beta"]},
        )
        rows = rows_for(self.root)[1]
        self.assertEqual([(row.skill, len(row.cases)) for row in rows], [(None, 9)])
        outputs, _ = build.generate(self.root)
        self.assertIn("Unattributed cases", outputs[build.DASHBOARD])

    def test_explicit_skill_beats_the_folder_match(self):
        self.assertEqual(build.attribute_skill("release-notes-x", "other", None, None, ["release-notes"]), "other")
        self.assertEqual(build.attribute_skill("release-notes-x", None, "from-path", None, ["release-notes"]), "from-path")
        self.assertEqual(build.attribute_skill("release-notesx", None, None, None, ["release-notes", "z"]), None)


class HistoryTests(TempRootTest):
    def test_a_newer_partial_run_does_not_replace_a_complete_one(self):
        older = support.load_sample()
        newer = support.load_sample()
        newer["startedAt"] = "2026-11-03T08:00:00.000Z"
        newer["date"] = "2026-11-03"
        newer["partial"] = True
        newer["partialReason"] = "cost_ceiling"
        for case in newer["cases"]:
            case["aggregates"]["score"] = 0.1
        support.make_root(
            self.root,
            summaries={
                support.SAMPLE_REL: older,
                "packs/sample-pack/evals/results/2026-11-03/summary.json": newer,
            },
        )
        runs, rows = rows_for(self.root)
        self.assertEqual({row.run.date for row in rows}, {"2026-11-01"})
        html = build.generate(self.root)[0][build.DASHBOARD]
        self.assertIn("partial: cost ceiling", html)
        self.assertIn("2 eval summaries", html)

    def test_a_partial_run_alone_is_used_but_cannot_qualify(self):
        data = support.load_sample()
        data["partial"] = True
        support.sample_root(self.root, data)
        row = by_skill(rows_for(self.root)[1])["release-notes"]
        self.assertTrue(row.run.partial)
        self.assertEqual(row.status, build.BELOW)
        self.assertIn("partial run", row.reasons)

    def test_the_latest_complete_run_wins(self):
        older = support.load_sample()
        newer = support.load_sample()
        newer["startedAt"] = "2026-11-04T08:00:00.000Z"
        newer["date"] = "2026-11-04"
        newer["cases"] = [c for c in newer["cases"] if c["skill"] == "release-notes"]
        for case in newer["cases"]:
            case["aggregates"]["delta"] = 0.25
        support.make_root(
            self.root,
            summaries={support.SAMPLE_REL: older, "packs/sample-pack/evals/results/2026-11-04/summary.json": newer},
        )
        rows = by_skill(rows_for(self.root)[1])
        self.assertEqual(rows["release-notes"].run.date, "2026-11-04")
        self.assertAlmostEqual(rows["release-notes"].delta, 0.25)
        self.assertEqual(rows["inbox-triage"].run.date, "2026-11-01")
        html = build.generate(self.root)[0][build.DASHBOARD]
        self.assertIn("6 of 9 cases; the rest have a newer run", html)


class ContractRuleTests(TempRootTest):
    """The stable rule as tools/repo/skills/CONTRACT.md states it."""

    def release_notes(self, data):
        support.sample_root(self.root, data)
        return by_skill(rows_for(self.root)[1])["release-notes"]

    def test_the_sample_skill_meets_the_full_rule(self):
        row = self.release_notes(support.load_sample())
        self.assertEqual((row.status, row.reasons), (build.STABLE, []))

    def test_an_unpinned_model_cannot_qualify(self):
        data = support.load_sample()
        data["suite"]["modelPinned"] = False
        row = self.release_notes(data)
        self.assertEqual((row.status, row.reasons), (build.BELOW, ["model not pinned"]))

    def test_unrecorded_pinning_cannot_qualify(self):
        data = support.load_sample()
        del data["suite"]["modelPinned"]
        self.assertEqual(self.release_notes(data).reasons, ["model pinning not recorded"])

    def test_fewer_than_three_cases_cannot_qualify(self):
        data = support.load_sample()
        data["cases"] = [c for c in data["cases"] if c["name"] != "release-notes-empty-history"]
        self.assertEqual(self.release_notes(data).reasons, ["2 cases, needs 3"])

    def test_every_case_needs_a_delta(self):
        data = support.load_sample()
        del data["cases"][2]["aggregates"]["delta"]
        row = self.release_notes(data)
        self.assertEqual(row.status, build.BELOW)
        self.assertEqual(row.reasons, ["Δ for 2 of 3 cases"])

    def test_pinning_must_be_boolean(self):
        data = support.load_sample()
        data["suite"]["modelPinned"] = "yes"
        support.sample_root(self.root, data)
        with self.assertRaises(build.InputError):
            build.generate(self.root)


class LabsTests(TempRootTest):
    def test_labs_are_read_and_tagged(self):
        support.make_root(self.root, summaries={"labs/trial-pack/evals/results/2026-11-02/summary.json": support.load_sample()})
        runs, rows = rows_for(self.root)
        self.assertEqual({(run.kind, run.pack) for run in runs}, {("lab", "trial-pack")})
        outputs, _ = build.generate(self.root)
        self.assertIn('trial-pack<span class="tag">lab</span>', outputs[build.DASHBOARD])
        self.assertIn("trial-pack (lab)", outputs[build.README])

    def test_packs_sort_before_labs(self):
        support.make_root(
            self.root,
            summaries={
                "labs/aaa/evals/results/2026-11-02/summary.json": support.load_sample(),
                "packs/zzz/evals/results/2026-11-02/summary.json": support.load_sample(),
            },
        )
        rows = rows_for(self.root)[1]
        self.assertEqual([row.kind for row in rows], ["pack"] * 3 + ["lab"] * 3)


class BadInputTests(TempRootTest):
    def assert_input_error(self, data, message):
        support.make_root(self.root, summaries={support.SAMPLE_REL: data})
        with self.assertRaises(build.InputError) as caught:
            build.generate(self.root)
        self.assertIn(message, str(caught.exception))
        self.assertIn(support.SAMPLE_REL, str(caught.exception), "errors name the file")

    def test_invalid_json(self):
        support.make_root(self.root)
        path = self.root / support.SAMPLE_REL
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(build.InputError) as caught:
            build.generate(self.root)
        self.assertIn("invalid JSON", str(caught.exception))

    def test_score_out_of_range(self):
        data = support.load_sample()
        data["cases"][0]["aggregates"]["score"] = 1.5
        self.assert_input_error(data, "aggregates.score")

    def test_score_of_the_wrong_type(self):
        data = support.load_sample()
        data["cases"][0]["aggregates"]["score"] = "high"
        self.assert_input_error(data, "expected a number")

    def test_missing_score(self):
        data = support.load_sample()
        del data["cases"][0]["aggregates"]["score"]
        self.assert_input_error(data, "score is missing")

    def test_duplicate_case_names(self):
        data = support.load_sample()
        data["cases"][1]["name"] = data["cases"][0]["name"]
        self.assert_input_error(data, "duplicate case name")

    def test_cases_must_be_a_list(self):
        data = support.load_sample()
        data["cases"] = {"a": 1}
        self.assert_input_error(data, "cases: expected a list")

    def test_partial_must_be_boolean(self):
        data = support.load_sample()
        data["partial"] = "yes"
        self.assert_input_error(data, "partial: expected true or false")

    def test_unreadable_date(self):
        data = support.load_sample()
        data["startedAt"] = "yesterday"
        self.assert_input_error(data, "no YYYY-MM-DD date")

    def test_the_cli_exits_2_on_bad_input(self):
        data = support.load_sample()
        data["cases"][0]["aggregates"]["delta"] = 7
        support.make_root(self.root, summaries={support.SAMPLE_REL: data})
        result = support.run_cli("--root", str(self.root), "--check")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("error: ", result.stderr)
        self.assertIn("aggregates.delta", result.stderr)


if __name__ == "__main__":
    unittest.main()
