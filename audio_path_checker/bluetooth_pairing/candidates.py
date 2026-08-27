"""Deterministic Bluetooth candidate classification and ranking.

Identity filtering (exact target address) runs before ranking/selection so a
sibling headset can never become the selected recovery target.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .identity import (
    DISPOSITION_ACCEPTED,
    DISPOSITION_REJECTED_WRONG_DEVICE,
    filter_candidates_by_identity,
    normalize_bluetooth_address,
    normalize_device_name,
)

# WinRT protocol GUIDs (classic vs BLE)
BT_CLASSIC_PROTOCOL = "{E0CBF06C-CD8B-4647-BB8A-263B43F0F974}"
BT_BLE_PROTOCOL = "{BB7BB05E-5972-42B5-94FC-76EAA7084D49}"

PAIRABLE = "PAIRABLE"
NOT_PAIRABLE = "NOT_PAIRABLE"
PAIRABILITY_UNKNOWN = "UNKNOWN"

CLASSIFICATION = (
    "PairableClassicBluetooth",
    "AlreadyPairedBluetooth",
    "NonPairableBluetoothEndpoint",
    "BLEEndpoint",
    "AudioEndpoint",
    "UnknownBluetoothEndpoint",
    "StaleEndpoint",
    "RejectedWrongDevice",
)


def _norm_name(value: str | None) -> str:
    return normalize_device_name(value)


def _norm_addr(value: str | None) -> str:
    return normalize_bluetooth_address(value)


def _protocol_kind(protocol_id: str | None) -> str:
    p = (protocol_id or "").upper()
    if BT_BLE_PROTOCOL.upper() in p:
        return "BLE"
    if BT_CLASSIC_PROTOCOL.upper() in p:
        return "Bluetooth"
    return "Unknown"


def classify_candidate(candidate: dict[str, Any]) -> str:
    """Classify a WinRT DeviceInformation candidate."""
    if candidate.get("disposition") == DISPOSITION_REJECTED_WRONG_DEVICE:
        return "RejectedWrongDevice"
    if not candidate.get("enumeration_succeeded", True):
        return "UnknownBluetoothEndpoint"
    can_pair = bool(candidate.get("can_pair"))
    is_paired = bool(candidate.get("is_paired"))
    kind = str(candidate.get("kind") or "")
    protocol = _protocol_kind(candidate.get("protocol_id") or candidate.get("aep_protocol_id"))

    if is_paired:
        return "AlreadyPairedBluetooth"
    if protocol == "BLE":
        return "BLEEndpoint"
    if kind == "AudioEndpoint":
        return "AudioEndpoint"
    if can_pair and protocol == "Bluetooth":
        return "PairableClassicBluetooth"
    if not can_pair and not is_paired:
        if protocol == "BLE":
            return "BLEEndpoint"
        return "NonPairableBluetoothEndpoint"
    if candidate.get("stale"):
        return "StaleEndpoint"
    return "UnknownBluetoothEndpoint"


def score_candidate_with_components(
    candidate: dict[str, Any],
    *,
    target_name: str,
    target_address: str | None = None,
) -> tuple[int, list[str]]:
    """Deterministic score with explainable components."""
    score = 0
    components: list[str] = []
    name = _norm_name(candidate.get("name"))
    target = _norm_name(target_name)
    addr = _norm_addr(candidate.get("device_address") or candidate.get("address"))
    expected = _norm_addr(target_address)
    classification = classify_candidate(candidate)
    protocol = _protocol_kind(candidate.get("protocol_id") or candidate.get("aep_protocol_id"))
    kind = str(candidate.get("kind") or "")

    if candidate.get("disposition") == DISPOSITION_REJECTED_WRONG_DEVICE:
        score -= 500
        components.append("-500 REJECTED_WRONG_DEVICE")
        return score, components

    if not candidate.get("enumeration_succeeded", True):
        score -= 200
        components.append("-200 enumeration failed for candidate")
        return score, components

    if expected and addr and addr == expected:
        score += 1000
        components.append("+1000 exact Bluetooth address")
    elif candidate.get("identity_matched"):
        score += 300
        components.append("+300 identity matched")

    if name == target:
        score += 100
        components.append("+100 exact target name")
    elif target and target in name:
        # Partial name is a hint only; never authoritative when address known.
        score += 20
        components.append("+20 partial name hint")

    if candidate.get("can_pair"):
        score += 40
        components.append("+40 CanPair=True")
    else:
        score -= 100
        components.append("-100 CanPair=False")

    if protocol == "Bluetooth" or candidate.get("is_classic"):
        score += 25
        components.append("+25 Bluetooth Classic")
    elif protocol == "BLE":
        score -= 80
        components.append("-80 BLE-only endpoint")

    if kind == "AssociationEndpoint":
        score += 15
        components.append("+15 AssociationEndpoint")
    elif kind == "Device":
        score += 10
        components.append("+10 Device kind")

    if not candidate.get("is_paired"):
        score += 10
        components.append("+10 IsPaired=False")
    else:
        score -= 10
        components.append("-10 already paired")

    if candidate.get("times_seen", 0) >= 2:
        score += 5
        components.append("+5 seen multiple scans")

    if candidate.get("stale"):
        score -= 70
        components.append("-70 stale endpoint")

    if classification == "NonPairableBluetoothEndpoint":
        score -= 50
        components.append("-50 non-pairable classic endpoint")
    if classification == "BLEEndpoint" and not candidate.get("can_pair"):
        score -= 80
        components.append("-80 non-pairable BLE endpoint")

    return score, components


def score_candidate(
    candidate: dict[str, Any],
    *,
    target_name: str,
    target_address: str | None = None,
) -> int:
    total, _ = score_candidate_with_components(
        candidate, target_name=target_name, target_address=target_address
    )
    return total


def determine_pairability(
    candidates: list[dict[str, Any]],
    *,
    classic_enumeration_succeeded: bool,
    aep_enumeration_succeeded: bool,
) -> str:
    """Tri-state pairability for the *exact target* population only."""
    if not classic_enumeration_succeeded and not aep_enumeration_succeeded:
        return PAIRABILITY_UNKNOWN

    classic_candidates = [
        c
        for c in candidates
        if c.get("disposition", DISPOSITION_ACCEPTED) == DISPOSITION_ACCEPTED
        and c.get("enumeration_succeeded", True)
        and (
            _protocol_kind(c.get("protocol_id") or c.get("aep_protocol_id")) == "Bluetooth"
            or c.get("is_classic")
        )
    ]

    if not classic_candidates:
        if not classic_enumeration_succeeded:
            return PAIRABILITY_UNKNOWN
        return NOT_PAIRABLE

    unpaired = [c for c in classic_candidates if not c.get("is_paired")]
    if not unpaired:
        return NOT_PAIRABLE

    if any(c.get("can_pair") for c in unpaired):
        return PAIRABLE

    if classic_enumeration_succeeded or aep_enumeration_succeeded:
        return NOT_PAIRABLE

    return PAIRABILITY_UNKNOWN


def rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    target_name: str,
    target_address: str | None = None,
    classic_enumeration_succeeded: bool = True,
    aep_enumeration_succeeded: bool = True,
) -> list[dict[str, Any]]:
    filtered = filter_candidates_by_identity(
        candidates, target_name=target_name, target_address=target_address
    )
    ranked: list[dict[str, Any]] = []
    for c in filtered["all"]:
        item = dict(c)
        item["classification"] = classify_candidate(item)
        total, components = score_candidate_with_components(
            item, target_name=target_name, target_address=target_address
        )
        item["score"] = total
        item["score_components"] = components
        ranked.append(item)
    ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    accepted = [c for c in ranked if c.get("disposition") == DISPOSITION_ACCEPTED]
    pairability = determine_pairability(
        accepted,
        classic_enumeration_succeeded=classic_enumeration_succeeded,
        aep_enumeration_succeeded=aep_enumeration_succeeded,
    )
    for item in ranked:
        item["target_pairability"] = pairability
        item["exact_target_discovered"] = filtered["exact_target_discovered"]

    return ranked


def select_pairable_candidate(
    ranked: list[dict[str, Any]],
    *,
    pairability: str | None = None,
) -> dict[str, Any] | None:
    """Select only identity-accepted candidates for PairAsync / already-paired."""
    if pairability == PAIRABILITY_UNKNOWN:
        return None
    for c in ranked:
        if c.get("disposition") != DISPOSITION_ACCEPTED:
            continue
        if not c.get("identity_matched", True):
            continue
        if not c.get("enumeration_succeeded", True):
            continue
        if c.get("can_pair") and not c.get("is_paired"):
            return c
        if c.get("is_paired"):
            return c
    return None


def group_candidates_by_physical_device(
    candidates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Group endpoints by Bluetooth address or container id."""
    groups: dict[str, dict[str, Any]] = {}
    for c in candidates:
        key = _norm_addr(c.get("device_address") or c.get("address"))
        if not key:
            key = str(c.get("container_id") or c.get("id") or "unknown")
        if key not in groups:
            groups[key] = {
                "address": c.get("device_address") or c.get("address"),
                "container_id": c.get("container_id"),
                "name": c.get("name"),
                "endpoints": [],
            }
        groups[key]["endpoints"].append(c)
    return groups


