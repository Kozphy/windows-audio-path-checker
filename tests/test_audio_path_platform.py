"""Unit tests for audio-path state / diagnosis / safety planning."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from audio_path_checker.diagnostics_engine import (
    check_invariants,
    classify_state,
    rank_hypotheses,
)
from audio_path_checker.models.states import AudioPathState
from audio_path_checker.pipeline import run_audio_path_diagnosis
from audio_path_checker.platform.winrt import format_capability_console
from audio_path_checker.providers.diagnosis import RuleDiagnosisProvider
from audio_path_checker.remediation.planner import plan_remediation


def _evidence(**overrides):
    base = {
        "timestamp": "2026-08-26T00:00:00+00:00",
        "device": {
            "name": "EDIFIER W800BT Pro",
            "paired": True,
            "connected": True,
            "address": "c8247887e57c",
            "instance_id": "BTHENUM\\DEV_C8247887E57C",
            "status": "OK",
            "last_connected": "1",
        },
        "bluetooth": {
            "adapter_present": True,
            "adapter_enabled": True,
            "adapter_status": "OK",
            "adapter_name": "MediaTek Bluetooth Adapter",
        },
        "audio": {
            "media_node_present": False,
            "a2dp_present": False,
            "endpoint_present": False,
            "endpoint_active": False,
            "is_default_playback": False,
        },
        "services": {
            "bthserv": "Running",
            "BTAGService": "Running",
            "BthAvctpSvc": "Running",
            "Audiosrv": "Running",
            "AudioEndpointBuilder": "Running",
        },
        "capabilities": {
            "capability": "bluetooth_discovery",
            "available": True,
            "reason": "",
        },
        "pnp": {"nodes": [], "a2dp_nodes": [], "media_nodes": [], "endpoint_nodes": []},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return base


class ScenarioTests(unittest.TestCase):
    def test_a_connected_without_endpoint(self):
        evidence = _evidence(
            audio={
                "media_node_present": True,
                "a2dp_present": True,
                "endpoint_present": False,
                "endpoint_active": False,
            }
        )
        classification = classify_state(evidence)
        self.assertEqual(classification["state"], AudioPathState.MEDIA_NO_ENDPOINT.value)
        self.assertLess(classification["confidence"], 1.0)
        hypotheses = rank_hypotheses(evidence, classification)
        self.assertEqual(hypotheses[0]["cause"], "audio_endpoint_enumeration_failure")
        inv = check_invariants(evidence)
        violated = [i for i in inv if not i["satisfied"]]
        self.assertTrue(
            any(i["invariant"] == "connected_device_requires_audio_endpoint" for i in violated)
        )

    def test_b_wrong_default_does_not_reset_bluetooth(self):
        evidence = _evidence(
            audio={
                "media_node_present": True,
                "a2dp_present": True,
                "endpoint_present": True,
                "endpoint_active": True,
                "is_default_playback": False,
            }
        )
        classification = classify_state(evidence)
        self.assertEqual(
            classification["state"], AudioPathState.ENDPOINT_NOT_DEFAULT.value
        )
        plan = plan_remediation(
            classification=classification,
            hypotheses=rank_hypotheses(evidence, classification),
            evidence=evidence,
            mode="aggressive-repair",
        )
        actions = [a["action"] for a in plan["actions"]]
        self.assertIn("set_default_playback_to_headset", actions)
        self.assertNotIn("clear_pairing_cache_and_repair", actions)
        self.assertNotIn("adapter_radio_bounce", actions)

    def test_c_winrt_unavailable_one_failure_no_spam(self):
        probe = {
            "capability": "bluetooth_discovery",
            "available": False,
            "reason": "winrt_type_unavailable",
            "powershell_version": "5.1.26100",
            "primary_failure": {
                "capability": "bluetooth_discovery",
                "available": False,
                "reason": "winrt_type_unavailable",
            },
        }
        text = format_capability_console(probe)
        self.assertIn("UNAVAILABLE", text)
        self.assertIn("Auto-pair has been skipped", text)
        self.assertEqual(text.count("UNAVAILABLE"), 1)

        evidence = _evidence(capabilities=probe)
        diagnosis = RuleDiagnosisProvider().diagnose(evidence)
        causes = [h["cause"] for h in diagnosis["hypotheses"]]
        self.assertIn("winrt_discovery_failure", causes)

    def test_d_adapter_disabled(self):
        evidence = _evidence(
            bluetooth={
                "adapter_present": True,
                "adapter_enabled": False,
                "adapter_status": "Error",
            },
            device={"paired": False, "connected": False},
        )
        classification = classify_state(evidence)
        self.assertEqual(
            classification["state"], AudioPathState.RADIO_UNAVAILABLE.value
        )

    def test_e_device_not_paired(self):
        evidence = _evidence(
            device={
                "name": "EDIFIER W800BT Pro",
                "paired": False,
                "connected": False,
                "address": None,
            }
        )
        classification = classify_state(evidence)
        self.assertEqual(
            classification["state"], AudioPathState.DEVICE_NOT_PAIRED.value
        )
        plan = plan_remediation(
            classification=classification,
            hypotheses=rank_hypotheses(evidence, classification),
            evidence=evidence,
            mode="diagnose",
        )
        self.assertEqual(plan["recommended"]["action"], "open_bluetooth_settings")

    def test_f_healthy_no_remediation(self):
        evidence = _evidence(
            audio={
                "media_node_present": True,
                "a2dp_present": True,
                "endpoint_present": True,
                "endpoint_active": True,
                "is_default_playback": True,
            }
        )
        classification = classify_state(evidence)
        self.assertEqual(
            classification["state"], AudioPathState.AUDIO_PATH_HEALTHY.value
        )
        plan = plan_remediation(
            classification=classification,
            hypotheses=rank_hypotheses(evidence, classification),
            evidence=evidence,
            mode="aggressive-repair",
        )
        self.assertEqual(plan["recommended"]["action"], "none")

    def test_pipeline_diagnose_writes_no_destructive_plan_by_default(self):
        evidence = _evidence(
            audio={
                "media_node_present": True,
                "a2dp_present": True,
                "endpoint_present": False,
                "endpoint_active": False,
            }
        )
        with patch(
            "audio_path_checker.pipeline.probe_winrt_capabilities",
            return_value={"available": True, "reason": ""},
        ):
            result = run_audio_path_diagnosis(
                device_name="EDIFIER W800BT Pro",
                mode="diagnose",
                evidence=evidence,
                write_artifacts=False,
                execute=False,
            )
        self.assertIn("MEDIA_NO_ENDPOINT", result["report_text"])
        self.assertEqual(result["plan"]["max_risk"], "R0")
        self.assertFalse(result["plan"].get("executable"))
        self.assertEqual(
            result["plan"]["recommended"]["action"], "refresh_audio_endpoint_inventory"
        )
        self.assertEqual(result["plan"]["recommended"]["risk"], "R1")
        self.assertTrue(
            any(
                a["action"] == "clear_pairing_cache_and_repair"
                for a in result["plan"]["blocked_actions"]
            )
        )


if __name__ == "__main__":
    unittest.main()
