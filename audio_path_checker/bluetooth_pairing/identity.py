"""Canonical Bluetooth target identity policy.

Root cause (false-positive SUCCESS): discovery/ranking treated any name-matched
or already-paired Bluetooth candidate as the recovery target, and verification
accepted any sibling headset's PnP/A2DP/audio endpoints (e.g. WH700NB) as proof
that W800BT Pro recovered. Address-aware identity matching is mandatory when a
target Bluetooth address is known.
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = 2

CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_EXACT = "EXACT"

DISPOSITION_ACCEPTED = "ACCEPTED"
DISPOSITION_REJECTED_WRONG_DEVICE = "REJECTED_WRONG_DEVICE"
DISPOSITION_REJECTED_INSUFFICIENT_IDENTITY = "REJECTED_INSUFFICIENT_IDENTITY"

REASON_ADDRESS_MATCH = "BLUETOOTH_ADDRESS_MATCH"
REASON_ADDRESS_MISMATCH = "BLUETOOTH_ADDRESS_MISMATCH"
REASON_NAME_EXACT_NO_ADDRESS = "EXACT_NAME_NO_ADDRESS"
REASON_NAME_MISMATCH = "NAME_MISMATCH"
REASON_NO_OBSERVED_IDENTITY = "NO_OBSERVED_IDENTITY"
REASON_EXPECTED_ADDRESS_REQUIRED = "EXPECTED_ADDRESS_REQUIRED_OBSERVED_ADDRESS_MISSING"
REASON_CONFIGURED_TARGET_MISMATCH = "CONFIGURED_TARGET_MISMATCH"

CANDIDATE_ROLE_NON_TARGET = "NON_TARGET_DEVICE"
IDENTITY_RESULT_DIFFERENT_DEVICE = "DIFFERENT_DEVICE"
ACTION_SKIP = "SKIP"


def normalize_bluetooth_address(value: str | None) -> str:
    """Normalize MAC variants to lowercase hex without separators.

    Args:
        value: Bluetooth address or embedded hex substring; empty returns ``""``.

    Returns:
        Up to 12 lowercase hex digits (trailing 12 kept if longer).

    Notes:
        Non-hex characters are stripped; unlike ``bluetooth.normalize_bluetooth_address``,
        invalid input yields empty string rather than raising — callers validate.
    """
    if not value:
        return ""
    hex_only = re.sub(r"[^0-9a-f]", "", str(value).casefold())
    if len(hex_only) > 12:
        hex_only = hex_only[-12:]
    return hex_only


def normalize_device_name(value: str | None) -> str:
    """Normalize a friendly name for exact comparison (casefold, collapse space).

    Args:
        value: Raw device or endpoint friendly name.

    Returns:
        Casefolded, whitespace-normalized string; empty when input is blank.

    Notes:
        Brand substring or partial matches are intentionally **not** supported.
    """
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def extract_address_from_instance_id(instance_id: str | None) -> str:
    """Pull a Bluetooth MAC from common Windows PnP / WinRT id forms.

    Args:
        instance_id: PnP ``InstanceId`` or WinRT device id string.

    Returns:
        Normalized 12-char hex address, or ``""`` if no MAC pattern matches.
    """
    text = instance_id or ""
    m = re.search(r"(?:DEV_|BluetoothDevice_|_)([0-9A-Fa-f]{12})(?:_|$|\\)", text)
    if m:
        return normalize_bluetooth_address(m.group(1))
    m = re.search(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", text)
    if m:
        return normalize_bluetooth_address(m.group(0))
    return ""


def build_target_identity(
    *,
    requested_name: str,
    bluetooth_address: str | None = None,
    pnp_instance_ids: list[str] | None = None,
    association_endpoint_ids: list[str] | None = None,
    container_ids: list[str] | None = None,
    audio_endpoint_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build the canonical ``TargetIdentity`` record for recovery.

    Args:
        requested_name: User-configured friendly name.
        bluetooth_address: Target MAC when known (primary identity key).
        pnp_instance_ids: Optional observed PnP ids for correlation.
        association_endpoint_ids: Optional WinRT AEP ids.
        container_ids: Optional device container GUIDs.
        audio_endpoint_ids: Optional Core Audio endpoint ids.

    Returns:
        Dict with normalized name/address fields and id lists.

    Notes:
        When ``bluetooth_address`` is set, all verification stages must match
        that address — sibling headsets with similar names are non-targets.
    """
    addr = normalize_bluetooth_address(bluetooth_address)
    return {
        "requested_name": requested_name or "",
        "normalized_name": normalize_device_name(requested_name),
        "bluetooth_address": addr,
        "normalized_bluetooth_address": addr,
        "pnp_instance_ids": list(pnp_instance_ids or []),
        "association_endpoint_ids": list(association_endpoint_ids or []),
        "container_ids": list(container_ids or []),
        "audio_endpoint_ids": list(audio_endpoint_ids or []),
    }


