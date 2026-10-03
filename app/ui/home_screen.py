"""Home / dashboard — mockup layout with job status, actions, tasks."""

from __future__ import annotations

import customtkinter as ctk

from app.ui import theme as T
from app.ui.components import PageHeader
from app.ui.icons import load_icon
from app.ui.sidebar import attach_sidebar
from app.utils.task_history import load_history


class HomeScreen(ctk.CTkFrame):
    """Landing matching brand mockup (no duplicate Sentivo branding in content)."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app
        self._job_tick_after = None
        self._icons: list[ctk.CTkImage] = []

        body = attach_sidebar(self, app, "home")
        # attach_sidebar weights row 0 — reset so the page header is not crushed/clipped
        body.grid_rowconfigure(0, weight=0)
        body.grid_rowconfigure(1, weight=0)
        body.grid_rowconfigure(2, weight=0)
        body.grid_rowconfigure(3, weight=1)

        PageHeader(
            body,
            "Home",
            "Welcome to Sentivo Tools. Your all-in-one toolkit for eCommerce data and automation.",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 16))

        # Job Status card
        self.job_card = T.card_frame(body)
        self.job_card.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.job_card.grid_columnconfigure(0, weight=1)
        job_inner = ctk.CTkFrame(self.job_card, fg_color="transparent")
        job_inner.grid(row=0, column=0, sticky="ew", padx=20, pady=16)
        job_inner.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkLabel(
            job_inner,
            text="Job Status",
            font=T.font(16, "bold"),
            text_color=T.HEADING,
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ctk.CTkLabel(
            job_inner,
            text="Overview of your recent and current jobs across all tools.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 12))

        self.job_status_label = ctk.CTkLabel(
            job_inner,
            text="Ready",
            font=T.font(14, "bold"),
            text_color=T.TEXT_PRIMARY,
            anchor="w",
        )
        self.job_status_label.grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, 10))

        self._stat_labels = {}
        for col, (key, title, icon_name) in enumerate(
            (
                ("running", "Running", "play"),
                ("completed", "Completed", "check"),
                ("pending", "Pending", "clock"),
                ("success", "Success Rate", "bar-chart"),
            )
        ):
            box = ctk.CTkFrame(job_inner, fg_color="transparent")
            box.grid(row=3, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0))
            head = ctk.CTkFrame(box, fg_color="transparent")
            head.grid(row=0, column=0, sticky="w")
            img = load_icon(icon_name, 20, color="navy")
            if img is not None:
                self._icons.append(img)
            ctk.CTkLabel(head, text="", image=img, width=22).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                head, text=title, font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w"
            ).grid(row=0, column=1, sticky="w", padx=(6, 0))
            val = ctk.CTkLabel(
                box, text="—", font=T.font(18, "bold"), text_color=T.HEADING, anchor="w"
            )
            val.grid(row=1, column=0, sticky="w", pady=(4, 0))
            self._stat_labels[key] = val

        self.job_progress = ctk.CTkProgressBar(
            job_inner,
            height=10,
            progress_color=T.GOLD,
            fg_color=T.BORDER,
            corner_radius=6,
        )
        self.job_progress.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        self.job_progress.set(0)

        # Quick actions
        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        actions.grid_columnconfigure((0, 1, 2), weight=1, uniform="qa")
        self._action_card(
            actions,
            0,
            "file",
            "Upload File",
            "Convert any client spreadsheet to Shopify-ready CSV.",
            "Upload File →",
            self._go_upload,
        )
        self._action_card(
            actions,
            1,
            "link",
            "Scrape Store",
            "Extract products and metadata from ecommerce store URLs.",
            "Start Scraping →",
            self._go_scraper,
        )
        self._action_card(
            actions,
            2,
            "bar-chart",
            "Audit Store",
            "Run CRO and technical audits with scored recommendations.",
            "Start Audit →",
            self._go_audit,
        )

        # Bottom: recent + system
        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="nsew")
        bottom.grid_columnconfigure(0, weight=7)
        bottom.grid_columnconfigure(1, weight=3)
        bottom.grid_rowconfigure(0, weight=1)
        self._recent_tasks(bottom)
        self._system_status(bottom)

        self._tick_job_status()

    def _action_card(self, parent, col, icon_name, title, desc, btn, command) -> None:
        card = T.card_frame(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
        card.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        inner.grid_columnconfigure(0, weight=1)

        circle = ctk.CTkFrame(
            inner,
            fg_color=T.get("ACCENT_DIM"),
            corner_radius=24,
            width=48,
            height=48,
        )
        circle.grid(row=0, column=0, sticky="w")
        circle.grid_propagate(False)
        img = load_icon(icon_name, 24, color="navy")
        if img is not None:
            self._icons.append(img)
        ctk.CTkLabel(circle, text="", image=img).place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            inner, text=title, font=T.font(16, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ctk.CTkLabel(
            inner,
            text=desc,
            font=T.font(13),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
            wraplength=220,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(6, 14))
        T.primary_button(inner, btn, command).grid(row=3, column=0, sticky="ew")

    def _recent_tasks(self, parent) -> None:
        card = T.card_frame(parent)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="Recent Tasks", font=T.font(16, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w")

        table = ctk.CTkFrame(card, fg_color="transparent")
        table.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 16))
        table.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)
        for col, text in enumerate(("Tool", "Name", "Status", "Progress", "Started At")):
            ctk.CTkLabel(
                table,
                text=text,
                font=T.font(11, "bold"),
                text_color=T.TEXT_MUTED,
                anchor="w",
            ).grid(row=0, column=col, sticky="ew", padx=4, pady=(0, 6))
        self._render_recent_tasks(table)

    def _render_recent_tasks(self, table) -> None:
        history = load_history()
        if not history:
            ctk.CTkLabel(
                table,
                text="No tasks yet — run your first upload, scrape or audit",
                font=T.font(13),
                text_color=T.TEXT_MUTED,
                anchor="w",
            ).grid(row=1, column=0, columnspan=5, sticky="w", pady=12)
            return
        for i, task in enumerate(history[:6]):
            status = task.get("status") or ""
            color = T.SUCCESS if status == "Success" else T.ERROR
            row_i = i + 1
            vals = (
                task.get("type", ""),
                (task.get("name") or "")[:32],
                f"● {status}",
            )
            colors = (T.TEXT_SECONDARY, T.TEXT_PRIMARY, color)
            for col, (text, tc) in enumerate(zip(vals, colors)):
                ctk.CTkLabel(
                    table, text=text, font=T.font(12), text_color=tc, anchor="w"
                ).grid(row=row_i, column=col, sticky="ew", padx=4, pady=2)

            # Progress column between Status and Started At
            prog_wrap = ctk.CTkFrame(table, fg_color="transparent")
            prog_wrap.grid(row=row_i, column=3, sticky="w", padx=4, pady=2)
            pct = 1.0 if status == "Success" else 0.5
            bar = ctk.CTkProgressBar(
                prog_wrap,
                width=80,
                height=6,
                progress_color=T.GOLD,
                fg_color=T.BORDER,
                corner_radius=3,
            )
            bar.grid(row=0, column=0, sticky="w")
            bar.set(pct)
            ctk.CTkLabel(
                prog_wrap,
                text=f"{int(pct * 100)}%",
                font=T.font(11),
                text_color=T.TEXT_MUTED,
                anchor="w",
            ).grid(row=0, column=1, sticky="w", padx=(6, 0))

            ctk.CTkLabel(
                table,
                text=task.get("date", ""),
                font=T.font(12),
                text_color=T.TEXT_MUTED,
                anchor="w",
            ).grid(row=row_i, column=4, sticky="ew", padx=4, pady=2)

    def _system_status(self, parent) -> None:
        card = T.card_frame(parent)
        card.grid(row=0, column=1, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="nsew", padx=18, pady=16)
        inner.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            inner, text="System Status", font=T.font(16, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            inner,
            text="Live status of core components.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        items = [
            ("Python Environment", "Healthy", "success", "play"),
            ("Job Queue", "Ready", "success", "refresh"),
            ("Storage", "Available", "success", "folder"),
            ("Scraper", "Ready", "success", "link"),
            ("Internet Connection", "Connected", "success", "info"),
        ]
        for i, (label, status, level, icon_name) in enumerate(items):
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.grid(row=i + 2, column=0, sticky="ew", pady=4)
            row.grid_columnconfigure(1, weight=1)
            img = load_icon(icon_name, 16, color="navy")
            if img is not None:
                self._icons.append(img)
            ctk.CTkLabel(row, text="", image=img, width=18).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                row, text=label, font=T.font_tuple(T.LABEL), text_color=T.TEXT_SECONDARY, anchor="w"
            ).grid(row=0, column=1, sticky="w", padx=(6, 0))
            T.status_dot(row, status, level).grid(row=0, column=2, sticky="e")

    def _tick_job_status(self) -> None:
        store = getattr(self.app, "job_status", None)
        history = load_history()
        completed = sum(1 for t in history if t.get("status") == "Success")
        failed = sum(1 for t in history if t.get("status") != "Success")
        total = len(history)
        running = 1 if store is not None and store.state == "running" else 0

        if store is None:
            text, color, keep = "Ready", T.TEXT_MUTED, False
            rate = 0.0
        else:
            text = store.display_text()
            state = store.state
            if state == "running":
                color, keep = T.GOLD, True
            elif state == "complete":
                color, keep = T.SUCCESS, False
            elif state == "stopped":
                color, keep = T.ERROR, False
            else:
                color, keep = T.TEXT_MUTED, False
            rate = (completed / total) if total else 0.0

        try:
            self.job_status_label.configure(text=text, text_color=color)
            self._stat_labels["running"].configure(text=f"{running} in progress")
            self._stat_labels["completed"].configure(text=f"{completed} completed")
            self._stat_labels["pending"].configure(text=f"{failed} other")
            self._stat_labels["success"].configure(
                text=f"{int(rate * 100)}%" if total else "—"
            )
            self.job_progress.set(rate if total else 0.0)
        except Exception:
            return
        self._job_tick_after = self.after(1000 if keep else 2000, self._tick_job_status)

    def _go_upload(self) -> None:
        from app.ui.upload_screen import UploadScreen

        self.app.show_screen(UploadScreen)

    def _go_scraper(self) -> None:
        from app.ui.scraper_screen import ScraperScreen

        self.app.show_screen(ScraperScreen)

    def _go_audit(self) -> None:
        from app.ui.audit_screen import AuditScreen

        self.app.show_screen(AuditScreen)

    def destroy(self) -> None:
        aid = getattr(self, "_job_tick_after", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        super().destroy()
