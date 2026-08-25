"""Build an auditable device-path graph from Windows audio/Bluetooth evidence."""

from __future__ import annotations

import re
from typing import Any


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "unknown"


def _node(node_id: str, kind: str, label: str, **attrs: Any) -> dict[str, Any]:
    return {"id": node_id, "kind": kind, "label": label, "attrs": attrs}


def _edge(source: str, target: str, relation: str, *, state: str = "observed", **evidence: Any) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "state": state,
        "evidence": evidence,
    }


def _matching_headset(headsets: list[dict[str, Any]], endpoint_name: str) -> dict[str, Any] | None:
    folded = endpoint_name.casefold()
    for headset in headsets:
        name = str(headset.get("name") or "")
        if name and name.casefold() in folded:
            return headset

    endpoint_words = set(re.findall(r"[a-z0-9]+", folded))
    best: dict[str, Any] | None = None
    best_score = 0
    for headset in headsets:
        name_words = set(re.findall(r"[a-z0-9]+", str(headset.get("name") or "").casefold()))
        score = len(endpoint_words & name_words)
        if score > best_score:
            best, best_score = headset, score
    return best if best_score else None


def build_device_graph(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Normalize collected evidence into nodes, edges, paths, and likely breakpoints.

    The graph is descriptive rather than a claim about Windows internals: every
    edge is backed by collector evidence or marked as inferred when a name-based
    association is required.
    """
    bluetooth = snapshot.get("bluetooth") or {}
    core_audio = snapshot.get("core_audio") or {}
    portaudio = snapshot.get("portaudio") or snapshot.get("playback") or {}

    adapters = list(bluetooth.get("adapters") or [])
    headsets = list(bluetooth.get("paired_headsets") or [])
    sessions = list(core_audio.get("sessions") or [])
    default_endpoint = core_audio.get("default_endpoint") or {}
    output_devices = list(portaudio.get("output_devices") or [])

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    breakpoints: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []

    adapter_ids: list[str] = []
    for index, adapter in enumerate(adapters):
        raw_id = str(adapter.get("instance_id") or index)
        node_id = f"bt-adapter:{_slug(raw_id)}"
        adapter_ids.append(node_id)
        status = str(adapter.get("status") or "unknown")
        problem = adapter.get("problem_code")
        healthy = status.casefold() in {"ok", "started"} and problem in {None, 0, "0"}
        nodes.append(
            _node(
                node_id,
                "bluetooth-adapter",
                str(adapter.get("name") or "Bluetooth adapter"),
                status=status,
                problem_code=problem,
                is_present=adapter.get("is_present"),
                healthy=healthy,
            )
        )
        if not healthy:
            breakpoints.append(
                {
                    "code": "bluetooth-adapter-unhealthy",
                    "node": node_id,
                    "confidence": "high",
                    "detail": f"Bluetooth adapter reports status={status!r}, problem_code={problem!r}.",
                }
            )

    headset_ids: dict[str, str] = {}
    for index, headset in enumerate(headsets):
        raw_id = str(headset.get("address") or headset.get("instance_id") or index)
        node_id = f"bt-device:{_slug(raw_id)}"
        headset_ids[raw_id] = node_id
        present = bool(headset.get("is_present"))
        nodes.append(
            _node(
                node_id,
                "bluetooth-audio-device",
                str(headset.get("name") or "Bluetooth audio device"),
                paired=True,
                present=present,
                status=headset.get("status"),
                last_connected=headset.get("last_connected"),
            )
        )
        if adapter_ids:
            edges.append(
                _edge(
                    adapter_ids[0],
                    node_id,
                    "radio-link",
                    state="observed" if present else "historical",
                    paired=True,
                    present=present,
                )
            )
        if not present:
            breakpoints.append(
                {
                    "code": "bluetooth-device-not-present",
                    "node": node_id,
                    "confidence": "medium",
                    "detail": "Device is paired historically but not currently present/connected.",
                }
            )

    endpoint_id: str | None = None
    endpoint_name = str(default_endpoint.get("name") or "")
    if endpoint_name:
        endpoint_id = f"endpoint:{_slug(str(default_endpoint.get('id') or endpoint_name))}"
        nodes.append(
            _node(
                endpoint_id,
                "audio-endpoint",
                endpoint_name,
                endpoint_id=default_endpoint.get("id"),
                is_default=True,
                present=bluetooth.get("default_endpoint_present"),
            )
        )
        matched = _matching_headset(headsets, endpoint_name)
        if matched is not None:
            raw_id = str(matched.get("address") or matched.get("instance_id") or headsets.index(matched))
            source_id = headset_ids.get(raw_id)
            if source_id:
                edges.append(
                    _edge(
                        source_id,
                        endpoint_id,
                        "exposes-audio-endpoint",
                        state="inferred",
                        match="friendly-name",
                    )
                )

    output_ids: dict[str, str] = {}
    for index, device in enumerate(output_devices):
        name = str(device.get("name") or f"Output {index}")
        node_id = f"output:{_slug(str(device.get('index', index)) + '-' + name)}"
        output_ids[name.casefold()] = node_id
        nodes.append(
            _node(
                node_id,
                "application-output",
                name,
                host_api=device.get("host_api"),
                is_default=bool(device.get("is_default")),
            )
        )
        if endpoint_id and bool(device.get("is_default")):
            edges.append(_edge(endpoint_id, node_id, "available-to-applications", state="observed"))

    browser_path_nodes: list[str] = []
    for index, session in enumerate(sessions):
        process = str(session.get("process") or f"session-{index}")
        raw_session_id = str(session.get("instance_id") or session.get("pid") or index)
        node_id = f"session:{_slug(raw_session_id)}"
        nodes.append(
            _node(
                node_id,
                "audio-session",
                process,
                pid=session.get("pid"),
                muted=bool(session.get("muted")),
                volume=session.get("volume"),
                state=session.get("state"),
                is_browser=bool(session.get("is_browser")),
                output_device=session.get("output_device"),
            )
        )
        output_name = str(session.get("output_device") or "")
        target = output_ids.get(output_name.casefold()) if output_name else None
        if target:
            edges.append(_edge(node_id, target, "routes-to", state="observed"))
        elif endpoint_id:
            edges.append(_edge(node_id, endpoint_id, "routes-to-default", state="inferred"))
        if bool(session.get("muted")) or float(session.get("volume") or 0.0) <= 0.01:
            breakpoints.append(
                {
                    "code": "session-silent",
                    "node": node_id,
                    "confidence": "high",
                    "detail": f"{process} session is muted or has near-zero volume.",
                }
            )
        if bool(session.get("is_browser")):
            browser_path_nodes.append(node_id)

    if headsets and not endpoint_name and any(bool(item.get("is_present")) for item in headsets):
        breakpoints.append(
            {
                "code": "bluetooth-endpoint-missing",
                "node": None,
                "confidence": "high",
                "detail": "A Bluetooth audio device is present, but no default Core Audio endpoint was collected.",
            }
        )

    for session_id in browser_path_nodes:
        path = [session_id]
        current = session_id
        visited = {current}
        while True:
            candidates = [edge for edge in edges if edge["source"] == current and edge["target"] not in visited]
            if not candidates:
                break
            edge = candidates[0]
            current = str(edge["target"])
            path.append(current)
            visited.add(current)
        paths.append({"kind": "browser-audio", "nodes": path, "complete": len(path) >= 2})

    return {
        "schema_version": 1,
        "nodes": nodes,
        "edges": edges,
        "paths": paths,
        "breakpoints": breakpoints,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "breakpoint_count": len(breakpoints),
            "bluetooth_adapter_observed": bool(adapters),
            "bluetooth_audio_device_observed": bool(headsets),
            "default_endpoint_observed": bool(endpoint_name),
            "browser_session_observed": bool(browser_path_nodes),
        },
    }
