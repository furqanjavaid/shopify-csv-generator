"""Reusable mockup UI components — colors from theme.py only."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import customtkinter as ctk

from app.ui import theme as T
from app.ui.icons import load_icon
from app.ui.theme import current_colors


# ── PageHeader ──────────────────────────────────────────


class PageHeader(ctk.CTkFrame):
    """Title + subtitle; never clipped (wraplength + padding)."""

    def __init__(self, master, title: str, subtitle: str = "", **kwargs):
        c = current_colors()
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.title_label = ctk.CTkLabel(
            self,
            text=title,
            font=T.font_tuple(T.H1),
            text_color=c["HEADING"],
            anchor="w",
            justify="left",
        )
        self.title_label.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.subtitle_label = None
        if subtitle:
            self.subtitle_label = ctk.CTkLabel(
                self,
                text=subtitle,
                font=T.font_tuple(T.BODY),
                text_color=c["TEXT_MUTED"],
                anchor="w",
                justify="left",
                wraplength=720,
            )
            self.subtitle_label.grid(row=1, column=0, sticky="ew")


# ── Card with circle-icon header ────────────────────────


class Card(ctk.CTkFrame):
    """White card with optional circle-icon header (title + subtitle)."""

    def __init__(
        self,
        master,
        *,
        title: str = "",
        subtitle: str = "",
        icon: str | None = None,
        **kwargs,
    ):
        c = current_colors()
        opts = {
            "fg_color": c["BG_SURFACE_A"],
            "corner_radius": T.CARD_RADIUS,
            "border_width": 1,
            "border_color": c["BORDER"],
        }
        opts.update(kwargs)
        super().__init__(master, **opts)
        self.grid_columnconfigure(0, weight=1)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        row = 0
        if title or icon:
            head = ctk.CTkFrame(self, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew", padx=T.CARD_PADDING, pady=(T.CARD_PADDING, 8))
            head.grid_columnconfigure(1, weight=1)
            if icon:
                circle = ctk.CTkFrame(
                    head,
                    width=36,
                    height=36,
                    corner_radius=18,
                    fg_color=c["CIRCLE_ICON_BG"],
                )
                circle.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 10))
                circle.grid_propagate(False)
                img = load_icon(icon, size=18, color="navy")
                ctk.CTkLabel(
                    circle,
                    text="",
                    image=img,
                    fg_color="transparent",
                ).place(relx=0.5, rely=0.5, anchor="center")
                self._header_icon = img
            text_col = 1 if icon else 0
            if title:
                ctk.CTkLabel(
                    head,
                    text=title,
                    font=T.font(16, "bold"),
                    text_color=c["HEADING"],
                    anchor="w",
                ).grid(row=0, column=text_col, sticky="w")
            if subtitle:
                ctk.CTkLabel(
                    head,
                    text=subtitle,
                    font=T.font_tuple(T.CAPTION),
                    text_color=c["TEXT_MUTED"],
                    anchor="w",
                    wraplength=560,
                ).grid(row=1, column=text_col, sticky="w", pady=(2, 0))
            row = 1
        self.body.grid(row=row, column=0, sticky="nsew", padx=T.CARD_PADDING, pady=(0, T.CARD_PADDING))
        self.body.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(row, weight=1)


# ── StatCard ────────────────────────────────────────────


class StatCard(ctk.CTkFrame):
    """Circle icon + big number + small label."""

    def __init__(
        self,
        master,
        *,
        label: str,
        value: str = "0",
        icon: str | None = None,
        icon_color: str = "navy",
        **kwargs,
    ):
        c = current_colors()
        opts = {
            "fg_color": c["BG_SURFACE_A"],
            "corner_radius": T.CARD_RADIUS,
            "border_width": 1,
            "border_color": c["BORDER"],
        }
        opts.update(kwargs)
        super().__init__(master, **opts)
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="ew", padx=14, pady=14)
        inner.grid_columnconfigure(0, weight=1)

        if icon:
            circle = ctk.CTkFrame(
                inner, width=36, height=36, corner_radius=18, fg_color=c["CIRCLE_ICON_BG"]
            )
            circle.grid(row=0, column=0, sticky="w", pady=(0, 8))
            circle.grid_propagate(False)
            img = load_icon(icon, size=18, color=icon_color)
            ctk.CTkLabel(circle, text="", image=img).place(relx=0.5, rely=0.5, anchor="center")
            self._icon = img

        self.value_label = ctk.CTkLabel(
            inner,
            text=str(value),
            font=T.font(28, "bold"),
            text_color=c["HEADING"],
            anchor="w",
        )
        self.value_label.grid(row=1, column=0, sticky="w")
        ctk.CTkLabel(
            inner,
            text=label,
            font=T.font_tuple(T.CAPTION),
            text_color=c["TEXT_MUTED"],
            anchor="w",
        ).grid(row=2, column=0, sticky="w", pady=(2, 0))

    def set_value(self, value: str | int) -> None:
        self.value_label.configure(text=str(value))


# ── Buttons ─────────────────────────────────────────────


class PrimaryButton(ctk.CTkButton):
    """Burgundy primary action; visible disabled state."""

    def __init__(
        self,
        master,
        text: str,
        command: Callable | None = None,
        *,
        icon: str | None = None,
        width: int = 160,
        height: int | None = None,
        **kwargs,
    ):
        c = current_colors()
        img = load_icon(icon, size=16, color="white") if icon else None
        super().__init__(
            master,
            text=text,
            command=command,
            image=img,
            compound="left",
            width=width,
            height=height or T.BTN_HEIGHT,
            fg_color=c["ACCENT"],
            hover_color=c["ACCENT_HOVER"],
            text_color=c["BTN_ON_ACCENT"],
            corner_radius=T.BORDER_RADIUS,
            font=T.font_tuple(T.BTN_TEXT),
            **kwargs,
        )
        self._icon_img = img
        self._normal = {
            "fg_color": c["ACCENT"],
            "text_color": c["BTN_ON_ACCENT"],
            "hover_color": c["ACCENT_HOVER"],
        }
        self._disabled = {
            "fg_color": c["DISABLED_BG"],
            "text_color": c["DISABLED_TEXT"],
            "hover_color": c["DISABLED_BG"],
        }

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.configure(state="normal", **self._normal)
        else:
            self.configure(state="disabled", **self._disabled)


class DangerButton(ctk.CTkButton):
    """Red danger / stop button."""

    def __init__(
        self,
        master,
        text: str,
        command: Callable | None = None,
        *,
        icon: str | None = None,
        width: int = 120,
        height: int | None = None,
        **kwargs,
    ):
        c = current_colors()
        img = load_icon(icon, size=16, color="white") if icon else None
        super().__init__(
            master,
            text=text,
            command=command,
            image=img,
            compound="left",
            width=width,
            height=height or T.BTN_HEIGHT,
            fg_color=c["ERROR"],
            hover_color="#B82530",
            text_color=c["BTN_ON_ACCENT"],
            corner_radius=T.BORDER_RADIUS,
            font=T.font_tuple(T.BTN_TEXT),
            **kwargs,
        )
        self._icon_img = img
        self._normal = {
            "fg_color": c["ERROR"],
            "text_color": c["BTN_ON_ACCENT"],
            "hover_color": "#B82530",
        }
        self._disabled = {
            "fg_color": c["DISABLED_BG"],
            "text_color": c["DISABLED_TEXT"],
            "hover_color": c["DISABLED_BG"],
        }

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.configure(state="normal", **self._normal)
        else:
            self.configure(state="disabled", **self._disabled)


class OutlineButton(ctk.CTkButton):
    """Outline secondary button."""

    def __init__(
        self,
        master,
        text: str,
        command: Callable | None = None,
        *,
        icon: str | None = None,
        width: int = 140,
        height: int | None = None,
        **kwargs,
    ):
        c = current_colors()
        img = load_icon(icon, size=16, color="navy") if icon else None
        super().__init__(
            master,
            text=text,
            command=command,
            image=img,
            compound="left",
            width=width,
            height=height or T.BTN_HEIGHT,
            fg_color="transparent",
            hover_color=c["BG_PRIMARY"],
            text_color=c["TEXT_PRIMARY"],
            border_width=1,
            border_color=c["BORDER"],
            corner_radius=T.BORDER_RADIUS,
            font=T.font_tuple(T.BTN_TEXT),
            **kwargs,
        )
        self._icon_img = img
        self._normal = {
            "fg_color": "transparent",
            "text_color": c["TEXT_PRIMARY"],
            "border_color": c["BORDER"],
            "hover_color": c["BG_PRIMARY"],
        }
        self._disabled = {
            "fg_color": c["DISABLED_BG"],
            "text_color": c["DISABLED_TEXT"],
            "border_color": c["BORDER"],
            "hover_color": c["DISABLED_BG"],
        }

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.configure(state="normal", **self._normal)
        else:
            self.configure(state="disabled", **self._disabled)


# ── LabeledInput ────────────────────────────────────────


class LabeledInput(ctk.CTkFrame):
    """Label above a styled entry."""

    def __init__(
        self,
        master,
        label: str,
        *,
        placeholder: str = "",
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        c = current_colors()
        ctk.CTkLabel(
            self,
            text=label,
            font=T.font(12, "bold"),
            text_color=c["TEXT_MUTED"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.entry = T.styled_entry(self, placeholder=placeholder)
        self.entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))

    def get(self) -> str:
        return self.entry.get()

    def set(self, value: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, value)


# ── Combobox (chevron inside bordered field) ────────────


class Combobox(ctk.CTkFrame):
    """
    Dropdown with chevron drawn inside the bordered field
    (not a detached burgundy square like CTkOptionMenu).
    """

    def __init__(
        self,
        master,
        values: list[str],
        *,
        command: Callable | None = None,
        width: int = 220,
        **kwargs,
    ):
        c = current_colors()
        super().__init__(
            master,
            fg_color=c["INPUT_BG"],
            border_width=1,
            border_color=c["BORDER"],
            corner_radius=T.BORDER_RADIUS,
            height=T.INPUT_HEIGHT,
            width=width,
            **kwargs,
        )
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self._values = list(values)
        self._command = command
        self._var = ctk.StringVar(value=values[0] if values else "")
        self._chevron = load_icon("chevron-down", size=14, color="muted")

        self._btn = ctk.CTkButton(
            self,
            textvariable=self._var,
            image=self._chevron,
            compound="right",
            anchor="w",
            fg_color="transparent",
            hover_color=c["BG_PRIMARY"],
            text_color=c["INPUT_TEXT"],
            font=T.font_tuple(T.LABEL),
            corner_radius=T.BORDER_RADIUS - 2,
            height=T.INPUT_HEIGHT - 4,
            command=self._open_menu,
        )
        self._btn.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self._menu: ctk.CTkToplevel | None = None

    def get(self) -> str:
        return self._var.get()

    def set(self, value: str) -> None:
        self._var.set(value)

    def configure_values(self, values: list[str]) -> None:
        self._values = list(values)
        if values and self._var.get() not in values:
            self._var.set(values[0])

    def _open_menu(self) -> None:
        if self._menu is not None:
            try:
                self._menu.destroy()
            except Exception:
                pass
            self._menu = None
            return
        c = current_colors()
        menu = ctk.CTkToplevel(self)
        menu.withdraw()
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        self._menu = menu
        wrap = ctk.CTkFrame(
            menu,
            fg_color=c["INPUT_BG"],
            border_width=1,
            border_color=c["BORDER"],
            corner_radius=T.BORDER_RADIUS,
        )
        wrap.pack(fill="both", expand=True)
        for val in self._values:
            b = ctk.CTkButton(
                wrap,
                text=val,
                anchor="w",
                fg_color="transparent",
                hover_color=c["BG_PRIMARY"],
                text_color=c["INPUT_TEXT"],
                font=T.font_tuple(T.LABEL),
                height=32,
                corner_radius=4,
                command=lambda v=val: self._pick(v),
            )
            b.pack(fill="x", padx=4, pady=2)
        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        w = max(self.winfo_width(), 160)
        menu.geometry(f"{w}x{min(280, 8 + 36 * max(1, len(self._values)))}+{x}+{y}")
        menu.deiconify()
        menu.bind("<FocusOut>", lambda _e: self._close_menu())
        menu.focus_force()

    def _pick(self, value: str) -> None:
        self._var.set(value)
        self._close_menu()
        if self._command:
            self._command(value)

    def _close_menu(self) -> None:
        if self._menu is not None:
            try:
                self._menu.destroy()
            except Exception:
                pass
            self._menu = None


# ── StatusDot / badge ───────────────────────────────────


class StatusDot(ctk.CTkFrame):
    """Colored status dot + optional label."""

    COLORS = {
        "success": "SUCCESS",
        "completed": "SUCCESS",
        "valid": "SUCCESS",
        "error": "ERROR",
        "danger": "ERROR",
        "warning": "WARNING",
        "pending": "TEXT_MUTED",
        "queued": "TEXT_MUTED",
        "running": "INFO",
        "info": "INFO",
        "gold": "GOLD",
    }

    def __init__(
        self,
        master,
        label: str = "",
        *,
        status: str = "success",
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        c = current_colors()
        key = self.COLORS.get(status.lower(), "TEXT_MUTED")
        color = c[key]
        self.dot = ctk.CTkLabel(
            self, text="●", font=T.font(11), text_color=color, width=14
        )
        self.dot.grid(row=0, column=0, sticky="w")
        self.label = ctk.CTkLabel(
            self,
            text=label,
            font=T.font(12),
            text_color=c["TEXT_PRIMARY"],
            anchor="w",
        )
        self.label.grid(row=0, column=1, sticky="w", padx=(2, 0))

    def set_status(self, status: str, label: str | None = None) -> None:
        c = current_colors()
        key = self.COLORS.get(status.lower(), "TEXT_MUTED")
        self.dot.configure(text_color=c[key])
        if label is not None:
            self.label.configure(text=label)


class StatusBadge(ctk.CTkLabel):
    """Pill badge for status text."""

    def __init__(self, master, text: str, *, kind: str = "info", **kwargs):
        c = current_colors()
        styles = {
            "success": (c["SUCCESS"], "#E8F8EF"),
            "error": (c["ERROR"], "#FCE8EA"),
            "warning": (c["WARNING"], "#FEF6E7"),
            "info": (c["INFO"], "#E8F0FE"),
            "neutral": (c["TEXT_MUTED"], c["BG_PRIMARY"]),
        }
        fg, bg = styles.get(kind, styles["info"])
        super().__init__(
            master,
            text=f"  {text}  ",
            font=T.font(11, "bold"),
            text_color=fg,
            fg_color=bg,
            corner_radius=6,
            height=24,
            **kwargs,
        )


# ── GoldProgressBar ─────────────────────────────────────


class GoldProgressBar(ctk.CTkProgressBar):
    """Gold filled progress bar."""

    def __init__(self, master, *, height: int = 10, **kwargs):
        c = current_colors()
        super().__init__(
            master,
            height=height,
            progress_color=c["GOLD"],
            fg_color=c["BORDER"],
            corner_radius=max(3, height // 2),
            **kwargs,
        )
        self.set(0)


# ── LogBox ──────────────────────────────────────────────


class LogBox(ctk.CTkFrame):
    """
    Consolas log viewer.
    Timestamp gray · INFO green · WARNING amber · ERROR red · message dark.
    """

    def __init__(self, master, *, height: int = 220, **kwargs):
        c = current_colors()
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.textbox = ctk.CTkTextbox(
            self,
            height=height,
            fg_color=c["INPUT_BG"],
            text_color=c["INPUT_TEXT"],
            font=ctk.CTkFont(family=T.FONT_MONO, size=11),
            border_width=1,
            border_color=c["BORDER"],
            corner_radius=T.BORDER_RADIUS,
            state="disabled",
            wrap="word",
        )
        self.textbox.grid(row=0, column=0, sticky="nsew")
        self._configure_tags()

    def _configure_tags(self) -> None:
        c = current_colors()
        try:
            tb = self.textbox._textbox  # noqa: SLF001
            tb.tag_configure("ts", foreground=c["TEXT_MUTED"])
            tb.tag_configure("info", foreground=c["SUCCESS"])
            tb.tag_configure("warn", foreground=c["WARNING"])
            tb.tag_configure("error", foreground=c["ERROR"])
            tb.tag_configure("msg", foreground=c["INPUT_TEXT"])
        except Exception:
            pass

    def clear(self) -> None:
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")

    def append(self, message: str, level: str | None = None) -> None:
        level = (level or self._infer_level(message)).lower()
        level_tag = {"warning": "warn", "warn": "warn", "error": "error", "err": "error"}.get(
            level, "info"
        )
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        level_label = {"info": "INFO", "warn": "WARNING", "error": "ERROR"}.get(
            level_tag, "INFO"
        )
        try:
            self.textbox.configure(state="normal")
            tb = getattr(self.textbox, "_textbox", None)
            if tb is not None:
                tb.insert("end", f"[{ts}] ", "ts")
                tb.insert("end", f"{level_label} ", level_tag)
                tb.insert("end", f"{message.rstrip()}\n", "msg")
                tb.see("end")
            else:
                self.textbox.insert("end", f"[{ts}] {level_label} {message.rstrip()}\n")
                self.textbox.see("end")
            self.textbox.configure(state="disabled")
        except Exception:
            pass

    @staticmethod
    def _infer_level(message: str) -> str:
        low = (message or "").lower()
        if "error" in low or "traceback" in low or low.startswith("fail"):
            return "error"
        if "warning" in low or " warn" in low:
            return "warning"
        return "info"


# ── StatusBar ───────────────────────────────────────────


class StatusBar(ctk.CTkFrame):
    """Bottom window status bar: left message + right summary."""

    def __init__(self, master, **kwargs):
        c = current_colors()
        opts = {
            "fg_color": "#E8E5E0",
            "height": 36,
            "corner_radius": 0,
            "border_width": 1,
            "border_color": c["BORDER"],
        }
        opts.update(kwargs)
        super().__init__(master, **opts)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)

        self.left = ctk.CTkLabel(
            self,
            text="Ready",
            font=T.font(12),
            text_color=c["TEXT_SECONDARY"],
            anchor="w",
        )
        self.left.grid(row=0, column=0, sticky="w", padx=14, pady=6)
        self.right = ctk.CTkLabel(
            self,
            text="",
            font=T.font(12),
            text_color=c["TEXT_MUTED"],
            anchor="e",
        )
        self.right.grid(row=0, column=1, sticky="e", padx=14, pady=6)

    def set_left(self, text: str) -> None:
        self.left.configure(text=text)

    def set_right(self, text: str) -> None:
        self.right.configure(text=text)

    def set_status(self, left: str, right: str = "") -> None:
        self.set_left(left)
        self.set_right(right)
