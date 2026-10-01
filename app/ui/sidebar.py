"""Reusable left sidebar navigation for all screens."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
from PIL import Image

from app.ui import theme as T
from app.ui.theme import current_colors

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
LOGO_PATH = ASSETS_DIR / "sentivo-tools-logo.png"
LOGO_MAX_WIDTH = 180

NAV_ITEMS = [
    ("home", "Home", "⌂"),
    ("upload", "File Upload", "◫"),
    ("scraper", "URL Scraper", "↗"),
    ("audit", "Store Auditor", "◎"),
    ("converter", "Image Converter", "▣"),
    ("settings", "Settings", "⚙"),
]


def _load_sidebar_logo() -> ctk.CTkImage | None:
    if not LOGO_PATH.exists():
        return None
    try:
        img = Image.open(LOGO_PATH)
        w, h = img.size
        if w <= 0 or h <= 0:
            return None
        new_w = min(LOGO_MAX_WIDTH, w)
        new_h = max(1, int(round(h * (new_w / w))))
        return ctk.CTkImage(light_image=img, dark_image=img, size=(new_w, new_h))
    except Exception:
        return None


class Sidebar(ctk.CTkFrame):
    """Navy left nav matching brand mockups."""

    def __init__(self, master, app, active_page: str = "home", **kwargs):
        c = current_colors()
        super().__init__(
            master,
            width=T.SIDEBAR_WIDTH,
            fg_color=c["SIDEBAR_BG"],
            corner_radius=0,
            border_width=0,
            **kwargs,
        )
        self.app = app
        self.active_page = active_page
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._logo_image = None
        self._build()

    def _build(self) -> None:
        c = current_colors()
        self.configure(fg_color=c["SIDEBAR_BG"])

        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=16, pady=(24, 16))
        brand.grid_columnconfigure(0, weight=1)

        self._logo_image = _load_sidebar_logo()
        if self._logo_image is not None:
            ctk.CTkLabel(
                brand, text="", image=self._logo_image, fg_color="transparent", anchor="w"
            ).grid(row=0, column=0, sticky="w")
        else:
            ctk.CTkLabel(
                brand,
                text="Sentivo Tools",
                font=T.font_tuple(T.H2),
                text_color=c["SIDEBAR_TEXT"],
                anchor="w",
            ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            brand,
            text="by Sentivo Limited",
            font=T.font(11),
            text_color=c["GOLD"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="nsew", padx=8, pady=(8, 0))
        nav.grid_columnconfigure(0, weight=1)

        for i, (page_id, label, icon) in enumerate(NAV_ITEMS):
            self._nav_item(nav, i, page_id, label, icon)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=16, pady=16)
        foot.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            foot,
            text="v1.0.0",
            font=T.font_tuple(T.CAPTION),
            text_color=c["SIDEBAR_MUTED"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            foot,
            text="Sentivo Tools",
            font=T.font(11),
            text_color=c["SIDEBAR_MUTED"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ctk.CTkLabel(
            foot,
            text="by Sentivo Limited",
            font=T.font(10),
            text_color=c["GOLD"],
            anchor="w",
        ).grid(row=2, column=0, sticky="w")

    def _nav_item(self, parent, row: int, page_id: str, label: str, icon: str) -> None:
        c = current_colors()
        active = page_id == self.active_page
        wrap = ctk.CTkFrame(
            parent,
            fg_color=c["SIDEBAR_ACTIVE_BORDER"] if active else "transparent",
            height=T.ROW_HEIGHT,
            corner_radius=6,
        )
        wrap.grid(row=row, column=0, sticky="ew", pady=2)
        wrap.grid_propagate(False)
        wrap.grid_columnconfigure(1, weight=1)

        accent = ctk.CTkFrame(
            wrap,
            width=4,
            fg_color=c["SIDEBAR_ACTIVE_BORDER"] if active else "transparent",
            corner_radius=0,
        )
        accent.grid(row=0, column=0, sticky="ns")

        inner = ctk.CTkFrame(
            wrap,
            fg_color=c["SIDEBAR_ACTIVE_BORDER"] if active else "transparent",
            corner_radius=6,
        )
        inner.grid(row=0, column=1, sticky="nsew", padx=(0, 2), pady=2)
        inner.grid_columnconfigure(0, weight=1)

        btn = ctk.CTkButton(
            inner,
            text=f"  {icon}   {label}",
            anchor="w",
            fg_color="transparent",
            hover_color=c["SIDEBAR_HOVER"],
            text_color=c["SIDEBAR_TEXT"],
            font=T.font_tuple(T.LABEL),
            corner_radius=4,
            height=T.ROW_HEIGHT - 8,
            command=lambda p=page_id: self._navigate(p),
        )
        btn.grid(row=0, column=0, sticky="ew", padx=4)

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
        elif page_id == "converter":
            from app.ui.converter_screen import ConverterScreen

            self.app.show_screen(ConverterScreen)
        elif page_id == "settings":
            from app.ui.settings_screen import SettingsScreen

            self.app.show_screen(SettingsScreen)


def attach_sidebar(parent, app, active_page: str) -> ctk.CTkFrame:
    """
    Attach navy sidebar + return main content frame.
    Content area uses grid; shell uses pack only where required by parent.
    """
    c = current_colors()
    shell = ctk.CTkFrame(parent, fg_color=c["BG_PRIMARY"], corner_radius=0)
    shell.pack(fill="both", expand=True)
    shell.grid_columnconfigure(1, weight=1)
    shell.grid_rowconfigure(0, weight=1)

    Sidebar(shell, app, active_page=active_page).grid(row=0, column=0, sticky="ns")

    content = ctk.CTkFrame(shell, fg_color=c["BG_PRIMARY"], corner_radius=0)
    content.grid(row=0, column=1, sticky="nsew")
    content.grid_columnconfigure(0, weight=1)
    content.grid_rowconfigure(0, weight=1)

    inner = ctk.CTkFrame(content, fg_color="transparent")
    inner.grid(row=0, column=0, sticky="nsew", padx=T.PAGE_PADDING, pady=T.PAGE_PADDING)
    inner.grid_columnconfigure(0, weight=1)
    inner.grid_rowconfigure(0, weight=1)
    return inner
