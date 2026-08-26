"""Additional tests for pairability tri-state and failure taxonomy."""

from __future__ import annotations

import json
import unittest

from audio_path_checker.bluetooth_pairing.candidates import (
    NOT_PAIRABLE,
    PAIRABILITY_UNKNOWN,
    PAIRABLE,
    determine_pairability,
    rank_candidates,
    select_pairable_candidate,
)
from audio_path_checker.bluetooth_pairing.failures import FailureReason, classify_outcome

BT_CLASSIC = "{E0CBF06C-CD8B-4647-BB8A-263B43F0F974}"
BT_BLE = "{BB7BB05E-5972-42B5-94FC-76EAA7084D49}"
TARGET = "EDIFIER W800BT Pro"
ADDR = "c8247887e57c"


class PairabilityTriStateTests(unittest.TestCase):
    def test_enumeration_error_yields_unknown_not_not_pairable(self):
        candidates = [
            {
                "name": TARGET,
                "can_pair": False,
                "is_paired": False,
                "kind": "AssociationEndpoint",
                "protocol_id": BT_BLE,
                "enumeration_succeeded": True,
                "is_classic": False,
                "is_ble": True,
            }
        ]
        pairability = determine_pairability(
            candidates,
            classic_enumeration_succeeded=False,
            aep_enumeration_succeeded=False,
        )
        self.assertEqual(pairability, PAIRABILITY_UNKNOWN)
        self.assertIsNone(select_pairable_candidate([], pairability=pairability))

    def test_all_classic_canpair_false_is_not_pairable_when_enum_ok(self):
        candidates = [
            {
                "name": TARGET,
                "can_pair": False,
                "is_paired": False,
                "protocol_id": BT_CLASSIC,
                "enumeration_succeeded": True,
                "is_classic": True,
            }
        ]
        pairability = determine_pairability(
            candidates,
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
        )
        self.assertEqual(pairability, NOT_PAIRABLE)

    def test_classic_canpair_true_is_pairable(self):
        candidates = [
            {
                "name": TARGET,
                "can_pair": True,
                "is_paired": False,
                "protocol_id": BT_CLASSIC,
                "enumeration_succeeded": True,
                "is_classic": True,
            }
        ]
        pairability = determine_pairability(
            candidates,
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
        )
        self.assertEqual(pairability, PAIRABLE)

    def test_enumeration_error_does_not_classify_as_discoverable_not_pairable(self):
        outcome = classify_outcome(
            pairability=PAIRABILITY_UNKNOWN,
            classic_enumeration_succeeded=False,
            aep_enumeration_succeeded=False,
            target_discovered=True,
            pair_success=False,
            audio_ready=False,
        )
        self.assertEqual(outcome, FailureReason.CLASSIC_ENDPOINT_ENUMERATION_FAILED)


class RankerInputTests(unittest.TestCase):
    def test_ranker_cli_rejects_invalid_json(self):
        from audio_path_checker.bluetooth_pairing.__main__ import main
        import io
        import sys
        from contextlib import redirect_stdout

        old = sys.stdin
        try:
            sys.stdin = io.StringIO("not-json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["rank"])
            self.assertEqual(code, 1)
            out = json.loads(buf.getvalue())
            self.assertEqual(out["error"], "RANKER_INPUT_INVALID")
        finally:
            sys.stdin = old

    def test_ranker_empty_array_reports_no_candidates(self):
        from audio_path_checker.bluetooth_pairing.__main__ import main
        import io
        import sys
        from contextlib import redirect_stdout

        old = sys.stdin
        try:
            sys.stdin = io.StringIO("[]")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["rank"])
            self.assertEqual(code, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual(out["error"], "NO_CANDIDATES")
        finally:
            sys.stdin = old

    def test_score_components_present(self):
        ranked = rank_candidates(
            [
                {
                    "name": TARGET,
                    "can_pair": True,
                    "is_paired": False,
                    "protocol_id": BT_CLASSIC,
                    "kind": "AssociationEndpoint",
                    "enumeration_succeeded": True,
                    "is_classic": True,
                }
            ],
            target_name=TARGET,
            target_address=ADDR,
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
        )
        self.assertTrue(ranked[0]["score_components"])
        self.assertGreater(ranked[0]["score"], 0)


if __name__ == "__main__":
    unittest.main()
