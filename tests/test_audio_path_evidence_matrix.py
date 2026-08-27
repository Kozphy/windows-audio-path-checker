"""Regression matrix for evidence-driven Bluetooth audio path diagnosis."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_path_checker.diagnostics_engine import (
    build_evidence_graph,
    classify_state,
    normalize_bluetooth_address,
    rank_hypotheses,
)
from audio_path_checker.models.states import AudioPathState, CheckStatus
from audio_path_checker.pipeline import replay_session, run_audio_path_diagnosis
from audio_path_checker.remediation.planner import plan_remediation
from audio_path_checker.remediation.refresh import refresh_audio_endpoint_inventory


SESSION = Path("artifacts/sessions/2026-08-27T170534")


def _evidence(**overrides):
    base = {
        "timestamp": "2026-08-27T00:00:00+00:00",
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
            "is_default_playback": None,
            "endpoints": [],
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
        "environment": {"device_filter": "EDIFIER W800BT Pro"},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return base


class AddressNormalizationTests(unittest.TestCase):
    def test_formats_collapse(self):
        variants = [
            "CC14BC0BDE24",
            "CC:14:BC:0B:DE:24",
            "cc-14-bc-0b-de-24",
            "cc14bc0bde24",
        ]
        normalized = {normalize_bluetooth_address(v) for v in variants}
        self.assertEqual(normalized, {"cc14bc0bde24"})


class SessionReplayTests(unittest.TestCase):
    def test_real_session_is_genuine_disconnect_not_stale_pnp(self):
        if not (SESSION / "evidence-before.json").is_file():
            self.skipTest("session artifacts not present")
        result = replay_session(SESSION, write_artifacts=False)
        classification = result["diagnosis"]["classification"]
        self.assertEqual(classification["state"], AudioPathState.PAIRED_NOT_CONNECTED.value)
        self.assertEqual(
            result["diagnosis"]["hypotheses"][0]["cause"],
            "bluetooth_device_disconnected",
        )
        self.assertEqual(
            result["plan"]["recommended"]["action"], "connect_headset_and_recheck"
        )
        checks = classification["checks"]
        self.assertEqual(checks["a2dp"], CheckStatus.NOT_APPLICABLE.value)
        self.assertEqual(checks["media"], CheckStatus.NOT_APPLICABLE.value)
        self.assertEqual(checks["endpoint"], CheckStatus.NOT_APPLICABLE.value)
        self.assertTrue(result["shadow_comparison"]["changed"])


class ScenarioMatrixTests(unittest.TestCase):
    def test_a_paired_physically_disconnected(self):
        evidence = _evidence(
            device={"paired": True, "connected": False, "last_connected": None},
            audio={
                "media_node_present": False,
                "a2dp_present": False,
                "endpoint_present": False,
            },
        )
        c = classify_state(evidence)
        self.assertEqual(c["state"], AudioPathState.PAIRED_NOT_CONNECTED.value)
        self.assertEqual(rank_hypotheses(evidence, c)[0]["cause"], "bluetooth_device_disconnected")

    def test_b_connected_a2dp_delayed_settling(self):
        evidence = _evidence(
            device={"connected": True},
            audio={"a2dp_present": False, "media_node_present": False},
        )
        pending = classify_state(evidence, settling=True, elapsed_ms=420)
        self.assertEqual(pending["state"], AudioPathState.PROFILE_ENUMERATION_PENDING.value)
        self.assertEqual(pending["checks"]["a2dp"], CheckStatus.PENDING.value)
        hard = classify_state(evidence, settling=False)
        self.assertEqual(hard["state"], AudioPathState.CONNECTED_NO_A2DP.value)

    def test_c_media_exists_endpoint_delayed(self):
        evidence = _evidence(
            audio={
                "a2dp_present": True,
                "media_node_present": True,
                "endpoint_present": False,
            },
            pnp={
                "a2dp_nodes": [{"name": "EDIFIER W800BT Pro", "address": "c8247887e57c"}],
                "media_nodes": [{"name": "EDIFIER W800BT Pro", "address": "c8247887e57c"}],
                "endpoint_nodes": [],
            },
        )
        pending = classify_state(evidence, settling=True)
        self.assertEqual(pending["state"], AudioPathState.ENDPOINT_ENUMERATION_PENDING.value)
        hard = classify_state(evidence)
        self.assertEqual(hard["state"], AudioPathState.MEDIA_NO_ENDPOINT.value)

    def test_d_endpoint_disabled(self):
        evidence = _evidence(
            audio={
                "a2dp_present": True,
                "media_node_present": True,
                "endpoint_present": True,
                "endpoint_active": False,
            }
        )
        self.assertEqual(
            classify_state(evidence)["state"], AudioPathState.ENDPOINT_DISABLED.value
        )

    def test_e_fully_connected(self):
        evidence = _evidence(
            audio={
                "a2dp_present": True,
                "media_node_present": True,
                "endpoint_present": True,
                "endpoint_active": True,
                "is_default_playback": True,
            }
        )
        c = classify_state(evidence)
        self.assertEqual(c["state"], AudioPathState.AUDIO_PATH_HEALTHY.value)
        plan = plan_remediation(
            classification=c,
            hypotheses=rank_hypotheses(evidence, c),
            evidence=evidence,
            mode="aggressive-repair",
        )
        self.assertEqual(plan["recommended"]["action"], "none")

    def test_f_windows_audio_stopped(self):
        evidence = _evidence(
            services={"Audiosrv": "Stopped", "AudioEndpointBuilder": "Running"}
        )
        c = classify_state(evidence)
        self.assertEqual(c["state"], AudioPathState.AUDIO_SERVICE_FAILURE.value)
        self.assertNotEqual(rank_hypotheses(evidence, c)[0]["cause"], "stale_pnp_state")

    def test_g_ghost_media_other_mac(self):
        evidence = _evidence(
            device={"paired": True, "connected": False, "address": "c8247887e57c"},
            audio={"media_node_present": True, "endpoint_present": True},
            pnp={
                "media_nodes": [
                    {"name": "EDIFIER WH700NB", "address": "cc14bc0bde24", "instance_id": "MEDIA\\DEV_CC14BC0BDE24"}
                ],
                "endpoint_nodes": [
                    {"name": "EDIFIER WH700NB", "address": "cc14bc0bde24"}
                ],
            },
        )
        c = classify_state(evidence)
        self.assertEqual(c["state"], AudioPathState.PAIRED_NOT_CONNECTED.value)
        self.assertFalse(c["evidence_graph"]["flags"]["inventory_present"])
        self.assertTrue(c["evidence_graph"]["flags"]["ghost_inventory"])

    def test_h_stale_inventory_same_mac(self):
        evidence = _evidence(
            device={"paired": True, "connected": False, "address": "c8247887e57c"},
            audio={"media_node_present": True, "endpoint_present": True},
            pnp={
                "media_nodes": [
                    {"name": "EDIFIER W800BT Pro", "address": "c8247887e57c"}
                ],
                "endpoint_nodes": [
                    {"name": "EDIFIER W800BT Pro", "address": "c8247887e57c"}
                ],
            },
        )
        c = classify_state(evidence)
        self.assertEqual(c["state"], AudioPathState.STALE_PNP_INVENTORY.value)
        self.assertEqual(rank_hypotheses(evidence, c)[0]["cause"], "stale_pnp_state")

    def test_i_two_edifier_devices_identity(self):
        evidence = _evidence(
            device={"name": "EDIFIER W800BT Pro", "address": "c8247887e57c", "connected": True},
            audio={"media_node_present": True, "a2dp_present": True, "endpoint_present": True, "endpoint_active": True},
            pnp={
                "media_nodes": [
                    {"name": "EDIFIER WH700NB", "address": "cc14bc0bde24"},
                    {"name": "EDIFIER W800BT Pro", "address": "c8247887e57c"},
                ],
                "a2dp_nodes": [{"name": "EDIFIER W800BT Pro", "address": "c8247887e57c"}],
                "endpoint_nodes": [{"name": "EDIFIER W800BT Pro", "address": "c8247887e57c"}],
            },
        )
        graph = build_evidence_graph(evidence)
        self.assertEqual(len(graph["matched"]["media_nodes"]), 1)
        self.assertEqual(graph["matched"]["media_nodes"][0]["address"], "c8247887e57c")
        self.assertEqual(len(graph["ghosts"]["media_nodes"]), 1)

    def test_j_default_output_unknown_not_fail(self):
        evidence = _evidence(audio={"is_default_playback": None})
        c = classify_state(evidence)
        self.assertEqual(c["checks"]["default_output"], CheckStatus.UNKNOWN.value)

    def test_k_r1_settle_partial_then_recover(self):
        stages = [
            _evidence(audio={"a2dp_present": False, "media_node_present": False, "endpoint_present": False}),
            _evidence(audio={"a2dp_present": True, "media_node_present": True, "endpoint_present": False}),
            _evidence(
                audio={
                    "a2dp_present": True,
                    "media_node_present": True,
                    "endpoint_present": True,
                    "endpoint_active": True,
                    "is_default_playback": True,
                }
            ),
        ]
        calls = {"n": 0}

        def collect():
            idx = min(calls["n"], len(stages) - 1)
            calls["n"] += 1
            return stages[idx]

        with (
            patch("audio_path_checker.remediation.refresh.sys.platform", "win32"),
            patch(
                "audio_path_checker.remediation.refresh.subprocess.run",
                return_value=type("C", (), {"returncode": 0, "stderr": ""})(),
            ),
            patch("audio_path_checker.remediation.refresh.time.sleep"),
        ):
            result = refresh_audio_endpoint_inventory(
                collect_fn=collect,
                schedule_ms=(0, 10, 20),
            )
        self.assertTrue(result["progress"] or result["recovered"])
        self.assertTrue(result["recovered"])
        self.assertTrue(result["postcondition_met"])
        self.assertGreaterEqual(len(result["attempts"]), 2)

    def test_l_r1_never_contains_mutations(self):
        with (
            patch("audio_path_checker.remediation.refresh.sys.platform", "win32"),
            patch(
                "audio_path_checker.remediation.refresh.subprocess.run",
                return_value=type("C", (), {"returncode": 0, "stderr": ""})(),
            ) as run,
            patch("audio_path_checker.remediation.refresh.time.sleep"),
        ):
            refresh_audio_endpoint_inventory(settle_seconds=0)
        script = run.call_args.args[0][-1]
        for banned in ("pnputil", "Disable-PnpDevice", "Restart-Service", "Remove-PnpDevice"):
            self.assertNotIn(banned, script)

    def test_m_pipeline_repair_escalates_after_failed_settle(self):
        evidence = _evidence(
            device={"connected": True},
            audio={"a2dp_present": False, "media_node_present": False, "endpoint_present": False},
        )

        def fake_refresh(**_kwargs):
            return {
                "action": "refresh_audio_endpoint_inventory",
                "attempted": True,
                "command_succeeded": True,
                "recovered": False,
                "progress": False,
                "postcondition_met": False,
                "attempts": [
                    {
                        "attempt": 1,
                        "elapsed_ms": 0,
                        "state": "PROFILE_ENUMERATION_PENDING",
                        "media": False,
                        "endpoint": False,
                        "connected": True,
                        "a2dp": False,
                    }
                ],
                "escalation_recommended": "restart_bluetooth_audio_services",
                "detail": "inventory_queried",
            }

        with (
            patch(
                "audio_path_checker.pipeline.refresh_audio_endpoint_inventory",
                side_effect=fake_refresh,
            ),
            patch("audio_path_checker.pipeline.collect_evidence", return_value=evidence),
        ):
            result = run_audio_path_diagnosis(
                mode="repair",
                evidence=evidence,
                write_artifacts=False,
                execute=True,
            )
        self.assertFalse(result["verification"]["system_recovered"])
        self.assertEqual(
            result["plan"]["recommended"]["action"],
            "restart_bluetooth_audio_services",
        )

    def test_endpoint_headphones_name_and_a2dp_instance_mac(self):
        evidence = _evidence(
            device={
                "name": "EDIFIER W800BT Pro Avrcp Transport",
                "address": "c8247887e57c",
                "connected": True,
                "instance_id": "BTHENUM\\{0000110C-0000-1000-8000-00805F9B34FB}_LOCAL\\C8247887E57C_C00000000",
            },
            environment={"device_filter": "EDIFIER W800BT Pro"},
            audio={
                "a2dp_present": True,
                "media_node_present": True,
                "endpoint_present": True,
                "endpoint_active": True,
                "is_default_playback": True,
            },
            pnp={
                "a2dp_nodes": [
                    {
                        "name": "EDIFIER W800BT Pro",
                        "status": "OK",
                        "class": "MEDIA",
                        "instance_id": (
                            "BTHENUM\\{0000110B-0000-1000-8000-00805F9B34FB}_VID&000105D6_PID&000A\\"
                            "A&19B543A3&0&C8247887E57C_C00000000"
                        ),
                    }
                ],
                "media_nodes": [
                    {
                        "name": "EDIFIER W800BT Pro",
                        "status": "OK",
                        "class": "MEDIA",
                        "instance_id": (
                            "BTHENUM\\{0000110B-0000-1000-8000-00805F9B34FB}_VID&000105D6_PID&000A\\"
                            "A&19B543A3&0&C8247887E57C_C00000000"
                        ),
                    }
                ],
                "endpoint_nodes": [
                    {
                        "name": "Headphones (EDIFIER W800BT Pro)",
                        "status": "OK",
                        "class": "AudioEndpoint",
                        "instance_id": "SWD\\MMDEVAPI\\{0.0.0.00000000}.{DE9F69F1}",
                    }
                ],
            },
        )
        c = classify_state(evidence)
        self.assertEqual(c["state"], AudioPathState.AUDIO_PATH_HEALTHY.value)
        self.assertGreaterEqual(len(c["evidence_graph"]["matched"]["a2dp_nodes"]), 1)
        self.assertGreaterEqual(len(c["evidence_graph"]["matched"]["endpoint_nodes"]), 1)


if __name__ == "__main__":
    unittest.main()
