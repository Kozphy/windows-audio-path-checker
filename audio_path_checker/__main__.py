from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .diagnostics import collect_snapshot, save_report


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
        "--report",
        type=Path,
        help="Save a JSON report to this path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.no_gui:
        from .gui import main as gui_main

        gui_main()
        return 0

    snapshot = collect_snapshot()
    if args.report:
        save_report(snapshot, args.report)
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
