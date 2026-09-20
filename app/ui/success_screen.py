"""Success screen after CSV generation."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.ui import theme as T
from app.ui.sidebar import attach_sidebar


class SuccessScreen(ctk.CTkFrame):
    """Show generation stats and open the output folder."""

    def __init__(self, parent, app, result=None, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
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

        body = attach_sidebar(self, app, "upload")

        center = ctk.CTkFrame(body, fg_color="transparent")
        center.pack(fill="both", expand=True)

        self.check_label = ctk.CTkLabel(
            center,
            text="✓",
            font=T.font(72, "bold"),
            text_color=T.SUCCESS,
        )
        self.check_label.pack(pady=(20, 8))
        self.after(50, self._fade_in_check)

        ctk.CTkLabel(
            center,
            text="CSV Ready to Import!",
            font=T.font_tuple(T.H1),
            text_color=T.TEXT_PRIMARY,
        ).pack(pady=(0, 8))

        if self.output_filename:
            ctk.CTkLabel(
                center,
                text=self.output_filename,
                font=T.font_tuple(T.BODY),
                text_color=T.TEXT_SECONDARY,
            ).pack(pady=(0, 20))

        stats = ctk.CTkFrame(center, fg_color="transparent")
        stats.pack(pady=8)
        self._stat_box(stats, "Products", str(self.result.get("products", 0)))
        self._stat_box(stats, "Variants", str(self.result.get("variants", 0)))
        self._stat_box(stats, "Total Rows", str(self.result.get("rows", 0)))

        btn_row = ctk.CTkFrame(center, fg_color="transparent")
        btn_row.pack(pady=32)

        T.primary_button(
            btn_row, "Open Folder", self._open_folder, width=150
        ).pack(side="left", padx=6)
        T.secondary_button(
            btn_row, "Download Again", self._download_again, width=150
        ).pack(side="left", padx=6)
        T.ghost_button(
            btn_row, "Run Another Task", self._go_home, width=160
        ).pack(side="left", padx=6)

        ctk.CTkLabel(
            body,
            text="Import directly in Shopify Admin → Products → Import",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
        ).pack(side="bottom")

    def _fade_in_check(self) -> None:
        self._check_alpha += 0.15
        if self._check_alpha >= 1.0:
            self.check_label.configure(text_color=T.SUCCESS, font=T.font(72, "bold"))
            return
        size = int(48 + 24 * self._check_alpha)
        self.check_label.configure(font=T.font(size, "bold"))
        self.after(40, self._fade_in_check)

    def _stat_box(self, parent, label: str, value: str) -> None:
        box = T.card_frame(parent, width=160, height=90)
        box.pack(side="left", padx=8)
        box.pack_propagate(False)
        ctk.CTkLabel(
            box, text=value, font=T.font(28, "bold"), text_color=T.ACCENT
        ).pack(pady=(18, 0))
        ctk.CTkLabel(
            box, text=label, font=T.font_tuple(T.CAPTION), text_color=T.TEXT_SECONDARY
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

    def _download_again(self) -> None:
        src = self.result.get("output_path") or ""
        if not src or not Path(src).exists():
            return
        dest = filedialog.asksaveasfilename(
            title="Save CSV again",
            defaultextension=".csv",
            initialfile=self.output_filename or "shopify_products.csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not dest:
            return
        try:
            shutil.copy2(src, dest)
            self.result["output_path"] = dest
            self.output_filename = Path(dest).name
        except Exception:
            pass

    def _go_home(self) -> None:
        from app.ui.home_screen import HomeScreen

        self.app.show_screen(HomeScreen)
