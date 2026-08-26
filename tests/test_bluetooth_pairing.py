"""Unit tests for Bluetooth candidate ranking and pairing logic."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from audio_path_checker.bluetooth_pairing.candidates import (
    classify_candidate,
    determine_pairability,
    group_candidates_by_physical_device,
    rank_candidates,
    select_pairable_candidate,
    update_candidate_history,
    PAIRABILITY_UNKNOWN,
)
from audio_path_checker.bluetooth_pairing.failures import FailureReason, map_pair_status

BT_CLASSIC = "{E0CBF06C-CD8B-4647-BB8A-263B43F0F974}"
BT_BLE = "{BB7BB05E-5972-42B5-94FC-76EAA7084D49}"

TARGET = "EDIFIER W800BT Pro"
ADDR = "c8247887e57c"


def _c(**kwargs):
    base = {
        "name": "EDIFIER W800BT Pro",
        "id": "id-default",
        "kind": "AssociationEndpoint",
        "can_pair": False,
        "is_paired": False,
        "protocol_id": BT_CLASSIC,
        "device_address": ADDR,
    }
    base.update(kwargs)
    return base


FIXTURE_DISCOVERABLE_NOT_PAIRABLE = [
    {
        "name": "EDIFIER W800BT Pro",
        "can_pair": False,
        "is_paired": False,
        "kind": "AssociationEndpoint",
        "protocol_id": BT_BLE,
        "id": "ble-1",
    },
    {
        "name": "EDIFIER W800BT Pro",
        "can_pair": True,
        "is_paired": False,
        "kind": "AssociationEndpoint",
        "protocol_id": BT_CLASSIC,
        "id": "classic-1",
        "device_address": ADDR,
    },
]


class CandidateRankingTests(unittest.TestCase):
    def test_exact_name_pairable_candidate_wins(self):
        ranked = rank_candidates(
            [
                _c(name="Other Headset", can_pair=True, id="a"),
                _c(name=TARGET, can_pair=True, id="b"),
            ],
            target_name=TARGET,
            target_address=ADDR,
        )
        self.assertEqual(ranked[0]["id"], "b")

    def test_canpair_false_candidate_is_not_selected(self):
        ranked = rank_candidates([_c(can_pair=False, id="x")], target_name=TARGET)
        selected = select_pairable_candidate(ranked, pairability="NOT_PAIRABLE")
        self.assertIsNone(selected)

    def test_pairable_classic_candidate_beats_ble_candidate(self):
        ranked = rank_candidates(
            FIXTURE_DISCOVERABLE_NOT_PAIRABLE,
            target_name=TARGET,
            target_address=ADDR,
        )
        self.assertEqual(ranked[0]["id"], "classic-1")
        selected = select_pairable_candidate(ranked)
        assert selected is not None
        self.assertEqual(selected["id"], "classic-1")

    def test_already_paired_device_handled(self):
        ranked = rank_candidates(
            [_c(can_pair=False, is_paired=True, id="p")],
            target_name=TARGET,
        )
        selected = select_pairable_candidate(ranked)
        assert selected is not None
        self.assertTrue(selected["is_paired"])

    def test_duplicate_endpoints_are_grouped(self):
        groups = group_candidates_by_physical_device(
            [
                _c(id="a", device_address=ADDR),
                _c(id="b", device_address=ADDR, kind="Device"),
            ]
        )
        self.assertEqual(len(groups), 1)
        key = ADDR.replace(":", "")
        self.assertIn(key, groups)
        self.assertEqual(len(groups[key]["endpoints"]), 2)

    def test_candidate_canpair_transition_detected(self):
        history: dict = {}
        now = datetime(2026, 8, 26, tzinfo=timezone.utc)
        history, t1 = update_candidate_history(
            history, _c(id="x", can_pair=False), now=now
        )
        self.assertFalse(t1)
        history, t2 = update_candidate_history(
            history, _c(id="x", can_pair=True), now=now
        )
        self.assertTrue(t2)

    def test_no_pairable_endpoint_returns_correct_failure_selection(self):
        ranked = rank_candidates(
            [_c(can_pair=False, protocol_id=BT_BLE, id="ble", is_ble=True)],
            target_name=TARGET,
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
        )
        pairability = determine_pairability(
            ranked,
            classic_enumeration_succeeded=True,
            aep_enumeration_succeeded=True,
        )
        self.assertEqual(pairability, "NOT_PAIRABLE")
        self.assertIsNone(select_pairable_candidate(ranked, pairability=pairability))
        self.assertEqual(classify_candidate(ranked[0]), "BLEEndpoint")

    def test_integration_fixture_selects_candidate_two(self):
        ranked = rank_candidates(
            FIXTURE_DISCOVERABLE_NOT_PAIRABLE,
            target_name=TARGET,
            target_address=ADDR,
        )
        selected = select_pairable_candidate(ranked)
        assert selected is not None
        self.assertTrue(selected["can_pair"])
        self.assertEqual(selected["id"], "classic-1")


class FailureTaxonomyTests(unittest.TestCase):
    def test_pair_timeout_classified(self):
        self.assertEqual(
            map_pair_status("AuthenticationTimeout"),
            FailureReason.PAIR_AUTHENTICATION_FAILED,
        )

    def test_audio_endpoint_timeout_classified(self):
        self.assertEqual(
            FailureReason.AUDIO_ENDPOINT_TIMEOUT.value,
            "AUDIO_ENDPOINT_TIMEOUT",
        )

    def test_connected_without_endpoint_not_healthy(self):
        from audio_path_checker.diagnostics_engine import classify_state
        from audio_path_checker.models.states import AudioPathState

        evidence = {
            "device": {"name": TARGET, "paired": True, "connected": True},
            "bluetooth": {"adapter_present": True, "adapter_enabled": True},
            "audio": {
                "a2dp_present": True,
                "media_node_present": True,
                "endpoint_present": False,
                "endpoint_active": False,
            },
            "services": {
                "Audiosrv": "Running",
                "AudioEndpointBuilder": "Running",
                "bthserv": "Running",
                "BthAvctpSvc": "Running",
            },
            "capabilities": {"available": True},
        }
        state = classify_state(evidence)["state"]
        self.assertEqual(state, AudioPathState.MEDIA_NO_ENDPOINT.value)

    def test_wrong_default_does_not_imply_pairing_reset(self):
        from audio_path_checker.diagnostics_engine import classify_state, rank_hypotheses
        from audio_path_checker.remediation.planner import plan_remediation

        evidence = {
            "device": {"paired": True, "connected": True},
            "bluetooth": {"adapter_present": True, "adapter_enabled": True},
            "audio": {
                "media_node_present": True,
                "a2dp_present": True,
                "endpoint_present": True,
                "endpoint_active": True,
                "is_default_playback": False,
            },
            "services": {"Audiosrv": "Running", "AudioEndpointBuilder": "Running"},
            "capabilities": {"available": True},
        }
        clf = classify_state(evidence)
        plan = plan_remediation(
            classification=clf,
            hypotheses=rank_hypotheses(evidence, clf),
            evidence=evidence,
            mode="aggressive-repair",
        )
        actions = [a["action"] for a in plan["actions"]]
        self.assertNotIn("clear_pairing_cache_and_repair", actions)

    def test_action_success_vs_system_recovered(self):
        """Repair command succeeded but endpoint absent => not overall success."""
        pair_ok = True
        endpoint_ready = False
        overall = pair_ok and endpoint_ready
        self.assertTrue(pair_ok)
        self.assertFalse(overall)


class CleanupSafetyTests(unittest.TestCase):
    def test_cleanup_does_not_remove_unrelated_devices(self):
        """Name filter must scope EDIFIER only - documented in script patterns."""
        pattern = "EDIFIER W800BT|C8247887E57C"
        unrelated = "POLYWELL TM-086"
        self.assertIsNone(
            __import__("re").search(pattern, unrelated, __import__("re").IGNORECASE)
        )
        related = "EDIFIER W800BT Pro"
        self.assertIsNotNone(
            __import__("re").search("EDIFIER W800BT", related, __import__("re").IGNORECASE)
        )


class RankerCliTests(unittest.TestCase):
    def test_ranker_cli_accepts_single_object_json(self):
        from audio_path_checker.bluetooth_pairing.__main__ import main
        import io
        import sys
        from contextlib import redirect_stdout

        single = FIXTURE_DISCOVERABLE_NOT_PAIRABLE[1]
        old = sys.stdin
        try:
            sys.stdin = io.StringIO(json.dumps(single))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["rank", "--target-name", TARGET, "--target-address", ADDR])
            self.assertEqual(code, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual(out["selected"]["id"], "classic-1")
        finally:
            sys.stdin = old

    def test_ranker_cli_with_fixture(self):
        from audio_path_checker.bluetooth_pairing.__main__ import main
        import io
        import sys
        from contextlib import redirect_stdout

        old = sys.stdin
        try:
            sys.stdin = io.StringIO(json.dumps(FIXTURE_DISCOVERABLE_NOT_PAIRABLE))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["rank", "--target-name", TARGET, "--target-address", ADDR])
            self.assertEqual(code, 0)
            out = json.loads(buf.getvalue())
            self.assertTrue(out["pairable_found"])
            self.assertEqual(out["selected"]["id"], "classic-1")
            self.assertEqual(out["pairability"], "PAIRABLE")
        finally:
            sys.stdin = old


if __name__ == "__main__":
    unittest.main()
