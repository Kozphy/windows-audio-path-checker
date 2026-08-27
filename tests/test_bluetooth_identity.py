"""Regression tests for Bluetooth target-identity safety.

Guards against the false-positive SUCCESS path where WH700NB (cc14bc0bde24)
was treated as proof that W800BT Pro (c8247887e57c) recovered.
"""

from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
import sys

from audio_path_checker.bluetooth_pairing.candidates import (
    build_rank_result,
    rank_candidates,
    select_pairable_candidate,
)
from audio_path_checker.bluetooth_pairing.failures import FailureReason, classify_outcome
from audio_path_checker.bluetooth_pairing.identity import (
    DISPOSITION_REJECTED_WRONG_DEVICE,
    REASON_CONFIGURED_TARGET_MISMATCH,
    build_target_identity,
    check_recovery_invariants,
    exit_code_for_classification,
    filter_candidates_by_identity,
    match_bluetooth_identity,
    normalize_bluetooth_address,
    pnp_node_matches_target,
)

BT_CLASSIC = "{E0CBF06C-CD8B-4647-BB8A-263B43F0F974}"
TARGET = "EDIFIER W800BT Pro"
ADDR = "c8247887e57c"
OTHER = "EDIFIER WH700NB"
OTHER_ADDR = "cc14bc0bde24"


def _c(**kwargs):
    base = {
        "name": TARGET,
        "id": "id-default",
        "kind": "AssociationEndpoint",
        "can_pair": False,
        "is_paired": False,
        "protocol_id": BT_CLASSIC,
        "device_address": ADDR,
        "enumeration_succeeded": True,
        "is_classic": True,
    }
    base.update(kwargs)
    return base


# Exact fixture from the production false-positive log.
FIXTURE_WH700_WRONG_DEVICE = [
    {
        "name": OTHER,
        "id": "Bluetooth#Bluetooth4c:23:38:dc:c0:9a-cc:14:bc:0b:de:24",
        "kind": "AssociationEndpoint",
        "can_pair": False,
        "is_paired": True,
        "protocol_id": BT_CLASSIC,
        "device_address": OTHER_ADDR,
        "selector": "ClassicPaired+Default",
        "enumeration_succeeded": True,
        "is_classic": True,
    }
]


class NormalizeAddressTests(unittest.TestCase):
    def test_address_variants_normalize(self):
        variants = [
            "c8247887e57c",
            "C8:24:78:87:E5:7C",
            "c8-24-78-87-e5-7c",
            "C8247887E57C",
        ]
        for v in variants:
            self.assertEqual(normalize_bluetooth_address(v), ADDR)


