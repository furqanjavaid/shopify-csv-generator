"""Global mousewheel containment for CustomTkinter scrollable widgets.

CTkScrollableFrame uses bind_all("<MouseWheel>"), so nested scrollables
(and dropdown popups) also scroll their parent page. This patch scrolls
only the innermost scrollable under the cursor and returns "break".
"""

from __future__ import annotations

import sys
import tkinter
from typing import Any

import customtkinter as ctk

_INSTALLED = False


def _is_scrollable_canvas(widget: Any) -> bool:
    if widget is None or not isinstance(widget, tkinter.Canvas):
        return False
    try:
        ycmd = widget.cget("yscrollcommand")
        xcmd = widget.cget("xscrollcommand")
    except Exception:
        return False
    return bool(ycmd) or bool(xcmd)


def _should_handle_wheel(scrollable: ctk.CTkScrollableFrame, widget: Any) -> bool:
    """True only if this scrollable is the innermost owner of ``widget``."""
    w = widget
    parent_canvas = getattr(scrollable, "_parent_canvas", None)
    while w is not None:
        if w == parent_canvas:
            return True
        # A nested scrollable canvas appears before ours → defer to it.
        if isinstance(w, tkinter.Canvas) and w is not parent_canvas and _is_scrollable_canvas(w):
            return False
        w = getattr(w, "master", None)
    return False


def _scroll_canvas(scrollable: ctk.CTkScrollableFrame, event: Any) -> None:
    canvas = getattr(scrollable, "_parent_canvas", None)
    if canvas is None:
        return
    shift = bool(getattr(scrollable, "_shift_pressed", False))
    orientation = getattr(scrollable, "_orientation", "vertical")

    if sys.platform.startswith("win"):
        delta_units = -int(getattr(event, "delta", 0) / 6) or 0
        if delta_units == 0:
            return
        if shift or orientation == "horizontal":
            if canvas.xview() != (0.0, 1.0):
                canvas.xview("scroll", delta_units, "units")
        else:
            if canvas.yview() != (0.0, 1.0):
                canvas.yview("scroll", delta_units, "units")
        return

    delta = int(getattr(event, "delta", 0) or 0)
    if delta == 0:
        num = getattr(event, "num", None)
        if num == 4:
            delta = 1
        elif num == 5:
            delta = -1
    if delta == 0:
        return
    step = -delta
    if shift or orientation == "horizontal":
        if canvas.xview() != (0.0, 1.0):
            canvas.xview("scroll", step, "units")
    else:
        if canvas.yview() != (0.0, 1.0):
            canvas.yview("scroll", step, "units")


def _patched_mouse_wheel_all(self: ctk.CTkScrollableFrame, event: Any):
    if not _should_handle_wheel(self, event.widget):
        return
    _scroll_canvas(self, event)
    return "break"


def contain_mousewheel(widget: Any) -> None:
    """Bind wheel on a dropdown/popup root and stop propagation to the page."""
    if widget is None:
        return

    def _on_wheel(event: Any):
        w = event.widget
        while w is not None:
            if isinstance(w, tkinter.Canvas) and _is_scrollable_canvas(w):
                try:
                    if sys.platform.startswith("win"):
                        w.yview_scroll(-int(event.delta / 6), "units")
                    else:
                        delta = int(getattr(event, "delta", 0) or 0)
                        if delta == 0 and getattr(event, "num", None) == 4:
                            delta = 1
                        elif delta == 0 and getattr(event, "num", None) == 5:
                            delta = -1
                        w.yview_scroll(-delta, "units")
                except Exception:
                    pass
                return "break"
            w = getattr(w, "master", None)
        return "break"

    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        try:
            widget.bind(seq, _on_wheel, add="+")
        except Exception:
            pass


def install_scroll_fix() -> None:
    """Patch CTkScrollableFrame mousewheel handling app-wide (idempotent)."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    ctk.CTkScrollableFrame._mouse_wheel_all = _patched_mouse_wheel_all  # type: ignore[method-assign]

    # Linux often uses Button-4/5 instead of MouseWheel.
    _orig_init = ctk.CTkScrollableFrame.__init__

    def _init_with_linux_binds(self, *args, **kwargs):  # noqa: ANN001
        _orig_init(self, *args, **kwargs)
        try:
            self.bind_all("<Button-4>", self._mouse_wheel_all, add="+")
            self.bind_all("<Button-5>", self._mouse_wheel_all, add="+")
        except Exception:
            pass

    ctk.CTkScrollableFrame.__init__ = _init_with_linux_binds  # type: ignore[method-assign]
