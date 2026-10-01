"""File upload screen — mockup layout, existing parse → mapping flow."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.core.file_parser import FileParser
from app.ui import theme as T
from app.ui.sidebar import attach_sidebar
from app.utils.helpers import output_filename_from_upload
from app.utils.job_status import TOOL_FILE_UPLOAD
from app.utils.task_history import save_task


class UploadScreen(ctk.CTkFrame):
    """Select a CSV/Excel file and continue to column mapping."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app
        self.filepath: str | None = None
        self.parsed_data: dict | None = None
        self.suggested_filename: str = "shopify_products.csv"
        self._hover_pulse = False
        self._uploaded_meta: dict | None = None

        body = attach_sidebar(self, app, "upload")
        body.grid_rowconfigure(3, weight=1)

        # Header
        header = ctk.CTkFrame(body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        ctk.CTkLabel(
            header, text="File Upload", font=T.font_tuple(T.H1), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Import your data files (CSV and Excel) to process and extract valuable information.",
            font=T.font_tuple(T.BODY),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
            wraplength=720,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # Upload card
        upload_card = T.card_frame(body)
        upload_card.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        upload_card.grid_columnconfigure(0, weight=1)
        dz = ctk.CTkFrame(upload_card, fg_color="transparent")
        dz.grid(row=0, column=0, sticky="ew", padx=24, pady=28)
        dz.grid_columnconfigure(0, weight=1)
        self.drop_zone = dz

        ctk.CTkLabel(dz, text="📄", font=T.font(32), text_color=T.HEADING).grid(row=0, column=0)
        ctk.CTkLabel(
            dz,
            text="Drag and drop your files here or click to browse and select files",
            font=T.font(15, "bold"),
            text_color=T.HEADING,
            wraplength=560,
        ).grid(row=1, column=0, pady=(10, 14))
        self.choose_btn = T.primary_button(dz, "Choose Files", self._pick_file, width=160)
        self.choose_btn.grid(row=2, column=0)
        ctk.CTkLabel(
            dz,
            text="Supports CSV and Excel files (.csv, .xlsx, .xls) up to 50MB per file",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
        ).grid(row=3, column=0, pady=(12, 0))
        for w in (dz, *dz.winfo_children()):
            w.bind("<Button-1>", lambda _e: self._pick_file())
            w.bind("<Enter>", self._drop_enter)
            w.bind("<Leave>", self._drop_leave)

        # Stats row
        stats = ctk.CTkFrame(body, fg_color="transparent")
        stats.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="st")
        self._stat_values = {}
        for col, (key, title, icon) in enumerate(
            (
                ("files", "Files uploaded", "▤"),
                ("valid", "Valid files", "✓"),
                ("issues", "Issues found", "⚠"),
                ("rows", "Total rows", "▣"),
            )
        ):
            card = T.card_frame(stats)
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0))
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.grid(row=0, column=0, sticky="ew", padx=16, pady=14)
            ctk.CTkLabel(
                inner, text=f"{icon}  {title}", font=T.font(12), text_color=T.TEXT_MUTED, anchor="w"
            ).grid(row=0, column=0, sticky="w")
            val = ctk.CTkLabel(
                inner, text="0", font=T.font(24, "bold"), text_color=T.HEADING, anchor="w"
            )
            val.grid(row=1, column=0, sticky="w", pady=(4, 0))
            self._stat_values[key] = val

        # Bottom: files table + side panels
        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="nsew")
        bottom.grid_columnconfigure(0, weight=7)
        bottom.grid_columnconfigure(1, weight=3)
        bottom.grid_rowconfigure(0, weight=1)

        files_card = T.card_frame(bottom)
        files_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        files_card.grid_columnconfigure(0, weight=1)
        files_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            files_card,
            text="Uploaded Files",
            font=T.font(16, "bold"),
            text_color=T.HEADING,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 8))
        self.files_table = ctk.CTkFrame(files_card, fg_color="transparent")
        self.files_table.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self.files_table.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=1)
        for col, h in enumerate(("#", "File Name", "Format", "Size", "Rows", "Status")):
            ctk.CTkLabel(
                self.files_table, text=h, font=T.font(11, "bold"), text_color=T.TEXT_MUTED, anchor="w"
            ).grid(row=0, column=col, sticky="ew", padx=4)
        self.files_empty = ctk.CTkLabel(
            self.files_table,
            text="No files uploaded yet",
            font=T.font(13),
            text_color=T.TEXT_MUTED,
            anchor="w",
        )
        self.files_empty.grid(row=1, column=0, columnspan=6, sticky="w", padx=4, pady=16)

        self.next_btn = T.primary_button(
            files_card, "Process Files →", self._go_mapping, width=220
        )
        self.next_btn.grid(row=2, column=0, sticky="ew", padx=18, pady=(4, 8))
        self.next_btn.configure(state="disabled", fg_color=T.BORDER, text_color=T.TEXT_MUTED)
        ctk.CTkLabel(
            files_card,
            text="Process all valid files to extract and analyse the data.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=18, pady=(0, 16))

        side = ctk.CTkFrame(bottom, fg_color="transparent")
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(1, weight=1)

        formats = T.card_frame(side)
        formats.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        formats.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            formats, text="Accepted Formats", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))
        for i, (name, badge) in enumerate((("CSV Files", "CSV"), ("Excel Files", "XLSX"))):
            row = ctk.CTkFrame(formats, fg_color="transparent")
            row.grid(row=i + 1, column=0, sticky="ew", padx=16, pady=4)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=name, font=T.font(13), text_color=T.TEXT_PRIMARY, anchor="w").grid(
                row=0, column=0, sticky="w"
            )
            ctk.CTkLabel(
                row,
                text=f" {badge} ",
                font=T.font(11, "bold"),
                text_color=T.HEADING,
                fg_color=T.ACCENT_DIM,
                corner_radius=4,
            ).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            formats,
            text="Multiple uploads supported. Max 50MB per file.",
            font=T.font(11),
            text_color=T.TEXT_MUTED,
            wraplength=220,
            anchor="w",
            justify="left",
        ).grid(row=3, column=0, sticky="w", padx=16, pady=(8, 14))

        recent = T.card_frame(side)
        recent.grid(row=1, column=0, sticky="nsew")
        recent.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            recent, text="Recent Uploads", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))
        self.recent_host = ctk.CTkFrame(recent, fg_color="transparent")
        self.recent_host.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 14))
        self.recent_host.grid_columnconfigure(0, weight=1)
        self._render_recent_empty()

        self.error_label = ctk.CTkLabel(
            body, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR, anchor="w"
        )
        self.error_label.grid(row=4, column=0, sticky="ew", pady=(8, 0))

        # Hidden preview host (keeps preview logic available)
        self.preview_card = T.card_frame(body)
        self.preview_frame = ctk.CTkScrollableFrame(
            self.preview_card,
            fg_color=T.BG_SURFACE_B,
            orientation="horizontal",
            corner_radius=T.BORDER_RADIUS,
            height=120,
        )
        self.preview_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        self.preview_card.grid_remove()

    def _render_recent_empty(self) -> None:
        for child in self.recent_host.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.recent_host,
            text="No recent uploads",
            font=T.font(12),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

    def _drop_enter(self, _e=None) -> None:
        self.choose_btn.configure(fg_color=T.ACCENT_HOVER)

    def _drop_leave(self, _e=None) -> None:
        self.choose_btn.configure(fg_color=T.ACCENT)

    def _pick_file(self) -> None:
        store = getattr(self.app, "job_status", None)
        if store is not None and store.is_running(TOOL_FILE_UPLOAD):
            self.error_label.configure(text="Already running")
            return

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
        if store is not None and not store.try_begin(TOOL_FILE_UPLOAD):
            self.error_label.configure(text="Already running")
            return

        try:
            parser = FileParser()
            self.parsed_data = parser.parse(path)
            self.filepath = path
            self.suggested_filename = output_filename_from_upload(path)
            filename = Path(path).name
            save_task("Upload", os.path.basename(path), "Success")
            size_kb = max(1, Path(path).stat().st_size // 1024)
            fmt = Path(path).suffix.lstrip(".").upper() or "CSV"
            self._uploaded_meta = {
                "name": filename,
                "format": fmt,
                "size": f"{size_kb} KB",
                "rows": str(self.parsed_data.get("row_count") or 0),
                "status": "Valid",
                "when": datetime.now().strftime("%H:%M"),
            }
            self._refresh_file_ui(ok=True)
            self.next_btn.configure(
                state="normal", fg_color=T.ACCENT, text_color=T.BTN_ON_ACCENT
            )
        except ValueError as exc:
            self.parsed_data = None
            self.filepath = None
            self.suggested_filename = "shopify_products.csv"
            self.error_label.configure(text=str(exc))
            self._uploaded_meta = {
                "name": Path(path).name,
                "format": Path(path).suffix.lstrip(".").upper(),
                "size": "—",
                "rows": "0",
                "status": "Validation Error",
                "when": datetime.now().strftime("%H:%M"),
            }
            self._refresh_file_ui(ok=False)
            self.next_btn.configure(state="disabled", fg_color=T.BORDER, text_color=T.TEXT_MUTED)
        finally:
            if store is not None:
                store.set_idle(tool_id=TOOL_FILE_UPLOAD)

    def _refresh_file_ui(self, *, ok: bool) -> None:
        meta = self._uploaded_meta or {}
        self._stat_values["files"].configure(text="1")
        self._stat_values["valid"].configure(text="1" if ok else "0")
        self._stat_values["issues"].configure(text="0" if ok else "1")
        self._stat_values["rows"].configure(text=meta.get("rows") or "0")

        self.files_empty.grid_remove()
        # clear previous data rows (keep header row 0)
        for child in self.files_table.winfo_children():
            info = child.grid_info()
            if info and int(info.get("row", 0)) > 0 and child is not self.files_empty:
                child.destroy()

        status_color = T.SUCCESS if ok else T.ERROR
        vals = (
            "1",
            meta.get("name", ""),
            meta.get("format", ""),
            meta.get("size", ""),
            meta.get("rows", ""),
            f"● {meta.get('status', '')}",
        )
        colors = (
            T.TEXT_MUTED,
            T.TEXT_PRIMARY,
            T.TEXT_SECONDARY,
            T.TEXT_SECONDARY,
            T.TEXT_SECONDARY,
            status_color,
        )
        for col, (text, tc) in enumerate(zip(vals, colors)):
            ctk.CTkLabel(
                self.files_table, text=text, font=T.font(12), text_color=tc, anchor="w"
            ).grid(row=1, column=col, sticky="ew", padx=4, pady=6)

        for child in self.recent_host.winfo_children():
            child.destroy()
        row = ctk.CTkFrame(self.recent_host, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew")
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            row, text=meta.get("name", ""), font=T.font(12), text_color=T.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            row,
            text=f"● {meta.get('status', '')}  {meta.get('when', '')}",
            font=T.font(11),
            text_color=status_color,
            anchor="e",
        ).grid(row=0, column=1, sticky="e")

    def _go_mapping(self) -> None:
        if not self.parsed_data:
            return
        from app.ui.mapping_screen import MappingScreen

        self.app.show_screen(
            MappingScreen,
            parsed_data=self.parsed_data,
            suggested_filename=self.suggested_filename,
        )
