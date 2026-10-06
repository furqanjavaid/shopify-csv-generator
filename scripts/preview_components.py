"""Temporary Step A component gallery — not wired into the app shell.

Usage:  python scripts/preview_components.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import customtkinter as ctk

from app.ui import theme as T
from app.ui.components import (
    Card,
    Combobox,
    DangerButton,
    GoldProgressBar,
    LabeledInput,
    LogBox,
    OutlineButton,
    PageHeader,
    PrimaryButton,
    StatCard,
    StatusBadge,
    StatusBar,
    StatusDot,
)
from app.ui.sidebar import Sidebar
from app.ui.theme import current_colors


class _FakeApp:
    def show_screen(self, _cls):
        pass


def main() -> None:
    T.apply_theme("light")
    c = current_colors()
    root = ctk.CTk()
    root.title("Step A — Component Preview")
    root.geometry("1100x780")
    root.minsize(*T.WINDOW_MIN)
    root.configure(fg_color=c["BG_PRIMARY"])

    root.grid_columnconfigure(1, weight=1)
    root.grid_rowconfigure(0, weight=1)

    Sidebar(root, _FakeApp(), active_page="home").grid(row=0, column=0, sticky="ns", rowspan=2)

    scroll = T.thin_scrollable_frame(root, fg_color=c["BG_PRIMARY"])
    scroll.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
    scroll.grid_columnconfigure(0, weight=1)

    body = ctk.CTkFrame(scroll, fg_color="transparent")
    body.grid(row=0, column=0, sticky="ew", padx=T.PAGE_PADDING, pady=T.PAGE_PADDING)
    body.grid_columnconfigure(0, weight=1)

    PageHeader(
        body,
        "Component gallery",
        "Step A reusable widgets — colors from theme.py only.",
    ).grid(row=0, column=0, sticky="ew", pady=(0, 16))

    # Stat cards row
    stats = ctk.CTkFrame(body, fg_color="transparent")
    stats.grid(row=1, column=0, sticky="ew", pady=(0, 16))
    for i in range(4):
        stats.grid_columnconfigure(i, weight=1, uniform="s")
    StatCard(stats, label="Files uploaded", value="5", icon="file", layout="horizontal").grid(
        row=0, column=0, sticky="ew", padx=(0, 8)
    )
    StatCard(
        stats,
        label="Valid files",
        value="4",
        icon="check",
        icon_color="navy",
        circle_bg="#E8F8EF",
        layout="horizontal",
    ).grid(row=0, column=1, sticky="ew", padx=8)
    StatCard(
        stats,
        label="Issues found",
        value="1",
        icon="alert-triangle",
        icon_color="burgundy",
        circle_bg="#FEF6E7",
        layout="horizontal",
    ).grid(row=0, column=2, sticky="ew", padx=8)
    StatCard(
        stats,
        label="Total rows",
        value="24,682",
        icon="bar-chart",
        icon_color="gold",
        circle_bg="#F5EDD8",
        layout="horizontal",
    ).grid(row=0, column=3, sticky="ew", padx=(8, 0))

    # Card with circle icon header
    card = Card(
        body,
        title="Extraction settings",
        subtitle="Demo card with circle icon header",
        icon="settings",
    )
    card.grid(row=2, column=0, sticky="ew", pady=(0, 16))

    LabeledInput(card.body, "Website URL", placeholder="https://example.com").grid(
        row=0, column=0, sticky="ew", pady=(0, 10)
    )
    ctk.CTkLabel(
        card.body, text="Platform", font=T.font(12, "bold"), text_color=c["TEXT_MUTED"], anchor="w"
    ).grid(row=1, column=0, sticky="w")
    Combobox(card.body, ["Auto-detect", "Shopify", "Magento", "WooCommerce"], width=280).grid(
        row=2, column=0, sticky="w", pady=(4, 12)
    )

    # Buttons
    btns = ctk.CTkFrame(card.body, fg_color="transparent")
    btns.grid(row=3, column=0, sticky="w")
    PrimaryButton(btns, "Start", icon="play", width=130).pack(side="left", padx=(0, 8))
    OutlineButton(btns, "Browse", icon="folder", width=120).pack(side="left", padx=(0, 8))
    DangerButton(btns, "Stop", icon="square", width=110).pack(side="left", padx=(0, 8))
    disabled = PrimaryButton(btns, "Disabled", icon="check", width=130)
    disabled.pack(side="left")
    disabled.set_enabled(False)

    # Status + progress
    mid = Card(body, title="Status & progress", subtitle="Dots, badges, gold bar", icon="info")
    mid.grid(row=3, column=0, sticky="ew", pady=(0, 16))
    row = ctk.CTkFrame(mid.body, fg_color="transparent")
    row.grid(row=0, column=0, sticky="w", pady=(0, 10))
    StatusDot(row, "Completed", status="success").pack(side="left", padx=(0, 16))
    StatusDot(row, "Running", status="running").pack(side="left", padx=(0, 16))
    StatusDot(row, "Warning", status="warning").pack(side="left", padx=(0, 16))
    StatusBadge(row, "Valid", kind="success").pack(side="left", padx=(0, 8))
    StatusBadge(row, "Needs review", kind="warning").pack(side="left")
    mid.body.grid_columnconfigure(0, weight=1)
    progress = GoldProgressBar(mid.body)
    progress.grid(row=1, column=0, sticky="ew", pady=(4, 0))
    progress.set(0.62)

    # Log box
    log_card = Card(body, title="Activity log", subtitle="Consolas + level colors", icon="clock")
    log_card.grid(row=4, column=0, sticky="ew", pady=(0, 8))
    log = LogBox(log_card.body, height=160)
    log.grid(row=0, column=0, sticky="ew")
    log_card.body.grid_columnconfigure(0, weight=1)
    log.append("Sitemap discovered — 248 product URLs", "info")
    log.append("Image CDN returned 429 — retrying", "warning")
    log.append("Failed to parse variant matrix on /p/abc", "error")
    log.append("Pilot batch complete", "info")

    StatusBar(root).grid(row=1, column=1, sticky="ew")
    # Access the bar we just made
    for child in root.winfo_children():
        if isinstance(child, StatusBar):
            child.set_status("Preview mode — Step A components", "v1.1.1")
            break

    root.mainloop()


if __name__ == "__main__":
    main()
