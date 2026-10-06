"""Settings screen — output preferences (light theme only)."""

from __future__ import annotations

import customtkinter as ctk

from app.ui import theme as T
from app.ui.components import Card, PageHeader
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

        PageHeader(
            body,
            "Settings",
            "Manage application preferences and defaults.",
        ).pack(fill="x", pady=(0, 14))

        theme_card = Card(
            body,
            title="Appearance",
            subtitle="Light theme is active. Dark mode is currently disabled.",
            icon="eye",
        )
        theme_card.pack(fill="x", pady=(0, T.GRID_GAP))

        out_card = Card(
            body,
            title="Output",
            subtitle="CSV files save via Save dialog. Audits write to outputs/.",
            icon="folder",
        )
        out_card.pack(fill="x", pady=(0, T.GRID_GAP))

        ver_card = Card(
            body,
            title="Version",
            subtitle=f"v{get_current_version()}  ·  from version.json",
            icon="info",
        )
        ver_card.pack(fill="x", pady=(0, T.GRID_GAP))
