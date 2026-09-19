"""Home / mode select screen."""

from __future__ import annotations

import customtkinter as ctk


class HomeScreen(ctk.CTkFrame):
    """Landing screen with Upload File and Scrape URL modes."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color="#1a1a2e", corner_radius=0)
        self.app = app

        title = ctk.CTkLabel(
            self,
            text="Shopify CSV Generator",
            font=ctk.CTkFont(size=32, weight="bold"),
            text_color="#ffffff",
        )
        title.pack(pady=(80, 8))

        subtitle = ctk.CTkLabel(
            self,
            text="Convert any product data to Shopify-ready CSV",
            font=ctk.CTkFont(size=14),
            text_color="#9ca3af",
        )
        subtitle.pack(pady=(0, 50))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack()

        upload_btn = ctk.CTkButton(
            btn_row,
            text="📂  Upload File",
            width=200,
            height=80,
            corner_radius=12,
            fg_color="#16213e",
            hover_color="#1f2f54",
            text_color="#ffffff",
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self._go_upload,
        )
        upload_btn.pack(side="left", padx=16)

        scrape_btn = ctk.CTkButton(
            btn_row,
            text="🔗  Scrape URL",
            width=200,
            height=80,
            corner_radius=12,
            fg_color="#16213e",
            hover_color="#1f2f54",
            text_color="#ffffff",
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self._go_scraper,
        )
        scrape_btn.pack(side="left", padx=16)

        hint = ctk.CTkLabel(
            self,
            text="CSV / Excel upload  ·  WooCommerce & generic product URLs",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
        )
        hint.pack(pady=(24, 0))

        version = ctk.CTkLabel(
            self,
            text="v1.0 — Sentivo",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
        )
        version.pack(side="bottom", pady=24)

    def _go_upload(self) -> None:
        from app.ui.upload_screen import UploadScreen

        self.app.show_screen(UploadScreen)

    def _go_scraper(self) -> None:
        from app.ui.scraper_screen import ScraperScreen

        self.app.show_screen(ScraperScreen)
