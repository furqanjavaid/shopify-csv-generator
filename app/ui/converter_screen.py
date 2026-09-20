"""Bulk image converter screen."""

from __future__ import annotations

import os
import subprocess
import threading
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
        self.open_folder_btn = ctk.CTkButton(
            self.bottom_bar,
            text="Open Folder",
            command=self._open_output_folder,
            width=140,
            **T.primary_btn(),
        )
        self.convert_more_btn = ctk.CTkButton(
            self.bottom_bar,
            text="Convert More",
            command=self._reset,
            width=140,
            **T.secondary_btn(),
        )
        self.clear_btn.pack(side="right", padx=(8, 16), pady=12)
        self.convert_btn.pack(side="right", pady=12)
        self._disable_convert()

        self.body = attach_sidebar(self, app, "converter")

        T.page_title(
            self.body,
            "Image Converter",
            "Bulk convert images to WebP, PNG or JPG",
        )

        # Drop zone
        self.drop_zone = ctk.CTkFrame(
            self.body,
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

        self.pick_row = ctk.CTkFrame(self.body, fg_color="transparent")
        self.pick_row.pack(fill="x", pady=(0, 8))
        self.select_files_btn = T.ghost_button(
            self.pick_row, "Select Files", self._pick_images, width=120
        )
        self.select_files_btn.pack(side="left", padx=(0, 8))
        self.select_folder_btn = T.ghost_button(
            self.pick_row, "Select Folder", self._pick_folder, width=120
        )
        self.select_folder_btn.pack(side="left")

        self.selection_label = ctk.CTkLabel(
            self.body,
            text="",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
            wraplength=560,
        )
        self.selection_label.pack(fill="x", pady=(0, T.GRID_GAP))

        # Options card
        self.opts_card = T.card_frame(self.body)
        self.opts_card.pack(fill="x", pady=(0, T.GRID_GAP))
        opts_inner = ctk.CTkFrame(self.opts_card, fg_color="transparent")
        opts_inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)

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

        row3 = ctk.CTkFrame(opts_inner, fg_color="transparent")
        row3.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            row3, text="Max Width", font=T.font(13, "bold"), text_color=T.TEXT_PRIMARY
        ).pack(side="left")
        self.max_width_entry = T.styled_entry(
            row3, placeholder="e.g. 2048 — leave blank to keep original", width=280
        )
        self.max_width_entry.pack(side="right")

        row4 = ctk.CTkFrame(opts_inner, fg_color="transparent")
        row4.pack(fill="x")
        ctk.CTkLabel(
            row4, text="Output Folder", font=T.font(13, "bold"), text_color=T.TEXT_PRIMARY
        ).pack(side="left")
        self.browse_btn = T.secondary_button(
            row4, "Browse", self._browse_output, width=90
        )
        self.browse_btn.pack(side="right")
        self.output_entry = T.styled_entry(row4, placeholder="…/converted", width=320)
        self.output_entry.pack(side="right", padx=(0, 8))

        self.log_box = T.log_box(self.body, height=160)
        self.log_box.pack(fill="both", expand=True, pady=(0, 8))

        self.error_label = ctk.CTkLabel(
            self.body, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR,
            wraplength=560, anchor="w",
        )
        self.error_label.pack(fill="x")

        self.success_host = ctk.CTkFrame(self.body, fg_color="transparent")
        self.success_host.pack(fill="x", pady=(8, 0))

    def _on_quality(self, value: float) -> None:
        self.quality_label.configure(text=f"{int(value)}%")

    def _busy(self) -> bool:
        return self._running or self._completed

    def _disable_convert(self) -> None:
        self.convert_btn.configure(
            state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED
        )

    def _enable_convert(self) -> None:
        self.convert_btn.configure(
            state="normal", fg_color=T.ACCENT, text_color=T.BG_PRIMARY
        )

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
        self.open_folder_btn.pack_forget()
        self.convert_more_btn.pack_forget()
        self.clear_btn.pack(side="right", padx=(8, 16), pady=12)
        self.convert_btn.pack(side="right", pady=12)

    def _show_complete_bar(self) -> None:
        self.convert_btn.pack_forget()
        self.clear_btn.pack_forget()
        self.convert_more_btn.pack(side="right", padx=(8, 16), pady=12)
        self.open_folder_btn.pack(side="right", pady=12)

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
        self._append_log(
            f"Converting {total} image(s) → {self.format_var.get()}…"
        )

        fmt = self.format_var.get() or "WEBP"
        quality = int(self.quality_slider.get())
        thread = threading.Thread(
            target=self._run_convert,
            args=(list(self.input_paths), out, fmt, quality, max_w),
            daemon=True,
        )
        thread.start()

    def _on_progress(self, message: str, total: int) -> None:
        self._append_log(message)
        # Messages look like: "Converted 3/42: name.png"
        if message.startswith("Converted "):
            part = message[len("Converted "):].split(":", 1)[0].strip()
            if "/" in part:
                self.count_badge.configure(text=f"Converting... {part}")
                return
        # Fallback — count lines isn't available; keep last known total
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

        self.count_badge.configure(
            text=f"{converted} converted"
            + (f" · {skipped} skipped" if skipped else "")
        )
        from app.utils.task_history import save_task

        save_task("Convert", f"{result['converted']} images", "Success")
        self.clear_btn.configure(state="normal")
        self._show_complete_bar()

        if self.success_card is not None:
            self.success_card.destroy()

        self.success_card = T.card_frame(self.success_host)
        self.success_card.pack(fill="x")

        ctk.CTkLabel(
            self.success_card,
            text="✓",
            font=T.font(32, "bold"),
            text_color=T.SUCCESS,
        ).pack(pady=(T.CARD_PADDING, 4))

        ctk.CTkLabel(
            self.success_card,
            text=f"{converted} images converted successfully!",
            font=T.font(16, "bold"),
            text_color=T.TEXT_PRIMARY,
        ).pack()

        if skipped:
            ctk.CTkLabel(
                self.success_card,
                text=f"{skipped} files skipped (unsupported format)",
                font=T.font(13),
                text_color=T.TEXT_SECONDARY,
            ).pack(pady=(2, 0))

        if errors:
            ctk.CTkLabel(
                self.success_card,
                text=f"{len(errors)} errors occurred",
                font=T.font(13),
                text_color=T.ERROR,
            ).pack(pady=(2, 0))

        btn_row = ctk.CTkFrame(self.success_card, fg_color="transparent")
        btn_row.pack(pady=T.CARD_PADDING)

        ctk.CTkButton(
            btn_row,
            text="Open Output Folder",
            command=self._open_output_folder,
            width=180,
            **T.primary_btn(),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text="Convert More",
            command=self._reset,
            width=140,
            **T.secondary_btn(),
        ).pack(side="left")

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
