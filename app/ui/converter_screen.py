"""Bulk image converter — mockup layout; existing convert logic preserved."""

from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.core.image_converter import (
    collect_images_from_folder,
    convert_images,
)
from app.ui import theme as T
from app.ui.sidebar import attach_sidebar


class ConverterScreen(ctk.CTkFrame):
    """Bulk convert images to WebP, PNG, or JPG."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app
        self.input_paths: list[str] = []
        self._running = False
        self._completed = False
        self._last_output_dir = ""
        self.success_card = None
        self._queue_rows: list[dict] = []
        self._selected_preview_idx = 0

        body = attach_sidebar(self, app, "converter")
        body.grid_columnconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(
            header, text="Image Converter", font=T.font_tuple(T.H1), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Convert image batches between different formats quickly and easily.",
            font=T.font_tuple(T.BODY),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
            wraplength=720,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # Convert Images config card
        config = T.card_frame(body)
        config.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        config.grid_columnconfigure((0, 1, 2), weight=1)
        cfg = ctk.CTkFrame(config, fg_color="transparent")
        cfg.grid(row=0, column=0, sticky="ew", padx=16, pady=16)
        cfg.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkLabel(
            cfg, text="Convert Images", font=T.font(16, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ctk.CTkLabel(
            cfg,
            text="Select files, choose an output format, and convert in one step.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 12))

        # Col 0: Select files drop zone
        drop = ctk.CTkFrame(
            cfg,
            fg_color="#FAFAF8",
            corner_radius=T.BORDER_RADIUS,
            border_width=2,
            border_color=T.BORDER,
            height=160,
        )
        drop.grid(row=2, column=0, sticky="nsew", padx=(0, 10))
        drop.grid_propagate(False)
        drop.grid_columnconfigure(0, weight=1)
        self.drop_zone = drop
        ctk.CTkLabel(drop, text="🖼", font=T.font(26), text_color=T.HEADING).grid(row=0, column=0, pady=(18, 4))
        ctk.CTkLabel(
            drop,
            text="Drag and drop image files here\nor click to browse",
            font=T.font(12, "bold"),
            text_color=T.HEADING,
            justify="center",
        ).grid(row=1, column=0, padx=8)
        self.select_files_btn = T.primary_button(drop, "Choose Files", self._pick_images, width=130, height=32)
        self.select_files_btn.grid(row=2, column=0, pady=(10, 4))
        self.select_folder_btn = T.secondary_button(drop, "Select Folder", self._pick_folder, width=130, height=28)
        self.select_folder_btn.grid(row=3, column=0, pady=(0, 8))
        ctk.CTkLabel(
            drop, text=".jpg · .png · .webp · .bmp", font=T.font(10), text_color=T.TEXT_MUTED
        ).grid(row=4, column=0, pady=(0, 10))
        for w in (drop, *drop.winfo_children()):
            w.bind("<Button-1>", lambda _e: self._pick_images())

        self.selection_label = ctk.CTkLabel(
            drop, text="", font=T.font(10), text_color=T.TEXT_SECONDARY, wraplength=180
        )
        # keep attribute; shown via count in preview

        # Col 1: Format & settings
        mid = ctk.CTkFrame(cfg, fg_color="transparent")
        mid.grid(row=2, column=1, sticky="nsew", padx=6)
        mid.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(mid, text="Output Format", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self.format_var = ctk.StringVar(value="PNG")
        self.format_btn = ctk.CTkOptionMenu(
            mid,
            variable=self.format_var,
            values=["PNG", "WEBP", "JPG"],
            width=160,
            **T.option_menu_style(),
        )
        self.format_btn.grid(row=1, column=0, sticky="ew", pady=(4, 12))
        self.format_btn.set("PNG")

        qrow = ctk.CTkFrame(mid, fg_color="transparent")
        qrow.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        qrow.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            qrow, text="Quality (for JPG/WEBP)", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        self.quality_label = ctk.CTkLabel(qrow, text="90%", font=T.font(12, "bold"), text_color=T.GOLD, width=40)
        self.quality_label.grid(row=0, column=1, sticky="e")
        self.quality_slider = ctk.CTkSlider(
            mid,
            from_=60,
            to=100,
            number_of_steps=40,
            command=self._on_quality,
            progress_color=T.GOLD,
            button_color=T.GOLD,
            button_hover_color="#B8943A",
            fg_color=T.BORDER,
        )
        self.quality_slider.set(90)
        self.quality_slider.grid(row=3, column=0, sticky="ew", pady=(0, 12))

        self.var_keep_names = ctk.BooleanVar(value=True)
        self.var_resize = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            mid,
            text="Keep original file names",
            variable=self.var_keep_names,
            font=T.font(12),
            text_color=T.TEXT_SECONDARY,
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
            border_color=T.BORDER,
            checkmark_color=T.BTN_ON_ACCENT,
        ).grid(row=4, column=0, sticky="w", pady=2)
        ctk.CTkCheckBox(
            mid,
            text="Resize images (optional)",
            variable=self.var_resize,
            command=self._on_resize_toggle,
            font=T.font(12),
            text_color=T.TEXT_SECONDARY,
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
            border_color=T.BORDER,
            checkmark_color=T.BTN_ON_ACCENT,
        ).grid(row=5, column=0, sticky="w", pady=2)
        self.max_width_entry = T.styled_entry(mid, placeholder="Max width e.g. 2048")
        self.max_width_entry.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        self.max_width_entry.grid_remove()

        # Col 2: Output folder + Convert
        right = ctk.CTkFrame(cfg, fg_color="transparent")
        right.grid(row=2, column=2, sticky="nsew", padx=(10, 0))
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="Output Folder", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        self.output_entry = T.styled_entry(right, placeholder="…/converted")
        self.output_entry.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(4, 0))
        self.browse_btn = T.secondary_button(right, "📁", self._browse_output, width=40, height=36)
        self.browse_btn.grid(row=1, column=1, pady=(4, 0))

        self.convert_btn = T.primary_button(right, "Convert Images →", self._start_convert, height=44)
        self.convert_btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        self._disable_convert()

        self.clear_btn = T.secondary_button(right, "Clear", self._clear, height=32)
        self.clear_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.open_folder_btn = T.primary_button(right, "Open Folder", self._open_output_folder, height=32)
        self.convert_more_btn = T.secondary_button(right, "Convert More", self._reset, height=32)
        # placed dynamically on complete

        self.error_label = ctk.CTkLabel(
            right, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR, wraplength=220, anchor="w"
        )
        self.error_label.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.count_badge = ctk.CTkLabel(
            right, text="0 images selected", font=T.font(11, "bold"), text_color=T.GOLD, anchor="w"
        )
        self.count_badge.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        # Bottom: Queue + Preview
        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.grid_columnconfigure(0, weight=7)
        bottom.grid_columnconfigure(1, weight=3)

        queue_card = T.card_frame(bottom)
        queue_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        queue_card.grid_columnconfigure(0, weight=1)
        queue_card.grid_rowconfigure(1, weight=1)

        qh = ctk.CTkFrame(queue_card, fg_color="transparent")
        qh.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        qh.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(qh, text="Conversion Queue", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        T.secondary_button(qh, "Clear Completed", self._clear_completed, width=120, height=28).grid(
            row=0, column=1, sticky="e"
        )

        # Table header
        thead = ctk.CTkFrame(queue_card, fg_color=T.BG_PRIMARY, corner_radius=4)
        thead.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 0))
        thead.grid_columnconfigure(1, weight=1)
        for col, txt in enumerate(("#", "File Name", "Original", "Output", "Status", "Progress", "Added")):
            ctk.CTkLabel(thead, text=txt, font=T.font(10, "bold"), text_color=T.TEXT_MUTED, anchor="w").grid(
                row=0, column=col, sticky="ew", padx=4, pady=4
            )

        self.queue_list = ctk.CTkScrollableFrame(queue_card, fg_color="transparent")
        self.queue_list.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 12))
        self.queue_list.grid_columnconfigure(0, weight=1)
        queue_card.grid_rowconfigure(2, weight=1)
        self._queue_empty = ctk.CTkLabel(
            self.queue_list,
            text="No images in queue. Choose files to begin.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        )
        self._queue_empty.grid(row=0, column=0, sticky="w", padx=8, pady=8)

        # Hidden log for messages
        self.log_box = ctk.CTkTextbox(body, height=1)
        self.log_box.grid_remove()

        # Preview card
        prev = T.card_frame(bottom)
        prev.grid(row=0, column=1, sticky="nsew")
        prev.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            prev, text="Preview & Status", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))

        self.preview_frame = ctk.CTkFrame(
            prev, fg_color=T.BG_PRIMARY, corner_radius=6, border_width=1, border_color=T.BORDER, height=120
        )
        self.preview_frame.grid(row=1, column=0, sticky="ew", padx=14)
        self.preview_frame.grid_propagate(False)
        self.preview_label = ctk.CTkLabel(
            self.preview_frame, text="No preview", font=T.font(12), text_color=T.TEXT_MUTED
        )
        self.preview_label.place(relx=0.5, rely=0.5, anchor="center")
        self._preview_image = None

        self.detail_labels: dict[str, ctk.CTkLabel] = {}
        details = ctk.CTkFrame(prev, fg_color="transparent")
        details.grid(row=2, column=0, sticky="ew", padx=14, pady=(10, 6))
        details.grid_columnconfigure(1, weight=1)
        for i, key in enumerate(("File Name", "Dimensions", "File Size", "Original Format", "Output Format")):
            ctk.CTkLabel(details, text=key, font=T.font(11), text_color=T.TEXT_MUTED, anchor="w").grid(
                row=i, column=0, sticky="w", pady=2
            )
            lab = ctk.CTkLabel(details, text="—", font=T.font(11, "bold"), text_color=T.TEXT_PRIMARY, anchor="e")
            lab.grid(row=i, column=1, sticky="e", pady=2)
            self.detail_labels[key] = lab

        summary = ctk.CTkFrame(prev, fg_color=T.BG_PRIMARY, corner_radius=6)
        summary.grid(row=3, column=0, sticky="ew", padx=14, pady=(8, 6))
        summary.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(summary, text="Conversion Summary", font=T.font(12, "bold"), text_color=T.HEADING, anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4)
        )
        self.summary_labels = {}
        for i, (key, title, color) in enumerate(
            (
                ("total", "Total Files", T.TEXT_PRIMARY),
                ("completed", "Completed", T.SUCCESS),
                ("converting", "Converting", "#3B6EA5"),
                ("pending", "Pending", T.TEXT_MUTED),
            )
        ):
            row = ctk.CTkFrame(summary, fg_color="transparent")
            row.grid(row=i + 1, column=0, sticky="ew", padx=10, pady=1)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text="●", font=T.font(10), text_color=color, width=14).grid(row=0, column=0)
            ctk.CTkLabel(row, text=title, font=T.font(11), text_color=T.TEXT_SECONDARY, anchor="w").grid(
                row=0, column=1, sticky="w"
            )
            lab = ctk.CTkLabel(row, text="0", font=T.font(11, "bold"), text_color=T.TEXT_PRIMARY, anchor="e")
            lab.grid(row=0, column=2, sticky="e")
            self.summary_labels[key] = lab
        ctk.CTkFrame(summary, height=6, fg_color="transparent").grid(row=6, column=0)

        self.output_path_label = ctk.CTkLabel(
            prev, text="Output: —", font=T.font(10), text_color=T.TEXT_MUTED, anchor="w", wraplength=220
        )
        self.output_path_label.grid(row=4, column=0, sticky="ew", padx=14, pady=(4, 14))

        self.success_host = ctk.CTkFrame(body, fg_color="transparent")
        self.success_host.grid(row=3, column=0, sticky="ew")

    def _on_quality(self, value: float) -> None:
        self.quality_label.configure(text=f"{int(value)}%")

    def _on_resize_toggle(self) -> None:
        if self.var_resize.get():
            self.max_width_entry.grid()
        else:
            self.max_width_entry.grid_remove()

    def _busy(self) -> bool:
        return self._running or self._completed

    def _disable_convert(self) -> None:
        self.convert_btn.configure(state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED)

    def _enable_convert(self) -> None:
        self.convert_btn.configure(state="normal", fg_color=T.ACCENT, text_color=T.BTN_ON_ACCENT)

    def _set_select_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.select_files_btn.configure(state=state)
        self.select_folder_btn.configure(state=state)
        self.browse_btn.configure(state=state)
        self.format_btn.configure(state=state)
        self.quality_slider.configure(state=state)
        entry_state = "normal" if enabled else "disabled"
        self.max_width_entry.configure(state=entry_state)
        self.output_entry.configure(state=entry_state)

    def _show_idle_bar(self) -> None:
        try:
            self.open_folder_btn.grid_forget()
            self.convert_more_btn.grid_forget()
        except Exception:
            pass
        self.clear_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.convert_btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))

    def _show_complete_bar(self) -> None:
        self.convert_btn.grid_forget()
        self.clear_btn.grid_forget()
        self.convert_more_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.open_folder_btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))

    def _update_count(self) -> None:
        if self._completed:
            return
        n = len(self.input_paths)
        self.count_badge.configure(text=f"{n} image{'s' if n != 1 else ''} selected")
        if n:
            self.selection_label.configure(text=f"{n} images selected")
            self._enable_convert()
        else:
            self.selection_label.configure(text="")
            self._disable_convert()
        self._rebuild_queue()
        self._update_summary()
        self._update_preview(0)

    def _update_summary(self) -> None:
        total = len(self._queue_rows)
        completed = sum(1 for r in self._queue_rows if r.get("status") == "Completed")
        converting = sum(1 for r in self._queue_rows if r.get("status") == "Converting")
        pending = total - completed - converting
        self.summary_labels["total"].configure(text=str(total))
        self.summary_labels["completed"].configure(text=str(completed))
        self.summary_labels["converting"].configure(text=str(converting))
        self.summary_labels["pending"].configure(text=str(pending))
        out = self.output_entry.get().strip() or self._last_output_dir or "—"
        self.output_path_label.configure(text=f"Output: {out}")

    def _rebuild_queue(self) -> None:
        for child in self.queue_list.winfo_children():
            child.destroy()
        self._queue_rows = []
        if not self.input_paths:
            self._queue_empty = ctk.CTkLabel(
                self.queue_list,
                text="No images in queue. Choose files to begin.",
                font=T.font_tuple(T.CAPTION),
                text_color=T.TEXT_MUTED,
                anchor="w",
            )
            self._queue_empty.grid(row=0, column=0, sticky="w", padx=8, pady=8)
            return

        out_fmt = (self.format_var.get() or "PNG").upper()
        now = datetime.now().strftime("%H:%M")
        for i, path in enumerate(self.input_paths):
            name = Path(path).name
            orig = Path(path).suffix.lstrip(".").upper() or "—"
            status = "Queued"
            progress = 0.0
            if self._completed:
                status = "Completed"
                progress = 1.0
            elif self._running:
                status = "Queued"
            row_data = {
                "path": path,
                "name": name,
                "orig": orig,
                "out": out_fmt,
                "status": status,
                "progress": progress,
                "added": now,
            }
            self._queue_rows.append(row_data)
            self._render_queue_row(i, row_data)

    def _render_queue_row(self, i: int, row_data: dict) -> None:
        row = ctk.CTkFrame(self.queue_list, fg_color="transparent")
        row.grid(row=i, column=0, sticky="ew", pady=2)
        row.grid_columnconfigure(1, weight=1)
        status = row_data["status"]
        if status == "Completed":
            scolor = T.SUCCESS
        elif status == "Converting":
            scolor = "#3B6EA5"
        else:
            scolor = T.TEXT_MUTED

        ctk.CTkLabel(row, text=str(i + 1), font=T.font(11), text_color=T.TEXT_MUTED, width=24, anchor="w").grid(
            row=0, column=0, padx=2
        )
        name_btn = ctk.CTkButton(
            row,
            text=row_data["name"][:28],
            font=T.font(11),
            text_color=T.TEXT_PRIMARY,
            fg_color="transparent",
            hover_color=T.BG_PRIMARY,
            anchor="w",
            height=22,
            command=lambda idx=i: self._update_preview(idx),
        )
        name_btn.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(row, text=row_data["orig"], font=T.font(11), text_color=T.TEXT_SECONDARY, width=50).grid(
            row=0, column=2, padx=2
        )
        ctk.CTkLabel(row, text=row_data["out"], font=T.font(11), text_color=T.TEXT_SECONDARY, width=50).grid(
            row=0, column=3, padx=2
        )
        st = ctk.CTkFrame(row, fg_color="transparent")
        st.grid(row=0, column=4, padx=2)
        ctk.CTkLabel(st, text="●", font=T.font(10), text_color=scolor, width=12).grid(row=0, column=0)
        ctk.CTkLabel(st, text=status, font=T.font(10), text_color=T.TEXT_PRIMARY).grid(row=0, column=1)
        bar = ctk.CTkProgressBar(row, width=60, height=8, progress_color=T.GOLD, fg_color=T.BORDER, corner_radius=4)
        bar.grid(row=0, column=5, padx=4)
        bar.set(row_data["progress"])
        row_data["bar"] = bar
        row_data["status_label"] = st
        ctk.CTkLabel(row, text=row_data["added"], font=T.font(10), text_color=T.TEXT_MUTED, width=40).grid(
            row=0, column=6, padx=2
        )

    def _update_preview(self, idx: int) -> None:
        self._selected_preview_idx = idx
        if not self.input_paths or idx < 0 or idx >= len(self.input_paths):
            self.preview_label.configure(text="No preview", image=None)
            for lab in self.detail_labels.values():
                lab.configure(text="—")
            return
        path = self.input_paths[idx]
        p = Path(path)
        size = "—"
        dims = "—"
        try:
            size_b = p.stat().st_size
            if size_b < 1024:
                size = f"{size_b} B"
            elif size_b < 1024 * 1024:
                size = f"{size_b / 1024:.1f} KB"
            else:
                size = f"{size_b / (1024 * 1024):.1f} MB"
        except Exception:
            pass
        try:
            from PIL import Image

            with Image.open(path) as im:
                dims = f"{im.width} × {im.height}"
                thumb = im.copy()
                thumb.thumbnail((160, 100))
                self._preview_image = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=thumb.size)
                self.preview_label.configure(text="", image=self._preview_image)
        except Exception:
            self.preview_label.configure(text=p.name[:24], image=None)
            self._preview_image = None

        self.detail_labels["File Name"].configure(text=p.name[:28])
        self.detail_labels["Dimensions"].configure(text=dims)
        self.detail_labels["File Size"].configure(text=size)
        self.detail_labels["Original Format"].configure(text=p.suffix.lstrip(".").upper() or "—")
        self.detail_labels["Output Format"].configure(text=(self.format_var.get() or "PNG").upper())

    def _clear_completed(self) -> None:
        if self._running:
            return
        if self._completed:
            self._reset()

    def _set_default_output(self) -> None:
        if not self.input_paths:
            return
        parent = str(Path(self.input_paths[0]).parent)
        default = os.path.join(parent, "converted")
        current = self.output_entry.get().strip()
        if not current:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, default)

    def _pick_images(self) -> None:
        if self._busy():
            return
        paths = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tiff *.tif"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        self.input_paths = list(paths)
        self._update_count()
        self._set_default_output()
        self._append_log(f"Selected {len(self.input_paths)} file(s)")

    def _pick_folder(self) -> None:
        if self._busy():
            return
        folder = filedialog.askdirectory(title="Select image folder")
        if not folder:
            return
        paths = collect_images_from_folder(folder)
        self.input_paths = paths
        self._update_count()
        if paths:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, os.path.join(folder, "converted"))
            self._append_log(f"Found {len(paths)} image(s) in {folder}")
        else:
            self._append_log("No supported images in that folder")

    def _browse_output(self) -> None:
        if self._busy():
            return
        folder = filedialog.askdirectory(title="Output folder")
        if folder:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, folder)
            self._update_summary()

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _clear_results(self) -> None:
        if self.success_card is not None:
            self.success_card.destroy()
            self.success_card = None
        self.error_label.configure(text="")
        self._clear_log()

    def _clear(self) -> None:
        if self._running:
            return
        self.input_paths = []
        self._last_output_dir = ""
        self._clear_results()
        self.output_entry.configure(state="normal")
        self.output_entry.delete(0, "end")
        self._completed = False
        self._show_idle_bar()
        self._set_select_enabled(True)
        self._update_count()

    def _reset(self) -> None:
        """Reset screen to initial state for another conversion."""
        self._running = False
        self._completed = False
        self.input_paths = []
        self._last_output_dir = ""
        self._clear_results()
        self.output_entry.configure(state="normal")
        self.output_entry.delete(0, "end")
        self.max_width_entry.configure(state="normal")
        self.selection_label.configure(text="")
        self._show_idle_bar()
        self._set_select_enabled(True)
        self._disable_convert()
        self.count_badge.configure(text="0 images selected")
        self._rebuild_queue()
        self._update_summary()
        self._update_preview(-1)

    def _append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"› {message.rstrip()}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _open_output_folder(self) -> None:
        folder = self._last_output_dir or self.output_entry.get().strip()
        if not folder or not os.path.isdir(folder):
            self.error_label.configure(text="Output folder not found.")
            return
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except AttributeError:
            try:
                subprocess.run(["open", folder], check=False)
            except Exception:
                subprocess.run(["xdg-open", folder], check=False)

    def _start_convert(self) -> None:
        if self._running or self._completed or not self.input_paths:
            return
        out = self.output_entry.get().strip()
        if not out:
            self.error_label.configure(text="Choose an output folder.")
            return

        max_w = None
        if self.var_resize.get():
            raw_w = self.max_width_entry.get().strip()
            if raw_w:
                try:
                    max_w = int(raw_w)
                    if max_w <= 0:
                        raise ValueError
                except ValueError:
                    self.error_label.configure(text="Max width must be a positive integer.")
                    return

        self.error_label.configure(text="")
        if self.success_card is not None:
            self.success_card.destroy()
            self.success_card = None

        self._running = True
        self._completed = False
        self._last_output_dir = out
        self._disable_convert()
        self.clear_btn.configure(state="disabled")
        self._set_select_enabled(False)
        total = len(self.input_paths)
        self.count_badge.configure(text=f"Converting... 0/{total}")
        self._append_log(f"Converting {total} image(s) → {self.format_var.get()}…")

        # Mark queue as converting first item
        for i, r in enumerate(self._queue_rows):
            r["status"] = "Converting" if i == 0 else "Queued"
            r["progress"] = 0.05 if i == 0 else 0.0
        self._rebuild_queue_status_only()

        fmt = self.format_var.get() or "PNG"
        quality = int(self.quality_slider.get())
        thread = threading.Thread(
            target=self._run_convert,
            args=(list(self.input_paths), out, fmt, quality, max_w),
            daemon=True,
        )
        thread.start()

    def _rebuild_queue_status_only(self) -> None:
        for child in self.queue_list.winfo_children():
            child.destroy()
        for i, row_data in enumerate(self._queue_rows):
            self._render_queue_row(i, row_data)
        self._update_summary()

    def _on_progress(self, message: str, total: int) -> None:
        self._append_log(message)
        if message.startswith("Converted "):
            part = message[len("Converted ") :].split(":", 1)[0].strip()
            if "/" in part:
                self.count_badge.configure(text=f"Converting... {part}")
                try:
                    done, tot = part.split("/", 1)
                    done_i = int(done)
                    tot_i = int(tot)
                    for i, r in enumerate(self._queue_rows):
                        if i < done_i:
                            r["status"] = "Completed"
                            r["progress"] = 1.0
                        elif i == done_i and done_i < tot_i:
                            r["status"] = "Converting"
                            r["progress"] = 0.5
                        else:
                            r["status"] = "Queued"
                            r["progress"] = 0.0
                    self._rebuild_queue_status_only()
                except Exception:
                    pass
                return
        self.count_badge.configure(text=f"Converting... ?/{total}")

    def _run_convert(
        self,
        paths: list[str],
        out: str,
        fmt: str,
        quality: int,
        max_w: int | None,
    ) -> None:
        total = len(paths)

        def progress(msg: str) -> None:
            self.after(0, lambda m=msg: self._on_progress(m, total))

        try:
            result = convert_images(
                paths,
                out,
                target_format=fmt,
                quality=quality,
                max_width=max_w,
                progress=progress,
            )
            self.after(0, lambda: self._on_complete(result, out))
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._on_error(str(exc)))

    def _on_complete(self, result: dict, output_dir: str) -> None:
        self._running = False
        self._completed = True
        self._last_output_dir = output_dir

        converted = result.get("converted", 0)
        skipped = result.get("skipped", 0)
        errors = result.get("errors") or []

        self._append_log(
            f"Done — {converted} converted, {skipped} skipped, {len(errors)} error(s)"
        )
        self._append_log(f"Output: {output_dir}")
        for err in errors[:10]:
            self._append_log(f"ERROR: {err}")

        for r in self._queue_rows:
            r["status"] = "Completed"
            r["progress"] = 1.0
        self._rebuild_queue_status_only()

        self.count_badge.configure(
            text=f"{converted} converted" + (f" · {skipped} skipped" if skipped else "")
        )
        from app.utils.task_history import save_task

        save_task("Convert", f"{result['converted']} images", "Success")
        self.clear_btn.configure(state="normal")
        self._show_complete_bar()

        if self.success_card is not None:
            self.success_card.destroy()

        self.success_card = T.card_frame(self.success_host)
        self.success_card.grid(row=0, column=0, sticky="ew", pady=(8, 0))
        inner = ctk.CTkFrame(self.success_card, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="ew", padx=16, pady=12)
        ctk.CTkLabel(
            inner,
            text=f"✓  {converted} images converted successfully!",
            font=T.font(14, "bold"),
            text_color=T.SUCCESS,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        if skipped:
            ctk.CTkLabel(
                inner,
                text=f"{skipped} files skipped (unsupported format)",
                font=T.font(12),
                text_color=T.TEXT_SECONDARY,
                anchor="w",
            ).grid(row=1, column=0, sticky="w")
        if errors:
            ctk.CTkLabel(
                inner,
                text=f"{len(errors)} errors occurred",
                font=T.font(12),
                text_color=T.ERROR,
                anchor="w",
            ).grid(row=2, column=0, sticky="w")

    def _on_error(self, message: str) -> None:
        self._running = False
        self._completed = False
        self.clear_btn.configure(state="normal")
        self._set_select_enabled(True)
        self._show_idle_bar()
        if self.input_paths:
            self._enable_convert()
            self.count_badge.configure(
                text=f"{len(self.input_paths)} image"
                f"{'s' if len(self.input_paths) != 1 else ''} selected"
            )
        else:
            self._disable_convert()
            self.count_badge.configure(text="0 images selected")
        self.error_label.configure(text=message)
        self._append_log(f"ERROR: {message}")
