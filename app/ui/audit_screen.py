"""Shopify store audit — premium UI."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import customtkinter as ctk

from app.ui import theme as T
from app.utils.helpers import is_valid_url


class AuditScreen(ctk.CTkFrame):
    """Run a full or CRO-only Shopify store audit and open the Word report."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG, corner_radius=0)
        self.app = app
        self.report_path: str | None = None
        self.output_dir: str | None = None
        self._running = False
        self.mode_var = ctk.StringVar(value="cro")

        T.header_bar(self, "Store Audit", self._go_home)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=16)

        # URL
        url_card = T.card_frame(body)
        url_card.pack(fill="x", pady=(0, 12))

        url_inner = ctk.CTkFrame(url_card, fg_color="transparent")
        url_inner.pack(fill="x", padx=16, pady=14)

        ctk.CTkLabel(
            url_inner, text="🌐", font=T.font(18), text_color=T.BLUE
        ).pack(side="left", padx=(0, 8))

        self.url_entry = T.styled_entry(
            url_inner, placeholder="https://your-store.com"
        )
        self.url_entry.pack(side="left", fill="x", expand=True)

        # Mode cards
        modes = ctk.CTkFrame(body, fg_color="transparent")
        modes.pack(fill="x", pady=(0, 12))

        self.cro_card = self._mode_card(
            modes,
            "⚡ CRO Only",
            "Faster · 2-3 min",
            "cro",
        )
        self.cro_card.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self.full_card = self._mode_card(
            modes,
            "🔍 Full Audit",
            "SEO + CRO · 4-6 min",
            "full",
        )
        self.full_card.pack(side="left", expand=True, fill="x", padx=(8, 0))

        self._refresh_mode_cards()

        self.start_btn = T.primary_button(
            body, "Start Audit →", self._start_audit, width=400, height=44
        )
        self.start_btn.pack(fill="x", pady=(4, 12))

        self.error_label = ctk.CTkLabel(
            body, text="", font=T.font(12), text_color=T.DANGER
        )
        self.error_label.pack()

        self.success_banner = ctk.CTkFrame(
            body,
            fg_color=T.ACCENT_DIM,
            corner_radius=8,
            border_width=1,
            border_color=T.ACCENT,
            height=40,
        )
        self.success_label = ctk.CTkLabel(
            self.success_banner,
            text="Report Ready",
            font=T.font(13, "bold"),
            text_color=T.ACCENT,
        )
        self.success_label.pack(pady=8)

        self.progress = T.progress_bar(body)
        self.log_box = T.log_box(body, height=200)
        self.log_box.pack(fill="both", expand=True, pady=(8, 0))

        bottom = ctk.CTkFrame(self, fg_color=T.SURFACE, height=64, corner_radius=0)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)

        bottom_inner = ctk.CTkFrame(bottom, fg_color="transparent")
        bottom_inner.pack(fill="both", expand=True, padx=24)

        self.open_folder_btn = T.secondary_button(
            bottom_inner, "Open Folder", self._open_folder, width=120
        )
        self.open_folder_btn.configure(state="disabled")
        self.open_folder_btn.pack(side="right", pady=12, padx=(8, 0))

        self.open_btn = T.primary_button(
            bottom_inner, "📄 Open Report", self._open_report, width=160
        )
        self.open_btn.configure(state="disabled")
        self.open_btn.pack(side="right", pady=12)

    def _mode_card(self, parent, title: str, subtitle: str, value: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            fg_color=T.CARD,
            corner_radius=12,
            border_width=2,
            border_color=T.BORDER,
            height=88,
        )
        card.pack_propagate(False)

        ctk.CTkLabel(
            card, text=title, font=T.font(15, "bold"), text_color=T.TEXT
        ).pack(pady=(18, 2))
        ctk.CTkLabel(
            card, text=subtitle, font=T.font(12), text_color=T.TEXT_SECONDARY
        ).pack()

        def select(_e=None, v=value):
            self.mode_var.set(v)
            self._refresh_mode_cards()

        card.bind("<Button-1>", select)
        for child in card.winfo_children():
            child.bind("<Button-1>", select)

        card._mode_value = value  # type: ignore[attr-defined]
        return card

    def _refresh_mode_cards(self) -> None:
        selected = self.mode_var.get()
        for card in (self.cro_card, self.full_card):
            val = getattr(card, "_mode_value", "")
            if val == selected:
                card.configure(border_color=T.ACCENT, fg_color=T.ACCENT_DIM)
            else:
                card.configure(border_color=T.BORDER, fg_color=T.CARD)

    def _go_home(self) -> None:
        if self._running:
            return
        from app.ui.home_screen import HomeScreen

        self.app.show_screen(HomeScreen)

    def _append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"› {message.rstrip()}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_progress(self, message: str) -> None:
        self.after(0, lambda m=message: self._append_log(m))

    def _start_audit(self) -> None:
        url = self.url_entry.get().strip()
        self.error_label.configure(text="")
        self.success_banner.pack_forget()
        self.report_path = None
        self.output_dir = None
        self.open_btn.configure(state="disabled")
        self.open_folder_btn.configure(state="disabled")

        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        if not is_valid_url(url):
            self.error_label.configure(text="URL must start with http:// or https://")
            return

        mode = self.mode_var.get() or "cro"
        self._running = True
        self.start_btn.configure(state="disabled")
        self.progress.pack(pady=(0, 8))
        self.progress.start()
        self._append_log(f"Starting audit ({mode}) for {url}...")

        thread = threading.Thread(
            target=self._run_audit_thread, args=(url, mode), daemon=True
        )
        thread.start()

    def _run_audit_thread(self, url: str, mode: str) -> None:
        try:
            from app.core.audit_report import generate_report
            from app.core.store_auditor import run_audit

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
        findings = audit_data.get("findings") or []
        issues = sum(1 for f in findings if not f.get("passed"))
        score = (audit_data.get("scores") or {}).get("overall", "—")
        self._append_log(f"Report saved: {report_path}")
        self.success_label.configure(
            text=f"✓  Report Ready — CRO {score}/10 · {pages} pages · {issues} issues"
        )
        self.success_banner.pack(fill="x", pady=(0, 8), before=self.log_box)

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
