from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bluetooth import (
    disabled_bluetooth_adapters,
    enable_bluetooth_adapter,
    preferred_bluetooth_adapter,
    preferred_bluetooth_repair_target,
    repair_bluetooth_pairing,
)
from .diagnostics import (
    collect_snapshot,
    open_windows_settings,
    save_report,
    unmute_silent_browser_sessions,
)
from .inference import enrich_snapshot
from .storage import connect as connect_history
from .storage import store_snapshot, store_timeline, summary as history_summary
from .timeline import record_timeline, state_fingerprint


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose Windows audio paths, rank root causes, and record reliability evidence."
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
        "--enable-bluetooth-adapter",
        action="store_true",
        help=(
            "Re-enable a disabled Bluetooth adapter (UAC). Fixes Windows "
            "'Couldn't connect' when the radio is disabled. Implies --no-gui."
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
    parser.add_argument(
        "--root-causes",
        action="store_true",
        help="Print a compact ranked root-cause summary before the full JSON report.",
    )
    parser.add_argument(
        "--timeline",
        type=float,
        metavar="SECONDS",
        help="Continuously sample the audio path for this many seconds and report state transitions.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="Sampling interval for --timeline (default: 5 seconds).",
    )
    parser.add_argument(
        "--database",
        type=Path,
        metavar="PATH",
        help="Persist scans and timeline transitions to a local SQLite database.",
    )
    parser.add_argument(
        "--history-summary",
        action="store_true",
        help="Print aggregate reliability metrics from --database.",
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
                    return {"name": item_name, "address": address.lower()}
        return None
    return preferred_bluetooth_repair_target(snapshot)


def _scan() -> dict:
    return enrich_snapshot(collect_snapshot())


def _print_root_causes(snapshot: dict) -> None:
    causes = ((snapshot.get("inference") or {}).get("root_causes") or [])
    print("\nRanked root causes")
    print("------------------")
    if not causes:
        print("No supported root cause could be ranked from the collected evidence.")
        return
    for index, cause in enumerate(causes, start=1):
        probability = float(cause.get("probability") or 0.0)
        print(
            f"{index}. {cause.get('title')} "
            f"[{cause.get('confidence')} confidence, {probability:.0%}]"
        )
        for evidence in cause.get("evidence") or []:
            print(f"   evidence: {evidence}")
        print(f"   action: {cause.get('recommendation')}")


def _print_timeline_summary(timeline: dict) -> None:
    metrics = timeline.get("metrics") or {}
    print("\nTimeline reliability summary")
    print("----------------------------")
    print(f"Samples: {metrics.get('sample_count', 0)}")
    print(f"Unique states: {metrics.get('unique_states', 0)}")
    print(f"Transitions: {metrics.get('transition_count', 0)}")
    print(f"State-change rate: {float(metrics.get('state_change_rate') or 0):.2f}")
    print(f"Critical sample ratio: {float(metrics.get('critical_sample_ratio') or 0):.0%}")
    for event in timeline.get("transitions") or []:
        label = event.get("code") or event.get("field") or event.get("type")
        print(f"  - {event.get('observed_at')}: {event.get('type')} [{label}]")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than zero")
    if args.timeline is not None and args.timeline <= 0:
        raise SystemExit("--timeline must be greater than zero")
    if args.history_summary and not args.database:
        raise SystemExit("--history-summary requires --database PATH")

    cli_mode = (
        args.no_gui
        or args.root_causes
        or args.timeline is not None
        or args.database is not None
        or args.history_summary
        or args.unmute_browsers
        or args.enable_bluetooth_adapter
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

    snapshot = _scan()

    if args.enable_bluetooth_adapter:
        disabled = disabled_bluetooth_adapters(snapshot)
        adapter = disabled[0] if disabled else preferred_bluetooth_adapter(snapshot)
        if not adapter:
            print("No Bluetooth adapter was found.", file=sys.stderr)
        else:
            print(
                f"Enabling Bluetooth adapter {adapter.get('name')} "
                f"[{adapter.get('status')}]… Approve UAC."
            )
            result = enable_bluetooth_adapter(
                instance_id=str(adapter.get("instance_id") or ""),
                elevate=True,
                wait=True,
            )
            if result.get("log"):
                print(result["log"])
            snapshot = _scan()
            if disabled_bluetooth_adapters(snapshot):
                print(
                    "Adapter still disabled. Approve UAC and retry, "
                    "or enable it in Device Manager.",
                    file=sys.stderr,
                )
            else:
                print(
                    "Adapter enabled. Put the headset in pairing mode, "
                    "then Add device in Bluetooth settings."
                )

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
            snapshot = _scan()

    timeline = None
    if args.timeline is not None:
        print(f"Recording audio state for {args.timeline:g}s every {args.interval:g}s…")
        timeline = record_timeline(
            _scan,
            duration_seconds=args.timeline,
            interval_seconds=args.interval,
        )
        _print_timeline_summary(timeline)
        samples = timeline.get("samples") or []
        if samples:
            snapshot = samples[-1].get("snapshot") or snapshot

    if args.database:
        with connect_history(args.database) as connection:
            if timeline is not None:
                stored_scans, stored_transitions = store_timeline(connection, timeline)
                print(
                    f"Stored {stored_scans} scans and {stored_transitions} transitions "
                    f"in {args.database}."
                )
            else:
                scan_id = store_snapshot(
                    connection,
                    snapshot,
                    fingerprint=state_fingerprint(snapshot),
                )
                print(f"Stored scan #{scan_id} in {args.database}.")
            if args.history_summary:
                print("\nHistory summary")
                print("---------------")
                print(json.dumps(history_summary(connection), indent=2, ensure_ascii=True))

    if args.report:
        report_payload = dict(snapshot)
        if timeline is not None:
            report_payload["timeline"] = timeline
        save_report(report_payload, args.report)
    if args.root_causes:
        _print_root_causes(snapshot)

    print(json.dumps(snapshot, indent=2, ensure_ascii=True))
    has_critical = any(
        finding.get("severity") == "critical"
        for finding in snapshot.get("findings", [])
    )
    return 2 if has_critical else 0


if __name__ == "__main__":
    sys.exit(main())
