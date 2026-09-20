"""Reusable left sidebar navigation for all screens."""

from __future__ import annotations

import customtkinter as ctk

from app.ui import theme as T

# page_id -> (label, screen factory key)
NAV_ITEMS = [
    ("home", "Home"),
    ("upload", "File Upload"),
    ("scraper", "URL Scraper"),
    ("audit", "Store Auditor"),
    ("settings", "Settings"),
]


class Sidebar(ctk.CTkFrame):
    """220px left nav. Pass active_page: home | upload | scraper | audit | settings."""

    def __init__(self, master, app, active_page: str = "home", **kwargs):
        super().__init__(
            master,
            width=T.SIDEBAR_WIDTH,
            fg_color=T.BG_SURFACE_A,
            corner_radius=0,
            **kwargs,
        )
        self.app = app
        self.active_page = active_page
        self.pack_propagate(False)

        # Brand
        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.pack(fill="x", padx=T.CARD_PADDING, pady=(28, 24))
        ctk.CTkLabel(
            brand,
            text="Sentivo",
            font=T.font_tuple(T.H2),
            text_color=T.TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            brand,
            text="Product Tools",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # Nav items
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(fill="x", padx=8)

        for page_id, label in NAV_ITEMS:
            self._nav_item(nav, page_id, label)

        # Footer version
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=T.CARD_PADDING, pady=16)
        ctk.CTkLabel(
            foot,
            text="v1.0",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).pack(fill="x")

    def _nav_item(self, parent, page_id: str, label: str) -> None:
        active = page_id == self.active_page
        row = ctk.CTkFrame(parent, fg_color="transparent", height=T.ROW_HEIGHT)
        row.pack(fill="x", pady=2)
        row.pack_propagate(False)

        # Active left accent bar
        accent = ctk.CTkFrame(
            row,
            width=3,
            fg_color=T.ACCENT if active else "transparent",
            corner_radius=0,
        )
        accent.pack(side="left", fill="y")

        btn = ctk.CTkButton(
            row,
            text=label,
            anchor="w",
            fg_color="transparent",
            hover_color=T.BG_SURFACE_B,
            text_color=T.TEXT_PRIMARY if active else T.TEXT_SECONDARY,
            font=T.font_tuple(T.LABEL),
            corner_radius=T.BORDER_RADIUS,
            height=T.ROW_HEIGHT - 4,
            command=lambda p=page_id: self._navigate(p),
        )
        btn.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=2)

    def _navigate(self, page_id: str) -> None:
        if page_id == self.active_page:
            return
        if page_id == "home":
            from app.ui.home_screen import HomeScreen

            self.app.show_screen(HomeScreen)
        elif page_id == "upload":
            from app.ui.upload_screen import UploadScreen

            self.app.show_screen(UploadScreen)
        elif page_id == "scraper":
            from app.ui.scraper_screen import ScraperScreen

            self.app.show_screen(ScraperScreen)
        elif page_id == "audit":
            from app.ui.audit_screen import AuditScreen

            self.app.show_screen(AuditScreen)
        elif page_id == "settings":
            from app.ui.settings_screen import SettingsScreen

            self.app.show_screen(SettingsScreen)


def attach_sidebar(parent, app, active_page: str) -> ctk.CTkFrame:
    """
    Pack sidebar + return the main content frame (BG_PRIMARY, padded).
    Use on every screen for consistent layout.
    """
    shell = ctk.CTkFrame(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
    shell.pack(fill="both", expand=True)

    Sidebar(shell, app, active_page=active_page).pack(side="left", fill="y")

    content = ctk.CTkFrame(shell, fg_color=T.BG_PRIMARY, corner_radius=0)
    content.pack(side="left", fill="both", expand=True)

    inner = ctk.CTkFrame(content, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=T.PAGE_PADDING, pady=T.PAGE_PADDING)
    return inner
