"""Home / mode select — premium landing."""

from __future__ import annotations

import customtkinter as ctk

from app.ui import theme as T


class HomeScreen(ctk.CTkFrame):
    """Landing with three feature cards."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG, corner_radius=0)
        self.app = app

        # Top brand bar
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=24, pady=(20, 0))

        ctk.CTkLabel(
            top,
            text="SENTIVO",
            font=T.font(12, "bold"),
            text_color=T.ACCENT,
        ).pack(side="left")

        ctk.CTkLabel(
            top,
            text="  v1.0  ",
            font=T.font(11),
            text_color=T.TEXT_MUTED,
            fg_color=T.SURFACE,
            corner_radius=6,
        ).pack(side="left", padx=10)

        # Center hero
        hero = ctk.CTkFrame(self, fg_color="transparent")
        hero.pack(expand=True, fill="both")

        ctk.CTkLabel(
            hero,
            text="Shopify Product Tools",
            font=T.font(34, "bold"),
            text_color=T.TEXT,
        ).pack(pady=(40, 6))

        ctk.CTkLabel(
            hero,
            text="Upload · Scrape · Audit — all in one place",
            font=T.font(14),
            text_color=T.TEXT_SECONDARY,
        ).pack(pady=(0, 36))

        cards = ctk.CTkFrame(hero, fg_color="transparent")
        cards.pack()

        self._feature_card(
            cards,
            "📂",
            "Upload File",
            "Convert any client spreadsheet to\nShopify CSV",
            self._go_upload,
        ).pack(side="left", padx=8)

        self._feature_card(
            cards,
            "🔗",
            "Scrape Store",
            "Extract products from any Shopify\nstore URL",
            self._go_scraper,
        ).pack(side="left", padx=8)

        self._feature_card(
            cards,
            "🔍",
            "Audit Store",
            "Full CRO + SEO audit with\nWord report",
            self._go_audit,
        ).pack(side="left", padx=8)

        # Footer
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=(0, 20))

        sep = ctk.CTkFrame(footer, fg_color=T.BORDER, height=1)
        sep.pack(fill="x", padx=80, pady=(0, 12))

        ctk.CTkLabel(
            footer,
            text="Built by Sentivo Limited",
            font=T.font(11),
            text_color=T.TEXT_MUTED,
        ).pack()

    def _feature_card(self, parent, icon: str, name: str, desc: str, command) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            fg_color=T.CARD,
            corner_radius=12,
            border_width=1,
            border_color=T.BORDER,
            width=250,
            height=240,
        )
        card.pack_propagate(False)

        ctk.CTkLabel(card, text=icon, font=T.font(32), text_color=T.TEXT).pack(
            pady=(28, 8)
        )
        ctk.CTkLabel(
            card, text=name, font=T.font(16, "bold"), text_color=T.TEXT
        ).pack()
        ctk.CTkLabel(
            card,
            text=desc,
            font=T.font(12),
            text_color=T.TEXT_SECONDARY,
            justify="center",
        ).pack(pady=(8, 16))

        open_btn = T.primary_button(card, "Open →", command, width=120, height=34)
        open_btn.pack(pady=(0, 20))

        def on_enter(_e=None):
            card.configure(border_color=T.ACCENT)

        def on_leave(_e=None):
            card.configure(border_color=T.BORDER)

        card.bind("<Enter>", on_enter)
        card.bind("<Leave>", on_leave)
        for child in card.winfo_children():
            child.bind("<Enter>", on_enter)
            child.bind("<Leave>", on_leave)

        return card

    def _go_upload(self) -> None:
        from app.ui.upload_screen import UploadScreen

        self.app.show_screen(UploadScreen)

    def _go_scraper(self) -> None:
        from app.ui.scraper_screen import ScraperScreen

        self.app.show_screen(ScraperScreen)

    def _go_audit(self) -> None:
        from app.ui.audit_screen import AuditScreen

        self.app.show_screen(AuditScreen)
