"""Bulk image converter screen."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.core.image_converter import (
    SUPPORTED_INPUT,
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

        # Bottom bar FIRST
        self.bottom_bar = ctk.CTkFrame(
            self,
            fg_color=T.BG_SURFACE_A,
            height=64,
            corner_radius=0,
            border_width=1,
            border_color=T.BORDER,
        )
        self.bottom_bar.pack(side="bottom", fill="x")
        self.bottom_bar.pack_propagate(False)

        self.count_badge = ctk.CTkLabel(
            self.bottom_bar,
            text="0 images selected",
            font=T.font(12, "bold"),
            text_color=T.ACCENT,
        )
        self.count_badge.pack(side="left", padx=16, pady=12)

        self.convert_btn = ctk.CTkButton(
            self.bottom_bar,
            text="Convert All →",
            command=self._start_convert,
            width=160,
            **T.primary_btn(),
        )
        self.clear_btn = ctk.CTkButton(
            self.bottom_bar,
            text="Clear",
            command=self._clear,
            width=100,
            **T.secondary_btn(),
        )
        self.clear_btn.pack(side="right", padx=(8, 16), pady=12)
        self.convert_btn.pack(side="right", pady=12)
        self._disable_convert()

        body = attach_sidebar(self, app, "converter")

        T.page_title(
            body,
            "Image Converter",
            "Bulk convert images to WebP, PNG or JPG",
        )

        # Drop zone
        self.drop_zone = ctk.CTkFrame(
            body,
            fg_color=T.BG_SURFACE_A,
            corner_radius=T.BORDER_RADIUS,
            border_width=2,
            border_color=T.BORDER,
            height=130,
        )
        self.drop_zone.pack(fill="x", pady=(0, T.GRID_GAP))
        self.drop_zone.pack_propagate(False)

        dz = ctk.CTkFrame(self.drop_zone, fg_color="transparent")
        dz.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(dz, text="🖼️", font=T.font(28)).pack()
        ctk.CTkLabel(
            dz,
            text="Click to select images or a folder",
            font=T.font_tuple(T.H3),
            text_color=T.TEXT_PRIMARY,
            wraplength=480,
        ).pack(pady=(4, 2))
        ctk.CTkLabel(
            dz,
            text=".jpg  ·  .jpeg  ·  .png  ·  .webp  ·  .bmp",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
        ).pack()

        for w in (self.drop_zone, dz, *dz.winfo_children()):
            w.bind("<Button-1>", lambda _e: self._pick_images())

        pick_row = ctk.CTkFrame(body, fg_color="transparent")
        pick_row.pack(fill="x", pady=(0, 8))
        T.ghost_button(pick_row, "Select Files", self._pick_images, width=120).pack(
            side="left", padx=(0, 8)
        )
        T.ghost_button(pick_row, "Select Folder", self._pick_folder, width=120).pack(
            side="left"
        )

        self.selection_label = ctk.CTkLabel(
            body,
            text="",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
            wraplength=560,
        )
        self.selection_label.pack(fill="x", pady=(0, T.GRID_GAP))

        # Options card
        opts = T.card_frame(body)
        opts.pack(fill="x", pady=(0, T.GRID_GAP))
        opts_inner = ctk.CTkFrame(opts, fg_color="transparent")
        opts_inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        # Format
        row1 = ctk.CTkFrame(opts_inner, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            row1, text="Output Format", font=T.font(13, "bold"),
            text_color=T.TEXT_PRIMARY,
        ).pack(side="left")
        self.format_var = ctk.StringVar(value="WEBP")
        self.format_btn = ctk.CTkSegmentedButton(
            row1,
            values=["WEBP", "PNG", "JPG"],
            variable=self.format_var,
            font=T.font(12),
            fg_color=T.BG_SURFACE_B,
            selected_color=T.ACCENT,
            selected_hover_color=T.ACCENT_HOVER,
            unselected_color=T.BG_SURFACE_B,
            unselected_hover_color=T.BORDER,
            text_color=T.TEXT_PRIMARY,
        )
        self.format_btn.set("WEBP")
        self.format_btn.pack(side="right")

        # Quality
        row2 = ctk.CTkFrame(opts_inner, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            row2, text="Quality", font=T.font(13, "bold"), text_color=T.TEXT_PRIMARY
        ).pack(side="left")
        self.quality_label = ctk.CTkLabel(
            row2, text="85%", font=T.font(13), text_color=T.TEXT_SECONDARY, width=40
        )
        self.quality_label.pack(side="right")
        self.quality_slider = ctk.CTkSlider(
            row2,
            from_=60,
            to=100,
            number_of_steps=40,
            command=self._on_quality,
            progress_color=T.ACCENT,
            button_color=T.ACCENT,
            button_hover_color=T.ACCENT_HOVER,
            fg_color=T.BORDER,
            width=200,
        )
        self.quality_slider.set(85)
        self.quality_slider.pack(side="right", padx=(0, 8))

        # Max width
        row3 = ctk.CTkFrame(opts_inner, fg_color="transparent")
        row3.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            row3, text="Max Width", font=T.font(13, "bold"), text_color=T.TEXT_PRIMARY
        ).pack(side="left")
        self.max_width_entry = T.styled_entry(
            row3, placeholder="e.g. 2048 — leave blank to keep original", width=280
        )
        self.max_width_entry.pack(side="right")

        # Output folder
        row4 = ctk.CTkFrame(opts_inner, fg_color="transparent")
        row4.pack(fill="x")
        ctk.CTkLabel(
            row4, text="Output Folder", font=T.font(13, "bold"), text_color=T.TEXT_PRIMARY
        ).pack(side="left")
        T.secondary_button(row4, "Browse", self._browse_output, width=90).pack(
            side="right"
        )
        self.output_entry = T.styled_entry(row4, placeholder="…/converted", width=320)
        self.output_entry.pack(side="right", padx=(0, 8))

        # Log
        self.log_box = T.log_box(body, height=160)
        self.log_box.pack(fill="both", expand=True, pady=(0, 8))

        self.error_label = ctk.CTkLabel(
            body, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR,
            wraplength=560, anchor="w",
        )
        self.error_label.pack(fill="x")

    def _on_quality(self, value: float) -> None:
        self.quality_label.configure(text=f"{int(value)}%")

    def _disable_convert(self) -> None:
        self.convert_btn.configure(
            state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED
        )

    def _enable_convert(self) -> None:
        self.convert_btn.configure(
            state="normal", fg_color=T.ACCENT, text_color=T.BG_PRIMARY
        )

    def _update_count(self) -> None:
        n = len(self.input_paths)
        self.count_badge.configure(text=f"{n} image{'s' if n != 1 else ''} selected")
        if n:
            self.selection_label.configure(text=f"{n} images selected")
            self._enable_convert()
        else:
            self.selection_label.configure(text="")
            self._disable_convert()

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
        if self._running:
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
        if self._running:
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
        folder = filedialog.askdirectory(title="Output folder")
        if folder:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, folder)

    def _clear(self) -> None:
        if self._running:
            return
        self.input_paths = []
        self._update_count()
        self.output_entry.delete(0, "end")
        self.error_label.configure(text="")
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"› {message.rstrip()}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _start_convert(self) -> None:
        if self._running or not self.input_paths:
            return
        out = self.output_entry.get().strip()
        if not out:
            self.error_label.configure(text="Choose an output folder.")
            return

        max_w = None
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
        self._running = True
        self._disable_convert()
        self._append_log(
            f"Converting {len(self.input_paths)} image(s) → {self.format_var.get()}…"
        )

        fmt = self.format_var.get() or "WEBP"
        quality = int(self.quality_slider.get())
        thread = threading.Thread(
            target=self._run_convert,
            args=(list(self.input_paths), out, fmt, quality, max_w),
            daemon=True,
        )
        thread.start()

    def _run_convert(
        self,
        paths: list[str],
        out: str,
        fmt: str,
        quality: int,
        max_w: int | None,
    ) -> None:
        def progress(msg: str) -> None:
            self.after(0, lambda m=msg: self._append_log(m))

        try:
            result = convert_images(
                paths,
                out,
                target_format=fmt,
                quality=quality,
                max_width=max_w,
                progress=progress,
            )
            self.after(0, lambda: self._on_done(result, out))
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._on_error(str(exc)))

    def _on_done(self, result: dict, out: str) -> None:
        self._running = False
        self._enable_convert()
        err_n = len(result.get("errors") or [])
        self._append_log(
            f"Done — {result['converted']} converted, "
            f"{result['skipped']} skipped, {err_n} error(s)"
        )
        self._append_log(f"Output: {out}")
        for err in (result.get("errors") or [])[:10]:
            self._append_log(f"ERROR: {err}")

    def _on_error(self, message: str) -> None:
        self._running = False
        self._enable_convert()
        self.error_label.configure(text=message)
        self._append_log(f"ERROR: {message}")
