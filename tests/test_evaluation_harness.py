from __future__ import annotations

import unittest

from audio_path_checker.evaluation.harness import aggregate_cases, proportion_ci


class ProportionCITests(unittest.TestCase):
    def test_zero_total_is_explicitly_unmeasured(self) -> None:
        result = proportion_ci(0, 0)
        self.assertEqual(result["successes"], 0)
        self.assertEqual(result["total"], 0)
        self.assertIsNone(result["estimate"])
        self.assertIsNone(result["low"])
        self.assertIsNone(result["high"])

    def test_invalid_counts_raise(self) -> None:
        with self.assertRaises(ValueError):
            proportion_ci(2, 1)

    def test_interval_contains_point_estimate(self) -> None:
        result = proportion_ci(8, 10)
        self.assertAlmostEqual(result["estimate"], 0.8)
        self.assertLessEqual(result["low"], result["estimate"])
        self.assertGreaterEqual(result["high"], result["estimate"])


class AggregateCasesTests(unittest.TestCase):
    def test_excluded_trials_do_not_enter_metric_denominators(self) -> None:
        records = [
            {
                "state_match": True,
                "cause_match": True,
                "unsafe_action": False,
                "unnecessary_reset": False,
                "recovery_attempted": True,
                "recovery_verified": True,
                "false_success": False,
            },
            {
                "state_match": False,
                "cause_match": False,
                "unsafe_action": True,
                "excluded": True,
            },
        ]
        result = aggregate_cases(records)
        self.assertEqual(result["trials"], 1)
        self.assertEqual(result["excluded_trials"], 1)
        self.assertEqual(result["state_accuracy"]["total"], 1)
        self.assertEqual(result["state_accuracy"]["successes"], 1)
        self.assertEqual(result["unsafe_action_rate"]["successes"], 0)
        self.assertEqual(result["recovery_success_rate"]["successes"], 1)

    def test_missing_fields_are_not_guessed(self) -> None:
        result = aggregate_cases([{"state_match": True}, {}])
        self.assertEqual(result["state_accuracy"]["total"], 1)
        self.assertEqual(result["root_cause_accuracy"]["total"], 0)

    def test_generators_are_supported(self) -> None:
        result = aggregate_cases({"state_match": True} for _ in range(3))
        self.assertEqual(result["trials"], 3)
        self.assertEqual(result["state_accuracy"]["successes"], 3)


if __name__ == "__main__":
    unittest.main()
