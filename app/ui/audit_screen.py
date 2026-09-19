"""Shopify store audit screen."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import customtkinter as ctk

from app.utils.helpers import is_valid_url


class AuditScreen(ctk.CTkFrame):
    """Run a full or CRO-only Shopify store audit and open the Word report."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color="#1a1a2e", corner_radius=0)
        self.app = app
        self.report_path: str | None = None
        self.output_dir: str | None = None
        self._running = False

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 8))

        back = ctk.CTkButton(
            top,
            text="← Back",
            width=80,
            height=28,
            fg_color="transparent",
            hover_color="#16213e",
            text_color="#9ca3af",
            anchor="w",
            command=self._go_home,
        )
        back.pack(side="left")

        title = ctk.CTkLabel(
            self,
            text="Audit Shopify Store",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#ffffff",
        )
        title.pack(pady=(8, 8))

        hint = ctk.CTkLabel(
            self,
            text="CRO + SEO audit with screenshots and a Word report",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
        )
        hint.pack(pady=(0, 16))

        mode_row = ctk.CTkFrame(self, fg_color="transparent")
        mode_row.pack(pady=(0, 12))

        self.mode_var = ctk.StringVar(value="full")
        ctk.CTkRadioButton(
            mode_row,
            text="Full Audit (SEO + CRO)",
            variable=self.mode_var,
            value="full",
            text_color="#d1d5db",
            fg_color="#3b82f6",
            hover_color="#1f2f54",
        ).pack(side="left", padx=12)

        ctk.CTkRadioButton(
            mode_row,
            text="CRO Only (Faster)",
            variable=self.mode_var,
            value="cro",
            text_color="#d1d5db",
            fg_color="#3b82f6",
            hover_color="#1f2f54",
        ).pack(side="left", padx=12)

        input_row = ctk.CTkFrame(self, fg_color="transparent")
        input_row.pack(fill="x", padx=40)

        self.url_entry = ctk.CTkEntry(
            input_row,
            placeholder_text="https://your-store.com",
            height=40,
            font=ctk.CTkFont(size=13),
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.start_btn = ctk.CTkButton(
            input_row,
            text="Start Audit →",
            width=140,
            height=40,
            command=self._start_audit,
        )
        self.start_btn.pack(side="right")

        self.error_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=13),
            text_color="#ef4444",
        )
        self.error_label.pack(pady=(8, 4))

        self.success_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=13),
            text_color="#22c55e",
        )
        self.success_label.pack(pady=(0, 4))

        self.progress = ctk.CTkProgressBar(self, width=400, mode="indeterminate")
        self.progress.pack(pady=4)
        self.progress.pack_forget()

        log_label = ctk.CTkLabel(
            self,
            text="Progress log",
            font=ctk.CTkFont(size=12),
            text_color="#9ca3af",
            anchor="w",
        )
        log_label.pack(fill="x", padx=40, pady=(8, 2))

        self.log_box = ctk.CTkTextbox(
            self,
            width=820,
            height=260,
            fg_color="#0f172a",
            text_color="#d1d5db",
            font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled",
        )
        self.log_box.pack(padx=40, pady=(0, 8), fill="both", expand=True)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", side="bottom", padx=20, pady=16)

        self.open_btn = ctk.CTkButton(
            bottom,
            text="Open Report",
            width=140,
            height=36,
            state="disabled",
            fg_color="#16213e",
            hover_color="#1f2f54",
            command=self._open_report,
        )
        self.open_btn.pack(side="right")

        self.open_folder_btn = ctk.CTkButton(
            bottom,
            text="Open Folder",
            width=120,
            height=36,
            state="disabled",
            fg_color="#16213e",
            hover_color="#1f2f54",
            command=self._open_folder,
        )
        self.open_folder_btn.pack(side="right", padx=(0, 8))

    def _go_home(self) -> None:
        if self._running:
            return
        from app.ui.home_screen import HomeScreen

        self.app.show_screen(HomeScreen)

    def _append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message.rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_progress(self, message: str) -> None:
        self.after(0, lambda m=message: self._append_log(m))

    def _start_audit(self) -> None:
        url = self.url_entry.get().strip()
        self.error_label.configure(text="")
        self.success_label.configure(text="")
        self.report_path = None
        self.output_dir = None
        self.open_btn.configure(state="disabled")
        self.open_folder_btn.configure(state="disabled")

        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        if not is_valid_url(url):
            self.error_label.configure(
                text="URL must start with http:// or https://"
            )
            return

        mode = self.mode_var.get() or "full"
        self._running = True
        self.start_btn.configure(state="disabled")
        self.progress.pack(pady=4)
        self.progress.start()
        self._append_log(f"Starting audit ({mode}) for {url}...")

        thread = threading.Thread(
            target=self._run_audit_thread,
            args=(url, mode),
            daemon=True,
        )
        thread.start()

    def _run_audit_thread(self, url: str, mode: str) -> None:
        try:
            from app.core.store_auditor import run_audit
            from app.core.audit_report import generate_report

            audit_data, output_dir = asyncio.run(
                run_audit(url, mode=mode, progress=self._on_progress)
            )
            self._on_progress("Generating report...")
            report_path = generate_report(audit_data, output_dir)
            self.after(
                0,
                lambda: self._on_success(output_dir, report_path, audit_data),
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            self.after(0, lambda: self._on_error(msg))

    def _on_success(self, output_dir: str, report_path: str, audit_data: dict) -> None:
        self._running = False
        self.progress.stop()
        self.progress.pack_forget()
        self.start_btn.configure(state="normal")
        self.output_dir = output_dir
        self.report_path = report_path
        self.open_btn.configure(state="normal")
        self.open_folder_btn.configure(state="normal")

        pages = len(audit_data.get("pages_crawled") or [])
        issues = sum(len(r.get("issues") or []) for r in audit_data.get("results") or [])
        self._append_log(f"Report saved: {report_path}")
        self.success_label.configure(
            text=f"Audit complete — {pages} pages crawled, {issues} issues. Report ready."
        )

    def _on_error(self, message: str) -> None:
        self._running = False
        self.progress.stop()
        self.progress.pack_forget()
        self.start_btn.configure(state="normal")
        self._append_log(f"ERROR: {message}")
        self.error_label.configure(text=message)

    def _open_report(self) -> None:
        path = self.report_path
        if not path or not Path(path).exists():
            self.error_label.configure(text="Report file not found.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as exc:  # noqa: BLE001
            self.error_label.configure(text=f"Could not open report: {exc}")

    def _open_folder(self) -> None:
        folder = self.output_dir
        if not folder or not Path(folder).exists():
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", folder], check=False)
            else:
                subprocess.run(["xdg-open", folder], check=False)
        except Exception:
            pass