class IdentityMatchTests(unittest.TestCase):
    def test_a_exact_target_pairable(self):
        m = match_bluetooth_identity(
            build_target_identity(requested_name=TARGET, bluetooth_address=ADDR),
            _c(can_pair=True, is_paired=False),
        )
        self.assertTrue(m["matched"])
        self.assertTrue(m["address_match"])

    def test_b_wrong_edifier_rejected(self):
        filtered = filter_candidates_by_identity(
            FIXTURE_WH700_WRONG_DEVICE, target_name=TARGET, target_address=ADDR
        )
        self.assertFalse(filtered["exact_target_discovered"])
        self.assertEqual(len(filtered["rejected"]), 1)
        self.assertEqual(
            filtered["rejected"][0]["disposition"], DISPOSITION_REJECTED_WRONG_DEVICE
        )
        ranked = rank_candidates(
            FIXTURE_WH700_WRONG_DEVICE, target_name=TARGET, target_address=ADDR
        )
        selected = select_pairable_candidate(ranked, pairability="NOT_PAIRABLE")
        self.assertIsNone(selected)
        result = build_rank_result(
            FIXTURE_WH700_WRONG_DEVICE, target_name=TARGET, target_address=ADDR
        )
        self.assertFalse(result["exact_target_discovered"])
        self.assertIsNone(result["selected"])
        self.assertFalse(result["target_discovered"])

    def test_c_same_name_different_mac_rejected(self):
        filtered = filter_candidates_by_identity(
            [_c(name=TARGET, device_address=OTHER_ADDR)],
            target_name=TARGET,
            target_address=ADDR,
        )
        self.assertFalse(filtered["exact_target_discovered"])
        self.assertEqual(
            filtered["rejected"][0]["rejection_reason"], REASON_CONFIGURED_TARGET_MISMATCH
        )

    def test_d_different_name_exact_mac_accepted(self):
        m = match_bluetooth_identity(
            build_target_identity(requested_name=TARGET, bluetooth_address=ADDR),
            _c(name="W800BT-Pro-Renamed", device_address=ADDR),
        )
        self.assertTrue(m["matched"])
        self.assertIn("name_mismatch_warning", m)

    def test_e_unrelated_a2dp_does_not_match_pnp(self):
        m = pnp_node_matches_target(
            friendly_name="Headphones (EDIFIER WH700NB)",
            instance_id=r"BTHENUM\Dev_CC14BC0BDE24\a&1&BluetoothDevice_CC14BC0BDE24",
            target_name=TARGET,
            target_address=ADDR,
        )
        self.assertFalse(m["matched"])
        self.assertEqual(m["reason"], "BLUETOOTH_ADDRESS_MISMATCH")

    def test_f_pair_request_not_run_invariant(self):
        violations = check_recovery_invariants(
            {
                "pair_request": "NOT_RUN",
                "pairing_succeeded": True,
                "exact_target_already_paired": False,
                "exact_target_discovered": True,
                "exact_target_audio_endpoint_found": True,
                "audio_endpoint_identity_match": True,
                "exact_target_a2dp_endpoint_found": True,
                "a2dp_endpoint_identity_match": True,
                "final_success": True,
            }
        )
        self.assertTrue(any(v["invariant"] == 1 for v in violations))
        self.assertEqual(
            exit_code_for_classification("INTERNAL_STATE_INVARIANT_FAILURE"), 90
        )

    def test_g_exact_target_already_paired_ok(self):
        result = build_rank_result(
            [_c(can_pair=False, is_paired=True)],
            target_name=TARGET,
            target_address=ADDR,
        )
        self.assertTrue(result["exact_target_discovered"])
        self.assertTrue(result["exact_target_already_paired"])
        self.assertIsNotNone(result["selected"])
        self.assertTrue(result["selected"]["is_paired"])

    def test_h_cleanup_pnp_correlation_skips_sibling(self):
        sibling = pnp_node_matches_target(
            friendly_name=OTHER,
            instance_id=r"BTHENUM\Dev_CC14BC0BDE24\x",
            target_name=TARGET,
            target_address=ADDR,
        )
        target = pnp_node_matches_target(
            friendly_name=TARGET,
            instance_id=r"BTHENUM\Dev_C8247887E57C\x",
            target_name=TARGET,
            target_address=ADDR,
        )
        self.assertFalse(sibling["matched"])
        self.assertTrue(target["matched"])

    def test_i_no_address_requires_exact_name(self):
        m = match_bluetooth_identity(
            build_target_identity(requested_name=TARGET, bluetooth_address=""),
            {"name": OTHER, "device_address": ""},
        )
        self.assertFalse(m["matched"])
        m2 = match_bluetooth_identity(
            build_target_identity(requested_name=TARGET, bluetooth_address=""),
            {"name": TARGET, "device_address": ""},
        )
        self.assertTrue(m2["matched"])

    def test_j_impossible_state_classifies(self):
        reason = classify_outcome(
            pairability="NOT_PAIRABLE",
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
            target_discovered=False,
            pair_success=True,
            audio_ready=True,
            invariant_violations=[{"invariant": 1}],
        )
        self.assertEqual(reason, FailureReason.INTERNAL_STATE_INVARIANT_FAILURE)

    def test_regression_fixture_never_success(self):
        result = build_rank_result(
            FIXTURE_WH700_WRONG_DEVICE, target_name=TARGET, target_address=ADDR
        )
        self.assertFalse(result["exact_target_discovered"])
        self.assertIsNone(result["selected"])
        self.assertFalse(result.get("pairable_found"))
        outcome = classify_outcome(
            pairability=result["pairability"],
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
            target_discovered=result["exact_target_discovered"],
            pair_success=False,
            audio_ready=False,
            identity_mismatch=True,
        )
        self.assertIn(
            outcome,
            {
                FailureReason.TARGET_NOT_DISCOVERED,
                FailureReason.TARGET_IDENTITY_MISMATCH,
            },
        )
        self.assertNotEqual(outcome, None)
        self.assertEqual(exit_code_for_classification(outcome.value), 11)

    def test_ranker_cli_rejects_wrong_device(self):
        from audio_path_checker.bluetooth_pairing.__main__ import main

        old = sys.stdin
        try:
            sys.stdin = StringIO(json.dumps(FIXTURE_WH700_WRONG_DEVICE))
            buf = StringIO()
            with redirect_stdout(buf):
                code = main(
                    ["rank", "--target-name", TARGET, "--target-address", ADDR]
                )
            self.assertEqual(code, 0)
            out = json.loads(buf.getvalue())
            self.assertFalse(out["exact_target_discovered"])
            self.assertIsNone(out["selected"])
            self.assertTrue(out["identity_rejections"])
        finally:
            sys.stdin = old


class BrandSubstringNotAuthoritativeTests(unittest.TestCase):
    def test_edifier_substring_insufficient_when_address_known(self):
        m = match_bluetooth_identity(
            build_target_identity(requested_name=TARGET, bluetooth_address=ADDR),
            {"name": "EDIFIER Something Else", "device_address": OTHER_ADDR},
        )
        self.assertFalse(m["matched"])
        self.assertEqual(m["reason"], "BLUETOOTH_ADDRESS_MISMATCH")


if __name__ == "__main__":
    unittest.main()
