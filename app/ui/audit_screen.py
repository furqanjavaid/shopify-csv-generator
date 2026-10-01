"""Shopify store audit — mockup layout; existing audit logic preserved."""

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
from app.utils.job_status import TOOL_STORE_AUDITOR


class AuditScreen(ctk.CTkFrame):
    """Run a full or CRO-only Shopify store audit and open the Word report."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app
        self.report_path: str | None = None
        self.output_dir: str | None = None
        self._running = False
        self.mode_var = ctk.StringVar(value="cro")
        self._findings_rows: list[ctk.CTkFrame] = []

        # Module checklist vars (UI; mode_var drives actual run)
        self.mod_atc = ctk.BooleanVar(value=True)
        self.mod_reviews = ctk.BooleanVar(value=True)
        self.mod_trust = ctk.BooleanVar(value=True)
        self.mod_cart = ctk.BooleanVar(value=True)
        self.mod_mobile = ctk.BooleanVar(value=True)
        self.mod_speed = ctk.BooleanVar(value=True)
        self.mod_email = ctk.BooleanVar(value=True)
        self.mod_seo = ctk.BooleanVar(value=False)

        # Bottom action bar
        self.bottom_bar = ctk.CTkFrame(
            self, fg_color="#E8E5E0", height=56, corner_radius=0, border_width=1, border_color=T.BORDER
        )
        self.bottom_bar.pack(side="bottom", fill="x")
        self.bottom_bar.pack_propagate(False)
        self.bottom_bar.grid_columnconfigure(0, weight=1)

        self.open_folder_btn = ctk.CTkButton(
            self.bottom_bar, text="Open Folder", command=self._open_folder, width=120, **T.secondary_btn()
        )
        self.open_btn = ctk.CTkButton(
            self.bottom_bar, text="Open Word Report", command=self._open_report, width=160, **T.primary_btn()
        )
        self.open_folder_btn.grid(row=0, column=1, sticky="e", padx=(8, 8), pady=10)
        self.open_btn.grid(row=0, column=2, sticky="e", padx=(0, 16), pady=10)
        self.open_btn.configure(state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED)
        self.open_folder_btn.configure(state="disabled")

        body = attach_sidebar(self, app, "audit")
        body.grid_rowconfigure(4, weight=1)

        # Header
        header = ctk.CTkFrame(body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(
            header, text="Store Auditor", font=T.font_tuple(T.H1), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Audit eCommerce stores for issues and opportunities to improve performance, SEO, content and more.",
            font=T.font_tuple(T.BODY),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
            wraplength=720,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # URL card
        url_card = T.card_frame(body)
        url_card.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        url_card.grid_columnconfigure(0, weight=1)
        url_inner = ctk.CTkFrame(url_card, fg_color="transparent")
        url_inner.grid(row=0, column=0, sticky="ew", padx=18, pady=16)
        url_inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            url_inner, text="Enter Store URL", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.url_entry = T.styled_entry(url_inner, placeholder="https://www.example-store.com")
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self.start_btn = T.primary_button(url_inner, "Run Audit →", self._start_audit, width=140)
        self.start_btn.grid(row=1, column=1)

        # Modules (compact, drives mode)
        mods = ctk.CTkFrame(url_inner, fg_color="transparent")
        mods.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        mods.grid_columnconfigure((0, 1, 2, 3), weight=1)
        items = [
            ("ATC above fold", self.mod_atc),
            ("Reviews", self.mod_reviews),
            ("Trust badges", self.mod_trust),
            ("Cart / checkout", self.mod_cart),
            ("Mobile", self.mod_mobile),
            ("Page speed", self.mod_speed),
            ("Email capture", self.mod_email),
            ("Full SEO pass", self.mod_seo),
        ]
        for i, (text, var) in enumerate(items):
            self._module_check(mods, text, var, row=i // 4, col=i % 4)
        self.mod_seo.trace_add("write", self._sync_mode_from_modules)

        self.error_label = ctk.CTkLabel(
            url_inner, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR, anchor="w"
        )
        self.error_label.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self.progress = ctk.CTkProgressBar(
            url_inner, height=10, mode="indeterminate", progress_color=T.GOLD, fg_color=T.BORDER, corner_radius=6
        )
        self.progress.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.progress.grid_remove()

        # Score cards
        self.summary_row = ctk.CTkFrame(body, fg_color="transparent")
        self.summary_row.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        self.summary_row.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.score_value = self._make_score_card(self.summary_row, 0, "Performance Score", "—", "/100", T.GOLD)
        self.seo_value = self._make_score_card(self.summary_row, 1, "SEO Score", "—", "/100", T.GOLD)
        self.content_value = self._make_score_card(self.summary_row, 2, "Content Completeness", "—", "/100", T.GOLD)
        self.issues_value = self._make_score_card(self.summary_row, 3, "Technical Issues", "0", "Issues", T.ACCENT)
        self.recs_value = self.issues_value  # alias for legacy updates
        # Hidden CRO label used by success handler
        self._cro_score_label = self.score_value

        # Bottom: Findings + Recommendations
        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="nsew")
        bottom.grid_columnconfigure(0, weight=7)
        bottom.grid_columnconfigure(1, weight=3)
        bottom.grid_rowconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)

        findings_card = T.card_frame(bottom)
        findings_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        findings_card.grid_columnconfigure(0, weight=1)
        findings_card.grid_rowconfigure(2, weight=1)

        fh = ctk.CTkFrame(findings_card, fg_color="transparent")
        fh.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        fh.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(fh, text="Audit Findings", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(
            fh, text="Issues and opportunities discovered during the audit.", font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED, anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.category_filter = ctk.CTkOptionMenu(
            fh,
            values=["All Categories"],
            width=140,
            **T.option_menu_style(),
        )
        self.category_filter.grid(row=0, column=1, rowspan=2, sticky="e")

        # Table header
        thead = ctk.CTkFrame(findings_card, fg_color=T.BG_PRIMARY, corner_radius=4)
        thead.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 0))
        thead.grid_columnconfigure(2, weight=1)
        for col, (txt, w) in enumerate((("#", 28), ("Priority", 80), ("Issue", 1), ("Status", 120))):
            ctk.CTkLabel(
                thead, text=txt, font=T.font(11, "bold"), text_color=T.TEXT_MUTED, width=w if col != 2 else 0, anchor="w"
            ).grid(row=0, column=col, sticky="ew" if col == 2 else "w", padx=6, pady=6)

        self.findings_list = ctk.CTkScrollableFrame(
            findings_card, fg_color="transparent", height=180
        )
        self.findings_list.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 12))
        self.findings_list.grid_columnconfigure(0, weight=1)
        self._findings_placeholder = ctk.CTkLabel(
            self.findings_list,
            text="Run an audit to see findings here.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        )
        self._findings_placeholder.grid(row=0, column=0, sticky="w", padx=8, pady=8)

        # Recommendations
        rec_card = T.card_frame(bottom)
        rec_card.grid(row=0, column=1, sticky="nsew")
        rec_card.grid_columnconfigure(0, weight=1)
        rec_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            rec_card, text="Top Recommendations", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            rec_card,
            text="Prioritised actions to improve the store.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(36, 0))

        self.recs_list = ctk.CTkScrollableFrame(rec_card, fg_color="transparent")
        self.recs_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=(8, 12))
        self.recs_list.grid_columnconfigure(0, weight=1)
        self._recs_placeholder = ctk.CTkLabel(
            self.recs_list,
            text="Recommendations will appear after an audit.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
            wraplength=200,
        )
        self._recs_placeholder.grid(row=0, column=0, sticky="w", padx=6, pady=6)

        # Hidden log for progress messages
        self.log_box = ctk.CTkTextbox(body, height=1, fg_color=T.BG_PRIMARY, text_color=T.TEXT_MUTED)
        self.log_box.grid_remove()

    def _module_check(self, parent, text: str, var: ctk.BooleanVar, row: int, col: int) -> None:
        ctk.CTkCheckBox(
            parent,
            text=text,
            variable=var,
            font=T.font(12),
            text_color=T.TEXT_SECONDARY,
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
            border_color=T.BORDER,
            checkmark_color=T.BTN_ON_ACCENT,
            corner_radius=T.BORDER_RADIUS,
            command=self._sync_mode_from_modules,
        ).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=2)

    def _sync_mode_from_modules(self, *_args) -> None:
        self.mode_var.set("full" if self.mod_seo.get() else "cro")

    def _make_score_card(
        self, parent, col: int, title: str, value: str, caption: str, color: str
    ) -> ctk.CTkLabel:
        card = T.card_frame(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0 if col == 3 else 6))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="ew", padx=14, pady=14)
        inner.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(inner, text=title, font=T.font(12, "bold"), text_color=T.HEADING, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        value_label = ctk.CTkLabel(inner, text=value, font=T.font(28, "bold"), text_color=color, anchor="w")
        value_label.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ctk.CTkLabel(inner, text=caption, font=T.font_tuple(T.CAPTION), text_color=T.TEXT_MUTED, anchor="w").grid(
            row=2, column=0, sticky="w", pady=(2, 0)
        )
        bar = ctk.CTkProgressBar(
            inner, height=8, progress_color=color, fg_color=T.BORDER, corner_radius=4
        )
        bar.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        bar.set(0)
        value_label._score_bar = bar  # type: ignore[attr-defined]
        return value_label

    def _append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"› {message.rstrip()}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_progress(self, message: str) -> None:
        self.after(0, lambda m=message: self._append_log(m))

    def _clear_findings_ui(self) -> None:
        for child in self.findings_list.winfo_children():
            child.destroy()
        for child in self.recs_list.winfo_children():
            child.destroy()
        self._findings_placeholder = ctk.CTkLabel(
            self.findings_list,
            text="Run an audit to see findings here.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        )
        self._findings_placeholder.grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self._recs_placeholder = ctk.CTkLabel(
            self.recs_list,
            text="Recommendations will appear after an audit.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
            wraplength=200,
        )
        self._recs_placeholder.grid(row=0, column=0, sticky="w", padx=6, pady=6)

    def _populate_findings(self, findings: list) -> None:
        for child in self.findings_list.winfo_children():
            child.destroy()
        for child in self.recs_list.winfo_children():
            child.destroy()

        failed = [f for f in findings if not f.get("passed")]
        if not failed:
            ctk.CTkLabel(
                self.findings_list,
                text="No issues found — great job!",
                font=T.font_tuple(T.CAPTION),
                text_color=T.SUCCESS,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
            return

        for i, f in enumerate(failed[:40]):
            row = ctk.CTkFrame(self.findings_list, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(2, weight=1)

            severity = (f.get("severity") or f.get("priority") or "medium").lower()
            if "high" in severity:
                priority, pcolor, status, scolor, sbg = "High", "#B94C3F", "Needs Fix", "#B94C3F", "#F8E8E6"
            elif "low" in severity:
                priority, pcolor, status, scolor, sbg = "Low", "#3B6EA5", "Opportunity", "#3B6EA5", "#E8F0F8"
            else:
                priority, pcolor, status, scolor, sbg = "Medium", "#C9A84C", "Needs Attention", "#8A7010", "#F5EDD8"

            ctk.CTkLabel(row, text=str(i + 1), font=T.font(11), text_color=T.TEXT_MUTED, width=28, anchor="w").grid(
                row=0, column=0, padx=4
            )
            pwrap = ctk.CTkFrame(row, fg_color="transparent")
            pwrap.grid(row=0, column=1, sticky="w", padx=4)
            ctk.CTkLabel(pwrap, text="●", font=T.font(10), text_color=pcolor, width=14).grid(row=0, column=0)
            ctk.CTkLabel(pwrap, text=priority, font=T.font(11), text_color=T.TEXT_PRIMARY, anchor="w").grid(
                row=0, column=1
            )

            issue = f.get("title") or f.get("name") or f.get("check") or "Issue"
            cat = f.get("category") or ""
            issue_txt = f"{issue}" + (f"  ·  {cat}" if cat else "")
            ctk.CTkLabel(
                row, text=issue_txt[:80], font=T.font(12), text_color=T.TEXT_PRIMARY, anchor="w"
            ).grid(row=0, column=2, sticky="ew", padx=6)

            ctk.CTkLabel(
                row,
                text=status,
                font=T.font(11, "bold"),
                text_color=scolor,
                fg_color=sbg,
                corner_radius=4,
                width=110,
                height=22,
            ).grid(row=0, column=3, padx=4)

        # Top recommendations from failed items with fix text
        recs = [f for f in failed if f.get("fix") or f.get("recommendation")][:5]
        if not recs:
            ctk.CTkLabel(
                self.recs_list,
                text="See the Word report for detailed recommendations.",
                font=T.font_tuple(T.CAPTION),
                text_color=T.TEXT_MUTED,
                wraplength=200,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=6, pady=6)
            return
        for i, f in enumerate(recs):
            item = ctk.CTkFrame(self.recs_list, fg_color="transparent")
            item.grid(row=i, column=0, sticky="ew", pady=6)
            item.grid_columnconfigure(1, weight=1)
            num = ctk.CTkLabel(
                item,
                text=str(i + 1),
                font=T.font(12, "bold"),
                text_color=T.BTN_ON_ACCENT,
                fg_color=T.GOLD,
                width=24,
                height=24,
                corner_radius=12,
            )
            num.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 8))
            title = f.get("title") or f.get("name") or f.get("check") or "Recommendation"
            ctk.CTkLabel(item, text=title[:48], font=T.font(12, "bold"), text_color=T.HEADING, anchor="w").grid(
                row=0, column=1, sticky="w"
            )
            fix = f.get("fix") or f.get("recommendation") or ""
            ctk.CTkLabel(
                item, text=str(fix)[:90], font=T.font(11), text_color=T.TEXT_MUTED, anchor="w", wraplength=200
            ).grid(row=1, column=1, sticky="w")

    def _start_audit(self) -> None:
        store = getattr(self.app, "job_status", None)
        if store is not None and store.is_running(TOOL_STORE_AUDITOR):
            self.error_label.configure(text="Already running")
            return

        url = self.url_entry.get().strip()
        self.error_label.configure(text="")
        self._clear_findings_ui()
        self.report_path = None
        self.output_dir = None
        self.open_btn.configure(state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED)
        self.open_folder_btn.configure(state="disabled")

        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        if not is_valid_url(url):
            self.error_label.configure(text="URL must start with http:// or https://")
            return

        if store is not None and not store.try_begin(TOOL_STORE_AUDITOR):
            self.error_label.configure(text="Already running")
            return

        mode = self.mode_var.get() or "cro"
        self._running = True
        self.start_btn.configure(state="disabled")
        self.progress.grid()
        self.progress.start()
        self._append_log(f"Starting audit ({mode}) for {url}...")

        thread = threading.Thread(target=self._run_audit_thread, args=(url, mode), daemon=True)
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
            self.after(0, lambda: self._on_success(output_dir, report_path, audit_data))
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            self.after(0, lambda: self._on_error(msg))

    def _on_success(self, output_dir: str, report_path: str, audit_data: dict) -> None:
        self._running = False
        self.progress.stop()
        self.progress.grid_remove()
        self.start_btn.configure(state="normal")
        self.output_dir = output_dir
        self.report_path = report_path
        self.open_btn.configure(state="normal", fg_color=T.ACCENT, text_color=T.BTN_ON_ACCENT)
        self.open_folder_btn.configure(state="normal")

        pages = len(audit_data.get("pages_crawled") or [])
        findings = audit_data.get("findings") or []
        issues = sum(1 for f in findings if not f.get("passed"))
        recs = sum(1 for f in findings if not f.get("passed") and f.get("fix"))
        scores = audit_data.get("scores") or {}
        overall = scores.get("overall", "—")
        seo = scores.get("seo", scores.get("SEO", overall))
        content = scores.get("content", scores.get("cro", overall))

        def _set_score(label: ctk.CTkLabel, raw, denom: float = 10.0) -> None:
            try:
                n = float(raw)
                # Display on /100 scale when source is /10
                disp = int(round(n * 10)) if n <= 10 else int(round(n))
                label.configure(text=str(disp))
                bar = getattr(label, "_score_bar", None)
                if bar is not None:
                    bar.set(min(1.0, max(0.0, disp / 100.0)))
            except (TypeError, ValueError):
                label.configure(text=str(raw))

        _set_score(self.score_value, overall)
        _set_score(self.seo_value, seo)
        _set_score(self.content_value, content)
        self.issues_value.configure(text=str(issues))
        bar = getattr(self.issues_value, "_score_bar", None)
        if bar is not None:
            bar.set(min(1.0, issues / 20.0) if issues else 0)

        self._populate_findings(findings)

        self._append_log(
            f"Report saved: {report_path} · CRO {overall}/10 · {pages} pages · {issues} issues · {recs} recs"
        )
        from app.utils.task_history import save_task

        save_task("Audit", audit_data.get("store_url") or "", "Success")
        store = getattr(self.app, "job_status", None)
        if store is not None:
            job = store.get(TOOL_STORE_AUDITOR)
            job.state = "complete"
            job.proc = None
            job.stop_requested = False
            job.message = f"{job.label} complete"
            job.updated_at = __import__("time").time()
            store.message = job.message

    def _on_error(self, message: str) -> None:
        self._running = False
        self.progress.stop()
        self.progress.grid_remove()
        self.start_btn.configure(state="normal")
        self._append_log(f"ERROR: {message}")
        self.error_label.configure(text=message)
        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.set_idle(tool_id=TOOL_STORE_AUDITOR)

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
