"""Design system for Sentivo Tools — light mockup navy/burgundy brand."""

from __future__ import annotations

import tkinter.font as tkfont

import customtkinter as ctk

# ── TYPOGRAPHY ──────────────────────────────────────────
_FONT_CACHE: str | None = None


def _resolve_ui_font() -> str:
    """Prefer Segoe UI (mockup); fall back to system UI font."""
    global _FONT_CACHE
    if _FONT_CACHE:
        return _FONT_CACHE
    preferred = "Segoe UI"
    try:
        families = {f.lower() for f in tkfont.families()}
        _FONT_CACHE = preferred if preferred.lower() in families else "Arial"
    except Exception:
        _FONT_CACHE = "Arial"
    return _FONT_CACHE


# Spec names — actual family resolved at font() call time
FONT_UI = "Segoe UI"
FONT_HEADING = "Segoe UI"
FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"

H1 = ("Segoe UI", 28, "bold")
H2 = ("Segoe UI", 20, "bold")
H3 = ("Segoe UI", 16, "bold")
BODY = ("Segoe UI", 14, "normal")
LABEL = ("Segoe UI", 13, "normal")
CAPTION = ("Segoe UI", 12, "normal")
BTN_TEXT = ("Segoe UI", 13, "bold")

# ── SIZES / LAYOUT ──────────────────────────────────────
SIDEBAR_WIDTH = 220
PAGE_PADDING = 24
CARD_PADDING = 16
GRID_GAP = 16
BORDER_RADIUS = 8
CARD_RADIUS = 12
INPUT_HEIGHT = 40
BTN_HEIGHT = 40
ROW_HEIGHT = 44
WINDOW_MIN = (960, 640)
SCROLLBAR_WIDTH = 8

# Brand tokens (light mockup — app is light-only for now)
_LIGHT = {
    "BG_PRIMARY": "#F0EDE8",
    "BG_SURFACE_A": "#FFFFFF",
    "BG_SURFACE_B": "#FFFFFF",
    "BORDER": "#D8D5D0",
    "TEXT_PRIMARY": "#1F2937",
    "TEXT_SECONDARY": "#6B7280",
    "TEXT_MUTED": "#6B7280",
    "HEADING": "#0D1B4B",
    "ACCENT": "#6B1228",
    "ACCENT_HOVER": "#8A1835",
    "GOLD": "#C9A84C",
    "SUCCESS": "#22A55B",
    "WARNING": "#F59E0B",
    "ERROR": "#D92D3A",
    "INFO": "#2563EB",
    "ACCENT_DIM": "#F3E6EA",
    "AMBER_BG": "#F5EDD8",
    "BORDER_HOVER": "#C4C0B8",
    "BTN_ON_ACCENT": "#F0EDE8",
    "INPUT_BG": "#FFFFFF",
    "INPUT_TEXT": "#1F2937",
    "INPUT_PLACEHOLDER": "#6B7280",
    "DISABLED_BG": "#E8E5E0",
    "DISABLED_TEXT": "#9CA3AF",
    "CIRCLE_ICON_BG": "#F0EDE8",
    "SIDEBAR_BG": "#0D1B4B",
    "SIDEBAR_TEXT": "#F0EDE8",
    "SIDEBAR_MUTED": "#A8B0C8",
    "SIDEBAR_HOVER": "#152456",
    "SIDEBAR_ACTIVE_BG": "#6B1228",
    "SIDEBAR_ACTIVE_BORDER": "#6B1228",
}

# Kept for compatibility; appearance is forced to light.
_DARK = dict(_LIGHT)


def current_colors() -> dict[str, str]:
    """Return light mockup tokens (dark mode disabled for now)."""
    return dict(_LIGHT)


def get(key: str) -> str:
    """Look up a theme color by name (dynamic)."""
    colors = current_colors()
    if key in colors:
        return colors[key]
    aliases = {
        "BG": "BG_PRIMARY",
        "SURFACE": "BG_SURFACE_A",
        "CARD": "BG_SURFACE_A",
        "DANGER": "ERROR",
        "TEXT": "TEXT_PRIMARY",
        "BLUE": "ACCENT",
        "BLUE_DIM": "BG_SURFACE_B",
        "AMBER": "GOLD",
        "HIGHLIGHT": "GOLD",
    }
    if key in aliases:
        return colors[aliases[key]]
    raise KeyError(key)


def __getattr__(name: str):
    """Make T.BG_PRIMARY / T.TEXT_PRIMARY etc. resolve dynamically."""
    try:
        return get(name)
    except KeyError as exc:
        raise AttributeError(f"module 'theme' has no attribute {name!r}") from exc


# Keep legacy palette dicts for any external readers
LIGHT_THEME = dict(_LIGHT)
DARK_THEME = dict(_DARK)


