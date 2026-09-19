"""Shared premium UI theme for Shopify Product Tools."""

from __future__ import annotations

import customtkinter as ctk

# ── Colors ──────────────────────────────────────────────
BG = "#0a0a0f"
SURFACE = "#12121a"
CARD = "#1a1a2e"
ACCENT = "#96bf48"
ACCENT_HOVER = "#7fa33a"
ACCENT_DIM = "#1e2a14"
BLUE = "#4facfe"
BLUE_DIM = "#0d2133"
TEXT = "#ffffff"
TEXT_SECONDARY = "#8892a4"
TEXT_MUTED = "#4a5568"
BORDER = "#2d2d3d"
BORDER_HOVER = "#3d3d5c"
AMBER = "#f59e0b"
AMBER_BG = "#2a2110"
DANGER = "#ef4444"
SUCCESS = "#96bf48"

FONT_FAMILY = "Segoe UI"  # Inter if installed; Segoe UI is clean on Windows


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def back_button(parent, command) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent,
        text="← Back",
        width=70,
        height=28,
        fg_color="transparent",
        hover_color=SURFACE,
        text_color=TEXT_SECONDARY,
        font=font(13),
        anchor="w",
        corner_radius=6,
        command=command,
    )


def primary_button(parent, text: str, command, width: int = 180, height: int = 40, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent,
        text=text,
        width=width,
        height=height,
        corner_radius=8,
        fg_color=ACCENT,
        hover_color=ACCENT_HOVER,
        text_color="#0a0a0f",
        font=font(14, "bold"),
        command=command,
        **kw,
    )


def secondary_button(parent, text: str, command, width: int = 140, height: int = 40, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent,
        text=text,
        width=width,
        height=height,
        corner_radius=8,
        fg_color=CARD,
        hover_color=BORDER_HOVER,
        border_width=1,
        border_color=BORDER,
        text_color=TEXT,
        font=font(13),
        command=command,
        **kw,
    )


def styled_entry(parent, placeholder: str = "", height: int = 44, **kw) -> ctk.CTkEntry:
    entry = ctk.CTkEntry(
        parent,
        placeholder_text=placeholder,
        height=height,
        corner_radius=6,
        fg_color=SURFACE,
        border_color=BORDER,
        border_width=1,
        text_color=TEXT,
        placeholder_text_color=TEXT_MUTED,
        font=font(13),
        **kw,
    )

    def on_focus_in(_e=None):
        entry.configure(border_color=ACCENT)

    def on_focus_out(_e=None):
        entry.configure(border_color=BORDER)

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    return entry


def header_bar(parent, title: str, back_cmd, step: str | None = None) -> ctk.CTkFrame:
    bar = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=0, height=56)
    bar.pack(fill="x")
    bar.pack_propagate(False)

    inner = ctk.CTkFrame(bar, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=24)

    back_button(inner, back_cmd).pack(side="left")

    ctk.CTkLabel(
        inner,
        text=title,
        font=font(16, "bold"),
        text_color=TEXT,
    ).pack(side="left", padx=16)

    if step:
        ctk.CTkLabel(
            inner,
            text=step,
            font=font(12),
            text_color=TEXT_MUTED,
        ).pack(side="right")

    return bar


def card_frame(parent, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        parent,
        fg_color=CARD,
        corner_radius=12,
        border_width=1,
        border_color=BORDER,
        **kw,
    )


def pill(parent, text: str, fg: str, text_color: str = TEXT) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=f"  {text}  ",
        font=font(12, "bold"),
        text_color=text_color,
        fg_color=fg,
        corner_radius=6,
        height=28,
    )


def confidence_badge(parent, level: str) -> ctk.CTkLabel:
    """level: high | medium | low"""
    styles = {
        "high": ("✓ High", "#1a2e1a", ACCENT),
        "medium": ("~ Medium", AMBER_BG, AMBER),
        "low": ("! Low", "#2a1515", DANGER),
    }
    text, bg, color = styles.get(level, styles["low"])
    return ctk.CTkLabel(
        parent,
        text=f" {text} ",
        font=font(11, "bold"),
        text_color=color,
        fg_color=bg,
        corner_radius=6,
        height=24,
        width=80,
    )


def progress_bar(parent, width: int = 500) -> ctk.CTkProgressBar:
    bar = ctk.CTkProgressBar(
        parent,
        width=width,
        height=6,
        mode="indeterminate",
        progress_color=ACCENT,
        fg_color=BORDER,
        corner_radius=3,
    )
    return bar


def log_box(parent, height: int = 200) -> ctk.CTkTextbox:
    box = ctk.CTkTextbox(
        parent,
        height=height,
        fg_color=SURFACE,
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(family="Consolas", size=12),
        border_width=1,
        border_color=BORDER,
        corner_radius=8,
        state="disabled",
    )
    return box