def _observed_address(observed: dict[str, Any]) -> str:
    """Extract normalized Bluetooth address from an observed device dict."""
    addr = normalize_bluetooth_address(
        observed.get("device_address")
        or observed.get("address")
        or observed.get("bluetooth_address")
    )
    if addr:
        return addr
    for key in ("id", "instance_id", "InstanceId", "pnp_instance_id"):
        addr = extract_address_from_instance_id(str(observed.get(key) or ""))
        if addr:
            return addr
    return ""


def match_bluetooth_identity(
    expected: dict[str, Any] | str,
    observed: dict[str, Any],
    *,
    expected_name: str | None = None,
    expected_address: str | None = None,
) -> dict[str, Any]:
    """Compare observed discovery/PnP evidence against expected target identity.

    Address dominates when configured: a name match without address proof is
    rejected if the expected MAC is known but missing on the candidate.

    Args:
        expected: ``TargetIdentity`` dict or legacy string name.
        observed: Candidate or PnP node dict (``name``, ``device_address``,
            ``id``, ``instance_id``, etc.).
        expected_name: Override name when ``expected`` is a string.
        expected_address: Override address when ``expected`` is a string.

    Returns:
        Structured result with ``matched``, ``confidence``, ``reason``,
        ``address_match``, ``name_match``, and optional ``name_mismatch_warning``
        when address matches but friendly names differ.

    Notes:
        Prevents false-positive SUCCESS from sibling headsets (e.g. WH700NB
        endpoints proving W800BT Pro "recovered"). Name-only matching uses
        exact normalized equality — never brand substring.
    """
    if isinstance(expected, str):
        expected = build_target_identity(
            requested_name=expected_name or expected,
            bluetooth_address=expected_address or "",
        )
    else:
        expected = dict(expected)
        if expected_name and not expected.get("requested_name"):
            expected["requested_name"] = expected_name
        if expected_address and not expected.get("normalized_bluetooth_address"):
            expected["normalized_bluetooth_address"] = normalize_bluetooth_address(
                expected_address
            )
            expected["bluetooth_address"] = expected["normalized_bluetooth_address"]

    exp_addr = normalize_bluetooth_address(
        expected.get("normalized_bluetooth_address")
        or expected.get("bluetooth_address")
        or expected_address
    )
    exp_name = normalize_device_name(
        expected.get("normalized_name")
        or expected.get("requested_name")
        or expected_name
    )
    obs_addr = _observed_address(observed)
    obs_name = normalize_device_name(observed.get("name") or observed.get("FriendlyName"))

    address_match = bool(exp_addr and obs_addr and exp_addr == obs_addr)
    name_match = bool(exp_name and obs_name and exp_name == obs_name)

    result: dict[str, Any] = {
        "matched": False,
        "confidence": CONFIDENCE_NONE,
        "expected_address": exp_addr,
        "observed_address": obs_addr,
        "expected_name": exp_name,
        "observed_name": obs_name,
        "address_match": address_match,
        "name_match": name_match,
        "reason": REASON_NO_OBSERVED_IDENTITY,
        "match_method": None,
    }

    if exp_addr and obs_addr:
        if address_match:
            result.update(
                {
                    "matched": True,
                    "confidence": CONFIDENCE_EXACT,
                    "reason": REASON_ADDRESS_MATCH,
                    "match_method": "bluetooth_address",
                }
            )
            if not name_match and obs_name and exp_name:
                result["name_mismatch_warning"] = (
                    f"address matches but name differs "
                    f"(expected={exp_name!r} observed={obs_name!r})"
                )
            return result
        result.update(
            {
                "matched": False,
                "confidence": CONFIDENCE_NONE,
                "reason": REASON_ADDRESS_MISMATCH,
                "match_method": "bluetooth_address",
            }
        )
        return result

    if exp_addr and not obs_addr:
        # Do not accept manufacturer/partial name when target address is known.
        if name_match:
            result.update(
                {
                    "matched": False,
                    "confidence": CONFIDENCE_LOW,
                    "reason": REASON_EXPECTED_ADDRESS_REQUIRED,
                    "match_method": "name_insufficient_without_address",
                }
            )
            return result
        result.update(
            {
                "matched": False,
                "confidence": CONFIDENCE_NONE,
                "reason": REASON_EXPECTED_ADDRESS_REQUIRED,
                "match_method": None,
            }
        )
        return result

    # No expected address: exact normalized name only (never brand substring).
    if name_match:
        result.update(
            {
                "matched": True,
                "confidence": CONFIDENCE_MEDIUM,
                "reason": REASON_NAME_EXACT_NO_ADDRESS,
                "match_method": "exact_normalized_name",
            }
        )
        return result

    result["reason"] = REASON_NAME_MISMATCH if obs_name else REASON_NO_OBSERVED_IDENTITY
    return result