def apply_theme(mode: str | None = None) -> str:
    """Force light appearance to match mockups (dark toggle removed)."""
    del mode
    try:
        ctk.set_appearance_mode("light")
    except Exception:
        pass
    return "light"


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=_resolve_ui_font(), size=size, weight=weight)


def font_tuple(spec: tuple) -> ctk.CTkFont:
    _family, size, weight = spec
    return ctk.CTkFont(family=_resolve_ui_font(), size=size, weight=weight)


# ── COMPONENT STYLE DICTS ───────────────────────────────
def primary_btn() -> dict:
    c = current_colors()
    return {
        "fg_color": c["ACCENT"],
        "text_color": c["BTN_ON_ACCENT"],
        "hover_color": c["ACCENT_HOVER"],
        "corner_radius": BORDER_RADIUS,
        "height": BTN_HEIGHT,
        "font": font_tuple(BTN_TEXT),
    }


def secondary_btn() -> dict:
    c = current_colors()
    return {
        "fg_color": "transparent",
        "text_color": c["TEXT_PRIMARY"],
        "border_color": c["BORDER"],
        "border_width": 1,
        "hover_color": c["BG_PRIMARY"],
        "corner_radius": BORDER_RADIUS,
        "height": BTN_HEIGHT,
        "font": font_tuple(BTN_TEXT),
    }


def ghost_btn() -> dict:
    c = current_colors()
    return {
        "fg_color": "transparent",
        "text_color": c["ACCENT"],
        "hover_color": c["BG_PRIMARY"],
        "corner_radius": BORDER_RADIUS,
        "height": BTN_HEIGHT,
        "font": font_tuple(BTN_TEXT),
    }


def input_field() -> dict:
    """Explicit light-mode input colors (never inherit dark CTk defaults)."""
    c = current_colors()
    return {
        "fg_color": c["INPUT_BG"],
        "border_color": c["BORDER"],
        "border_width": 1,
        "text_color": c["INPUT_TEXT"],
        "placeholder_text_color": c["INPUT_PLACEHOLDER"],
        "corner_radius": BORDER_RADIUS,
        "height": INPUT_HEIGHT,
        "font": font_tuple(BODY),
    }


def option_menu_style() -> dict:
    c = current_colors()
    return {
        "fg_color": c["INPUT_BG"],
        "button_color": c["INPUT_BG"],
        "button_hover_color": c["BG_PRIMARY"],
        "text_color": c["INPUT_TEXT"],
        "dropdown_fg_color": c["INPUT_BG"],
        "dropdown_hover_color": c["BG_PRIMARY"],
        "dropdown_text_color": c["INPUT_TEXT"],
        "corner_radius": BORDER_RADIUS,
        "font": font_tuple(LABEL),
    }


def combo_box_style() -> dict:
    """CTkComboBox style — neutral border, no burgundy arrow square."""
    c = current_colors()
    return {
        "fg_color": c["INPUT_BG"],
        "border_color": c["BORDER"],
        "border_width": 1,
        "button_color": c["INPUT_BG"],
        "button_hover_color": c["BG_PRIMARY"],
        "text_color": c["INPUT_TEXT"],
        "dropdown_fg_color": c["INPUT_BG"],
        "dropdown_hover_color": c["BG_PRIMARY"],
        "dropdown_text_color": c["INPUT_TEXT"],
        "corner_radius": BORDER_RADIUS,
        "font": font_tuple(LABEL),
        "height": INPUT_HEIGHT,
    }


def textbox_style() -> dict:
    c = current_colors()
    return {
        "fg_color": c["INPUT_BG"],
        "text_color": c["INPUT_TEXT"],
        "border_color": c["BORDER"],
        "border_width": 1,
        "corner_radius": BORDER_RADIUS,
        "font": font_tuple(BODY),
    }


def card_frame(master, **kw) -> ctk.CTkFrame:
    """Card surface — white panels with brand border."""
    c = current_colors()
    opts = {
        "fg_color": c["BG_SURFACE_A"],
        "corner_radius": CARD_RADIUS,
        "border_width": 1,
        "border_color": c["BORDER"],
    }
    opts.update(kw)
    opts["border_width"] = 1
    opts["border_color"] = c["BORDER"]
    if "fg_color" not in kw:
        opts["fg_color"] = c["BG_SURFACE_A"]
    if "corner_radius" not in kw:
        opts["corner_radius"] = CARD_RADIUS
    return ctk.CTkFrame(master, **opts)


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
        entry.configure(border_color=get("GOLD"))

    def on_focus_out(_e=None):
        entry.configure(border_color=get("BORDER"))

    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    return entry


def styled_option_menu(parent, values: list[str], **kw) -> ctk.CTkOptionMenu:
    style = option_menu_style()
    style.update(kw)
    return ctk.CTkOptionMenu(parent, values=values, **style)


def styled_textbox(parent, height: int = 100, **kw) -> ctk.CTkTextbox:
    style = textbox_style()
    style["height"] = height
    style.update(kw)
    return ctk.CTkTextbox(parent, **style)


