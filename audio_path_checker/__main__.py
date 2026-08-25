from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bluetooth import (
    preferred_bluetooth_repair_target,
    repair_bluetooth_pairing,
)
from .diagnostics import (
    collect_snapshot,
    open_windows_settings,
    save_report,
    unmute_silent_browser_sessions,
)


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
        "--repair-bluetooth",
        nargs="?",
        const="__auto__",
        metavar="NAME",
        help=(
            "Clear pairing cache for a Bluetooth headset (UAC). "
            "Optional NAME matches a paired headset; omit to use the default "
            "playback headset. Implies --no-gui. Reboot + re-pair after."
        ),
    )
    parser.add_argument(
        "--open-bluetooth-settings",
        action="store_true",
        help="Open Windows Bluetooth settings (implies --no-gui).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Save a JSON report to this path.",
    )
    return parser


def _select_bluetooth_target(snapshot: dict, name: str | None) -> dict | None:
    if name and name != "__auto__":
        needle = name.casefold()
        bluetooth = snapshot.get("bluetooth") or {}
        for item in bluetooth.get("paired_headsets") or []:
            item_name = str(item.get("name") or "")
            address = str(item.get("address") or "")
            if needle in item_name.casefold() or needle == address.casefold():
                if address:
                    return {
                        "name": item_name,
                        "address": address.lower(),
                    }
        return None
    return preferred_bluetooth_repair_target(snapshot)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cli_mode = (
        args.no_gui
        or args.unmute_browsers
        or args.repair_bluetooth is not None
        or args.open_bluetooth_settings
    )
    if not cli_mode:
        from .gui import main as gui_main

        gui_main()
        return 0

    if args.open_bluetooth_settings:
        open_windows_settings("ms-settings:bluetooth")

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

    if args.repair_bluetooth is not None:
        target = _select_bluetooth_target(snapshot, args.repair_bluetooth)
        if not target:
            print(
                "No matching Bluetooth headset with an address was found. "
                "Scan output is below; open Bluetooth settings if needed.",
                file=sys.stderr,
            )
        else:
            print(
                f"Repairing Bluetooth pairing for {target.get('name')} "
                f"({target.get('address')})… Approve UAC."
            )
            result = repair_bluetooth_pairing(
                address=str(target["address"]),
                friendly_name=str(target.get("name") or "Bluetooth headset"),
                elevate=True,
                wait=True,
            )
            if result.get("log"):
                print(result["log"])
            print(
                "Reboot required. After reboot, re-pair the headset "
                "in Bluetooth settings."
            )
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