def annotate_candidate_identity(
    candidate: dict[str, Any],
    *,
    target_name: str,
    target_address: str | None,
) -> dict[str, Any]:
    """Annotate one candidate with identity match fields and disposition.

    Args:
        candidate: Raw discovery candidate dict.
        target_name: Configured target name.
        target_address: Configured Bluetooth MAC when known.

    Returns:
        Copy of ``candidate`` with ``identity_match``, ``identity_matched``,
        ``disposition``, ``rejection_reason``, and related action fields set.
    """
    item = dict(candidate)
    identity = match_bluetooth_identity(
        build_target_identity(
            requested_name=target_name, bluetooth_address=target_address
        ),
        item,
    )
    item["identity_match"] = identity
    item["identity_matched"] = bool(identity.get("matched"))
    if identity.get("reason") == REASON_ADDRESS_MISMATCH:
        item["disposition"] = DISPOSITION_REJECTED_WRONG_DEVICE
        item["rejection_reason"] = REASON_CONFIGURED_TARGET_MISMATCH
        item["candidate_role"] = CANDIDATE_ROLE_NON_TARGET
        item["identity_result"] = IDENTITY_RESULT_DIFFERENT_DEVICE
        item["action"] = ACTION_SKIP
    elif not identity.get("matched"):
        item["disposition"] = DISPOSITION_REJECTED_INSUFFICIENT_IDENTITY
        item["rejection_reason"] = identity.get("reason")
    else:
        item["disposition"] = DISPOSITION_ACCEPTED
        item["rejection_reason"] = None
    return item


