"""Settings screen — output preferences (light theme only)."""

from __future__ import annotations

import customtkinter as ctk

from app.ui import theme as T
from app.ui.sidebar import attach_sidebar
from app.ui.theme import current_colors
from app.utils.updater import get_current_version


class SettingsScreen(ctk.CTkFrame):
    """App settings — appearance is locked to light to match mockups."""

    def __init__(self, parent, app, **kwargs):
        c = current_colors()
        super().__init__(parent, fg_color=c["BG_PRIMARY"], corner_radius=0)
        self.app = app
        body = attach_sidebar(self, app, "settings")
        T.page_title(body, "Settings", "App preferences and defaults")

        # Appearance (light only — dark toggle removed)
        theme_card = T.card_frame(body)
        theme_card.pack(fill="x", pady=(0, T.GRID_GAP))
        inner = ctk.CTkFrame(theme_card, fg_color="transparent")
        inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        ctk.CTkLabel(
            inner,
            text="Appearance",
            font=T.font(14, "bold"),
            text_color=c["HEADING"],
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            inner,
            text="Light theme (mockup). Dark mode is temporarily disabled.",
            font=T.font_tuple(T.BODY),
            text_color=c["TEXT_MUTED"],
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        # Output info
        out_card = T.card_frame(body)
        out_card.pack(fill="x", pady=(0, T.GRID_GAP))
        out_inner = ctk.CTkFrame(out_card, fg_color="transparent")
        out_inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        ctk.CTkLabel(
            out_inner,
            text="Output",
            font=T.font_tuple(T.H3),
            text_color=c["HEADING"],
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            out_inner,
            text="CSV files save via Save dialog · Audits write to outputs/",
            font=T.font_tuple(T.BODY),
            text_color=c["TEXT_SECONDARY"],
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        ver_card = T.card_frame(body)
        ver_card.pack(fill="x", pady=(0, T.GRID_GAP))
        ver_inner = ctk.CTkFrame(ver_card, fg_color="transparent")
        ver_inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)
        ctk.CTkLabel(
            ver_inner,
            text="Version",
            font=T.font_tuple(T.H3),
            text_color=c["HEADING"],
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            ver_inner,
            text=f"v{get_current_version()}  ·  from version.json",
            font=T.font_tuple(T.BODY),
            text_color=c["TEXT_SECONDARY"],
            anchor="w",
        ).pack(fill="x", pady=(4, 0))
