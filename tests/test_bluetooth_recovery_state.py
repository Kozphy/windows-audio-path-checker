"""Bluetooth recovery state machine regression tests (scenarios A-F)."""

from __future__ import annotations

import unittest

from audio_path_checker.bluetooth_pairing.candidates import build_rank_result
from audio_path_checker.bluetooth_pairing.failures import FailureReason, classify_outcome
from audio_path_checker.bluetooth_pairing.identity import (
    REASON_CONFIGURED_TARGET_MISMATCH,
    annotate_candidate_identity,
    check_recovery_invariants,
    filter_candidates_by_identity,
    repair_stage_results,
    test_recovery_state,
)

TARGET = "EDIFIER W800BT Pro"
ADDR = "c8247887e57c"
OTHER = "EDIFIER WH700NB"
OTHER_ADDR = "cc14bc0bde24"

FIXTURE_WH700 = [
    {
        "name": OTHER,
        "id": "Bluetooth#Bluetooth4c:23:38:dc:c0:9a-cc:14:bc:0b:de:24",
        "kind": "AssociationEndpoint",
        "can_pair": False,
        "is_paired": True,
        "protocol_id": "{E0CBF06C-CD8B-4647-BB8A-263B43F0F974}",
        "device_address": OTHER_ADDR,
        "enumeration_succeeded": True,
        "is_classic": True,
    }
]


def _target_not_discovered_stages() -> dict[str, str]:
    return {
        "DiscoveryApi": "PASS",
        "ClassicEnumerationCapability": "PASS",
        "TargetDiscovered": "FAIL",
        "TargetClassicEndpoint": "NOT_RUN",
        "Pairability": "NOT_RUN",
        "PairRequest": "NOT_RUN",
        "PairResult": "NOT_RUN",
        "AudioEndpoint": "NOT_RUN",
    }


class ScenarioATargetNotDiscoveredTests(unittest.TestCase):
    """Scenario A: discovery API works, target not found."""

    def test_stages_short_circuit(self):
        stages = _target_not_discovered_stages()
        repair_stage_results(stages)
        self.assertEqual(stages["PairResult"], "NOT_RUN")
        self.assertEqual(stages["Pairability"], "NOT_RUN")

    def test_classification_not_invariant(self):
        outcome = classify_outcome(
            pairability="NOT_PAIRABLE",
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
            target_discovered=False,
            pair_success=False,
            audio_ready=False,
            identity_mismatch=True,
        )
        self.assertEqual(outcome, FailureReason.TARGET_IDENTITY_MISMATCH)
        self.assertNotEqual(outcome, FailureReason.INTERNAL_STATE_INVARIANT_FAILURE)

    def test_valid_state_no_invariant_violation(self):
        stages = _target_not_discovered_stages()
        repair_stage_results(stages)
        violations = check_recovery_invariants(
            {
                "pair_request": stages["PairRequest"],
                "pair_result": stages["PairResult"],
                "pairing_succeeded": False,
                "exact_target_already_paired": False,
                "exact_target_discovered": False,
                "target_discovered_stage": stages["TargetDiscovered"],
                "final_success": False,
            }
        )
        self.assertEqual(violations, [])


class ScenarioBPairingNeverRequestedTests(unittest.TestCase):
    """Scenario B: PairRequest NOT_RUN => PairResult must stay NOT_RUN."""

    def test_repair_clears_invalid_fail(self):
        stages = {
            "TargetDiscovered": "FAIL",
            "PairRequest": "NOT_RUN",
            "PairResult": "FAIL",
        }
        repair_stage_results(stages)
        self.assertEqual(stages["PairResult"], "NOT_RUN")

    def test_invariant_detects_fail_without_request(self):
        violations = check_recovery_invariants(
            {
                "pair_request": "NOT_RUN",
                "pair_result": "FAIL",
                "pairing_succeeded": False,
                "exact_target_discovered": False,
                "target_discovered_stage": "FAIL",
            }
        )
        self.assertTrue(any(v["code"] == "PAIR_RESULT_FAIL_WITHOUT_REQUEST" for v in violations))


class ScenarioCPairabilityMessageTests(unittest.TestCase):
    """Scenario C: enum API OK but target classic endpoint absent."""

    def test_pairability_not_pairable_when_only_wrong_device(self):
        result = build_rank_result(
            FIXTURE_WH700, target_name=TARGET, target_address=ADDR
        )
        self.assertFalse(result["exact_target_discovered"])
        self.assertIn(result["pairability"], {"NOT_PAIRABLE", "UNKNOWN"})


class ScenarioDWrongEdifierTests(unittest.TestCase):
    """Scenario D: WH700NB observed while W800BT Pro configured."""

    def test_identity_annotation_structured_skip(self):
        item = annotate_candidate_identity(
            FIXTURE_WH700[0], target_name=TARGET, target_address=ADDR
        )
        self.assertEqual(item["rejection_reason"], REASON_CONFIGURED_TARGET_MISMATCH)
        self.assertEqual(item["candidate_role"], "NON_TARGET_DEVICE")
        self.assertEqual(item["action"], "SKIP")

    def test_no_destructive_target_discovery(self):
        filtered = filter_candidates_by_identity(
            FIXTURE_WH700, target_name=TARGET, target_address=ADDR
        )
        self.assertFalse(filtered["exact_target_discovered"])
        self.assertEqual(len(filtered["accepted"]), 0)


class ScenarioEInvariantFailureTests(unittest.TestCase):
    """Scenario E: deliberately impossible state still detected."""

    def test_pair_result_pass_without_request(self):
        violations = test_recovery_state(
            {
                "pair_request": "NOT_RUN",
                "pair_result": "PASS",
                "pairing_succeeded": True,
                "exact_target_already_paired": False,
                "exact_target_discovered": True,
                "target_discovered_stage": "PASS",
            }
        )
        self.assertTrue(violations)
        outcome = classify_outcome(
            pairability="PAIRABLE",
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
            target_discovered=True,
            pair_success=True,
            audio_ready=True,
            invariant_violations=violations,
        )
        self.assertEqual(outcome, FailureReason.INTERNAL_STATE_INVARIANT_FAILURE)


class ScenarioFSuccessPathTests(unittest.TestCase):
    """Scenario F: successful pairing path preserved."""

    def test_exact_target_pairable(self):
        candidate = {
            "name": TARGET,
            "device_address": ADDR,
            "can_pair": True,
            "is_paired": False,
            "is_classic": True,
            "enumeration_succeeded": True,
            "protocol_id": "{E0CBF06C-CD8B-4647-BB8A-263B43F0F974}",
        }
        filtered = filter_candidates_by_identity(
            [candidate], target_name=TARGET, target_address=ADDR
        )
        self.assertTrue(filtered["exact_target_discovered"])
        result = build_rank_result(
            [candidate], target_name=TARGET, target_address=ADDR
        )
        self.assertTrue(result["exact_target_discovered"])
        self.assertEqual(result["pairability"], "PAIRABLE")
        outcome = classify_outcome(
            pairability="PAIRABLE",
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
            target_discovered=True,
            pair_success=True,
            audio_ready=True,
        )
        self.assertIsNone(outcome)


if __name__ == "__main__":
    unittest.main()
