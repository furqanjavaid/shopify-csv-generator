"""Shopify store audit — themed UI with modules + summary cards."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import customtkinter as ctk

from app.ui import theme as T
from app.ui.sidebar import attach_sidebar
from app.utils.helpers import is_valid_url


class AuditScreen(ctk.CTkFrame):
    """Run a full or CRO-only Shopify store audit and open the Word report."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app
        self.report_path: str | None = None
        self.output_dir: str | None = None
        self._running = False
        self.mode_var = ctk.StringVar(value="cro")

        # Module checklist vars (UI; mode_var drives actual run)
        self.mod_atc = ctk.BooleanVar(value=True)
        self.mod_reviews = ctk.BooleanVar(value=True)
        self.mod_trust = ctk.BooleanVar(value=True)
        self.mod_cart = ctk.BooleanVar(value=True)
        self.mod_mobile = ctk.BooleanVar(value=True)
        self.mod_speed = ctk.BooleanVar(value=True)
        self.mod_email = ctk.BooleanVar(value=True)
        self.mod_seo = ctk.BooleanVar(value=False)

        # Persistent bottom bar FIRST
        self.bottom_bar = ctk.CTkFrame(
            self, fg_color=T.BG_SURFACE_A, height=64, corner_radius=0
        )
        self.bottom_bar.pack(side="bottom", fill="x")
        self.bottom_bar.pack_propagate(False)

        self.open_folder_btn = ctk.CTkButton(
            self.bottom_bar,
            text="Open Folder",
            command=self._open_folder,
            width=120,
            **T.secondary_btn(),
        )
        self.open_btn = ctk.CTkButton(
            self.bottom_bar,
            text="Open Word Report",
            command=self._open_report,
            width=160,
            **T.primary_btn(),
        )
        self.open_folder_btn.pack(side="right", padx=(8, 16), pady=12)
        self.open_btn.pack(side="right", pady=12)
        self.open_btn.configure(
            state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED
        )
        self.open_folder_btn.configure(state="disabled")

        body = attach_sidebar(self, app, "audit")

        T.page_title(
            body,
            "Store Auditor",
            "CRO conversion audit with scored Word report",
        )

        # URL + Run
        url_card = T.card_frame(body)
        url_card.pack(fill="x", pady=(0, T.GRID_GAP))
        url_inner = ctk.CTkFrame(url_card, fg_color="transparent")
        url_inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        self.url_entry = T.styled_entry(
            url_inner, placeholder="https://your-store.com"
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.start_btn = T.primary_button(
            url_inner, "Run Audit", self._start_audit, width=130
        )
        self.start_btn.pack(side="right")

        # Modules checklist — two columns
        mods_card = T.card_frame(body)
        mods_card.pack(fill="x", pady=(0, T.GRID_GAP))
        mods_inner = ctk.CTkFrame(mods_card, fg_color="transparent")
        mods_inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        ctk.CTkLabel(
            mods_inner, text="Audit Modules",
            font=T.font_tuple(T.H3), text_color=T.TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", pady=(0, 8))

        cols = ctk.CTkFrame(mods_inner, fg_color="transparent")
        cols.pack(fill="x")
        left = ctk.CTkFrame(cols, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        right = ctk.CTkFrame(cols, fg_color="transparent")
        right.pack(side="left", fill="x", expand=True)

        left_items = [
            ("ATC above fold", self.mod_atc),
            ("Reviews present", self.mod_reviews),
            ("Trust badges", self.mod_trust),
            ("Cart / checkout", self.mod_cart),
        ]
        right_items = [
            ("Mobile tap target", self.mod_mobile),
            ("Page speed", self.mod_speed),
            ("Email capture", self.mod_email),
            ("Full SEO pass", self.mod_seo),
        ]
        for text, var in left_items:
            self._module_check(left, text, var)
        for text, var in right_items:
            self._module_check(right, text, var)

        # Mode hint from SEO checkbox
        self.mod_seo.trace_add("write", self._sync_mode_from_modules)

        self.error_label = ctk.CTkLabel(
            body, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR
        )
        self.error_label.pack()

        # Results summary cards (hidden until success)
        self.summary_row = ctk.CTkFrame(body, fg_color="transparent")
        self.score_value = self._make_summary_card(self.summary_row, "CRO Score", "—", "/10", T.ACCENT)
        self.issues_value = self._make_summary_card(self.summary_row, "Issues", "0", "found", T.ERROR)
        self.recs_value = self._make_summary_card(
            self.summary_row, "Recommendations", "0", "actions", T.SUCCESS
        )

        self.progress = T.progress_bar(body)
        self.log_box = T.log_box(body, height=180)
        self.log_box.pack(fill="both", expand=True, pady=(8, 0))

    def _module_check(self, parent, text: str, var: ctk.BooleanVar) -> None:
        ctk.CTkCheckBox(
            parent,
            text=text,
            variable=var,
            font=T.font_tuple(T.LABEL),
            text_color=T.TEXT_SECONDARY,
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
            border_color=T.BORDER,
            checkmark_color=T.BG_PRIMARY,
            corner_radius=T.BORDER_RADIUS,
            command=self._sync_mode_from_modules,
        ).pack(anchor="w", pady=4)

    def _sync_mode_from_modules(self, *_args) -> None:
        self.mode_var.set("full" if self.mod_seo.get() else "cro")

    def _make_summary_card(
        self, parent, title: str, value: str, caption: str, color: str
    ) -> ctk.CTkLabel:
        card = T.card_frame(parent)
        card.pack(side="left", expand=True, fill="x", padx=(0, 8))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)
        ctk.CTkLabel(
            inner, text=title, font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED, anchor="w",
        ).pack(fill="x")
        value_label = ctk.CTkLabel(
            inner, text=value, font=T.font(28, "bold"), text_color=color, anchor="w"
        )
        value_label.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(
            inner, text=caption, font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_SECONDARY, anchor="w",
        ).pack(fill="x")
        return value_label

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
        self.summary_row.pack_forget()
        self.report_path = None
        self.output_dir = None
        self.open_btn.configure(
            state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED
        )
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
        self.progress.pack(pady=(0, 8), before=self.log_box)
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
        self.open_btn.configure(
            state="normal", fg_color=T.ACCENT, text_color=T.BG_PRIMARY
        )
        self.open_folder_btn.configure(state="normal")

        pages = len(audit_data.get("pages_crawled") or [])
        findings = audit_data.get("findings") or []
        issues = sum(1 for f in findings if not f.get("passed"))
        recs = sum(1 for f in findings if not f.get("passed") and f.get("fix"))
        score = (audit_data.get("scores") or {}).get("overall", "—")

        self.score_value.configure(text=str(score))
        self.issues_value.configure(text=str(issues))
        self.recs_value.configure(text=str(recs))
        self.summary_row.pack(fill="x", pady=(0, 8), before=self.log_box)

        self._append_log(
            f"Report saved: {report_path} · CRO {score}/10 · {pages} pages · {issues} issues"
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
