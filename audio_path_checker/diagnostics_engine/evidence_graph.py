"""Evidence graph: identity correlation + structured observation model.

Separates raw collector booleans from interpreted relationships.

Identity rules (Invariant E):
* Canonical Bluetooth MAC (12 lowercase hex) dominates FriendlyName.
* Nodes whose address/instance id maps to a different MAC are ghosts for
  the target and must not raise target confidence (Invariant B).
* FriendlyName is weak supporting evidence only.
"""

from __future__ import annotations

import re
from typing import Any

from ..models.states import CheckStatus

_HEX = re.compile(r"[^0-9a-f]")


def normalize_bluetooth_address(value: str | None) -> str:
    """Normalize MAC variants to lowercase 12-hex (empty if unusable)."""
    if not value:
        return ""
    hex_only = _HEX.sub("", str(value).casefold())
    if len(hex_only) > 12:
        hex_only = hex_only[-12:]
    if len(hex_only) < 12:
        return ""
    return hex_only


def extract_address_from_instance_id(instance_id: str | None) -> str:
    """Pull a Bluetooth MAC from common Windows PnP / WinRT id forms."""
    text = instance_id or ""
    patterns = (
        r"(?:DEV_|BluetoothDevice_|_)([0-9A-Fa-f]{12})(?:_|$|\\)",
        # A2DP/AVRCP transport nodes: ...&0&C8247887E57C_C00000000
        r"&([0-9A-Fa-f]{12})(?:_|$|\\)",
        r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}",
    )
    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        raw = m.group(1) if m.lastindex else m.group(0)
        return normalize_bluetooth_address(raw)
    return ""


def _node_address(node: dict[str, Any]) -> str:
    addr = normalize_bluetooth_address(
        node.get("address") or node.get("device_address") or node.get("bluetooth_address")
    )
    if addr:
        return addr
    for key in ("instance_id", "InstanceId", "id", "parent_instance_id"):
        addr = extract_address_from_instance_id(str(node.get(key) or ""))
        if addr:
            return addr
    return ""


