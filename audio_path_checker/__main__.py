from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .diagnostics import (
    collect_snapshot,
    save_report,
    unmute_silent_browser_sessions,
)
from .engine import build_diagnosis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose why Windows test sounds work while browsers and apps are silent."
        )
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Scan in the terminal instead of opening the friendly window.",
    )
    parser.add_argument(
        "--unmute-browsers",
        action="store_true",
        help=(
            "Unmute recognized browser sessions, raise low volumes to 50%%, "
            "then rescan and print the verified report (implies --no-gui)."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Save a JSON report to this path.",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help=(
            "Print a compact, ranked root-cause explanation instead of the full JSON "
            "report (implies --no-gui)."
        ),
    )
    return parser


def _print_explanation(snapshot: dict[str, object]) -> None:
    diagnosis = snapshot.get("diagnosis") or {}
    hypotheses = diagnosis.get("hypotheses", []) if isinstance(diagnosis, dict) else []
    if not hypotheses:
        print("No actionable root-cause hypothesis was produced.")
        return

    print("Ranked root-cause hypotheses")
    print("=" * 30)
    for rank, hypothesis in enumerate(hypotheses, start=1):
        confidence = float(hypothesis.get("confidence", 0.0)) * 100
        print(f"{rank}. {hypothesis.get('title')} ({confidence:.0f}% confidence)")
        print(f"   Severity: {hypothesis.get('severity')}")
        print(f"   Why: {hypothesis.get('explanation')}")
        evidence_ids = hypothesis.get("evidence_ids") or []
        if evidence_ids:
            print(f"   Evidence: {', '.join(str(item) for item in evidence_ids)}")
        print(f"   Next action: {hypothesis.get('recommendation')}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.no_gui and not args.unmute_browsers and not args.explain:
        from .gui import main as gui_main

        gui_main()
        return 0

    if args.unmute_browsers:
        changed = unmute_silent_browser_sessions()
        if changed:
            print("Browser audio adjusted:")
            for item in changed:
                print(f"  - {item}")
        else:
            print(
                "No recognized browser session was muted or below 50%. "
                "Start YouTube playback and scan again."
            )
        print("Rescanning to verify…")

    snapshot = collect_snapshot()
    snapshot["diagnosis"] = build_diagnosis(snapshot)
    if args.report:
        save_report(snapshot, args.report)

    if args.explain:
        _print_explanation(snapshot)
    else:
        # Escaping non-ASCII here keeps the CLI reliable in legacy Windows
        # consoles that still use a narrow code page. Saved reports remain UTF-8.
        print(json.dumps(snapshot, indent=2, ensure_ascii=True))

    has_critical = any(
        finding.get("severity") == "critical"
        for finding in snapshot.get("findings", [])
    )
    return 2 if has_critical else 0


if __name__ == "__main__":
    sys.exit(main())
