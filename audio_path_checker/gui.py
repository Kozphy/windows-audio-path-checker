from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from .diagnostics import (
    collect_snapshot,
    open_windows_settings,
    output_device_choices,
    play_test_tone,
    save_report,
    stop_test_tone,
    unmute_silent_browser_sessions,
)
from .bluetooth import (
    preferred_bluetooth_repair_target,
    repair_bluetooth_pairing,
)


class AudioCheckerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.snapshot: dict = {}
        self.device_by_label: dict[str, int] = {}

        root.title("Windows Audio Path Checker")
        root.geometry("1040x720")
        root.minsize(880, 600)

        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Heading.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Subheading.TLabel", font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=28)

        outer = ttk.Frame(root, padding=16)
        outer.pack(fill=BOTH, expand=True)

        ttk.Label(
            outer, text="Why does the Windows test work, but apps are silent?",
            style="Heading.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Start a YouTube video, leave it playing, then scan. "
                "This checker compares the headphone, app, and browser audio paths."
            ),
            style="Subheading.TLabel",
            wraplength=960,
        ).pack(anchor="w", pady=(4, 12))

        action_bar = ttk.Frame(outer)
        action_bar.pack(fill=X, pady=(0, 10))
        self.scan_button = ttk.Button(
            action_bar, text="Scan again", command=self.start_scan,
            style="Accent.TButton"
        )
        self.scan_button.pack(side=LEFT)
        ttk.Button(
            action_bar, text="Open Volume mixer",
            command=lambda: self._open_settings("ms-settings:apps-volume")
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Button(
            action_bar, text="Open Sound settings",
            command=lambda: self._open_settings("ms-settings:sound")
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Button(
            action_bar, text="Unmute browser sessions",
            command=self.unmute_browsers
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Button(
            action_bar, text="Save report", command=self.save_current_report
        ).pack(side=RIGHT)

        bt_bar = ttk.Frame(outer)
        bt_bar.pack(fill=X, pady=(0, 10))
        ttk.Button(
            bt_bar,
            text="Open Bluetooth settings",
            command=lambda: self._open_settings("ms-settings:bluetooth"),
        ).pack(side=LEFT)
        ttk.Button(
            bt_bar,
            text="Repair Bluetooth pairing",
            command=self.repair_bluetooth,
        ).pack(side=LEFT, padx=(8, 0))
        ttk.Label(
            bt_bar,
            text="Use when audio works but the Bluetooth icon says disconnected, or Remove device is stuck/grayed out.",
            wraplength=620,
        ).pack(side=LEFT, padx=(12, 0))

        test_frame = ttk.LabelFrame(outer, text="Sound path tests", padding=10)
        test_frame.pack(fill=X, pady=(0, 10))
        ttk.Label(test_frame, text="Output:").pack(side=LEFT)
        self.device_choice = ttk.Combobox(
            test_frame, state="readonly", width=48
        )
        self.device_choice.pack(side=LEFT, padx=(8, 8), fill=X, expand=True)
        ttk.Button(
            test_frame, text="Test app sound", command=self.play_tone
        ).pack(side=LEFT)
        ttk.Button(
            test_frame, text="Stop", command=self.stop_tone
        ).pack(side=LEFT, padx=(6, 0))
        ttk.Button(
            test_frame, text="Test browser sound", command=self.open_browser_test
        ).pack(side=LEFT, padx=(6, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=BOTH, expand=True)

        results_tab = ttk.Frame(notebook, padding=8)
        sessions_tab = ttk.Frame(notebook, padding=8)
        report_tab = ttk.Frame(notebook, padding=8)
        notebook.add(results_tab, text="Results")
        notebook.add(sessions_tab, text="App sessions")
        notebook.add(report_tab, text="Technical report")

        result_columns = ("severity", "check", "details", "next")
        self.results = ttk.Treeview(
            results_tab, columns=result_columns, show="headings"
        )
        self.results.heading("severity", text="Status")
        self.results.heading("check", text="Check")
        self.results.heading("details", text="What was found")
        self.results.heading("next", text="What to do")
        self.results.column("severity", width=85, stretch=False)
        self.results.column("check", width=230)
        self.results.column("details", width=310)
        self.results.column("next", width=330)
        self.results.tag_configure("critical", foreground="#b42318")
        self.results.tag_configure("warning", foreground="#9a6700")
        self.results.tag_configure("ok", foreground="#067647")
        self.results.tag_configure("info", foreground="#175cd3")
        result_scroll = ttk.Scrollbar(
            results_tab, orient="vertical", command=self.results.yview
        )
        self.results.configure(yscrollcommand=result_scroll.set)
        self.results.pack(side=LEFT, fill=BOTH, expand=True)
        result_scroll.pack(side=RIGHT, fill=Y)

        session_columns = ("process", "volume", "muted", "output", "state")
        self.sessions = ttk.Treeview(
            sessions_tab, columns=session_columns, show="headings"
        )
        self.sessions.heading("process", text="Application")
        self.sessions.heading("volume", text="App volume")
        self.sessions.heading("muted", text="Muted")
        self.sessions.heading("output", text="Output device")
        self.sessions.heading("state", text="Session state")
        self.sessions.column("process", width=180)
        self.sessions.column("volume", width=90)
        self.sessions.column("muted", width=70)
        self.sessions.column("output", width=280)
        self.sessions.column("state", width=160)
        session_scroll = ttk.Scrollbar(
            sessions_tab, orient="vertical", command=self.sessions.yview
        )
        self.sessions.configure(yscrollcommand=session_scroll.set)
        self.sessions.pack(side=LEFT, fill=BOTH, expand=True)
        session_scroll.pack(side=RIGHT, fill=Y)

        self.report_text = tk.Text(
            report_tab,
            wrap="none",
            font=("Consolas", 9),
            background="#101828",
            foreground="#f2f4f7",
            insertbackground="#f2f4f7",
        )
        report_y = ttk.Scrollbar(
            report_tab, orient="vertical", command=self.report_text.yview
        )
        report_x = ttk.Scrollbar(
            report_tab, orient="horizontal", command=self.report_text.xview
        )
        self.report_text.configure(
            yscrollcommand=report_y.set, xscrollcommand=report_x.set
        )
        self.report_text.grid(row=0, column=0, sticky="nsew")
        report_y.grid(row=0, column=1, sticky="ns")
        report_x.grid(row=1, column=0, sticky="ew")
        report_tab.rowconfigure(0, weight=1)
        report_tab.columnconfigure(0, weight=1)

        self.status = tk.StringVar(value="Ready to scan.")
        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=(8, 0))
        root.after(300, self.start_scan)

    def start_scan(self) -> None:
        self.scan_button.configure(state="disabled")
        self.status.set("Scanning playback devices and app audio sessions…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        try:
            snapshot = collect_snapshot()
            self.root.after(0, lambda: self._display_snapshot(snapshot))
        except Exception as exc:
            self.root.after(0, lambda: self._show_error("Scan failed", exc))

    def _display_snapshot(self, snapshot: dict) -> None:
        self.snapshot = snapshot
        for item in self.results.get_children():
            self.results.delete(item)
        labels = {
            "critical": "FIX",
            "warning": "CHECK",
            "ok": "OK",
            "info": "INFO",
        }
        for finding in snapshot.get("findings", []):
            severity = finding.get("severity", "info")
            self.results.insert(
                "",
                END,
                values=(
                    labels.get(severity, severity.upper()),
                    finding.get("title", ""),
                    finding.get("detail", ""),
                    finding.get("action", ""),
                ),
                tags=(severity,),
            )

        for item in self.sessions.get_children():
            self.sessions.delete(item)
        for session in snapshot.get("core_audio", {}).get("sessions", []):
            self.sessions.insert(
                "",
                END,
                values=(
                    session.get("process", ""),
                    f"{float(session.get('volume', 0)) * 100:.0f}%",
                    "Yes" if session.get("muted") else "No",
                    session.get("output_device") or "(unknown)",
                    session.get("state", ""),
                ),
            )

        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", END)
        self.report_text.insert(
            "1.0", json.dumps(snapshot, indent=2, ensure_ascii=False)
        )
        self.report_text.configure(state="disabled")

        devices = output_device_choices(snapshot)
        self.device_by_label.clear()
        values: list[str] = []
        for device in devices:
            default_mark = " — Windows default" if device.get("is_default") else ""
            label = (
                f"{device.get('name')} [{device.get('host_api')}]"
                f"{default_mark}"
            )
            values.append(label)
            self.device_by_label[label] = int(device["index"])
        self.device_choice["values"] = values
        if values:
            default_position = next(
                (
                    index for index, device in enumerate(devices)
                    if device.get("is_default")
                ),
                0,
            )
            self.device_choice.current(default_position)

        critical_count = sum(
            finding.get("severity") == "critical"
            for finding in snapshot.get("findings", [])
        )
        warning_count = sum(
            finding.get("severity") == "warning"
            for finding in snapshot.get("findings", [])
        )
        self.status.set(
            f"Scan complete: {critical_count} fix item(s), "
            f"{warning_count} item(s) to check."
        )
        self.scan_button.configure(state="normal")

    def play_tone(self) -> None:
        label = self.device_choice.get()
        if not label:
            messagebox.showwarning(
                "No output", "Scan first, then choose a playback output."
            )
            return
        try:
            self.status.set(play_test_tone(self.device_by_label[label]))
        except Exception as exc:
            self._show_error("The app sound test failed", exc)

    def stop_tone(self) -> None:
        try:
            stop_test_tone()
            self.status.set("Sound test stopped.")
        except Exception as exc:
            self._show_error("Could not stop the sound test", exc)

    def open_browser_test(self) -> None:
        test_page = Path(__file__).with_name("browser_test.html").resolve()
        if not webbrowser.open(test_page.as_uri()):
            messagebox.showinfo(
                "Browser test",
                f"Open this file in your browser:\n{test_page}",
            )
        self.status.set(
            "Browser test opened. Select Play browser test sound on that page."
        )

    def unmute_browsers(self) -> None:
        if not messagebox.askyesno(
            "Unmute browser sessions?",
            (
                "This will unmute recognized browser sessions and raise any "
                "browser session below 50% to 50%.\n\n"
                "After that, the checker will rescan to verify the fix.\n"
                "You can change the volume again in Windows Volume mixer."
            ),
        ):
            return
        self.scan_button.configure(state="disabled")
        self.status.set("Adjusting browser sessions and rescanning…")
        threading.Thread(target=self._unmute_worker, daemon=True).start()

    def _unmute_worker(self) -> None:
        try:
            changed = unmute_silent_browser_sessions()
            snapshot = collect_snapshot()
            self.root.after(
                0, lambda: self._after_unmute(changed, snapshot)
            )
        except Exception as exc:
            self.root.after(
                0, lambda: self._show_error("Could not adjust browser sessions", exc)
            )

    def _after_unmute(self, changed: list[str], snapshot: dict) -> None:
        self._display_snapshot(snapshot)
        still_silent = any(
            finding.get("code") == "browser-session-silent"
            for finding in snapshot.get("findings", [])
        )
        if changed:
            body = "\n".join(changed)
            if still_silent:
                body += (
                    "\n\nRescan: a browser session is still silent. "
                    "Check its Output device in Volume mixer."
                )
            else:
                body += "\n\nRescan: no silent browser sessions remain."
            messagebox.showinfo("Browser audio adjusted", body)
        else:
            messagebox.showinfo(
                "No change needed",
                (
                    "No recognized browser session was muted or below 50%. "
                    "Start YouTube playback and scan again."
                ),
            )

    def repair_bluetooth(self) -> None:
        if not self.snapshot:
            messagebox.showwarning("No scan yet", "Run a scan first.")
            return
        target = preferred_bluetooth_repair_target(self.snapshot)
        if not target:
            messagebox.showinfo(
                "No Bluetooth headset found",
                (
                    "No paired Bluetooth headset with an address was found. "
                    "Open Bluetooth settings and check the device list."
                ),
            )
            self._open_settings("ms-settings:bluetooth")
            return
        if not messagebox.askyesno(
            "Repair Bluetooth pairing?",
            (
                f"This will clear the Windows pairing cache for:\n"
                f"  {target.get('name')} ({target.get('address')})\n\n"
                "Windows will ask for Administrator permission (UAC).\n"
                "After it finishes you must reboot, then re-pair the headset.\n\n"
                "Use this when:\n"
                "• audio works but the Bluetooth icon says disconnected\n"
                "• Remove device is stuck on Removing / grayed out"
            ),
        ):
            return
        self.scan_button.configure(state="disabled")
        self.status.set(
            f"Repairing Bluetooth pairing for {target.get('name')}… Approve UAC."
        )
        threading.Thread(
            target=self._repair_bluetooth_worker,
            args=(target,),
            daemon=True,
        ).start()

    def _repair_bluetooth_worker(self, target: dict) -> None:
        try:
            result = repair_bluetooth_pairing(
                address=str(target.get("address")),
                friendly_name=str(target.get("name") or "Bluetooth headset"),
                elevate=True,
                wait=True,
            )
            snapshot = collect_snapshot()
            self.root.after(
                0, lambda: self._after_bluetooth_repair(result, snapshot)
            )
        except Exception as exc:
            self.root.after(
                0,
                lambda: self._show_error("Bluetooth repair failed", exc),
            )

    def _after_bluetooth_repair(self, result: dict, snapshot: dict) -> None:
        self._display_snapshot(snapshot)
        log_tail = "\n".join(
            line for line in str(result.get("log") or "").splitlines() if line
        )[-800:]
        messagebox.showinfo(
            "Bluetooth repair finished",
            (
                f"Target: {result.get('friendly_name')} ({result.get('address')})\n"
                f"Reboot required: yes\n\n"
                f"{log_tail or 'No log captured (UAC may have been declined).'}\n\n"
                "Reboot now, then re-pair the headset in Bluetooth settings."
            ),
        )

    def save_current_report(self) -> None:
        if not self.snapshot:
            messagebox.showwarning("No report", "Run a scan first.")
            return
        default_name = f"audio-report-{datetime.now():%Y%m%d-%H%M%S}.json"
        path = filedialog.asksaveasfilename(
            title="Save audio diagnostic report",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON report", "*.json")],
        )
        if not path:
            return
        try:
            saved = save_report(self.snapshot, path)
            self.status.set(f"Report saved to {saved}.")
        except Exception as exc:
            self._show_error("Could not save the report", exc)

    def _open_settings(self, uri: str) -> None:
        try:
            open_windows_settings(uri)
        except Exception as exc:
            self._show_error("Could not open Windows Settings", exc)

    def _show_error(self, title: str, exc: BaseException) -> None:
        self.scan_button.configure(state="normal")
        self.status.set(f"{title}: {exc}")
        messagebox.showerror(title, str(exc))


def main() -> None:
    root = tk.Tk()
    AudioCheckerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