def thin_scrollable_frame(parent, **kw) -> ctk.CTkScrollableFrame:
    """CTkScrollableFrame with a thin light scrollbar."""
    c = current_colors()
    opts = {
        "fg_color": "transparent",
        "scrollbar_button_color": c["BORDER"],
        "scrollbar_button_hover_color": c["BORDER_HOVER"],
        "corner_radius": 0,
    }
    opts.update(kw)
    frame = ctk.CTkScrollableFrame(parent, **opts)
    try:
        sb = getattr(frame, "_scrollbar", None)
        if sb is not None:
            sb.configure(width=SCROLLBAR_WIDTH)
    except Exception:
        pass
    return frame


def apply_thin_scrollbars(root) -> None:
    """Best-effort: shrink CTk scrollbars under a widget tree."""
    try:
        for child in root.winfo_children():
            apply_thin_scrollbars(child)
        if root.__class__.__name__ == "CTkScrollbar":
            root.configure(width=SCROLLBAR_WIDTH)
        sb = getattr(root, "_scrollbar", None)
        if sb is not None:
            sb.configure(width=SCROLLBAR_WIDTH)
    except Exception:
        pass


def header_bar(parent, title: str, back_cmd, step: str | None = None) -> ctk.CTkFrame:
    """Legacy top bar — prefer sidebar layout on new screens."""
    c = current_colors()
    bar = ctk.CTkFrame(parent, fg_color=c["BG_SURFACE_A"], corner_radius=0, height=56)
    bar.pack(fill="x")
    bar.pack_propagate(False)

    inner = ctk.CTkFrame(bar, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=PAGE_PADDING)

    back_button(inner, back_cmd).pack(side="left")
    ctk.CTkLabel(
        inner, text=title, font=font_tuple(H3), text_color=c["HEADING"]
    ).pack(side="left", padx=16)

    if step:
        ctk.CTkLabel(
            inner, text=step, font=font_tuple(CAPTION), text_color=c["TEXT_MUTED"]
        ).pack(side="right")
    return bar


def pill(parent, text: str, fg: str, text_color: str | None = None) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=f"  {text}  ",
        font=font(12, "bold"),
        text_color=text_color if text_color is not None else get("TEXT_PRIMARY"),
        fg_color=fg,
        corner_radius=BORDER_RADIUS,
        height=28,
    )


def confidence_badge(parent, level: str) -> ctk.CTkLabel:
    c = current_colors()
    styles = {
        "high": ("● High", c["SUCCESS"]),
        "medium": ("● Medium", c["WARNING"]),
        "low": ("● Low", c["ERROR"]),
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
    c = current_colors()
    colors = {"success": c["SUCCESS"], "warning": c["WARNING"], "error": c["ERROR"]}
    color = colors.get(level, c["TEXT_SECONDARY"])
    return ctk.CTkLabel(
        parent,
        text=f"●  {label}",
        font=font_tuple(CAPTION),
        text_color=color,
        anchor="w",
    )


def progress_bar(parent, width: int = 500) -> ctk.CTkProgressBar:
    c = current_colors()
    return ctk.CTkProgressBar(
        parent,
        width=width,
        height=6,
        mode="indeterminate",
        progress_color=c["GOLD"],
        fg_color=c["BORDER"],
        corner_radius=3,
    )


def log_box(parent, height: int = 200) -> ctk.CTkTextbox:
    c = current_colors()
    return ctk.CTkTextbox(
        parent,
        height=height,
        fg_color=c["INPUT_BG"],
        text_color=c["INPUT_TEXT"],
        font=ctk.CTkFont(family=FONT_MONO, size=12),
        border_width=1,
        border_color=c["BORDER"],
        corner_radius=BORDER_RADIUS,
        state="disabled",
    )


def step_indicator(parent, current: int, total: int = 4) -> ctk.CTkFrame:
    """Numbered step circles: 1 → 2 → 3 → 4."""
    c = current_colors()
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    for i in range(1, total + 1):
        active = i == current
        done = i < current
        bg = c["ACCENT"] if active or done else c["BORDER"]
        tc = c["BTN_ON_ACCENT"] if active or done else c["TEXT_MUTED"]
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
                wrap, text="→", font=font(12), text_color=c["TEXT_MUTED"], width=20
            ).pack(side="left")
    return wrap


def page_title(parent, title: str, subtitle: str = "") -> ctk.CTkFrame:
    c = current_colors()
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    wrap.pack(fill="x", pady=(0, GRID_GAP))
    ctk.CTkLabel(
        wrap, text=title, font=font_tuple(H1), text_color=c["HEADING"], anchor="w"
    ).pack(fill="x")
    if subtitle:
        ctk.CTkLabel(
            wrap,
            text=subtitle,
            font=font_tuple(BODY),
            text_color=c["TEXT_SECONDARY"],
            anchor="w",
        ).pack(fill="x", pady=(4, 0))
    return wrap