def filter_candidates_by_identity(
    candidates: list[dict[str, Any]],
    *,
    target_name: str,
    target_address: str | None,
) -> dict[str, Any]:
    """Filter discovery candidates before ranking or pair selection.

    Pipeline stage: DISCOVER → **IDENTITY FILTER**. Wrong-address devices
    receive ``REJECTED_WRONG_DEVICE`` and never enter selection.

    Args:
        candidates: Raw WinRT candidates from discovery.
        target_name: Configured target friendly name.
        target_address: Configured Bluetooth MAC (authoritative when non-empty).

    Returns:
        Dict with ``all``, ``accepted``, ``rejected``,
        ``exact_target_discovered``, and ``any_bluetooth_device_discovered``.

    Notes:
        ``any_bluetooth_device_discovered`` can be True while
        ``exact_target_discovered`` is False (sibling device visible).
    """
    annotated: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for c in candidates:
        item = annotate_candidate_identity(
            c, target_name=target_name, target_address=target_address
        )
        annotated.append(item)
        if item["disposition"] == DISPOSITION_ACCEPTED:
            accepted.append(item)
        else:
            rejected.append(item)
    return {
        "all": annotated,
        "accepted": accepted,
        "rejected": rejected,
        "exact_target_discovered": len(accepted) > 0,
        "any_bluetooth_device_discovered": len(annotated) > 0,
    }


def pnp_node_matches_target(
    *,
    friendly_name: str | None,
    instance_id: str | None,
    target_name: str,
    target_address: str | None,
) -> dict[str, Any]:
    """Correlate a PnP/MEDIA/AudioEndpoint node to ``TargetIdentity``.

    Args:
        friendly_name: PnP ``FriendlyName`` or endpoint label.
        instance_id: PnP ``InstanceId`` (MAC may be embedded).
        target_name: Configured recovery target name.
        target_address: Configured Bluetooth MAC when known.

    Returns:
        Same structured dict as ``match_bluetooth_identity``.

    Notes:
        Audio endpoint presence without identity match must not count as
        recovery success — connected Bluetooth ≠ working audio path.
    """
    observed = {
        "name": friendly_name,
        "instance_id": instance_id,
        "device_address": extract_address_from_instance_id(instance_id),
    }
    return match_bluetooth_identity(
        build_target_identity(
            requested_name=target_name, bluetooth_address=target_address
        ),
        observed,
    )


def repair_stage_results(stages: dict[str, str]) -> list[str]:
    """Normalize impossible stage combinations (mirrors Repair-WapcStageResults).

    Args:
        stages: Mutable map of stage name → status (``PASS``, ``FAIL``,
            ``NOT_RUN``, etc.).

    Returns:
        List of human-readable repair actions applied (e.g.
        ``PairResult->NOT_RUN(pair_request_not_executed)``).

    Notes:
        Mutates ``stages`` in place. Decision inputs may read lowercase /
        JSON aliases (``configured_target_found``, ``pair_request``), but
        writes target PascalCase orchestrator keys
        (``TargetClassicEndpoint``, ``PairResult``, …). Prevents reporting
        pair/audio success when configured-target discovery failed.
    """
    repaired: list[str] = []
    target_found = stages.get("TargetDiscovered") or stages.get("configured_target_found")
    pair_request = stages.get("PairRequest") or stages.get("pair_request") or "NOT_RUN"
    pair_result = stages.get("PairResult") or stages.get("pair_result") or "NOT_RUN"

    if target_found in {"FAIL", "NOT_FOUND"}:
        for stage in (
            "TargetClassicEndpoint",
            "Pairability",
            "PairableEndpoint",
            "PairRequest",
            "PairResult",
            "AudioEndpoint",
        ):
            if stages.get(stage) in {"PASS", "FAIL", "ERROR", "UNKNOWN"}:
                stages[stage] = "NOT_RUN"
                repaired.append(f"{stage}->NOT_RUN(target_not_discovered)")

    if pair_request in {"NOT_RUN", "NOT_ATTEMPTED", "BLOCKED", "SKIPPED"}:
        if pair_result in {"PASS", "FAIL", "ERROR"}:
            stages["PairResult"] = "NOT_RUN"
            repaired.append("PairResult->NOT_RUN(pair_request_not_executed)")

    return repaired


