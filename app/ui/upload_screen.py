"""File upload screen — premium drop zone + preview."""

from __future__ import annotations

from tkinter import filedialog

import customtkinter as ctk

from app.core.file_parser import FileParser
from app.ui import theme as T
from app.utils.helpers import output_filename_from_upload


class UploadScreen(ctk.CTkFrame):
    """Select a CSV/Excel file and preview the first rows."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG, corner_radius=0)
        self.app = app
        self.filepath: str | None = None
        self.parsed_data: dict | None = None
        self.suggested_filename: str = "shopify_products.csv"
        self._hover_pulse = False

        T.header_bar(self, "Upload Product File", self._go_home, "Step 1 of 3")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=16)

        # Drop zone
        self.drop_zone = ctk.CTkFrame(
            body,
            fg_color=T.CARD,
            corner_radius=12,
            border_width=2,
            border_color=T.BORDER,
            height=140,
        )
        self.drop_zone.pack(fill="x", pady=(8, 12))
        self.drop_zone.pack_propagate(False)

        dz_inner = ctk.CTkFrame(self.drop_zone, fg_color="transparent")
        dz_inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(dz_inner, text="📂", font=T.font(28)).pack()
        ctk.CTkLabel(
            dz_inner,
            text="Click to select file or drag & drop",
            font=T.font(14, "bold"),
            text_color=T.TEXT,
        ).pack(pady=(4, 2))
        ctk.CTkLabel(
            dz_inner,
            text=".csv  ·  .xlsx  ·  .xls",
            font=T.font(12),
            text_color=T.TEXT_MUTED,
        ).pack()

        for w in (self.drop_zone, dz_inner, *dz_inner.winfo_children()):
            w.bind("<Button-1>", lambda _e: self._pick_file())
            w.bind("<Enter>", self._drop_enter)
            w.bind("<Leave>", self._drop_leave)

        self.status_pill = ctk.CTkFrame(body, fg_color="transparent")
        self.status_pill.pack(fill="x", pady=(0, 8))

        self.error_label = ctk.CTkLabel(
            body, text="", font=T.font(12), text_color=T.DANGER
        )
        self.error_label.pack()

        # Preview card
        preview_card = T.card_frame(body)
        preview_card.pack(fill="both", expand=True, pady=(4, 0))

        ctk.CTkLabel(
            preview_card,
            text="Preview",
            font=T.font(12, "bold"),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
        ).pack(fill="x", padx=16, pady=(12, 4))

        self.preview_frame = ctk.CTkScrollableFrame(
            preview_card,
            fg_color=T.SURFACE,
            orientation="horizontal",
            corner_radius=8,
            height=160,
        )
        self.preview_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Bottom bar
        bottom = ctk.CTkFrame(self, fg_color=T.SURFACE, height=64, corner_radius=0)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)

        bottom_inner = ctk.CTkFrame(bottom, fg_color="transparent")
        bottom_inner.pack(fill="both", expand=True, padx=24)

        self.next_btn = T.primary_button(
            bottom_inner,
            "Next: Map Columns →",
            self._go_mapping,
            width=200,
        )
        self.next_btn.configure(state="disabled")
        self.next_btn.pack(side="right", pady=12)

    def _drop_enter(self, _e=None) -> None:
        self.drop_zone.configure(border_color=T.ACCENT)
        if not self._hover_pulse:
            self._hover_pulse = True
            self._pulse_border(0)

    def _drop_leave(self, _e=None) -> None:
        self._hover_pulse = False
        self.drop_zone.configure(border_color=T.BORDER)

    def _pulse_border(self, step: int) -> None:
        if not self._hover_pulse:
            return
        colors = [T.ACCENT, T.BORDER_HOVER, T.ACCENT, T.BLUE]
        self.drop_zone.configure(border_color=colors[step % len(colors)])
        self.after(400, lambda: self._pulse_border(step + 1))

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
        for child in self.status_pill.winfo_children():
            child.destroy()

        try:
            parser = FileParser()
            self.parsed_data = parser.parse(path)
            self.filepath = path
            self.suggested_filename = output_filename_from_upload(path)
            filename = path.replace("\\", "/").split("/")[-1]

            T.pill(
                self.status_pill,
                f"✓  {filename}",
                T.ACCENT_DIM,
                T.ACCENT,
            ).pack(side="left", padx=(0, 8))

            T.pill(
                self.status_pill,
                f"{self.parsed_data['row_count']} rows · {len(self.parsed_data['headers'])} cols",
                T.BLUE_DIM,
                T.BLUE,
            ).pack(side="left")

            self._render_preview()
            self.next_btn.configure(state="normal")
            self.drop_zone.configure(border_color=T.ACCENT)
        except ValueError as exc:
            self.parsed_data = None
            self.filepath = None
            self.suggested_filename = "shopify_products.csv"
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
        rows = self.parsed_data["rows"][:5]

        for col_idx, header in enumerate(headers):
            col = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
            col.grid(row=0, column=col_idx, padx=4, sticky="nw")

            ctk.CTkLabel(
                col,
                text=header,
                font=T.font(11, "bold"),
                text_color=T.BLUE,
                width=120,
                anchor="w",
                fg_color=T.CARD,
                corner_radius=4,
            ).pack(anchor="w", pady=(0, 4), ipady=2)

            for i, row in enumerate(rows):
                value = str(row.get(header, ""))[:40]
                bg = T.SURFACE if i % 2 == 0 else T.CARD
                ctk.CTkLabel(
                    col,
                    text=value or "—",
                    font=T.font(11),
                    text_color=T.TEXT_SECONDARY,
                    width=120,
                    anchor="w",
                    fg_color=bg,
                ).pack(anchor="w", ipady=2)

    def _go_mapping(self) -> None:
        if not self.parsed_data:
            return
        from app.ui.mapping_screen import MappingScreen

        self.app.show_screen(
            MappingScreen,
            parsed_data=self.parsed_data,
            suggested_filename=self.suggested_filename,
        )
