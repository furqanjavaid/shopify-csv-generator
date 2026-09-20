"""File upload screen — dashed drop zone + step progress."""

from __future__ import annotations

from tkinter import filedialog

import customtkinter as ctk

from app.core.file_parser import FileParser
from app.ui import theme as T
from app.ui.sidebar import attach_sidebar
from app.utils.helpers import output_filename_from_upload


class UploadScreen(ctk.CTkFrame):
    """Select a CSV/Excel file and preview the first rows."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app
        self.filepath: str | None = None
        self.parsed_data: dict | None = None
        self.suggested_filename: str = "shopify_products.csv"
        self._hover_pulse = False

        body = attach_sidebar(self, app, "upload")

        # Title row + step indicator
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x", pady=(0, T.GRID_GAP))
        title_wrap = ctk.CTkFrame(top, fg_color="transparent")
        title_wrap.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            title_wrap, text="Upload Product File",
            font=T.font_tuple(T.H1), text_color=T.TEXT_PRIMARY, anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            title_wrap, text="Import a client spreadsheet to begin mapping",
            font=T.font_tuple(T.BODY), text_color=T.TEXT_SECONDARY, anchor="w",
        ).pack(fill="x", pady=(4, 0))
        T.step_indicator(top, current=1, total=4).pack(side="right", padx=(12, 0))

        # Drop zone (dashed look via thicker border)
        self.drop_zone = ctk.CTkFrame(
            body,
            fg_color=T.BG_SURFACE_A,
            corner_radius=T.BORDER_RADIUS,
            border_width=2,
            border_color=T.BORDER,
            height=150,
        )
        self.drop_zone.pack(fill="x", pady=(0, 12))
        self.drop_zone.pack_propagate(False)

        dz_inner = ctk.CTkFrame(self.drop_zone, fg_color="transparent")
        dz_inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(dz_inner, text="📄", font=T.font(28)).pack()
        ctk.CTkLabel(
            dz_inner, text="Click to select file or drag & drop",
            font=T.font_tuple(T.H3), text_color=T.TEXT_PRIMARY,
        ).pack(pady=(4, 2))
        ctk.CTkLabel(
            dz_inner, text=".csv  ·  .xlsx  ·  .xls",
            font=T.font_tuple(T.CAPTION), text_color=T.TEXT_MUTED,
        ).pack()
        # Visual dashed hint
        ctk.CTkLabel(
            dz_inner, text="─ ─ ─ ─ ─ ─ ─ ─",
            font=T.font_tuple(T.CAPTION), text_color=T.BORDER,
        ).pack(pady=(6, 0))

        for w in (self.drop_zone, dz_inner, *dz_inner.winfo_children()):
            w.bind("<Button-1>", lambda _e: self._pick_file())
            w.bind("<Enter>", self._drop_enter)
            w.bind("<Leave>", self._drop_leave)

        self.status_pill = ctk.CTkFrame(body, fg_color="transparent")
        self.status_pill.pack(fill="x", pady=(0, 8))

        self.error_label = ctk.CTkLabel(
            body, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR
        )
        self.error_label.pack()

        # Preview card
        preview_card = T.card_frame(body)
        preview_card.pack(fill="both", expand=True, pady=(4, 12))

        ctk.CTkLabel(
            preview_card, text="Preview",
            font=T.font(12, "bold"), text_color=T.TEXT_SECONDARY, anchor="w",
        ).pack(fill="x", padx=T.CARD_PADDING, pady=(12, 4))

        self.preview_frame = ctk.CTkScrollableFrame(
            preview_card,
            fg_color=T.BG_SURFACE_B,
            orientation="horizontal",
            corner_radius=T.BORDER_RADIUS,
            height=160,
        )
        self.preview_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Actions
        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x", side="bottom")
        self.next_btn = T.primary_button(
            actions, "Next: Map Columns →", self._go_mapping, width=200
        )
        self.next_btn.configure(state="disabled")
        self.next_btn.pack(side="right")

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
        colors = [T.ACCENT, T.BORDER_HOVER, T.ACCENT, T.WARNING]
        self.drop_zone.configure(border_color=colors[step % len(colors)])
        self.after(400, lambda: self._pulse_border(step + 1))

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
                self.status_pill, f"✓  {filename}", T.ACCENT_DIM, T.ACCENT
            ).pack(side="left", padx=(0, 8))
            T.pill(
                self.status_pill,
                f"{self.parsed_data['row_count']} rows · {len(self.parsed_data['headers'])} cols",
                T.BG_SURFACE_B,
                T.TEXT_SECONDARY,
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
                col, text=header, font=T.font(11, "bold"),
                text_color=T.ACCENT, width=120, anchor="w",
                fg_color=T.BG_SURFACE_A, corner_radius=T.BORDER_RADIUS,
            ).pack(anchor="w", pady=(0, 4), ipady=2)

            for i, row in enumerate(rows):
                value = str(row.get(header, ""))[:40]
                bg = T.BG_SURFACE_A if i % 2 == 0 else T.BG_SURFACE_B
                ctk.CTkLabel(
                    col, text=value or "—", font=T.font(11),
                    text_color=T.TEXT_SECONDARY, width=120, anchor="w",
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
