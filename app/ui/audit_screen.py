"""Shopify store audit — mockup layout; existing audit logic preserved."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk

from app.ui import theme as T
from app.ui.components import PageHeader
from app.ui.icons import load_icon
from app.ui.sidebar import attach_sidebar
from app.utils.helpers import is_valid_url
from app.utils.job_status import TOOL_STORE_AUDITOR


class ScoreRing(ctk.CTkFrame):
    """Lightweight circular progress ring (canvas) with centered value label."""

    def __init__(
        self,
        master,
        *,
        size: int = 72,
        thickness: int = 7,
        color: str = "#C9A84C",
        track: str = "#E8E5E0",
        bg: str = "#FFFFFF",
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", width=size, height=size, **kwargs)
        self.grid_propagate(False)
        self.pack_propagate(False)
        self._size = size
        self._thickness = thickness
        self._color = color
        self._track = track
        self._canvas = tk.Canvas(
            self,
            width=size,
            height=size,
            highlightthickness=0,
            bd=0,
            bg=bg,
        )
        self._canvas.place(relx=0.5, rely=0.5, anchor="center")
        self.value_label = ctk.CTkLabel(
            self,
            text="—",
            font=T.font(16, "bold"),
            text_color=T.HEADING,
            fg_color="transparent",
        )
        self.value_label.place(relx=0.5, rely=0.5, anchor="center")
        self.set_progress(0.0)

    def set_progress(self, ratio: float) -> None:
        ratio = max(0.0, min(1.0, float(ratio)))
        c = self._canvas
        c.delete("all")
        pad = self._thickness / 2 + 2
        x0, y0 = pad, pad
        x1, y1 = self._size - pad, self._size - pad
        c.create_oval(x0, y0, x1, y1, outline=self._track, width=self._thickness)
        if ratio > 0.001:
            # Tk arcs: 0° at 3 o'clock, counter-clockwise; start at top (-90°)
            extent = -360.0 * ratio
            c.create_arc(
                x0,
                y0,
                x1,
                y1,
                start=90,
                extent=extent,
                style=tk.ARC,
                outline=self._color,
                width=self._thickness,
            )

    def set_value_text(self, text: str) -> None:
        self.value_label.configure(text=text)


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
        self._findings_data: list[dict] = []
        self._icons: list[ctk.CTkImage] = []

        # Module checklist vars (UI; mode_var drives actual run)
        self.mod_atc = ctk.BooleanVar(value=True)
        self.mod_reviews = ctk.BooleanVar(value=True)
        self.mod_trust = ctk.BooleanVar(value=True)
        self.mod_cart = ctk.BooleanVar(value=True)
        self.mod_mobile = ctk.BooleanVar(value=True)
        self.mod_speed = ctk.BooleanVar(value=True)
        self.mod_email = ctk.BooleanVar(value=True)
        self.mod_seo = ctk.BooleanVar(value=False)

        body = attach_sidebar(self, app, "audit")
        body.grid_columnconfigure(0, weight=1)

        PageHeader(
            body,
            "Store Auditor",
            "Audit eCommerce stores for issues and opportunities to improve performance, SEO, content and more.",
        ).grid(row=0, column=0, sticky="nw", pady=(0, 14))

        # ── URL card ──────────────────────────────────────
        url_card = T.card_frame(body)
        url_card.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        url_card.grid_columnconfigure(0, weight=1)
        url_inner = ctk.CTkFrame(url_card, fg_color="transparent")
        url_inner.grid(row=0, column=0, sticky="ew", padx=18, pady=16)
        url_inner.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(url_inner, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        head.grid_columnconfigure(1, weight=1)
        link_circle = ctk.CTkFrame(
            head,
            width=36,
            height=36,
            corner_radius=18,
            fg_color=T.get("CIRCLE_ICON_BG"),
        )
        link_circle.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 10))
        link_circle.grid_propagate(False)
        link_img = load_icon("link", size=18, color="navy")
        if link_img is not None:
            self._icons.append(link_img)
        ctk.CTkLabel(link_circle, text="", image=link_img, fg_color="transparent").place(
            relx=0.5, rely=0.5, anchor="center"
        )
        ctk.CTkLabel(
            head, text="Enter Store URL", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            head,
            text="Paste a storefront URL to run CRO and technical checks.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))

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

        # ── Score cards ───────────────────────────────────
        self.summary_row = ctk.CTkFrame(body, fg_color="transparent")
        self.summary_row.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        self.summary_row.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="scores")
        self.score_value = self._make_score_card(
            self.summary_row,
            0,
            title="Performance Score",
            description="Overall store performance across key metrics.",
            icon_name="bar-chart",
            ring_color=T.GOLD,
            initial="—",
            is_issues=False,
        )
        self.seo_value = self._make_score_card(
            self.summary_row,
            1,
            title="SEO Score",
            description="Search visibility and on-page SEO health.",
            icon_name="eye",
            ring_color=T.GOLD,
            initial="—",
            is_issues=False,
        )
        self.content_value = self._make_score_card(
            self.summary_row,
            2,
            title="Content Completeness",
            description="Product and page content quality coverage.",
            icon_name="file",
            ring_color=T.GOLD,
            initial="—",
            is_issues=False,
        )
        self.issues_value = self._make_score_card(
            self.summary_row,
            3,
            title="Technical Issues",
            description="Open technical and conversion issues found.",
            icon_name="alert-triangle",
            ring_color=T.ACCENT,
            initial="0",
            is_issues=True,
        )
        self.recs_value = self.issues_value  # alias for legacy updates
        self._cro_score_label = self.score_value

        # ── Findings + Recommendations (7 / 3) ─────────────
        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.grid_columnconfigure(0, weight=7)
        bottom.grid_columnconfigure(1, weight=3)

        findings_card = T.card_frame(bottom)
        findings_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        findings_card.grid_columnconfigure(0, weight=1)

        fh = ctk.CTkFrame(findings_card, fg_color="transparent")
        fh.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        fh.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(fh, text="Audit Findings", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(
            fh,
            text="Issues and opportunities discovered during the audit.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.category_filter = ctk.CTkOptionMenu(
            fh,
            values=["All Categories"],
            width=150,
            command=self._apply_category_filter,
            **T.option_menu_style(),
        )
        self.category_filter.grid(row=0, column=1, rowspan=2, sticky="e")
        self.category_filter.set("All Categories")

        # Table header: Priority | Category | Issue / Opportunity | Status
        thead = ctk.CTkFrame(findings_card, fg_color=T.BG_PRIMARY, corner_radius=4)
        thead.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 0))
        thead.grid_columnconfigure(2, weight=1)
        for col, (txt, w) in enumerate(
            (("Priority", 88), ("Category", 110), ("Issue / Opportunity", 1), ("Status", 120))
        ):
            ctk.CTkLabel(
                thead,
                text=txt,
                font=T.font(11, "bold"),
                text_color=T.TEXT_MUTED,
                width=w if col != 2 else 0,
                anchor="w",
            ).grid(row=0, column=col, sticky="ew" if col == 2 else "w", padx=6, pady=6)

        self.findings_list = ctk.CTkScrollableFrame(findings_card, fg_color="transparent", height=220)
        self.findings_list.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 12))
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

        rh = ctk.CTkFrame(rec_card, fg_color="transparent")
        rh.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        rh.grid_columnconfigure(1, weight=1)
        gold_circle = ctk.CTkFrame(
            rh, width=36, height=36, corner_radius=18, fg_color=T.get("AMBER_BG")
        )
        gold_circle.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 10))
        gold_circle.grid_propagate(False)
        rec_img = load_icon("check", size=18, color="gold")
        if rec_img is not None:
            self._icons.append(rec_img)
        ctk.CTkLabel(gold_circle, text="", image=rec_img, fg_color="transparent").place(
            relx=0.5, rely=0.5, anchor="center"
        )
        ctk.CTkLabel(
            rh, text="Top Recommendations", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            rh,
            text="Prioritised actions to improve the store.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
            wraplength=200,
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))

        self.recs_list = ctk.CTkScrollableFrame(rec_card, fg_color="transparent", height=220)
        self.recs_list.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 12))
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

        # Action buttons (real app functionality)
        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", pady=(14, 8))
        actions.grid_columnconfigure(0, weight=1)
        self.open_folder_btn = ctk.CTkButton(
            actions, text="Open Folder", command=self._open_folder, width=120, **T.secondary_btn()
        )
        self.open_btn = ctk.CTkButton(
            actions, text="Open Word Report", command=self._open_report, width=160, **T.primary_btn()
        )
        self.open_folder_btn.grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.open_btn.grid(row=0, column=2, sticky="e")
        self.open_btn.configure(state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED)
        self.open_folder_btn.configure(state="disabled")

        # Hidden log for progress messages
        self.log_box = ctk.CTkTextbox(body, height=1, fg_color=T.BG_PRIMARY, text_color=T.TEXT_MUTED)
        self.log_box.grid_remove()

    # ── UI helpers ────────────────────────────────────────

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
        self,
        parent,
        col: int,
        *,
        title: str,
        description: str,
        icon_name: str,
        ring_color: str,
        initial: str,
        is_issues: bool,
    ) -> ctk.CTkLabel:
        card = T.card_frame(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0 if col == 3 else 6))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="ew", padx=14, pady=14)
        inner.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.grid(row=0, column=0, sticky="w")

        ring = ScoreRing(
            top,
            size=72,
            thickness=7,
            color=ring_color,
            track="#E8E5E0",
            bg=T.BG_SURFACE_A,
        )
        ring.grid(row=0, column=0, sticky="w")
        ring.set_value_text(initial)

        icon_circle = ctk.CTkFrame(
            top,
            width=32,
            height=32,
            corner_radius=16,
            fg_color=T.get("CIRCLE_ICON_BG"),
        )
        icon_circle.grid(row=0, column=1, sticky="n", padx=(10, 0), pady=(4, 0))
        icon_circle.grid_propagate(False)
        img = load_icon(icon_name, size=16, color="navy")
        if img is not None:
            self._icons.append(img)
        ctk.CTkLabel(icon_circle, text="", image=img, fg_color="transparent").place(
            relx=0.5, rely=0.5, anchor="center"
        )

        ctk.CTkLabel(inner, text=title, font=T.font(13, "bold"), text_color=T.HEADING, anchor="w").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        ctk.CTkLabel(
            inner,
            text=description,
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
            wraplength=180,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(2, 0))

        ctk.CTkFrame(inner, height=1, fg_color=T.BORDER, corner_radius=0).grid(
            row=3, column=0, sticky="ew", pady=(10, 6)
        )
        delta = ctk.CTkLabel(
            inner,
            text="Awaiting audit" if not is_issues else "No issues yet",
            font=T.font(11),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
        )
        delta.grid(row=4, column=0, sticky="w")

        value_label = ring.value_label
        value_label._score_ring = ring  # type: ignore[attr-defined]
        value_label._delta_label = delta  # type: ignore[attr-defined]
        value_label._is_issues = is_issues  # type: ignore[attr-defined]
        return value_label

    @staticmethod
    def _priority_style(severity: str) -> tuple[str, str, str]:
        s = (severity or "medium").lower()
        if "high" in s:
            return "High", "#B94C3F", "#F8E8E6"
        if "low" in s:
            return "Low", "#3B6EA5", "#E8F0F8"
        return "Medium", "#8A7010", "#F5EDD8"

    @staticmethod
    def _status_style(severity: str) -> tuple[str, str, str]:
        s = (severity or "medium").lower()
        if "high" in s:
            return "Needs Fix", "#B94C3F", "#F8E8E6"
        if "low" in s:
            return "Opportunity", "#3B6EA5", "#E8F0F8"
        return "Needs Attention", "#8A7010", "#F5EDD8"

    @staticmethod
    def _score_status_text(disp: str | int) -> str:
        try:
            n = int(disp)
        except (TypeError, ValueError):
            return "Awaiting audit"
        if n >= 80:
            return "Strong"
        if n >= 60:
            return "Fair"
        return "Needs attention"

    def _pill(self, parent, text: str, fg: str, bg: str, width: int = 72) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            font=T.font(11, "bold"),
            text_color=fg,
            fg_color=bg,
            corner_radius=10,
            width=width,
            height=22,
        )

    def _append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"› {message.rstrip()}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_progress(self, message: str) -> None:
        self.after(0, lambda m=message: self._append_log(m))

    def _clear_findings_ui(self) -> None:
        self._findings_data = []
        for child in self.findings_list.winfo_children():
            child.destroy()
        for child in self.recs_list.winfo_children():
            child.destroy()
        self.category_filter.configure(values=["All Categories"])
        self.category_filter.set("All Categories")
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

    def _apply_category_filter(self, _choice: str | None = None) -> None:
        self._render_findings_rows()

    def _render_findings_rows(self) -> None:
        for child in self.findings_list.winfo_children():
            child.destroy()

        failed = self._findings_data
        if not failed:
            ctk.CTkLabel(
                self.findings_list,
                text="No issues found — great job!",
                font=T.font_tuple(T.CAPTION),
                text_color=T.SUCCESS,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
            return

        selected = self.category_filter.get() if self.category_filter else "All Categories"
        rows = failed
        if selected and selected != "All Categories":
            rows = [
                f
                for f in failed
                if (f.get("category") or "—") == selected
            ]

        if not rows:
            ctk.CTkLabel(
                self.findings_list,
                text="No findings in this category.",
                font=T.font_tuple(T.CAPTION),
                text_color=T.TEXT_MUTED,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
            return

        for i, f in enumerate(rows[:40]):
            row = ctk.CTkFrame(self.findings_list, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(2, weight=1)

            severity = (f.get("severity") or f.get("priority") or "medium").lower()
            priority, pfg, pbg = self._priority_style(severity)
            status, sfg, sbg = self._status_style(severity)

            self._pill(row, priority, pfg, pbg, width=72).grid(row=0, column=0, sticky="w", padx=(4, 6))

            cat = f.get("category")
            cat_txt = str(cat).strip() if cat else "—"
            ctk.CTkLabel(
                row, text=cat_txt[:28], font=T.font(11), text_color=T.TEXT_SECONDARY, anchor="w", width=110
            ).grid(row=0, column=1, sticky="w", padx=4)

            issue = f.get("title") or f.get("name") or f.get("check") or "Issue"
            ctk.CTkLabel(
                row, text=str(issue)[:80], font=T.font(12), text_color=T.TEXT_PRIMARY, anchor="w"
            ).grid(row=0, column=2, sticky="ew", padx=6)

            self._pill(row, status, sfg, sbg, width=118).grid(row=0, column=3, sticky="e", padx=4)

    def _populate_findings(self, findings: list) -> None:
        for child in self.findings_list.winfo_children():
            child.destroy()
        for child in self.recs_list.winfo_children():
            child.destroy()

        failed = [f for f in findings if not f.get("passed")]
        self._findings_data = failed

        cats = sorted(
            {
                str(f.get("category")).strip()
                for f in failed
                if f.get("category") and str(f.get("category")).strip()
            }
        )
        self.category_filter.configure(values=["All Categories", *cats] if cats else ["All Categories"])
        self.category_filter.set("All Categories")

        if not failed:
            ctk.CTkLabel(
                self.findings_list,
                text="No issues found — great job!",
                font=T.font_tuple(T.CAPTION),
                text_color=T.SUCCESS,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        else:
            self._render_findings_rows()

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
            r = i * 2
            item = ctk.CTkFrame(self.recs_list, fg_color="transparent")
            item.grid(row=r, column=0, sticky="ew")
            item.grid_columnconfigure(1, weight=1)
            num = ctk.CTkLabel(
                item,
                text=str(i + 1),
                font=T.font(12, "bold"),
                text_color=T.BTN_ON_ACCENT,
                fg_color=T.GOLD,
                width=26,
                height=26,
                corner_radius=13,
            )
            num.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 10), pady=(8, 0))
            title = f.get("title") or f.get("name") or f.get("check") or "Recommendation"
            ctk.CTkLabel(
                item, text=str(title)[:56], font=T.font(12, "bold"), text_color=T.HEADING, anchor="w"
            ).grid(row=0, column=1, sticky="w", pady=(8, 0))
            fix = f.get("fix") or f.get("recommendation") or ""
            if fix:
                ctk.CTkLabel(
                    item,
                    text=str(fix)[:110],
                    font=T.font(11),
                    text_color=T.TEXT_MUTED,
                    anchor="w",
                    wraplength=200,
                    justify="left",
                ).grid(row=1, column=1, sticky="w", pady=(2, 8))
            else:
                ctk.CTkFrame(item, height=8, fg_color="transparent").grid(row=1, column=1)
            if i < len(recs) - 1:
                ctk.CTkFrame(self.recs_list, height=1, fg_color=T.BORDER, corner_radius=0).grid(
                    row=r + 1, column=0, sticky="ew", padx=2, pady=2
                )

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

        def _set_score(label: ctk.CTkLabel, raw, *, is_issues: bool = False) -> None:
            ring: ScoreRing | None = getattr(label, "_score_ring", None)
            delta: ctk.CTkLabel | None = getattr(label, "_delta_label", None)
            try:
                n = float(raw)
                disp = int(round(n * 10)) if n <= 10 else int(round(n))
                label.configure(text=str(disp))
                if ring is not None:
                    ring.set_value_text(str(disp))
                    ring.set_progress(min(1.0, max(0.0, disp / 100.0)))
                if delta is not None and not is_issues:
                    delta.configure(text=self._score_status_text(disp))
            except (TypeError, ValueError):
                label.configure(text=str(raw))
                if ring is not None:
                    ring.set_value_text(str(raw))

        _set_score(self.score_value, overall)
        _set_score(self.seo_value, seo)
        _set_score(self.content_value, content)

        self.issues_value.configure(text=str(issues))
        ring = getattr(self.issues_value, "_score_ring", None)
        if ring is not None:
            ring.set_value_text(str(issues))
            ring.set_progress(min(1.0, issues / 20.0) if issues else 0.0)
        delta = getattr(self.issues_value, "_delta_label", None)
        if delta is not None:
            delta.configure(text="No issues found" if issues == 0 else f"{issues} open issue{'s' if issues != 1 else ''}")

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
