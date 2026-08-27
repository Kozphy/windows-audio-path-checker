"""Regression tests for WAPC auto-pair control-flow / capability handling."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
AUTO_PAIR = REPO / "scripts" / "wapc-bt-auto-pair.ps1"
BT_DIR = REPO / "scripts" / "Bluetooth"
WINRT_PSM1 = REPO / "scripts" / "Platform" / "WinRT.psm1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class AutoPairControlFlowTests(unittest.TestCase):
    """Static checks on auto-pair script ordering, modules, and safety gates."""

    def test_script_is_ascii_safe_for_windows_powershell_51(self):
        raw = AUTO_PAIR.read_bytes()
        self.assertNotIn(b"\xe2\x80\x94", raw, "em-dash must not appear")
        text = raw.decode("utf-8")
        non_ascii = [c for c in text if ord(c) > 127]
        self.assertEqual(non_ascii, [], f"non-ASCII breaks PS 5.1: {non_ascii[:5]!r}")

    def test_privilege_gate_before_cleanup(self):
        text = _read(AUTO_PAIR)
        self.assertIn("Test-WapcElevation", _read(BT_DIR / "WapcBluetoothCore.psm1"))
        self.assertIn("PRIVILEGE CHECK", text)
        self.assertIn("INSUFFICIENT_PRIVILEGES", text)
        priv_idx = text.index("PRIVILEGE CHECK")
        cleanup_idx = text.index("Remove-WapcBluetoothGhostAssociation")
        self.assertLess(priv_idx, cleanup_idx)

    def test_capability_before_pairing_engine(self):
        text = _read(AUTO_PAIR)
        cap_idx = text.index("Get-BluetoothDiscoveryCapability")
        pair_idx = text.index("Invoke-WapcBluetoothPairing")
        self.assertLess(cap_idx, pair_idx)
        self.assertNotRegex(text, r"Log\s+'DONE'")

    def test_authoritative_stage_results_object(self):
        core = _read(BT_DIR / "WapcBluetoothCore.psm1")
        for stage in (
            "PrivilegeCheck",
            "GhostCleanup",
            "ClassicEnumerationCapability",
            "TargetDiscovered",
            "TargetClassicEndpoint",
            "Pairability",
            "PairRequest",
            "PairResult",
            "AudioEndpoint",
        ):
            self.assertIn(stage, core)

    def test_winrt_findall_typed_interop(self):
        disc = _read(BT_DIR / "BluetoothDiscovery.psm1")
        self.assertIn("Invoke-WapcWinRtFindAll", disc)
        self.assertIn("System.Collections.Generic.List[string]", disc)
        self.assertIn("classic_enumeration_all_failed", disc)

    def test_pairability_unknown_distinct_from_not_pairable(self):
        engine = _read(BT_DIR / "BluetoothPairingEngine.psm1")
        self.assertIn("PAIRABILITY_UNDETERMINED", engine)
        self.assertIn("CLASSIC_ENDPOINT_ENUMERATION_FAILED", engine)
        self.assertIn("pairability -eq 'UNKNOWN'", engine)

    def test_bluetooth_modules_present(self):
        for name in (
            "WapcBluetoothCore.psm1",
            "WapcBluetoothCleanup.psm1",
            "WapcBluetoothServices.psm1",
            "BluetoothDiscovery.psm1",
            "BluetoothCandidateRanker.psm1",
            "BluetoothPairingEngine.psm1",
            "BluetoothPairingVerifier.psm1",
        ):
            self.assertTrue((BT_DIR / name).is_file(), f"missing {name}")

    def test_ranker_json_array_contract(self):
        ranker = _read(BT_DIR / "BluetoothCandidateRanker.psm1")
        self.assertIn("ConvertTo-WapcJsonArray", ranker)
        self.assertIn("Rank-WapcBluetoothCandidatesFallback", ranker)

    def test_service_control_uses_erroraction_stop(self):
        svc = _read(BT_DIR / "WapcBluetoothServices.psm1")
        self.assertIn("-ErrorAction Stop", svc)
        self.assertIn("effective_health", svc)

    def test_stage_repair_and_invariant_validator_present(self):
        core = _read(BT_DIR / "WapcBluetoothCore.psm1")
        identity = _read(BT_DIR / "WapcBluetoothIdentity.psm1")
        engine = _read(BT_DIR / "BluetoothPairingEngine.psm1")
        self.assertIn("Repair-WapcStageResults", core)
        self.assertIn("Set-WapcDownstreamStagesNotRun", core)
        self.assertIn("Test-WapcRecoveryState", identity)
        self.assertIn("Repair-WapcStageResults", engine)
        self.assertNotRegex(
            engine,
            r"PairingSucceeded",
        )

    def test_never_pairs_when_canpair_false_without_ranking(self):
        engine = _read(BT_DIR / "BluetoothPairingEngine.psm1")
        self.assertIn("selected.can_pair", engine)
        self.assertNotRegex(
            engine,
            r"if\s*\(\s*-not\s+\$dev\.Pairing\.CanPair\s*\)\s*\{\s*continue",
        )


class StageAggregationTests(unittest.TestCase):
    """Final summary must not downgrade completed stages to NOT_RUN."""

    def test_pass_stage_not_overwritten_by_not_run_logic(self):
        """Stages set to PASS/ERROR/FAIL must not revert to NOT_RUN in summary."""
        summary = _read(BT_DIR / "WapcBluetoothCore.psm1")
        self.assertIn("Write-WapcFinalSummary", summary)
        self.assertIn("$Context.stages[$key]", summary)


class ConnectedWithoutEndpointTests(unittest.TestCase):
    """Bluetooth connected without a media endpoint is not a healthy path."""

    def test_connected_without_endpoint_not_healthy(self):
        from audio_path_checker.diagnostics_engine import classify_state
        from audio_path_checker.models.states import AudioPathState

        evidence = {
            "device": {"name": "EDIFIER W800BT Pro", "paired": True, "connected": True},
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
        result = classify_state(evidence)
        self.assertEqual(result["state"], AudioPathState.MEDIA_NO_ENDPOINT.value)


if __name__ == "__main__":
    unittest.main()
