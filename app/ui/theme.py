"""Design system for Shopify Product Tools — Sentivo amber theme."""

from __future__ import annotations

import tkinter.font as tkfont

import customtkinter as ctk

# ── COLORS ──────────────────────────────────────────────
BG_PRIMARY = "#111111"       # Main background
BG_SURFACE_A = "#141414"     # Card / panel background
BG_SURFACE_B = "#1A1A1A"     # Table row alternate, input bg
BORDER = "#2A2A2A"           # Subtle borders
ACCENT = "#D4A843"           # Amber — buttons, active states, highlights
SUCCESS = "#3CA370"
WARNING = "#D4A843"
ERROR = "#C75B5B"
TEXT_PRIMARY = "#F0EDE8"     # Off-white
TEXT_SECONDARY = "#B9B3AA"   # Muted labels
TEXT_MUTED = "#6B6560"       # Captions, placeholders

# Compatibility aliases (main.py + older references)
BG = BG_PRIMARY
SURFACE = BG_SURFACE_A
CARD = BG_SURFACE_A
DANGER = ERROR
ACCENT_HOVER = "#C49833"
ACCENT_DIM = "#2A2210"
BLUE = ACCENT
BLUE_DIM = BG_SURFACE_B
AMBER = WARNING
AMBER_BG = "#2A2210"
BORDER_HOVER = "#3A3A3A"
TEXT = TEXT_PRIMARY

# ── TYPOGRAPHY ──────────────────────────────────────────
_FONT_CACHE: str | None = None


def _resolve_ui_font() -> str:
    """Prefer DM Sans; fall back to Segoe UI when unavailable."""
    global _FONT_CACHE
    if _FONT_CACHE:
        return _FONT_CACHE
    preferred = "DM Sans"
    try:
        families = {f.lower() for f in tkfont.families()}
        _FONT_CACHE = preferred if preferred.lower() in families else "Segoe UI"
    except Exception:
        _FONT_CACHE = "Segoe UI"
    return _FONT_CACHE


# Spec names — actual family resolved at font() call time
FONT_UI = "DM Sans"
FONT_HEADING = "DM Sans"
FONT_FAMILY = "DM Sans"
FONT_MONO = "Consolas"

H1 = ("DM Sans", 28, "bold")
H2 = ("DM Sans", 20, "bold")
H3 = ("DM Sans", 16, "bold")
BODY = ("DM Sans", 14, "normal")
LABEL = ("DM Sans", 13, "normal")
CAPTION = ("DM Sans", 12, "normal")
BTN_TEXT = ("DM Sans", 13, "bold")

# ── SIZES / LAYOUT ──────────────────────────────────────
SIDEBAR_WIDTH = 220
PAGE_PADDING = 24
CARD_PADDING = 16
GRID_GAP = 16
BORDER_RADIUS = 4
INPUT_HEIGHT = 40
BTN_HEIGHT = 40
ROW_HEIGHT = 44
WINDOW_MIN = (960, 640)


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=_resolve_ui_font(), size=size, weight=weight)


def font_tuple(spec: tuple) -> ctk.CTkFont:
    _family, size, weight = spec
    return ctk.CTkFont(family=_resolve_ui_font(), size=size, weight=weight)


# ── COMPONENT STYLE DICTS ───────────────────────────────
def primary_btn() -> dict:
    return {
        "fg_color": ACCENT,
        "text_color": "#111111",
        "hover_color": ACCENT_HOVER,
        "corner_radius": BORDER_RADIUS,
        "height": BTN_HEIGHT,
        "font": font_tuple(BTN_TEXT),
    }


def secondary_btn() -> dict:
    return {
        "fg_color": "transparent",
        "text_color": TEXT_PRIMARY,
        "border_color": BORDER,
        "border_width": 1,
        "hover_color": BG_SURFACE_B,
        "corner_radius": BORDER_RADIUS,
        "height": BTN_HEIGHT,
        "font": font_tuple(BTN_TEXT),
    }


def ghost_btn() -> dict:
    return {
        "fg_color": "transparent",
        "text_color": ACCENT,
        "hover_color": BG_SURFACE_B,
        "corner_radius": BORDER_RADIUS,
        "height": BTN_HEIGHT,
        "font": font_tuple(BTN_TEXT),
    }


def input_field() -> dict:
    return {
        "fg_color": BG_SURFACE_B,
        "border_color": BORDER,
        "border_width": 1,
        "text_color": TEXT_PRIMARY,
        "placeholder_text_color": TEXT_MUTED,
        "corner_radius": BORDER_RADIUS,
        "height": INPUT_HEIGHT,
        "font": font_tuple(BODY),
    }


def card_frame(master, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        master,
        fg_color=BG_SURFACE_A,
        corner_radius=BORDER_RADIUS,
        border_width=1,
        border_color=BORDER,
        **kw,
    )


# ── BUTTON / INPUT FACTORIES ────────────────────────────
def primary_button(parent, text: str, command, width: int = 180, height: int | None = None, **kw) -> ctk.CTkButton:
    style = primary_btn()
    if height is not None:
        style["height"] = height
    style.update(kw)
    return ctk.CTkButton(parent, text=text, width=width, command=command, **style)


