from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BluetoothHypothesis:
    code: str
    title: str
    probability: float
    confidence: str
    evidence: tuple[str, ...]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = list(self.evidence)
        return payload


def _confidence(probability: float) -> str:
    if probability >= 0.90:
        return "high"
    if probability >= 0.75:
        return "medium"
    return "low"


def _service_running(service: Any) -> bool | None:
    if not isinstance(service, dict):
        return None
    status = str(service.get("status") or "").casefold()
    if not status:
        return None
    return status == "running"


def _adapter_healthy(adapter: dict[str, Any]) -> bool:
    status = str(adapter.get("status") or "").casefold()
    problem = adapter.get("problem_code")
    cm_error = str(adapter.get("config_manager_error") or "").casefold()
    present = adapter.get("is_present")
    return (
        status in {"ok", "started"}
        and problem in {None, 0}
        and not cm_error
        and present is not False
    )


def infer_bluetooth_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Infer Bluetooth capability and audio-path state from collected evidence.

    The result intentionally distinguishes observed facts from hypotheses. Scores
    are transparent rule-derived diagnostic confidence values, not calibrated
    population probabilities.
    """
    bluetooth = snapshot.get("bluetooth") or {}
    core_audio = snapshot.get("core_audio") or {}

    adapters = list(bluetooth.get("adapters") or [])
    headsets = list(bluetooth.get("paired_headsets") or [])
    default_endpoint = core_audio.get("default_endpoint") or {}
    default_name = str(default_endpoint.get("name") or bluetooth.get("default_endpoint_name") or "")
    endpoint_present = bluetooth.get("default_endpoint_present")

    healthy_adapters = [item for item in adapters if _adapter_healthy(item)]
    unhealthy_adapters = [item for item in adapters if not _adapter_healthy(item)]
    present_headsets = [item for item in headsets if item.get("is_present") is True]

    bthserv_running = _service_running(bluetooth.get("bluetooth_service"))
    avctp_running = _service_running(bluetooth.get("avctp_service"))
    gateway_running = _service_running(bluetooth.get("audio_gateway_service"))

    evidence: list[str] = []
    if adapters:
        evidence.append(f"Bluetooth adapters observed: {len(adapters)}")
    else:
        evidence.append("No Bluetooth adapter was observed in the current PnP scan")
    if healthy_adapters:
        evidence.append(f"Healthy Bluetooth adapters: {len(healthy_adapters)}")
    if unhealthy_adapters:
        evidence.append(f"Unhealthy/disabled Bluetooth adapters: {len(unhealthy_adapters)}")
    if headsets:
        evidence.append(f"Previously paired audio-capable Bluetooth devices: {len(headsets)}")
    if present_headsets:
        evidence.append(f"Bluetooth headsets currently present: {len(present_headsets)}")
    if default_name:
        evidence.append(f"Current default audio endpoint: {default_name}")
    if endpoint_present is not None:
        evidence.append(f"Default endpoint present in PnP: {bool(endpoint_present)}")

    hypotheses: list[BluetoothHypothesis] = []

    if healthy_adapters:
        hypotheses.append(
            BluetoothHypothesis(
                code="bluetooth-capability-present",
                title="Bluetooth hardware is present and currently usable",
                probability=0.99,
                confidence="high",
                evidence=tuple(evidence[:4]),
                recommendation="Continue with pairing, profile, endpoint, and app-routing checks.",
            )
        )
    elif adapters:
        hypotheses.append(
            BluetoothHypothesis(
                code="bluetooth-adapter-unhealthy",
                title="Bluetooth hardware is detected but the adapter is disabled or unhealthy",
                probability=0.97,
                confidence="high",
                evidence=tuple(evidence[:4]),
                recommendation="Enable or repair the Bluetooth adapter and driver, then rescan before re-pairing devices.",
            )
        )
    elif headsets:
        hypotheses.append(
            BluetoothHypothesis(
                code="bluetooth-capability-historical",
                title="This machine likely has Bluetooth capability, but no adapter is currently enumerated",
                probability=0.92,
                confidence="high",
                evidence=tuple(evidence[:3]),
                recommendation="Check Device Manager, BIOS/radio state, USB/PCIe enumeration, and the Bluetooth driver.",
            )
        )
    else:
        hypotheses.append(
            BluetoothHypothesis(
                code="bluetooth-capability-unknown",
                title="Bluetooth capability cannot be established from the current evidence",
                probability=0.58,
                confidence="low",
                evidence=tuple(evidence[:2]),
                recommendation="Inspect PnP hardware inventory and OEM specifications before concluding the machine lacks Bluetooth.",
            )
        )

    if healthy_adapters and headsets and not present_headsets:
        hypotheses.append(
            BluetoothHypothesis(
                code="headset-not-currently-connected",
                title="A Bluetooth audio device was paired previously but is probably not connected now",
                probability=0.88,
                confidence="medium",
                evidence=tuple(evidence),
                recommendation="Put the headset in connectable mode, reconnect it, and verify that an A2DP/HFP audio endpoint appears.",
            )
        )

    if healthy_adapters and present_headsets and endpoint_present is False:
        hypotheses.append(
            BluetoothHypothesis(
                code="bluetooth-audio-endpoint-missing",
                title="Bluetooth is connected at the device layer but the Windows audio endpoint is missing",
                probability=0.91,
                confidence="high",
                evidence=tuple(evidence),
                recommendation="Restart Bluetooth audio/profile services or re-pair the device, then verify A2DP/HFP endpoint creation.",
            )
        )

    if healthy_adapters and bthserv_running is False:
        hypotheses.append(
            BluetoothHypothesis(
                code="bluetooth-service-stopped",
                title="Bluetooth hardware is available but the core Bluetooth service is stopped",
                probability=0.96,
                confidence="high",
                evidence=tuple(evidence + ["bthserv is not running"]),
                recommendation="Start the Bluetooth Support Service and rescan.",
            )
        )

    if healthy_adapters and present_headsets and endpoint_present is not False:
        if avctp_running is False or gateway_running is False:
            hypotheses.append(
                BluetoothHypothesis(
                    code="bluetooth-audio-profile-service-degraded",
                    title="Bluetooth device connectivity exists but an audio-profile service is degraded",
                    probability=0.83,
                    confidence="medium",
                    evidence=tuple(evidence),
                    recommendation="Restart BthAvctpSvc/BTAGService and verify A2DP/HFP profile activation.",
                )
            )
        elif default_name:
            hypotheses.append(
                BluetoothHypothesis(
                    code="bluetooth-audio-path-available",
                    title="Bluetooth hardware, device presence, and an audio endpoint are all observed",
                    probability=0.90,
                    confidence="high",
                    evidence=tuple(evidence),
                    recommendation="If audio is still silent, continue at the Windows/app session routing layer rather than the Bluetooth radio layer.",
                )
            )

    hypotheses.sort(key=lambda item: item.probability, reverse=True)

    capability = (
        "present"
        if healthy_adapters
        else "detected-unhealthy"
        if adapters
        else "historical-likely"
        if headsets
        else "unknown"
    )
    connection = (
        "present" if present_headsets else "paired-not-present" if headsets else "none-observed"
    )

    return {
        "schema_version": 1,
        "method": "transparent-evidence-state-inference",
        "capability": capability,
        "connection": connection,
        "adapter_count": len(adapters),
        "healthy_adapter_count": len(healthy_adapters),
        "paired_headset_count": len(headsets),
        "present_headset_count": len(present_headsets),
        "default_endpoint_name": default_name or None,
        "default_endpoint_present": endpoint_present,
        "hypotheses": [item.to_dict() for item in hypotheses],
        "top_hypothesis": hypotheses[0].to_dict() if hypotheses else None,
        "disclaimer": (
            "Scores are rule-derived diagnostic confidence values and are not population-calibrated probabilities."
        ),
    }
