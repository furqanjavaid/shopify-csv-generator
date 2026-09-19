"""Success screen after CSV generation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import customtkinter as ctk


class SuccessScreen(ctk.CTkFrame):
    """Show generation stats and open the output folder."""

    def __init__(self, parent, app, result=None, **kwargs):
        super().__init__(parent, fg_color="#1a1a2e", corner_radius=0)
        self.app = app
        self.result = result or {
            "products": 0,
            "variants": 0,
            "rows": 0,
            "output_path": "",
        }

        check = ctk.CTkLabel(
            self,
            text="✓",
            font=ctk.CTkFont(size=64, weight="bold"),
            text_color="#22c55e",
        )
        check.pack(pady=(60, 8))

        title = ctk.CTkLabel(
            self,
            text="Shopify CSV Ready!",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color="#ffffff",
        )
        title.pack(pady=(0, 24))

        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.pack(pady=8)

        self._stat_box(stats, "Products", str(self.result.get("products", 0)))
        self._stat_box(stats, "Variants", str(self.result.get("variants", 0)))
        self._stat_box(stats, "Total Rows", str(self.result.get("rows", 0)))

        path_label = ctk.CTkLabel(
            self,
            text="Output file",
            font=ctk.CTkFont(size=12),
            text_color="#9ca3af",
        )
        path_label.pack(pady=(28, 4))

        self.path_entry = ctk.CTkEntry(
            self,
            width=700,
            height=36,
            font=ctk.CTkFont(size=12),
        )
        self.path_entry.pack(pady=4)
        self.path_entry.insert(0, self.result.get("output_path", ""))
        self.path_entry.configure(state="readonly")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=28)

        ctk.CTkButton(
            btn_row,
            text="Open Folder",
            width=140,
            height=36,
            fg_color="#16213e",
            hover_color="#1f2f54",
            command=self._open_folder,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_row,
            text="Generate Another",
            width=160,
            height=36,
            command=self._go_home,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_row,
            text="Exit",
            width=100,
            height=36,
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            command=self.app.destroy,
        ).pack(side="left", padx=8)

    def _stat_box(self, parent, label: str, value: str) -> None:
        box = ctk.CTkFrame(parent, fg_color="#16213e", corner_radius=10, width=160, height=80)
        box.pack(side="left", padx=10)
        box.pack_propagate(False)
        ctk.CTkLabel(
            box,
            text=value,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#ffffff",
        ).pack(pady=(14, 0))
        ctk.CTkLabel(
            box,
            text=label,
            font=ctk.CTkFont(size=12),
            text_color="#9ca3af",
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
