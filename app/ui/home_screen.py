"""Home / dashboard — sidebar + feature cards + recent tasks."""

from __future__ import annotations

import customtkinter as ctk

from app.ui import theme as T
from app.ui.sidebar import attach_sidebar


class HomeScreen(ctk.CTkFrame):
    """Landing with feature cards, recent tasks, and system status."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app

        body = attach_sidebar(self, app, "home")

        content = ctk.CTkFrame(body, fg_color="transparent")
        content.pack(fill="both", expand=True)

        T.page_title(
            content,
            "Shopify Product Tools",
            "Upload · Scrape · Audit — all in one place",
        )

        # Feature cards
        cards = ctk.CTkFrame(content, fg_color="transparent")
        cards.pack(fill="x", pady=(0, T.GRID_GAP))
        cards.grid_columnconfigure((0, 1, 2), weight=1)

        self._feature_card(
            cards, 0, "📄", "Upload File",
            "Convert any client spreadsheet to Shopify CSV",
            "Open Upload", self._go_upload,
        )
        self._feature_card(
            cards, 1, "🔗", "Scrape Store",
            "Extract products from any Shopify store URL",
            "Open Scraper", self._go_scraper,
        )
        self._feature_card(
            cards, 2, "🔍", "Audit Store",
            "Full CRO audit with Word report",
            "Open Auditor", self._go_audit,
        )

        # Two columns: Recent Tasks + System Status
        lower = ctk.CTkFrame(content, fg_color="transparent")
        lower.pack(fill="both", expand=True, pady=(0, 8))
        lower.grid_columnconfigure(0, weight=3)
        lower.grid_columnconfigure(1, weight=2)
        lower.grid_rowconfigure(0, weight=1)

        self._recent_tasks(lower)
        self._system_status(lower)

        # Bottom bar
        footer = ctk.CTkFrame(content, fg_color="transparent", height=28)
        footer.pack(fill="x", side="bottom")
        ctk.CTkLabel(
            footer, text="v1.0", font=T.font_tuple(T.CAPTION), text_color=T.TEXT_MUTED
        ).pack(side="left")
        ctk.CTkLabel(
            footer,
            text="Python · CustomTkinter",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
        ).pack(side="right")

    def _feature_card(self, parent, col: int, icon: str, title: str, desc: str, btn: str, command) -> None:
        card = T.card_frame(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else T.GRID_GAP // 2, 0 if col == 2 else T.GRID_GAP // 2))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        ctk.CTkLabel(inner, text=icon, font=T.font(28), text_color=T.TEXT_PRIMARY, anchor="w").pack(fill="x")
        ctk.CTkLabel(
            inner, text=title, font=T.font_tuple(T.H3), text_color=T.TEXT_PRIMARY, anchor="w"
        ).pack(fill="x", pady=(8, 4))
        ctk.CTkLabel(
            inner,
            text=desc,
            font=T.font_tuple(T.BODY),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
            wraplength=220,
            justify="left",
        ).pack(fill="x", pady=(0, 16))

        T.primary_button(inner, btn, command, width=140).pack(anchor="w")

    def _recent_tasks(self, parent) -> None:
        card = T.card_frame(parent)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, T.GRID_GAP // 2))

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=T.CARD_PADDING, pady=(T.CARD_PADDING, 8))
        ctk.CTkLabel(
            head, text="Recent Tasks", font=T.font_tuple(T.H3), text_color=T.TEXT_PRIMARY, anchor="w"
        ).pack(fill="x")

        # Header row
        cols = ("Type", "Name", "Status", "Date")
        widths = (70, 160, 100, 90)
        header = ctk.CTkFrame(card, fg_color=T.BG_SURFACE_B, height=T.ROW_HEIGHT)
        header.pack(fill="x", padx=1)
        header.pack_propagate(False)
        for i, (col, w) in enumerate(zip(cols, widths)):
            ctk.CTkLabel(
                header, text=col, font=T.font(12, "bold"), text_color=T.TEXT_MUTED,
                width=w, anchor="w",
            ).pack(side="left", padx=(12 if i == 0 else 8, 0))

        rows = [
            ("Upload", "spring_catalog.csv", "success", "Success", "Today"),
            ("Scrape", "store.myshopify.com", "warning", "Running", "Today"),
            ("Audit", "example.com", "success", "Success", "Yesterday"),
            ("Upload", "client_products.xlsx", "error", "Failed", "Mon"),
        ]
        for idx, (typ, name, level, status, date) in enumerate(rows):
            bg = T.BG_SURFACE_A if idx % 2 == 0 else T.BG_SURFACE_B
            row = ctk.CTkFrame(card, fg_color=bg, height=T.ROW_HEIGHT)
            row.pack(fill="x", padx=1)
            row.pack_propagate(False)
            values = (typ, name, None, date)
            for i, (val, w) in enumerate(zip(values, widths)):
                if i == 2:
                    T.status_dot(row, status, level).pack(side="left", padx=8)
                else:
                    ctk.CTkLabel(
                        row, text=val, font=T.font_tuple(T.CAPTION),
                        text_color=T.TEXT_SECONDARY, width=w, anchor="w",
                    ).pack(side="left", padx=(12 if i == 0 else 8, 0))

    def _system_status(self, parent) -> None:
        card = T.card_frame(parent)
        card.grid(row=0, column=1, sticky="nsew", padx=(T.GRID_GAP // 2, 0))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        ctk.CTkLabel(
            inner, text="System Status", font=T.font_tuple(T.H3),
            text_color=T.TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", pady=(0, 12))

        items = [
            ("Parser engine", "success", "Ready"),
            ("Scraper", "success", "Ready"),
            ("Auditor", "success", "Ready"),
            ("Playwright", "warning", "Optional"),
        ]
        for label, level, status in items:
            row = ctk.CTkFrame(inner, fg_color="transparent", height=T.ROW_HEIGHT)
            row.pack(fill="x")
            row.pack_propagate(False)
            ctk.CTkLabel(
                row, text=label, font=T.font_tuple(T.LABEL),
                text_color=T.TEXT_SECONDARY, anchor="w",
            ).pack(side="left")
            T.status_dot(row, status, level).pack(side="right")

    def _go_upload(self) -> None:
        from app.ui.upload_screen import UploadScreen

        self.app.show_screen(UploadScreen)

    def _go_scraper(self) -> None:
        from app.ui.scraper_screen import ScraperScreen

        self.app.show_screen(ScraperScreen)

    def _go_audit(self) -> None:
        from app.ui.audit_screen import AuditScreen

        self.app.show_screen(AuditScreen)