def update_candidate_history(
    history: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Merge candidate into history. Returns (history, can_pair_became_true)."""
    now = now or datetime.now(timezone.utc)
    key = str(candidate.get("id") or candidate.get("device_address") or candidate.get("name"))
    prev_can = None
    if key in history:
        prev_can = history[key].get("can_pair")
    entry = history.get(key, {})
    entry.setdefault("first_seen", now.isoformat())
    entry["last_seen"] = now.isoformat()
    entry["times_seen"] = int(entry.get("times_seen", 0)) + 1
    entry["can_pair"] = bool(candidate.get("can_pair"))
    entry["is_paired"] = bool(candidate.get("is_paired"))
    entry["name"] = candidate.get("name")
    entry["classification"] = classify_candidate(candidate)
    kinds = set(entry.get("endpoint_types") or [])
    kinds.add(str(candidate.get("kind") or "Unknown"))
    entry["endpoint_types"] = sorted(kinds)
    history[key] = entry
    transition = prev_can is False and entry["can_pair"] is True
    return history, transition


def build_rank_result(
    candidates: list[dict[str, Any]],
    *,
    target_name: str,
    target_address: str | None = None,
    classic_enumeration_succeeded: bool = True,
    aep_enumeration_succeeded: bool = True,
) -> dict[str, Any]:
    """Full ranker payload including identity diagnostics."""
    filtered = filter_candidates_by_identity(
        candidates, target_name=target_name, target_address=target_address
    )
    ranked = rank_candidates(
        candidates,
        target_name=target_name,
        target_address=target_address,
        classic_enumeration_succeeded=classic_enumeration_succeeded,
        aep_enumeration_succeeded=aep_enumeration_succeeded,
    )
    accepted = [c for c in ranked if c.get("disposition") == DISPOSITION_ACCEPTED]
    pairability = determine_pairability(
        accepted,
        classic_enumeration_succeeded=classic_enumeration_succeeded,
        aep_enumeration_succeeded=aep_enumeration_succeeded,
    )
    selected = select_pairable_candidate(ranked, pairability=pairability)
    return {
        "schema_version": 2,
        "ranked": ranked,
        "selected": selected,
        "rejected": filtered["rejected"],
        "groups": group_candidates_by_physical_device(candidates),
        "pairability": pairability,
        "pairable_found": selected is not None and bool(selected.get("can_pair")),
        "exact_target_discovered": filtered["exact_target_discovered"],
        "any_bluetooth_device_discovered": filtered["any_bluetooth_device_discovered"],
        "exact_target_already_paired": bool(
            selected and selected.get("is_paired") and selected.get("identity_matched")
        ),
        "target_discovered": filtered["exact_target_discovered"],
    }