def _name_norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _names_related(node_name: str, target_name: str) -> bool:
    """True when names are exact or one contains the other (Headphones (...))."""
    n = _name_norm(node_name)
    t = _name_norm(target_name)
    if not n or not t:
        return False
    if n == t:
        return True
    for prefix in ("headphones (", "headset (", "speakers ("):
        if n.startswith(prefix) and n.endswith(")"):
            n = n[len(prefix) : -1].strip()
    for suffix in (" avrcp transport", " hands-free", " hands-free ag", " stereo"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n == t or t in n or n in t


def score_node_for_target(
    node: dict[str, Any],
    *,
    target_address: str,
    target_name: str,
) -> dict[str, Any]:
    """Score one PnP/audio node against the configured recovery target.

    Returns:
        Dict with ``score``, ``matched``, ``ghost``, ``reason``, ``address``.
        Ghost nodes (different MAC) never count as target evidence.
    """
    expected = normalize_bluetooth_address(target_address)
    observed = _node_address(node)
    name_ok = _names_related(
        str(node.get("name") or node.get("FriendlyName") or ""),
        target_name,
    )
    if expected and observed and observed != expected:
        return {
            "score": -1000,
            "matched": False,
            "ghost": True,
            "reason": "address_mismatch_ghost",
            "address": observed,
        }
    if expected and observed and observed == expected:
        return {
            "score": 1000 + (50 if name_ok else 0),
            "matched": True,
            "ghost": False,
            "reason": "bluetooth_address_match",
            "address": observed,
        }
    if name_ok and (not expected or not observed):
        # Endpoint MMDEVAPI nodes often omit MAC; related-name match is allowed
        # when we do not already know a conflicting address.
        return {
            "score": 120 if expected else 100,
            "matched": True,
            "ghost": False,
            "reason": "related_name_match",
            "address": observed,
        }
    return {
        "score": 0,
        "matched": False,
        "ghost": False,
        "reason": "no_identity_match",
        "address": observed,
    }


def filter_correlated_nodes(
    nodes: list[dict[str, Any]] | None,
    *,
    target_address: str,
    target_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split nodes into (matched_for_target, ghosts_other_mac)."""
    matched: list[dict[str, Any]] = []
    ghosts: list[dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        scored = score_node_for_target(
            node, target_address=target_address, target_name=target_name
        )
        annotated = dict(node)
        annotated["_identity"] = scored
        if scored["ghost"]:
            ghosts.append(annotated)
        elif scored["matched"]:
            matched.append(annotated)
    return matched, ghosts


def path_maturity_from_flags(
    *,
    paired: bool,
    connected: bool,
    a2dp: bool,
    media: bool,
    endpoint: bool,
    active: bool,
) -> int:
    """Return 0–6 maturity rank from boolean path flags."""
    if not paired:
        return 0
    if not connected:
        return 1
    if not (a2dp or media):
        return 2
    if a2dp and not media:
        return 3
    if media and not endpoint:
        return 4
    if endpoint and not active:
        return 5
    if endpoint and active:
        return 6
    return 1


def build_evidence_graph(
    evidence: dict[str, Any],
    *,
    settling: bool = False,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    """Build a structured evidence graph from a collector evidence document.

    Does not mutate Windows. Pure function of ``evidence`` (+ settle context).
    """
    device = evidence.get("device") or {}
    bluetooth = evidence.get("bluetooth") or {}
    audio = evidence.get("audio") or {}
    pnp = evidence.get("pnp") or {}
    services = evidence.get("services") or {}

    target_name = str(
        evidence.get("environment", {}).get("device_filter")
        or device.get("name")
        or ""
    )
    # Prefer the user filter when the collector latched onto a transport node
    # (e.g. "… Avrcp Transport") as device.name.
    requested = str(evidence.get("environment", {}).get("device_filter") or "").strip()
    if requested:
        target_name = requested
    target_address = normalize_bluetooth_address(device.get("address"))
    if not target_address:
        target_address = extract_address_from_instance_id(str(device.get("instance_id") or ""))

    media_matched, media_ghosts = filter_correlated_nodes(
        pnp.get("media_nodes") or [],
        target_address=target_address,
        target_name=target_name,
    )
    a2dp_matched, a2dp_ghosts = filter_correlated_nodes(
        pnp.get("a2dp_nodes") or [],
        target_address=target_address,
        target_name=target_name,
    )
    endpoint_matched, endpoint_ghosts = filter_correlated_nodes(
        pnp.get("endpoint_nodes") or audio.get("endpoints") or [],
        target_address=target_address,
        target_name=target_name,
    )

    # Prefer identity-correlated inventory; fall back to collector booleans when
    # PnP lists are empty (older fixtures / non-Windows stubs).
    a2dp_present = bool(a2dp_matched) or (
        not (pnp.get("a2dp_nodes") or []) and bool(audio.get("a2dp_present"))
    )
    media_present = bool(media_matched) or (
        not (pnp.get("media_nodes") or []) and bool(audio.get("media_node_present"))
    )
    endpoint_present = bool(endpoint_matched) or (
        not (pnp.get("endpoint_nodes") or audio.get("endpoints") or [])
        and bool(audio.get("endpoint_present"))
    )
    # If ghosts exist and matched lists empty, collector booleans may be polluted
    # by FriendlyName — force false when target address is known.
    if target_address and (media_ghosts or a2dp_ghosts or endpoint_ghosts):
        if not media_matched:
            media_present = False
        if not a2dp_matched:
            a2dp_present = False
        if not endpoint_matched:
            endpoint_present = False

    paired = device.get("paired")
    connected = device.get("connected")
    default_playback = audio.get("is_default_playback")
    audiosrv = str(services.get("Audiosrv", "")).casefold()
    aeb = str(services.get("AudioEndpointBuilder", "")).casefold()

    inventory_present = a2dp_present or media_present or endpoint_present
    ghost_inventory = bool(media_ghosts or a2dp_ghosts or endpoint_ghosts)

    checks: dict[str, str] = {
        "adapter": (
            CheckStatus.PASS.value
            if bluetooth.get("adapter_enabled")
            else CheckStatus.FAIL.value
            if bluetooth.get("adapter_present") is False
            or bluetooth.get("adapter_enabled") is False
            else CheckStatus.UNKNOWN.value
        ),
        "identity": (
            CheckStatus.PASS.value
            if target_address
            else CheckStatus.UNKNOWN.value
            if target_name
            else CheckStatus.FAIL.value
        ),
        "paired": _tri(paired),
        "connected": _connected_status(connected, settling=settling),
        "a2dp": _path_child_status(
            connected, a2dp_present, settling=settling, inventory_present=inventory_present
        ),
        "media": _path_child_status(
            connected, media_present, settling=settling, inventory_present=inventory_present
        ),
        "endpoint": _path_child_status(
            connected, endpoint_present, settling=settling, inventory_present=inventory_present
        ),
        "windows_audio": (
            CheckStatus.PASS.value
            if audiosrv == "running"
            else CheckStatus.FAIL.value
            if audiosrv and audiosrv not in {"unknown", ""}
            else CheckStatus.UNKNOWN.value
        ),
        "default_output": (
            CheckStatus.PASS.value
            if default_playback is True
            else CheckStatus.FAIL.value
            if default_playback is False
            else CheckStatus.UNKNOWN.value
        ),
    }

    observations = [
        _obs("adapter_enabled", bluetooth.get("adapter_enabled"), "pnp_bluetooth", "high", "direct"),
        _obs("paired", paired, "bthenum", "high", "direct"),
        _obs("connected", connected, "collector_device", "medium", "inferred"),
        _obs("last_connected", device.get("last_connected"), "devpkey", "medium", "direct"),
        _obs("a2dp_present", a2dp_present, "pnp_correlated", "high", "direct"),
        _obs("media_present", media_present, "pnp_correlated", "high", "direct"),
        _obs("endpoint_present", endpoint_present, "pnp_correlated", "high", "direct"),
        _obs("audiosrv", services.get("Audiosrv"), "scm", "high", "direct"),
        _obs("ghost_media_count", len(media_ghosts), "identity", "high", "derived"),
    ]

    return {
        "schema_version": 2,
        "target": {
            "requested_name": target_name,
            "canonical_bluetooth_address": target_address or None,
            "instance_id": device.get("instance_id"),
        },
        "settling": bool(settling),
        "elapsed_ms": elapsed_ms,
        "flags": {
            "paired": bool(paired),
            "connected": bool(connected),
            "a2dp_present": a2dp_present,
            "media_present": media_present,
            "endpoint_present": endpoint_present,
            "endpoint_active": bool(audio.get("endpoint_active")),
            "inventory_present": inventory_present,
            "ghost_inventory": ghost_inventory,
            "audio_services_running": audiosrv == "running"
            and (aeb in {"running", "unknown", ""} or aeb == "running"),
            "audiosrv": audiosrv or "unknown",
            "default_playback": default_playback,
        },
        "checks": checks,
        "matched": {
            "a2dp_nodes": a2dp_matched,
            "media_nodes": media_matched,
            "endpoint_nodes": endpoint_matched,
        },
        "ghosts": {
            "a2dp_nodes": a2dp_ghosts,
            "media_nodes": media_ghosts,
            "endpoint_nodes": endpoint_ghosts,
        },
        "maturity": path_maturity_from_flags(
            paired=bool(paired),
            connected=bool(connected),
            a2dp=a2dp_present,
            media=media_present,
            endpoint=endpoint_present,
            active=bool(audio.get("endpoint_active")),
        ),
        "observations": observations,
    }


def _obs(signal: str, value: Any, source: str, reliability: str, kind: str) -> dict[str, Any]:
    return {
        "signal": signal,
        "value": value,
        "source": source,
        "reliability": reliability,
        "kind": kind,
        "freshness": "current",
    }


def _tri(value: Any) -> str:
    if value is True:
        return CheckStatus.PASS.value
    if value is False:
        return CheckStatus.FAIL.value
    return CheckStatus.UNKNOWN.value


def _connected_status(connected: Any, *, settling: bool) -> str:
    if connected is True:
        return CheckStatus.PASS.value if not settling else CheckStatus.PENDING.value
    if connected is False:
        return CheckStatus.FAIL.value
    return CheckStatus.UNKNOWN.value


def _path_child_status(
    connected: Any,
    present: bool,
    *,
    settling: bool,
    inventory_present: bool,
) -> str:
    if connected is not True:
        # Without a live link, missing profile nodes are expected — not FAIL.
        if present:
            return CheckStatus.STALE.value
        return CheckStatus.NOT_APPLICABLE.value
    if present:
        return CheckStatus.PASS.value
    if settling:
        return CheckStatus.PENDING.value
    return CheckStatus.FAIL.value
