"""File upload screen with preview."""

from __future__ import annotations

from tkinter import filedialog

import customtkinter as ctk

from app.core.file_parser import FileParser
from app.utils.helpers import output_filename_from_upload


class UploadScreen(ctk.CTkFrame):
    """Select a CSV/Excel file and preview the first rows."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color="#1a1a2e", corner_radius=0)
        self.app = app
        self.filepath: str | None = None
        self.parsed_data: dict | None = None
        self.suggested_filename: str = "shopify_products.csv"

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 8))

        back = ctk.CTkButton(
            top,
            text="← Back",
            width=80,
            height=28,
            fg_color="transparent",
            hover_color="#16213e",
            text_color="#9ca3af",
            anchor="w",
            command=self._go_home,
        )
        back.pack(side="left")

        title = ctk.CTkLabel(
            self,
            text="Upload Product File",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#ffffff",
        )
        title.pack(pady=(8, 20))

        self.drop_zone = ctk.CTkButton(
            self,
            text="Click to select file or drag & drop\n\n.csv  ·  .xlsx  ·  .xls",
            width=400,
            height=150,
            corner_radius=12,
            fg_color="#16213e",
            hover_color="#1f2f54",
            border_width=2,
            border_color="#3b82f6",
            text_color="#d1d5db",
            font=ctk.CTkFont(size=14),
            command=self._pick_file,
        )
        self.drop_zone.pack(pady=10)

        self.status_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=13),
            text_color="#22c55e",
        )
        self.status_label.pack(pady=(4, 8))

        self.error_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=13),
            text_color="#ef4444",
        )
        self.error_label.pack()

        self.preview_frame = ctk.CTkScrollableFrame(
            self,
            width=820,
            height=160,
            fg_color="#0f172a",
            orientation="horizontal",
        )
        self.preview_frame.pack(padx=20, pady=10, fill="x")

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", side="bottom", padx=20, pady=16)

        self.next_btn = ctk.CTkButton(
            bottom,
            text="Next: Map Columns →",
            width=180,
            height=36,
            state="disabled",
            command=self._go_mapping,
        )
        self.next_btn.pack(side="right")

    def _go_home(self) -> None:
        from app.ui.home_screen import HomeScreen

        self.app.show_screen(HomeScreen)

    def _pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select product file",
            filetypes=[
                ("CSV / Excel", "*.csv *.xlsx *.xls"),
                ("CSV files", "*.csv"),
                ("Excel files", "*.xlsx *.xls"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        self.error_label.configure(text="")
        try:
            parser = FileParser()
            self.parsed_data = parser.parse(path)
            self.filepath = path
            self.suggested_filename = output_filename_from_upload(path)
            filename = path.replace("\\", "/").split("/")[-1]
            self.status_label.configure(
                text=(
                    f"✓ {filename}  —  "
                    f"{self.parsed_data['row_count']} rows, "
                    f"{len(self.parsed_data['headers'])} columns"
                    f"  ·  → {self.suggested_filename}"
                )
            )
            self._render_preview()
            self.next_btn.configure(state="normal")
        except ValueError as exc:
            self.parsed_data = None
            self.filepath = None
            self.suggested_filename = "shopify_products.csv"
            self.status_label.configure(text="")
            self.error_label.configure(text=str(exc))
            self.next_btn.configure(state="disabled")
            self._clear_preview()

    def _clear_preview(self) -> None:
        for child in self.preview_frame.winfo_children():
            child.destroy()

    def _render_preview(self) -> None:
        self._clear_preview()
        if not self.parsed_data:
            return

        headers = self.parsed_data["headers"]
        rows = self.parsed_data["rows"][:3]

        for col_idx, header in enumerate(headers):
            col = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
            col.grid(row=0, column=col_idx, padx=6, sticky="nw")

            ctk.CTkLabel(
                col,
                text=header,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#93c5fd",
                width=120,
                anchor="w",
            ).pack(anchor="w")

            for row in rows:
                value = str(row.get(header, ""))[:40]
                ctk.CTkLabel(
                    col,
                    text=value or "—",
                    font=ctk.CTkFont(size=11),
                    text_color="#d1d5db",
                    width=120,
                    anchor="w",
                ).pack(anchor="w")

    def _go_mapping(self) -> None:
        if not self.parsed_data:
            return
        from app.ui.mapping_screen import MappingScreen

        self.app.show_screen(
            MappingScreen,
            parsed_data=self.parsed_data,
            suggested_filename=self.suggested_filename,
        )
