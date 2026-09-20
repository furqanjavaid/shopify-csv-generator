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
            "Sentivo Tools",
            "by Sentivo Limited",
        )

        # Feature cards — equal width, fixed compact height
        cards_frame = ctk.CTkFrame(content, fg_color="transparent")
        cards_frame.pack(fill="x", pady=(0, T.GRID_GAP))
        cards_frame.grid_columnconfigure((0, 1, 2), weight=1, uniform="card")
        cards_frame.grid_rowconfigure(0, minsize=180, weight=0)

        self._feature_card(
            cards_frame, 0, "📄", "Upload File",
            "Convert any client spreadsheet to Shopify CSV",
            "Open Upload", self._go_upload,
        )
        self._feature_card(
            cards_frame, 1, "🔗", "Scrape Store",
            "Extract products from any Shopify store URL",
            "Open Scraper", self._go_scraper,
        )
        self._feature_card(
            cards_frame, 2, "🔍", "Audit Store",
            "Full CRO audit with Word report",
            "Open Auditor", self._go_audit,
        )

        # Bottom — Recent Tasks + System Status
        bottom = ctk.CTkFrame(content, fg_color="transparent")
        bottom.pack(fill="both", expand=True, pady=(T.GRID_GAP, 0))
        bottom.grid_columnconfigure(0, weight=3)
        bottom.grid_columnconfigure(1, weight=2)
        bottom.grid_rowconfigure(0, weight=1)

        self._recent_tasks(bottom)
        self._system_status(bottom)

        # Footer
        footer = ctk.CTkFrame(content, fg_color="transparent", height=28)
        footer.pack(fill="x", side="bottom", pady=(8, 0))
        ctk.CTkLabel(
            footer, text="v1.0", font=T.font_tuple(T.CAPTION), text_color=T.TEXT_MUTED
        ).pack(side="left")
        ctk.CTkLabel(
            footer,
            text="Python · CustomTkinter",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
        ).pack(side="right")

    def _feature_card(
        self, parent, col: int, icon: str, title: str, desc: str, btn: str, command
    ) -> None:
        padx = (0, 8) if col == 0 else (8, 0) if col == 2 else 8
        card = T.card_frame(parent)
        card.grid(row=0, column=col, padx=padx, sticky="nsew")
        card.configure(height=180)
        card.pack_propagate(False)

        ctk.CTkLabel(
            card, text=icon, font=T.font(24), text_color=T.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", padx=T.CARD_PADDING, pady=(T.CARD_PADDING, 4))

        ctk.CTkLabel(
            card, text=title, font=T.font(16, "bold"), text_color=T.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", padx=T.CARD_PADDING)

        ctk.CTkLabel(
            card,
            text=desc,
            font=T.font(13),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
            wraplength=200,
            justify="left",
        ).pack(anchor="w", padx=T.CARD_PADDING, pady=(4, 0))

        # Small fixed spacer — not expanding
        ctk.CTkFrame(card, fg_color="transparent", height=12).pack()

        T.primary_button(card, btn, command).pack(
            fill="x", padx=T.CARD_PADDING, pady=(0, T.CARD_PADDING)
        )

    def _recent_tasks(self, parent) -> None:
        card = T.card_frame(parent)
        card.grid(row=0, column=0, padx=(0, 8), sticky="nsew")

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=T.CARD_PADDING, pady=(T.CARD_PADDING, 8))
        ctk.CTkLabel(
            head, text="Recent Tasks", font=T.font_tuple(T.H3),
            text_color=T.TEXT_PRIMARY, anchor="w",
        ).pack(fill="x")

        table = ctk.CTkFrame(card, fg_color="transparent")
        table.pack(fill="both", expand=True, padx=1, pady=(0, T.CARD_PADDING))
        table.grid_columnconfigure(0, weight=1)
        table.grid_columnconfigure(1, weight=3)
        table.grid_columnconfigure(2, weight=2)
        table.grid_columnconfigure(3, weight=1)

        # Table header row
        header = ctk.CTkFrame(table, fg_color=T.BG_SURFACE_B, height=T.ROW_HEIGHT)
        header.grid(row=0, column=0, columnspan=4, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=3)
        header.grid_columnconfigure(2, weight=2)
        header.grid_columnconfigure(3, weight=1)
        for col, (text, width) in enumerate([
            ("Type", 80), ("Name", 160), ("Status", 100), ("Date", 90)
        ]):
            ctk.CTkLabel(
                header,
                text=text,
                font=T.font(12, "bold"),
                text_color=T.TEXT_MUTED,
                width=width,
                anchor="w",
            ).grid(row=0, column=col, padx=4, sticky="w")

        rows = [
            ("Upload", "spring_catalog.csv", "success", "Success", "Today"),
            ("Scrape", "store.myshopify.com", "warning", "Running", "Today"),
            ("Audit", "example.com", "success", "Success", "Yesterday"),
            ("Upload", "client_products.xlsx", "error", "Failed", "Mon"),
        ]
        for idx, (typ, name, level, status, date) in enumerate(rows):
            bg = T.BG_SURFACE_A if idx % 2 == 0 else T.BG_SURFACE_B
            row = ctk.CTkFrame(table, fg_color=bg, height=T.ROW_HEIGHT)
            row.grid(row=idx + 1, column=0, columnspan=4, sticky="ew")
            row.grid_propagate(False)
            row.grid_columnconfigure(0, weight=1)
            row.grid_columnconfigure(1, weight=3)
            row.grid_columnconfigure(2, weight=2)
            row.grid_columnconfigure(3, weight=1)

            ctk.CTkLabel(
                row, text=typ, font=T.font_tuple(T.CAPTION),
                text_color=T.TEXT_SECONDARY, width=80, anchor="w",
            ).grid(row=0, column=0, padx=4, sticky="w")
            ctk.CTkLabel(
                row, text=name, font=T.font_tuple(T.CAPTION),
                text_color=T.TEXT_SECONDARY, width=160, anchor="w",
            ).grid(row=0, column=1, padx=4, sticky="w")
            T.status_dot(row, status, level).grid(row=0, column=2, padx=4, sticky="w")
            ctk.CTkLabel(
                row, text=date, font=T.font_tuple(T.CAPTION),
                text_color=T.TEXT_SECONDARY, width=90, anchor="w",
            ).grid(row=0, column=3, padx=4, sticky="w")

    def _system_status(self, parent) -> None:
        card = T.card_frame(parent)
        card.grid(row=0, column=1, sticky="nsew")

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