def check_recovery_invariants(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return invariant violations; empty list means state is consistent.

    Args:
        state: Orchestrator recovery snapshot (pair stages, identity flags,
            ``final_success``, endpoint discovery flags).

    Returns:
        List of violation dicts with ``invariant``, ``code``, and ``detail``.
        Empty when no logical contradictions are detected.

    Notes:
        Key invariants: ``final_success`` requires exact target discovery;
        audio/A2DP endpoint flags require matching identity proof; pair result
        cannot pass without pair request (unless already paired).
    """
    violations: list[dict[str, Any]] = []

    pair_request = str(state.get("pair_request") or "NOT_RUN").upper()
    pair_result = str(state.get("pair_result") or "").upper()
    pairing_succeeded = bool(state.get("pairing_succeeded"))
    if not pair_result and pairing_succeeded:
        pair_result = "PASS"
    exact_already_paired = bool(state.get("exact_target_already_paired"))
    exact_discovered = bool(state.get("exact_target_discovered"))
    exact_audio = bool(state.get("exact_target_audio_endpoint_found"))
    exact_a2dp = bool(state.get("exact_target_a2dp_endpoint_found"))
    audio_identity = bool(state.get("audio_endpoint_identity_match"))
    a2dp_identity = bool(state.get("a2dp_endpoint_identity_match"))
    final_success = bool(state.get("final_success"))
    target_discovered_stage = str(state.get("target_discovered_stage") or "").upper()

    if (
        pair_result == "PASS"
        and pair_request in {"NOT_RUN", "NOT_ATTEMPTED"}
        and not exact_already_paired
    ):
        violations.append(
            {
                "invariant": 1,
                "code": "PAIRING_SUCCEEDED_WITHOUT_REQUEST_OR_ALREADY_PAIRED",
                "detail": "PairResult cannot be PASS when PairRequest was not run "
                "and ExactTargetAlreadyPaired is false",
            }
        )

    if pair_result == "FAIL" and pair_request in {"NOT_RUN", "NOT_ATTEMPTED"}:
        violations.append(
            {
                "invariant": 7,
                "code": "PAIR_RESULT_FAIL_WITHOUT_REQUEST",
                "detail": "PairResult FAIL requires an executed PairRequest",
            }
        )

    if target_discovered_stage in {"FAIL", "NOT_FOUND"} and pair_request in {"PASS", "ATTEMPTED"}:
        violations.append(
            {
                "invariant": 9,
                "code": "PAIR_REQUEST_WITHOUT_TARGET_DISCOVERY",
                "detail": "PairRequest cannot succeed when configured target was not discovered",
            }
        )

    if exact_audio and not audio_identity:
        violations.append(
            {
                "invariant": 2,
                "code": "AUDIO_ENDPOINT_WITHOUT_IDENTITY",
                "detail": "ExactTargetAudioEndpointFound requires AudioEndpointIdentityMatch",
            }
        )

    if exact_a2dp and not a2dp_identity:
        violations.append(
            {
                "invariant": 3,
                "code": "A2DP_ENDPOINT_WITHOUT_IDENTITY",
                "detail": "ExactTargetA2dpEndpointFound requires A2dpEndpointIdentityMatch",
            }
        )

    if exact_discovered is False and state.get("target_discovered_stage") == "PASS":
        violations.append(
            {
                "invariant": 4,
                "code": "TARGET_DISCOVERED_WITHOUT_IDENTITY",
                "detail": "TargetDiscovered PASS requires an identity-matched candidate",
            }
        )

    if final_success and not exact_discovered:
        violations.append(
            {
                "invariant": 5,
                "code": "SUCCESS_WITHOUT_EXACT_TARGET",
                "detail": "Wrong-device candidates cannot affect success state",
            }
        )

    if state.get("cleanup_removed_wrong_device"):
        violations.append(
            {
                "invariant": 6,
                "code": "CLEANUP_REMOVED_WRONG_DEVICE",
                "detail": "Cleanup must never remove an identity-mismatched device",
            }
        )

    return violations


def test_recovery_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Alias for ``check_recovery_invariants`` (Test-WapcRecoveryState mirror).

    Args:
        state: Orchestrator recovery snapshot.

    Returns:
        Same violation list as ``check_recovery_invariants``.
    """
    return check_recovery_invariants(state)


# Exit codes for CLI / orchestrator (extend, do not invent conflicting meanings).
EXIT_SUCCESS = 0
EXIT_TARGET_NOT_DISCOVERED = 10
EXIT_TARGET_IDENTITY_MISMATCH = 11
EXIT_TARGET_NOT_PAIRABLE = 12
EXIT_PAIRING_FAILED = 13
EXIT_PAIRING_TIMEOUT = 14
EXIT_PNP_PATH_MISSING = 20
EXIT_A2DP_PATH_MISSING = 21
EXIT_AUDIO_ENDPOINT_MISSING = 22
EXIT_DISCOVERY_FAILURE = 30
EXIT_SERVICE_FAILURE = 31
EXIT_ADAPTER_FAILURE = 32
EXIT_CLEANUP_FAILURE = 40
EXIT_INVARIANT_FAILURE = 90

CLASSIFICATION_EXIT_CODES: dict[str, int] = {
    "SUCCESS": EXIT_SUCCESS,
    "TARGET_NOT_DISCOVERED": EXIT_TARGET_NOT_DISCOVERED,
    "TARGET_IDENTITY_MISMATCH": EXIT_TARGET_IDENTITY_MISMATCH,
    "TARGET_NOT_PAIRABLE": EXIT_TARGET_NOT_PAIRABLE,
    "DISCOVERABLE_NOT_PAIRABLE": EXIT_TARGET_NOT_PAIRABLE,
    "PAIRING_FAILED": EXIT_PAIRING_FAILED,
    "PAIR_REQUEST_FAILED": EXIT_PAIRING_FAILED,
    "PAIRING_REJECTED": EXIT_PAIRING_FAILED,
    "PAIRING_TIMEOUT": EXIT_PAIRING_TIMEOUT,
    "PNP_PATH_MISSING": EXIT_PNP_PATH_MISSING,
    "A2DP_PATH_MISSING": EXIT_A2DP_PATH_MISSING,
    "A2DP_ENDPOINT_TIMEOUT": EXIT_A2DP_PATH_MISSING,
    "AUDIO_ENDPOINT_MISSING": EXIT_AUDIO_ENDPOINT_MISSING,
    "AUDIO_ENDPOINT_TIMEOUT": EXIT_AUDIO_ENDPOINT_MISSING,
    "PAIRING_SUCCEEDED_AUDIO_ENDPOINT_MISSING": EXIT_AUDIO_ENDPOINT_MISSING,
    "DISCOVERY_API_UNAVAILABLE": EXIT_DISCOVERY_FAILURE,
    "DISCOVERY_ENUMERATION_FAILED": EXIT_DISCOVERY_FAILURE,
    "CLASSIC_ENDPOINT_ENUMERATION_FAILED": EXIT_DISCOVERY_FAILURE,
    "SERVICE_FAILURE": EXIT_SERVICE_FAILURE,
    "SERVICE_CONTROL_FAILED": EXIT_SERVICE_FAILURE,
    "ADAPTER_FAILURE": EXIT_ADAPTER_FAILURE,
    "ADAPTER_RESET_FAILED": EXIT_ADAPTER_FAILURE,
    "CLEANUP_FAILURE": EXIT_CLEANUP_FAILURE,
    "GHOST_CLEANUP_FAILED": EXIT_CLEANUP_FAILURE,
    "INTERNAL_STATE_INVARIANT_FAILURE": EXIT_INVARIANT_FAILURE,
}


def exit_code_for_classification(classification: str | None) -> int:
    """Map a failure classification string to a process exit code.

    Args:
        classification: ``FailureReason`` value or orchestrator classification
            label; ``None`` or unknown values map to ``1``.

    Returns:
        Integer exit code (``0`` for ``SUCCESS``; see ``CLASSIFICATION_EXIT_CODES``).
    """
    if not classification:
        return 1
    return CLASSIFICATION_EXIT_CODES.get(classification, 1)
