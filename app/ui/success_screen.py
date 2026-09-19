"""Success screen after CSV generation — premium."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import customtkinter as ctk

from app.ui import theme as T


class SuccessScreen(ctk.CTkFrame):
    """Show generation stats and open the output folder."""

    def __init__(self, parent, app, result=None, **kwargs):
        super().__init__(parent, fg_color=T.BG, corner_radius=0)
        self.app = app
        self.result = result or {
            "products": 0,
            "variants": 0,
            "rows": 0,
            "output_path": "",
        }
        output_path = self.result.get("output_path", "") or ""
        self.output_filename = Path(output_path).name if output_path else ""
        self._check_alpha = 0.0

        center = ctk.CTkFrame(self, fg_color="transparent")
        center.pack(expand=True)

        self.check_label = ctk.CTkLabel(
            center,
            text="✓",
            font=T.font(72, "bold"),
            text_color=T.ACCENT,
        )
        self.check_label.pack(pady=(40, 8))
        self.after(50, self._fade_in_check)

        ctk.CTkLabel(
            center,
            text="CSV Ready to Import!",
            font=T.font(28, "bold"),
            text_color=T.TEXT,
        ).pack(pady=(0, 8))

        if self.output_filename:
            T.pill(
                center, self.output_filename, T.BLUE_DIM, T.BLUE
            ).pack(pady=(0, 20))

        stats = ctk.CTkFrame(center, fg_color="transparent")
        stats.pack(pady=8)

        self._stat_box(stats, "Products", str(self.result.get("products", 0)))
        self._stat_box(stats, "Variants", str(self.result.get("variants", 0)))
        self._stat_box(stats, "Total Rows", str(self.result.get("rows", 0)))

        ctk.CTkLabel(
            center,
            text="File path",
            font=T.font(11),
            text_color=T.TEXT_MUTED,
        ).pack(pady=(28, 4))

        self.path_entry = T.styled_entry(center, height=40)
        self.path_entry.configure(width=640)
        self.path_entry.pack(pady=4)
        self.path_entry.insert(0, output_path)
        self.path_entry.configure(state="readonly")

        btn_row = ctk.CTkFrame(center, fg_color="transparent")
        btn_row.pack(pady=28)

        T.secondary_button(
            btn_row, "📁 Open Folder", self._open_folder, width=160
        ).pack(side="left", padx=8)

        T.primary_button(
            btn_row, "Generate Another", self._go_home, width=180
        ).pack(side="left", padx=8)

        ctk.CTkLabel(
            self,
            text="Import directly in Shopify Admin → Products → Import",
            font=T.font(11),
            text_color=T.TEXT_MUTED,
        ).pack(side="bottom", pady=20)

    def _fade_in_check(self) -> None:
        # Approximate fade-in by stepping size/opacity feel via color brightness
        self._check_alpha += 0.15
        if self._check_alpha >= 1.0:
            self.check_label.configure(text_color=T.ACCENT, font=T.font(72, "bold"))
            return
        # Grow slightly
        size = int(48 + 24 * self._check_alpha)
        self.check_label.configure(font=T.font(size, "bold"))
        self.after(40, self._fade_in_check)

    def _stat_box(self, parent, label: str, value: str) -> None:
        box = ctk.CTkFrame(
            parent,
            fg_color=T.CARD,
            corner_radius=12,
            border_width=1,
            border_color=T.BORDER,
            width=170,
            height=90,
        )
        box.pack(side="left", padx=10)
        box.pack_propagate(False)
        ctk.CTkLabel(
            box,
            text=value,
            font=T.font(28, "bold"),
            text_color=T.ACCENT,
        ).pack(pady=(18, 0))
        ctk.CTkLabel(
            box,
            text=label,
            font=T.font(12),
            text_color=T.TEXT_SECONDARY,
        ).pack()

    def _open_folder(self) -> None:
        output = self.result.get("output_path") or ""
        folder = str(Path(output).parent) if output else ""
        if not folder or not Path(folder).exists():
            return
        if sys.platform.startswith("win"):
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", folder], check=False)
        else:
            subprocess.run(["xdg-open", folder], check=False)

    def _go_home(self) -> None:
        from app.ui.home_screen import HomeScreen

        self.app.show_screen(HomeScreen)
