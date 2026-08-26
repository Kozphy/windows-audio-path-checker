"""CLI: rank Bluetooth candidates from JSON on stdin."""

from __future__ import annotations

import argparse
import json
import sys

from .candidates import (
    PAIRABILITY_UNKNOWN,
    determine_pairability,
    group_candidates_by_physical_device,
    rank_candidates,
    select_pairable_candidate,
)


def _parse_candidates(raw: str) -> tuple[list[dict] | None, str | None]:
    if not raw.strip():
        return None, "empty_input"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, "RANKER_INPUT_INVALID"
    if isinstance(parsed, dict):
        if parsed.get("error"):
            return None, str(parsed["error"])
        return [parsed], None
    if isinstance(parsed, list):
        if len(parsed) == 0:
            return [], "NO_CANDIDATES"
        return parsed, None
    return None, "RANKER_INPUT_INVALID"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank Bluetooth pairing candidates.")
    parser.add_argument("command", nargs="?", default="rank", choices=["rank"])
    parser.add_argument("--target-name", default="EDIFIER W800BT Pro")
    parser.add_argument("--target-address", default="c8247887e57c")
    parser.add_argument("--classic-enum-ok", action="store_true", default=True)
    parser.add_argument("--aep-enum-ok", action="store_true", default=True)
    parser.add_argument("--json", action="store_true", help="Ignored; stdin is always JSON.")
    args = parser.parse_args(argv)

    candidates, err = _parse_candidates(sys.stdin.read())
    if err:
        print(json.dumps({"error": err}))
        return 1 if err != "NO_CANDIDATES" else 0

    assert candidates is not None
    classic_ok = args.classic_enum_ok
    aep_ok = args.aep_enum_ok

    ranked = rank_candidates(
        candidates,
        target_name=args.target_name,
        target_address=args.target_address,
        classic_enumeration_succeeded=classic_ok,
        aep_enumeration_succeeded=aep_ok,
    )
    pairability = determine_pairability(
        candidates,
        classic_enumeration_succeeded=classic_ok,
        aep_enumeration_succeeded=aep_ok,
    )
    selected = select_pairable_candidate(ranked, pairability=pairability)
    groups = group_candidates_by_physical_device(candidates)
    out = {
        "ranked": ranked,
        "selected": selected,
        "groups": groups,
        "pairability": pairability,
        "pairable_found": selected is not None and bool(selected.get("can_pair")),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
