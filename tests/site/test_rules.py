"""The stable rule and number formatting."""

import unittest

from tests.site.support import build


class StableRuleTests(unittest.TestCase):
    def test_exactly_at_the_bar_is_stable(self):
        self.assertEqual(build.classify(0.8, 0.2, 3), (build.STABLE, []))

    def test_float_noise_from_averaging_does_not_fail_the_bar(self):
        score = (0.7 + 0.8 + 0.9) / 3  # 0.7999999999999999
        self.assertLess(score, 0.8)
        self.assertEqual(build.classify(score, 0.2, 3)[0], build.STABLE)

    def test_score_under_the_bar(self):
        status, reasons = build.classify(0.79, 0.5, 3)
        self.assertEqual(status, build.BELOW)
        self.assertEqual(reasons, ["score 0.79 is under 0.80"])

    def test_rounding_never_hides_a_miss(self):
        # 0.795 would print as 0.80; the reason must not claim "0.80 is under 0.80".
        self.assertEqual(build.classify(0.795, 0.5, 3)[1], ["score 0.795 is under 0.80"])
        self.assertEqual(build.classify(0.9, 0.198, 3)[1], ["Δ +0.198 is under +0.20"])

    def test_delta_under_the_bar(self):
        self.assertEqual(build.classify(0.9, 0.1, 3), (build.BELOW, ["Δ +0.10 is under +0.20"]))

    def test_zero_delta_is_a_regression(self):
        self.assertEqual(build.classify(0.95, 0.0, 3), (build.REGRESSION, ["no better than no skill"]))

    def test_negative_delta_is_a_regression(self):
        self.assertEqual(build.classify(0.95, -0.1, 3), (build.REGRESSION, ["worse than no skill"]))

    def test_regression_wins_over_other_reasons(self):
        self.assertEqual(build.classify(0.1, -0.5, 1)[0], build.REGRESSION)

    def test_no_baseline(self):
        status, _ = build.classify(0.95, None, 3)
        self.assertEqual(status, build.NO_BASELINE)

    def test_too_few_runs(self):
        self.assertEqual(build.classify(0.9, 0.3, 2), (build.BELOW, ["2 runs per arm, needs 3"]))
        self.assertEqual(build.classify(0.9, 0.3, 1), (build.BELOW, ["1 run per arm, needs 3"]))

    def test_unknown_runs_cannot_qualify(self):
        self.assertEqual(build.classify(0.9, 0.3, None), (build.BELOW, ["runs not recorded"]))

    def test_partial_runs_cannot_qualify(self):
        self.assertEqual(build.classify(0.9, 0.3, 3, partial=True), (build.BELOW, ["partial run"]))

    def test_every_reason_is_listed(self):
        _, reasons = build.classify(0.5, 0.05, 2)
        self.assertEqual(len(reasons), 3)


class FormattingTests(unittest.TestCase):
    def test_scores(self):
        self.assertEqual(build.fmt_score(0.9), "0.90")
        self.assertEqual(build.fmt_score(1), "1.00")

    def test_deltas_are_signed_except_zero(self):
        cases = {0.47: "+0.47", -0.07: "-0.07", 0.0: "0.00", -0.0: "0.00", 0.004: "0.00", -0.004: "0.00", 1: "+1.00"}
        for value, text in cases.items():
            with self.subTest(value=value):
                self.assertEqual(build.fmt_delta(value), text)

    def test_cost_and_duration(self):
        self.assertEqual(build.fmt_cost(2.405), "$2.40")
        self.assertEqual(build.fmt_cost(1234.5), "$1,234.50")
        self.assertEqual(build.fmt_duration(45), "45 s")
        self.assertEqual(build.fmt_duration(538), "8 min 58 s")
        self.assertEqual(build.fmt_duration(3720), "1 h 2 min")

    def test_percent_widths(self):
        self.assertEqual(build._pct(80.0), "80")
        self.assertEqual(build._pct(93.33333), "93.3")
        self.assertEqual(build._pct(-5), "0")
        self.assertEqual(build._pct(150), "100")


if __name__ == "__main__":
    unittest.main()
