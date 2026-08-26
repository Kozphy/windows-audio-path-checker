from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AuditRecord:
    event_id: str
    observed_at: str
    event_type: str
    payload: dict[str, Any]
    previous_hash: str | None
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(event_id: str, observed_at: str, event_type: str, payload: dict[str, Any], previous_hash: str | None) -> str:
    material = {
        "event_id": event_id,
        "observed_at": observed_at,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def create_record(
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    previous_hash: str | None = None,
    observed_at: str | None = None,
) -> AuditRecord:
    timestamp = observed_at or datetime.now(timezone.utc).isoformat()
    digest = _digest(event_id, timestamp, event_type, payload, previous_hash)
    return AuditRecord(
        event_id=event_id,
        observed_at=timestamp,
        event_type=event_type,
        payload=payload,
        previous_hash=previous_hash,
        hash=digest,
    )


def verify_chain(records: list[dict[str, Any]]) -> bool:
    previous_hash: str | None = None
    for record in records:
        if record.get("previous_hash") != previous_hash:
            return False
        expected = _digest(
            str(record.get("event_id") or ""),
            str(record.get("observed_at") or ""),
            str(record.get("event_type") or ""),
            dict(record.get("payload") or {}),
            previous_hash,
        )
        if record.get("hash") != expected:
            return False
        previous_hash = expected
    return True
