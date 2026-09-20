"""Settings screen — appearance and output preferences."""

from __future__ import annotations

import customtkinter as ctk

from app.ui import theme as T
from app.ui.sidebar import attach_sidebar
from app.ui.theme import current_colors
from app.utils.config import get_theme, set_theme


class SettingsScreen(ctk.CTkFrame):
    """App settings including light/dark theme toggle."""

    def __init__(self, parent, app, **kwargs):
        c = current_colors()
        super().__init__(parent, fg_color=c["BG_PRIMARY"], corner_radius=0)
        self.app = app
        body = attach_sidebar(self, app, "settings")
        T.page_title(body, "Settings", "App preferences and defaults")

        # Appearance / theme
        theme_card = T.card_frame(body)
        theme_card.pack(fill="x", pady=(0, T.GRID_GAP))

        inner = ctk.CTkFrame(theme_card, fg_color="transparent")
        inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        ctk.CTkLabel(
            inner,
            text="Appearance",
            font=T.font(14, "bold"),
            text_color=c["TEXT_PRIMARY"],
        ).pack(side="left")

        current = get_theme()
        toggle = ctk.CTkSegmentedButton(
            inner,
            values=["Dark", "Light"],
            command=self._on_theme_change,
            font=T.font(13),
            fg_color=c["BG_SURFACE_B"],
            selected_color=c["ACCENT"],
            selected_hover_color=c["ACCENT_HOVER"],
            unselected_color=c["BG_SURFACE_B"],
            unselected_hover_color=c["BORDER"],
            text_color=c["TEXT_PRIMARY"],
        )
        toggle.set("Dark" if current == "dark" else "Light")
        toggle.pack(side="right")

        # Output info
        out_card = T.card_frame(body)
        out_card.pack(fill="x", pady=(0, T.GRID_GAP))
        out_inner = ctk.CTkFrame(out_card, fg_color="transparent")
        out_inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        ctk.CTkLabel(
            out_inner,
            text="Output",
            font=T.font_tuple(T.H3),
            text_color=c["TEXT_PRIMARY"],
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            out_inner,
            text="CSV files save via Save dialog · Audits write to outputs/",
            font=T.font_tuple(T.BODY),
            text_color=c["TEXT_SECONDARY"],
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

    def _on_theme_change(self, val: str) -> None:
        theme_val = "dark" if val == "Dark" else "light"
        set_theme(theme_val)
        T.apply_theme(theme_val)
        ctk.set_appearance_mode(theme_val)
        # Rebuild this screen + window chrome so palette applies immediately
        try:
            self.app.configure(fg_color=T.get("BG_PRIMARY"))
            self.app.container.configure(fg_color=T.get("BG_PRIMARY"))
        except Exception:
            pass
        self.app.show_screen(SettingsScreen)
