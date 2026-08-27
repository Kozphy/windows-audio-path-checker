"""Command-line entry surface for Windows Audio Path Checker.

This module is the programmatic API boundary invoked by the
``windows-audio-checker-cli`` console script. It exposes two user-facing
surfaces:

* **GUI mode** (default): launches the Tkinter application when no CLI flags
  are present.
* **CLI mode**: runs scans, diagnosis/repair pipelines, Bluetooth remediation,
  timeline sampling, and history persistence without opening a window.

Exit-code contract (by path):

* Scan / report path: ``0`` if no *critical* findings; ``2`` if any critical
  finding remains (warnings alone do not force ``2``).
* ``--diagnose`` / ``--repair`` / ``--aggressive-repair``: ``0`` only when
  classified state is ``AUDIO_PATH_HEALTHY``; otherwise ``2`` for *any*
  non-healthy state (not only critical findings).
* ``--add-bluetooth``: returns the auto-pair script exit code on failure
  (often taxonomy codes such as 10/11), else ``0``.
* Some Bluetooth helpers that cannot find a target print an error then fall
  through to the scan exit-code path rather than a dedicated failure code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .bluetooth import (
    DEFAULT_ADD_BLUETOOTH_ADDRESS,
    DEFAULT_ADD_BLUETOOTH_NAME,
    add_bluetooth_device,
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
from .pipeline import run_audio_path_diagnosis
from .storage import connect as connect_history
from .storage import store_snapshot, store_timeline, summary as history_summary
from .timeline import record_timeline, state_fingerprint


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for all CLI flags and sub-modes.

    Returns:
        A fully configured ``ArgumentParser`` whose flags control scan-only
        output, the evidence-driven diagnosis pipeline, Bluetooth repair
        actions, timeline recording, SQLite history, and report export.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Evidence-driven Windows audio / Bluetooth path diagnostics. "
            "Bluetooth Connected is not treated as Audio Working."
        )
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"windows-audio-path-checker {__version__}",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Scan in the terminal instead of opening the friendly window.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=(
            "Run the audio-path diagnosis pipeline (evidence → state → "
            "hypotheses → plan). Non-destructive. Implies --no-gui."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Same as --diagnose: recommend actions without executing repairs.",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help=(
            "Allow low/medium risk remediation (up to R3), with verification. "
            "Implies --no-gui."
        ),
    )
    parser.add_argument(
        "--aggressive-repair",
        action="store_true",
        help=(
            "Allow high-risk scoped remediation (up to R5 pairing clear). "
            "Implies --no-gui."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON for diagnose/repair modes.",
    )
    parser.add_argument(
        "--device",
        default="EDIFIER W800BT Pro",
        help="Headset name filter for path diagnosis (default: EDIFIER W800BT Pro).",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="Root directory for session artifacts (default: ./artifacts/sessions).",
    )
    parser.add_argument(
        "--no-artifacts",
        action="store_true",
        help="Do not write artifacts/sessions evidence trail.",
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
        "--add-bluetooth",
        nargs="?",
        const=DEFAULT_ADD_BLUETOOTH_NAME,
        metavar="NAME",
        help=(
            "Launch identity-safe elevated auto-pair for a Bluetooth headset. "
            f"Optional NAME (default: {DEFAULT_ADD_BLUETOOTH_NAME}). "
            "Put the headset in pairing mode first. Implies --no-gui."
        ),
    )
    parser.add_argument(
        "--bluetooth-address",
        default=DEFAULT_ADD_BLUETOOTH_ADDRESS,
        metavar="MAC",
        help=(
            "Bluetooth MAC for --add-bluetooth "
            f"(default: {DEFAULT_ADD_BLUETOOTH_ADDRESS})."
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
    """Resolve the Bluetooth headset targeted by ``--repair-bluetooth``.

    Args:
        snapshot: Latest diagnostic snapshot containing paired headsets.
        name: User-supplied name or MAC, ``"__auto__"`` / ``None`` for preferred
            default-playback matching.

    Returns:
        Dict with ``name`` and ``address`` when a match is found, else ``None``.

    Notes:
        Explicit ``name`` matching is **substring** on friendly name (first
        address-bearing hit wins) or exact casefolded MAC equality — ambiguous
        for similarly named sibling headsets. Auto mode uses
        :func:`~audio_path_checker.bluetooth.preferred_bluetooth_repair_target`
        (endpoint match first, then first paired headset with an address).
    """
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


def _pipeline_mode(args: argparse.Namespace) -> str | None:
    """Map mutually exclusive repair flags to a pipeline mode name.

    Args:
        args: Parsed CLI namespace.

    Returns:
        One of ``"aggressive-repair"``, ``"repair"``, ``"diagnose"``, or
        ``None`` when no diagnosis pipeline flag was requested.
    """
    if args.aggressive_repair:
        return "aggressive-repair"
    if args.repair:
        return "repair"
    if args.diagnose or args.dry_run:
        return "diagnose"
    return None


def _scan() -> dict:
    """Collect live system evidence and attach ranked root-cause inference.

    Returns:
        An enriched diagnostic snapshot ready for printing, storage, or
        downstream Bluetooth target selection.
    """
    return enrich_snapshot(collect_snapshot())

def _print_root_causes(snapshot: dict) -> None:
    """Print a human-readable ranked root-cause summary to stdout.

    Args:
        snapshot: Enriched snapshot whose ``inference.root_causes`` list
            drives the output.
    """
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
    """Print reliability metrics and state transitions from a timeline run.

    Args:
        timeline: Result dict from :func:`record_timeline` containing
            ``metrics`` and ``transitions`` keys.
    """
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
    """Run the checker in GUI or CLI mode and return a process exit code.

    Flow:
        1. Parse and validate CLI arguments.
        2. Launch the GUI when no CLI-oriented flags are present.
        3. Otherwise run the requested path: diagnosis pipeline, Bluetooth
           remediation, browser unmute, timeline sampling, SQLite persistence,
           and/or JSON report emission.

    Args:
        argv: Optional argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Path-dependent exit code — see module docstring. Diagnose modes treat
        any non-healthy classification as ``2``; scan mode keys off critical
        findings only.
    """
    args = build_parser().parse_args(argv)
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than zero")
    if args.timeline is not None and args.timeline <= 0:
        raise SystemExit("--timeline must be greater than zero")
    if args.history_summary and not args.database:
        raise SystemExit("--history-summary requires --database PATH")

    mode = _pipeline_mode(args)
    cli_mode = (
        args.no_gui
        or mode is not None
        or args.root_causes
        or args.timeline is not None
        or args.database is not None
        or args.history_summary
        or args.unmute_browsers
        or args.enable_bluetooth_adapter
        or args.repair_bluetooth is not None
        or args.add_bluetooth is not None
        or args.open_bluetooth_settings
        or args.json
    )
    if not cli_mode:
        from .gui import main as gui_main

        gui_main()
        return 0

    if mode is not None:
        result = run_audio_path_diagnosis(
            device_name=args.device,
            mode=mode,
            artifacts_root=args.artifacts_dir,
            write_artifacts=not args.no_artifacts,
            execute=mode in {"repair", "aggressive-repair"},
        )
        if args.json:
            payload = {
                "evidence": result["evidence"],
                "diagnosis": result["diagnosis"],
                "plan": result["plan"],
                "verification": result["verification"],
                "summary": result["summary"],
                "session_dir": result["session_dir"],
            }
            print(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            print(result["report_text"])
            if result.get("session_dir"):
                print(f"\nSession artifacts: {result['session_dir']}")
        state = str(
            ((result.get("diagnosis") or {}).get("classification") or {}).get("state")
            or ""
        )
        if state and state != "AUDIO_PATH_HEALTHY":
            return 2
        return 0

    if args.open_bluetooth_settings:
        open_windows_settings("ms-settings:bluetooth")

    if args.add_bluetooth is not None:
        target_name = args.add_bluetooth or DEFAULT_ADD_BLUETOOTH_NAME
        print(
            "Put the headset in pairing mode (LED flashing) now.\n"
            f"Adding Bluetooth device {target_name} "
            f"({args.bluetooth_address})… Approve UAC."
        )
        result = add_bluetooth_device(
            name=target_name,
            address=args.bluetooth_address,
            elevate=True,
            wait=True,
        )
        if result.get("log"):
            print(result["log"])
        print(
            f"Result: {result.get('overall_result') or ('SUCCESS' if result.get('success') else 'FAILED')}"
        )
        if result.get("classification"):
            print(f"Classification: {result['classification']}")
        print(f"Exit code: {result.get('exit_code')}")
        print(f"Status JSON: {result.get('status_path')}")
        if not result.get("success"):
            return int(result.get("exit_code") or 1)
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
