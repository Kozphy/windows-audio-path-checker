from __future__ import annotations

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


BROWSER_PROCESSES = {
    "brave.exe",
    "chrome.exe",
    "firefox.exe",
    "msedge.exe",
    "opera.exe",
    "vivaldi.exe",
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "ok": 2, "info": 3}


def _error(source: str, exc: BaseException) -> dict[str, str]:
    return {
        "source": source,
        "type": type(exc).__name__,
        "message": str(exc),
    }


def _friendly_process_name(session: Any) -> str:
    process = getattr(session, "Process", None)
    if process is not None:
        try:
            return str(process.name())
        except Exception:
            pass
    display_name = getattr(session, "DisplayName", None)
    if display_name:
        return str(display_name)
    pid = getattr(session, "ProcessId", 0)
    return "System Sounds" if pid == 0 else f"PID {pid}"


def _init_com() -> tuple[Any, Any]:
    """Initialize COM when available, returning cleanup functions."""
    try:
        from comtypes import CoInitialize, CoUninitialize

        CoInitialize()
        return CoInitialize, CoUninitialize
    except Exception:
        return None, None


def _collect_audio_services() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    services: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if sys.platform != "win32":
        return services, errors

    try:
        import psutil
    except Exception as exc:
        return services, [_error("Windows services", exc)]

    for service_name, friendly_name in (
        ("Audiosrv", "Windows Audio"),
        ("AudioEndpointBuilder", "Windows Audio Endpoint Builder"),
    ):
        try:
            service = psutil.win_service_get(service_name)
            data = service.as_dict()
            services.append(
                {
                    "name": service_name,
                    "friendly_name": friendly_name,
                    "status": data.get("status", "unknown"),
                    "start_type": data.get("start_type", "unknown"),
                }
            )
        except Exception as exc:
            errors.append(_error(f"Service {service_name}", exc))
    return services, errors


def _collect_portaudio() -> tuple[dict[str, Any], list[dict[str, str]]]:
    result: dict[str, Any] = {
        "default_output_index": None,
        "default_output_name": None,
        "output_devices": [],
        "host_apis": [],
    }
    errors: list[dict[str, str]] = []

    try:
        import sounddevice as sd

        host_apis = list(sd.query_hostapis())
        result["host_apis"] = [
            {
                ï^ú¶‰žËkºwµçheading("details", text="What was found")
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

        session_columns = ("process", "volume", "muted", "state")
        self.sessions = ttk.Treeview(
            sessions_tab, columns=session_columns, show="headings"
        )
        self.sessions.heading("process", text="Application")
        self.sessions.heading("volume", text="App volume")
        self.sessions.heading("muted", text="Muted")
        self.sessions.heading("state", text="Session state")
        self.sessions.column("process", width=330)
        self.sessions.column("volume", width=120)
        self.sessions.column("muted", width=100)
        self.sessions.column("state", width=260)
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
        self.status.set("Scanning playback devices and app audio sessionsâ€¦")
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
            default_mark = " â€” Windows default" if device.get("is_default") else ""
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
                "You can change the volume again in Windows Volume mixer."
            ),
        ):
            return
        try:
            changed = unmute_silent_browser_sessions()
            if changed:
                messagebox.showinfo(
                    "Browser audio adjusted", "\n".join(changed)
                )
            else:
                messagebox.showinfo(
                    "No change needed",
                    (
                        "No recognized browser session was muted or below 50%. "
                        "Start YouTube playback and scan again."
                    ),
                )
            self.start_scan()
        except Exception as exc:
            self._show_error("Could not adjust browser sessions", exc)

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