def secondary_button(parent, text: str, command, width: int = 140, height: int | None = None, **kw) -> ctk.CTkButton:
    style = secondary_btn()
    if height is not None:
        style["height"] = height
    style.update(kw)
    return ctk.CTkButton(parent, text=text, width=width, command=command, **style)


def ghost_button(parent, text: str, command, width: int = 140, height: int | None = None, **kw) -> ctk.CTkButton:
    style = ghost_btn()
    if height is not None:
        style["height"] = height
    style.update(kw)
    return ctk.CTkButton(parent, text=text, width=width, command=command, **style)


def back_button(parent, command) -> ctk.CTkButton:
    return ghost_button(parent, "← Back", command, width=80, height=28)


def styled_entry(parent, placeholder: str = "", height: int | None = None, **kw) -> ctk.CTkEntry:
    style = input_field()
    if height is not None:
        style["height"] = height
    style.update(kw)
    entry = ctk.CTkEntry(parent, placeholder_text=placeholder, **style)

    def on_focus_in(_e=None):
        entry.configure(border_color=ACCENT)

    def on_focus_out(_e=None):
        entry.configure(border_color=BORDER)

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    return entry


def header_bar(parent, title: str, back_cmd, step: str | None = None) -> ctk.CTkFrame:
    """Legacy top bar — prefer sidebar layout on new screens."""
    bar = ctk.CTkFrame(parent, fg_color=BG_SURFACE_A, corner_radius=0, height=56)
    bar.pack(fill="x")
    bar.pack_propagate(False)

    inner = ctk.CTkFrame(bar, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=PAGE_PADDING)

    back_button(inner, back_cmd).pack(side="left")
    ctk.CTkLabel(
        inner, text=title, font=font_tuple(H3), text_color=TEXT_PRIMARY
    ).pack(side="left", padx=16)

    if step:
        ctk.CTkLabel(
            inner, text=step, font=font_tuple(CAPTION), text_color=TEXT_MUTED
        ).pack(side="right")
    return bar


def pill(parent, text: str, fg: str, text_color: str = TEXT_PRIMARY) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=f"  {text}  ",
        font=font(12, "bold"),
        text_color=text_color,
        fg_color=fg,
        corner_radius=BORDER_RADIUS,
        height=28,
    )


def confidence_badge(parent, level: str) -> ctk.CTkLabel:
    styles = {
        "high": ("● High", SUCCESS),
        "medium": ("● Medium", WARNING),
        "low": ("● Low", ERROR),
    }
    text, color = styles.get(level, styles["low"])
    return ctk.CTkLabel(
        parent,
        text=text,
        font=font(11, "bold"),
        text_color=color,
        fg_color="transparent",
        height=24,
        width=80,
    )


def status_dot(parent, label: str, level: str = "success") -> ctk.CTkLabel:
    colors = {"success": SUCCESS, "warning": WARNING, "error": ERROR}
    color = colors.get(level, TEXT_SECONDARY)
    return ctk.CTkLabel(
        parent,
        text=f"●  {label}",
        font=font_tuple(CAPTION),
        text_color=color,
        anchor="w",
    )


def progress_bar(parent, width: int = 500) -> ctk.CTkProgressBar:
    return ctk.CTkProgressBar(
        parent,
        width=width,
        height=6,
        mode="indeterminate",
        progress_color=ACCENT,
        fg_color=BORDER,
        corner_radius=3,
    )


def log_box(parent, height: int = 200) -> ctk.CTkTextbox:
    return ctk.CTkTextbox(
        parent,
        height=height,
        fg_color=BG_SURFACE_B,
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(family=FONT_MONO, size=12),
        border_width=1,
        border_color=BORDER,
        corner_radius=BORDER_RADIUS,
        state="disabled",
    )


def step_indicator(parent, current: int, total: int = 4) -> ctk.CTkFrame:
    """Numbered step circles: 1 → 2 → 3 → 4."""
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    for i in range(1, total + 1):
        active = i == current
        done = i < current
        bg = ACCENT if active or done else BG_SURFACE_B
        tc = "#111111" if active or done else TEXT_MUTED
        circle = ctk.CTkLabel(
            wrap,
            text=str(i),
            width=28,
            height=28,
            corner_radius=14,
            fg_color=bg,
            text_color=tc,
            font=font(12, "bold"),
        )
        circle.pack(side="left")
        if i < total:
            ctk.CTkLabel(
                wrap, text="→", font=font(12), text_color=TEXT_MUTED, width=20
            ).pack(side="left")
    return wrap


def page_title(parent, title: str, subtitle: str = "") -> ctk.CTkFrame:
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    wrap.pack(fill="x", pady=(0, GRID_GAP))
    ctk.CTkLabel(
        wrap, text=title, font=font_tuple(H1), text_color=TEXT_PRIMARY, anchor="w"
    ).pack(fill="x")
    if subtitle:
        ctk.CTkLabel(
            wrap,
            text=subtitle,
            font=font_tuple(BODY),
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).pack(fill="x", pady=(4, 0))
    return wrap
