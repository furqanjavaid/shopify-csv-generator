"""URL / collection scraper — themed UI with options + live log.

Modes:
  1. Universal Extractor Full (default) — multi-URL extract → shopify_import.csv
  2. Universal Extractor Pilot — capped pilot extract → shopify_import.csv
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import threading
import time
import tkinter.messagebox as messagebox
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.ui import theme as T
from app.ui.components import (
    Card,
    Combobox,
    DangerButton,
    GoldProgressBar,
    LogBox,
    OutlineButton,
    PageHeader,
    PrimaryButton,
    StatusBar,
)
from app.ui.icons import load_icon
from app.ui.sidebar import attach_sidebar
from app.utils.helpers import is_valid_url, output_filename_from_url
from app.utils.job_status import TOOL_URL_SCRAPER
from sentivo_extractor.core.output_layout import (
    domain_artifact_path,
    domain_folder_name,
    domain_output_dir,
    ensure_domain_dir,
)

MODE_UNIVERSAL = "Universal Extractor Pilot"
MODE_UNIVERSAL_FULL = "Universal Extractor Full"
ALL_CATEGORIES_LABEL = "All Categories (Full Site)"
ALL_CATEGORIES_KEY = "__ALL__"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "gui_extract"

LOG_COLOR_INFO = "#8B7340"
LOG_COLOR_WARN = "#E88C00"
LOG_COLOR_ERROR = "#CC3333"
LOG_MIN_HEIGHT = 160
PROGRESS_AMBER = "#C9A84C"
SEED_URL_PLACEHOLDER = "https://your-store.com/collections/all"

PILOT_OUTPUT_BUTTONS = (
    ("Open output folder", "folder"),
    ("Open shopify_import.csv", "csv"),
    ("Open production_summary.xlsx", "summary"),
    ("Open qa/sample_review.xlsx", "qa"),
    ("Open shopify_pre_import_validation.xlsx", "preimport"),
)
FULL_OUTPUT_BUTTONS = (
    ("Open output folder", "folder"),
    ("Open shopify_import.csv", "csv"),
    ("Open production_summary.xlsx", "summary"),
    ("Open shopify_pre_import_validation.xlsx", "preimport"),
    ("Open validation_report.xlsx", "validation"),
    ("Open images_manifest.csv", "images"),
)


class ScraperScreen(ctk.CTkFrame):
    """Scrape catalog URLs via Universal Extractor (Full or Pilot)."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app
        self.parsed_data: dict | None = None
        self.source_url: str = ""
        self.suggested_filename: str = "shopify_products.csv"
        self._universal_output_dir: Path | None = None
        self._universal_run_kind: str = "full"  # "pilot" | "full"
        self._output_folder = str(DEFAULT_OUTPUT_DIR)
        self._extract_proc: subprocess.Popen | None = None
        self._stop_requested = False
        self._running = False
        self._reprocessing = False
        self._elapsed_after = None

        # Slim status strip first (pack before expanding shell so it stays a hairline footer)
        self.status_bar = StatusBar(self)
        self.status_bar.pack(side="bottom", fill="x")
        self.count_badge = self.status_bar.left
        self.status_right = self.status_bar.right
        self.next_btn = None
        self.clear_btn = None
        self._status_spin_i = 0
        self._status_spin_frames = ("◐", "◓", "◑", "◒")

        body = attach_sidebar(self, app, "scraper")
        body.grid_columnconfigure(0, weight=1)

        PageHeader(
            body,
            "URL Scraper",
            "Scrape products and metadata from ecommerce stores using seed URLs.",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        # ── Scraper Configuration card ────────────────────
        config = Card(
            body,
            title="Scraper Configuration",
            subtitle="Configure your scraping settings and provide seed URLs to start.",
            icon="link",
            expand_body=False,
            border_width=1,
            border_color=T.BORDER,
            fg_color=T.BG_SURFACE_A,
        )
        config.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        cfg = config.body
        cfg.configure(fg_color="transparent")
        cfg.grid_columnconfigure((0, 1), weight=1, uniform="scraper_cfg")

        # Left column: Mode + Max Products
        left_col = ctk.CTkFrame(cfg, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="new", padx=(0, 10), pady=(0, 4))
        left_col.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            left_col, text="Mode", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        self.mode_var = ctk.StringVar(value=MODE_UNIVERSAL_FULL)
        # Themed Combobox (white field + navy text + soft burgundy hover) — matches mockup
        self.mode_menu = Combobox(
            left_col,
            [MODE_UNIVERSAL_FULL, MODE_UNIVERSAL],
            variable=self.mode_var,
            command=self._on_mode_changed,
            width=280,
        )
        self.mode_menu.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.mode_hint = ctk.CTkLabel(
            left_col, text="", font=T.font_tuple(T.CAPTION), text_color=T.TEXT_MUTED, anchor="w"
        )
        self.mode_hint.grid(row=2, column=0, sticky="w", pady=(2, 0))

        self.universal_opts = ctk.CTkFrame(cfg, fg_color="transparent")
        # Placeholder grid later; max sits in left, vendor in right via nested frames
        self._max_host = ctk.CTkFrame(left_col, fg_color="transparent")
        self._max_host.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self._max_host.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self._max_host,
            text="Max Products (per store)",
            font=T.font(12, "bold"),
            text_color=T.TEXT_MUTED,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.max_products_entry = T.styled_entry(self._max_host, placeholder="empty = unlimited")
        self.max_products_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        # Right column: Output Folder + Custom Vendor
        right_col = ctk.CTkFrame(cfg, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="new", padx=(10, 0), pady=(0, 4))
        right_col.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            right_col, text="Output Folder", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        # Entry + Browse on one aligned row
        out_row = ctk.CTkFrame(right_col, fg_color="transparent")
        out_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        out_row.grid_columnconfigure(0, weight=1)
        self.output_entry = T.styled_entry(out_row, placeholder="Select output folder…")
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.output_entry.insert(0, self._output_folder)
        self.browse_btn = OutlineButton(
            out_row,
            "Browse",
            self._browse_output_folder,
            icon="folder",
            width=110,
            height=T.INPUT_HEIGHT,
        )
        self.browse_btn.grid(row=0, column=1, sticky="e")

        self._vendor_host = ctk.CTkFrame(right_col, fg_color="transparent")
        self._vendor_host.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self._vendor_host.grid_columnconfigure(0, weight=1)
        self.var_custom_vendor = ctk.BooleanVar(value=False)
        self.vendor_checkbox = ctk.CTkCheckBox(
            self._vendor_host,
            text="Use Custom Vendor Name",
            variable=self.var_custom_vendor,
            command=self._on_vendor_toggle,
            font=T.font_tuple(T.LABEL),
            text_color=T.TEXT_SECONDARY,
            fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER,
            border_color=T.BORDER,
            checkmark_color=T.BTN_ON_ACCENT,
            corner_radius=T.BORDER_RADIUS,
        )
        self.vendor_checkbox.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            self._vendor_host,
            text="Apply a custom vendor name to all scraped products.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
            wraplength=360,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.vendor_entry = T.styled_entry(self._vendor_host, placeholder="Vendor name for all products…")
        self.vendor_entry.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.vendor_entry.grid_remove()

        # Keep universal_opts as a grouping flag host (show/hide max+vendor hosts)
        self.universal_opts = ctk.CTkFrame(cfg, fg_color="transparent")
        self.universal_opts.grid_remove()  # not used for layout; hosts toggled directly
        self.pilot_hint = ctk.CTkLabel(
            left_col,
            text="Pilot mode caps at 20 products per domain for QA review.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        )
        self.pilot_hint.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.pilot_hint.grid_remove()

        # Seed URLs
        ctk.CTkLabel(
            cfg, text="Seed URLs", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w"
        ).grid(row=1, column=0, sticky="w", pady=(2, 2))
        self.fetch_categories_btn = T.secondary_button(
            cfg,
            "Fetch Categories →",
            self._fetch_categories,
            width=160,
            height=28,
        )
        self.fetch_categories_btn.grid(row=1, column=1, sticky="e", pady=(2, 2))
        self.url_text = ctk.CTkTextbox(
            cfg,
            height=72,
            wrap="none",
            **T.textbox_style(),
        )
        self.url_text.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._seed_placeholder_active = False
        self._install_seed_url_placeholder()

        # Category filter (populated by Fetch Categories)
        self._category_options: dict[str, str] = {ALL_CATEGORIES_LABEL: ALL_CATEGORIES_KEY}
        self._fetching_categories = False
        self.category_row = ctk.CTkFrame(cfg, fg_color="transparent")
        self.category_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.category_row.grid_columnconfigure(0, weight=1)
        self.category_status = ctk.CTkLabel(
            self.category_row,
            text="",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        )
        self.category_status.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        self.category_menu = Combobox(
            self.category_row,
            [ALL_CATEGORIES_LABEL],
            command=self._on_category_selected,
            width=420,
        )
        self.category_menu.grid(row=1, column=0, sticky="ew")
        self.category_menu.set(ALL_CATEGORIES_LABEL)

        self.platform_label = ctk.CTkLabel(
            cfg, text="", font=T.font_tuple(T.CAPTION), text_color=T.GOLD, anchor="w"
        )
        self.platform_label.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # Run / Stop
        btn_row = ctk.CTkFrame(cfg, fg_color="transparent")
        btn_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        btn_row.grid_columnconfigure((0, 1), weight=1)
        self.scrape_btn = PrimaryButton(
            btn_row, "Run Scraper →", self._start_scrape, icon="play", width=200
        )
        self.scrape_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.stop_btn = DangerButton(btn_row, "Stop →", self._stop_scrape, icon="square", width=140)
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.stop_btn.set_enabled(False)
        self.reprocess_btn = T.secondary_button(
            btn_row,
            "Reprocess Existing Data →",
            self._start_reprocess,
            height=T.BTN_HEIGHT,
        )
        self.reprocess_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        # Progress + percentage (always visible; idle = 0%)
        self.progress_section = ctk.CTkFrame(cfg, fg_color="transparent")
        self.progress_section.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(0, 0))
        self.progress_section.grid_columnconfigure(0, weight=1)
        self.loading_label = ctk.CTkLabel(
            self.progress_section,
            text="",
            font=T.font_tuple(T.CAPTION),
            text_color=T.GOLD,
            anchor="w",
        )
        self.loading_label.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        self.loading_label.grid_remove()
        self.progress = GoldProgressBar(self.progress_section, height=10)
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self.progress_pct = ctk.CTkLabel(
            self.progress_section,
            text="0%",
            font=T.font(12, "bold"),
            text_color=T.TEXT_MUTED,
            width=44,
            anchor="e",
        )
        self.progress_pct.grid(row=1, column=1, sticky="e")

        self.error_label = ctk.CTkLabel(
            cfg, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR, anchor="w"
        )
        self.error_label.grid(row=9, column=0, columnspan=2, sticky="ew")
        self.strategy_label = ctk.CTkLabel(
            cfg, text="", font=T.font_tuple(T.CAPTION), text_color=T.TEXT_SECONDARY, anchor="w"
        )
        self.strategy_label.grid(row=10, column=0, columnspan=2, sticky="ew")
        self.output_actions = ctk.CTkFrame(cfg, fg_color="transparent")
        self.output_actions.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.output_actions.grid_remove()
        self._output_buttons: list[ctk.CTkButton] = []

        # ── Bottom: Log + Results ─────────────────────────
        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.grid_columnconfigure(0, weight=7)
        bottom.grid_columnconfigure(1, weight=3)

        log_card = Card(
            bottom,
            title="Log Area",
            subtitle="Live output from the scraping process.",
            icon="clock",
            expand_body=True,
        )
        log_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        log_card.body.grid_columnconfigure(0, weight=1)
        log_card.body.grid_rowconfigure(1, weight=1)
        log_head = ctk.CTkFrame(log_card.body, fg_color="transparent")
        log_head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        log_head.grid_columnconfigure(0, weight=1)
        self.clear_btn = OutlineButton(
            log_head, "Clear Log", self._clear_results, icon="trash", width=120, height=28
        )
        self.clear_btn.grid(row=0, column=0, sticky="e")
        self.log_view = LogBox(log_card.body, height=LOG_MIN_HEIGHT)
        self.log_view.grid(row=1, column=0, sticky="nsew")
        self.log_box = self.log_view.textbox

        results = Card(
            bottom,
            title="Results & Status",
            subtitle="Live run summary",
            icon="bar-chart",
            expand_body=True,
        )
        results.grid(row=0, column=1, sticky="nsew")
        results.body.grid_columnconfigure(0, weight=1)
        self.result_labels = {}
        self.result_icons = {}
        result_rows = (
            ("seeds", "Seed URLs Processed", "link"),
            ("products", "Products Found", "file"),
            ("output", "Output Path", "folder"),
            ("elapsed", "Elapsed Time", "clock"),
            ("status", "Status", "check"),
        )
        for i, (key, title, icon_name) in enumerate(result_rows):
            row = ctk.CTkFrame(results.body, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", pady=4)
            row.grid_columnconfigure(2, weight=1)
            img = load_icon(icon_name, size=14, color="navy")
            self.result_icons[key] = img
            ctk.CTkLabel(row, text="", image=img, width=18).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                row, text=title, font=T.font(12), text_color=T.TEXT_MUTED, anchor="w"
            ).grid(row=0, column=1, sticky="w", padx=(6, 8))
            lab = ctk.CTkLabel(
                row, text="—", font=T.font(12, "bold"), text_color=T.TEXT_PRIMARY, anchor="e"
            )
            lab.grid(row=0, column=2, sticky="e")
            self.result_labels[key] = lab
        self.next_btn = PrimaryButton(
            results.body,
            "Generate Final CSV →",
            self._go_mapping,
            icon="arrow-right",
            width=220,
        )
        self.next_btn.grid(row=6, column=0, sticky="ew", pady=(16, 0))

        # Hidden preview frame for legacy compatibility
        self.preview_frame = ctk.CTkScrollableFrame(body, fg_color=T.BG_SURFACE_B, height=1)
        self.preview_frame.grid_remove()

        self._disable_actions()
        self._on_mode_changed(MODE_UNIVERSAL_FULL)
        self._restore_running_job_ui()

    # ── Mode ──────────────────────────────────────────────

    def _is_universal_pilot(self) -> bool:
        return self.mode_var.get() == MODE_UNIVERSAL

    def _is_universal_full(self) -> bool:
        return self.mode_var.get() == MODE_UNIVERSAL_FULL

    def _on_mode_changed(self, _value: str | None = None) -> None:
        if self._is_universal_full():
            self.scrape_btn.configure(text="Run Scraper →")
            self.next_btn.configure(text="Generate Final CSV →")
            self.mode_hint.configure(
                text="Full → shopify_import.csv (no MappingScreen)"
            )
            self._show_universal_opts(show_pilot_hint=False)
            self._disable_actions()
            self.clear_btn.configure(state="normal")
        else:
            self.scrape_btn.configure(text="Run Pilot →")
            self.next_btn.configure(text="Generate Final CSV →")
            self.mode_hint.configure(
                text="Pilot → shopify_import.csv (no MappingScreen)"
            )
            self._show_universal_opts(show_pilot_hint=True)
            self._disable_actions()
            self.clear_btn.configure(state="normal")

    def _show_universal_opts(self, *, show_pilot_hint: bool) -> None:
        try:
            self._max_host.grid()
        except Exception:
            pass
        try:
            self._vendor_host.grid()
        except Exception:
            pass
        if show_pilot_hint:
            self.pilot_hint.grid()
        else:
            self.pilot_hint.grid_remove()

    def _on_vendor_toggle(self) -> None:
        """Show/hide custom vendor text field based on checkbox."""
        if self.var_custom_vendor.get():
            self.vendor_entry.grid()
        else:
            self.vendor_entry.grid_remove()

    def _read_custom_vendor(self) -> str | None:
        """Return custom vendor when checkbox is on and name is non-empty."""
        if not self.var_custom_vendor.get():
            return None
        name = (self.vendor_entry.get() or "").strip()
        return name or None

    def _browse_output_folder(self) -> None:
        folder = filedialog.askdirectory(
            title="Select output folder",
            initialdir=self._output_folder or str(PROJECT_ROOT),
        )
        if folder:
            self._output_folder = folder
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, folder)

    def _rebuild_output_buttons(self, kind: str) -> None:
        for btn in self._output_buttons:
            btn.destroy()
        self._output_buttons.clear()
        specs = FULL_OUTPUT_BUTTONS if kind == "full" else PILOT_OUTPUT_BUTTONS
        for col, (label, key) in enumerate(specs):
            btn = ctk.CTkButton(
                self.output_actions,
                text=label,
                width=200,
                height=28,
                command=lambda k=key: self._open_universal_artifact(k),
                **T.secondary_btn(),
            )
            btn.grid(row=0, column=col, sticky="w", padx=(0, 8), pady=(4, 0))
            self._output_buttons.append(btn)

    def _show_output_actions(self) -> None:
        self.output_actions.grid()

    def _hide_output_actions(self) -> None:
        self.output_actions.grid_remove()
    # ── Actions bar ───────────────────────────────────────

    def _disable_actions(self) -> None:
        if hasattr(self.next_btn, "set_enabled"):
            self.next_btn.set_enabled(False)
        else:
            self.next_btn.configure(state="disabled")
        if hasattr(self.clear_btn, "set_enabled"):
            self.clear_btn.set_enabled(False)
        else:
            self.clear_btn.configure(state="disabled")

    def _enable_actions(self) -> None:
        if hasattr(self.next_btn, "set_enabled"):
            self.next_btn.set_enabled(False)
        else:
            self.next_btn.configure(state="disabled")
        if hasattr(self.clear_btn, "set_enabled"):
            self.clear_btn.set_enabled(True)
        else:
            self.clear_btn.configure(state="normal")

    def _configure_log_tags(self) -> None:
        """Ensure LogBox / textbox tags match theme level colors."""
        view = getattr(self, "log_view", None)
        if view is not None and hasattr(view, "_configure_tags"):
            view._configure_tags()
            return
        c = T.current_colors()
        try:
            tb = self.log_box._textbox  # noqa: SLF001
            tb.tag_configure("ts", foreground=c["TEXT_MUTED"])
            tb.tag_configure("info", foreground=c["SUCCESS"])
            tb.tag_configure("warn", foreground=c["WARNING"])
            tb.tag_configure("error", foreground=c["ERROR"])
            tb.tag_configure("msg", foreground=c["INPUT_TEXT"])
        except Exception:
            pass

    @staticmethod
    def _log_level_tag(message: str) -> str:
        low = (message or "").lower()
        if "error" in low or "traceback" in low or low.startswith("fail"):
            return "error"
        if "warning" in low or " warn" in low or low.startswith("warn"):
            return "warn"
        if "info" in low:
            return "info"
        return "info"

    def _show_progress(self, text: str = "") -> None:
        """Show running progress: indeterminate bar + '…' when no numeric fraction."""
        if text:
            self.loading_label.configure(text=text[:120])
            self.loading_label.grid()
        self.progress.grid()
        self.progress_pct.grid()
        self.progress_pct.configure(text="…")
        try:
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        except Exception:
            pass

    def _hide_progress(self) -> None:
        """Reset to idle: determinate 0% (bar stays visible)."""
        try:
            self.progress.stop()
        except Exception:
            pass
        try:
            self.progress.configure(mode="determinate")
            self.progress.set(0)
        except Exception:
            pass
        self.progress_pct.configure(text="0%")
        self.loading_label.grid_remove()
        self.loading_label.configure(text="")

    def _set_progress_pct(self, pct: float | None) -> None:
        """Update determinate progress when a real fraction is known (0–1)."""
        try:
            if pct is None:
                self.progress_pct.configure(text="…")
                return
            pct = max(0.0, min(1.0, float(pct)))
            try:
                self.progress.stop()
            except Exception:
                pass
            self.progress.configure(mode="determinate")
            self.progress.set(pct)
            self.progress_pct.configure(text=f"{int(round(pct * 100))}%")
        except Exception:
            pass

    def _set_status_badge(self, text: str) -> None:
        try:
            self.count_badge.configure(text=text)
        except Exception:
            pass

    def _update_results(
        self,
        *,
        seeds: str | None = None,
        products: str | None = None,
        output: str | None = None,
        elapsed: str | None = None,
        status: str | None = None,
        status_color: str | None = None,
    ) -> None:
        labels = getattr(self, "result_labels", None) or {}
        mapping = {
            "seeds": seeds,
            "products": products,
            "output": output,
            "elapsed": elapsed,
            "status": status,
        }
        for key, value in mapping.items():
            if value is None or key not in labels:
                continue
            try:
                kw = {"text": value}
                if key == "status" and status_color:
                    kw["text_color"] = status_color
                labels[key].configure(**kw)
            except Exception:
                pass
        right_parts = []
        if elapsed:
            right_parts.append(f"Elapsed: {elapsed}")
        if products:
            right_parts.append(f"{products} products" if str(products).isdigit() else str(products))
        if seeds:
            right_parts.append(str(seeds))
        try:
            self.status_right.configure(text=" | ".join(right_parts))
        except Exception:
            pass

    def _append_log(self, message: str) -> None:
        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.append_log(TOOL_URL_SCRAPER, message)
        self._append_log_ui(message)

    def _append_log_ui(self, message: str) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        view = getattr(self, "log_view", None)
        if view is not None and hasattr(view, "append"):
            try:
                view.append(message.rstrip(), self._log_level_tag(message))
                return
            except Exception:
                pass
        line = f"› {message.rstrip()}\n"
        tag = self._log_level_tag(message)
        try:
            self.log_box.configure(state="normal")
            tb = getattr(self.log_box, "_textbox", None)
            if tb is not None:
                tb.insert("end", line, tag)
                tb.see("end")
            else:
                self.log_box.insert("end", line)
                self.log_box.see("end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass

    def _emit_log(self, message: str) -> None:
        """Thread-safe: persist log + update scraper UI if that tab is open."""
        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.append_log(TOOL_URL_SCRAPER, message)
        self._post_to_scraper_ui(lambda s, m=message: s._append_log_ui(m))

    def _post_to_scraper_ui(self, callback, *, fallback=None) -> None:
        app = self.app

        def _run() -> None:
            screen = getattr(app, "current_screen", None)
            if screen is not None and screen.__class__.__name__ == "ScraperScreen":
                try:
                    callback(screen)
                    return
                except Exception:
                    pass
            if fallback is not None:
                try:
                    fallback()
                except Exception:
                    pass

        try:
            app.after(0, _run)
        except Exception:
            if fallback is not None:
                try:
                    fallback()
                except Exception:
                    pass

    def _store_mark_complete(self, product_count: int) -> None:
        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.set_complete(product_count, "URL Scraper", tool_id=TOOL_URL_SCRAPER)
        from app.utils.job_status import notify_extraction_complete

        notify_extraction_complete(int(product_count or 0), parent=self.app)

    def _store_mark_stopped(self) -> None:
        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.set_stopped("URL Scraper", tool_id=TOOL_URL_SCRAPER)

    def _store_mark_error(self, message: str) -> None:
        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.append_log(TOOL_URL_SCRAPER, f"ERROR: {message}")
            # Release the running slot so Run can be clicked again
            job = store.get(TOOL_URL_SCRAPER)
            job.state = "idle"
            job.proc = None
            job.stop_requested = False
            job.message = "Ready"
            job.updated_at = time.time()
            store.message = "Ready"

    def _job_stop_requested(self) -> bool:
        store = getattr(self.app, "job_status", None)
        if store is not None and store.stop_requested(TOOL_URL_SCRAPER):
            return True
        return bool(self._stop_requested)

    def _snapshot_ui_meta(self) -> None:
        store = getattr(self.app, "job_status", None)
        if store is None:
            return
        try:
            loading = str(self.loading_label.cget("text") or "")
        except Exception:
            loading = "Running…"
        store.set_ui_meta(
            TOOL_URL_SCRAPER,
            {
                "mode": self.mode_var.get(),
                "urls": self.url_text.get("1.0", "end-1c"),
                "output": self.output_entry.get(),
                "max_products": self.max_products_entry.get(),
                "custom_vendor": bool(self.var_custom_vendor.get()),
                "vendor": self.vendor_entry.get(),
                "loading": loading,
            },
        )

    def _apply_running_chrome(self, running: bool) -> None:
        """Run/Stop button visuals + input lock (does not touch job store)."""
        self._running = running
        self._set_inputs_locked(running)
        if hasattr(self.scrape_btn, "set_enabled"):
            self.scrape_btn.set_enabled(not running)
        else:
            self.scrape_btn.configure(state="disabled" if running else "normal")
        if hasattr(self.stop_btn, "set_enabled"):
            self.stop_btn.set_enabled(running)
        else:
            self.stop_btn.configure(state="normal" if running else "disabled")
        if hasattr(self, "reprocess_btn"):
            try:
                self.reprocess_btn.configure(
                    state="disabled" if running else "normal"
                )
            except Exception:
                pass

    def _set_running(self, running: bool) -> None:
        """Toggle Run/Stop button states and lock/unlock inputs."""
        self._apply_running_chrome(running)
        if running:
            self._snapshot_ui_meta()
            self._start_elapsed_tick()
        else:
            self._reprocessing = False
            self._extract_proc = None
            store = getattr(self.app, "job_status", None)
            if store is not None:
                store.set_proc(TOOL_URL_SCRAPER, None)
            self._stop_elapsed_tick()

    def _start_elapsed_tick(self) -> None:
        self._stop_elapsed_tick()
        self._tick_elapsed()

    def _stop_elapsed_tick(self) -> None:
        aid = getattr(self, "_elapsed_after", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
            self._elapsed_after = None

    def _tick_elapsed(self) -> None:
        store = getattr(self.app, "job_status", None)
        if store is None or not store.is_running(TOOL_URL_SCRAPER):
            return
        job = store.get(TOOL_URL_SCRAPER)
        elapsed = job.elapsed_text()
        meta = job.ui_meta or {}
        spin = self._status_spin_frames[self._status_spin_i % len(self._status_spin_frames)]
        self._status_spin_i += 1
        url_hint = (meta.get("current_url") or self.source_url or "").strip()
        if len(url_hint) > 48:
            url_hint = url_hint[:45] + "…"
        products = job.product_count
        left_parts = [f"{spin} Running..."]
        if url_hint:
            left_parts.append(url_hint)
        if products is not None:
            left_parts.append(f"{products} found")
        try:
            self.count_badge.configure(text="  ·  ".join(left_parts))
        except Exception:
            return
        seeds_meta = meta.get("seed_total")
        seeds_done = meta.get("seed_done")
        right_parts = [f"Elapsed: {elapsed}"]
        if products is not None:
            right_parts.append(f"{products} products")
        if seeds_meta and seeds_done is not None:
            right_parts.append(f"{seeds_done} of {seeds_meta} URLs")
        try:
            self.status_right.configure(text=" | ".join(right_parts))
        except Exception:
            pass
        self._update_results(
            elapsed=elapsed,
            products=str(products) if products is not None else None,
            status="Scraping in progress...",
            status_color=T.SUCCESS,
        )
        self._elapsed_after = self.after(1000, self._tick_elapsed)

    def _restore_running_job_ui(self) -> None:
        """Rehydrate this tab when navigated back while a job is still running."""
        store = getattr(self.app, "job_status", None)
        if store is None or not store.is_running(TOOL_URL_SCRAPER):
            return
        job = store.get(TOOL_URL_SCRAPER)
        meta = job.ui_meta or {}

        # Restore inputs from snapshot (then lock)
        try:
            mode = meta.get("mode")
            if mode:
                self.mode_var.set(mode)
                self._on_mode_changed(mode)
            urls = meta.get("urls")
            if urls is not None:
                self.url_text.configure(state="normal", text_color=T.INPUT_TEXT)
                self.url_text.delete("1.0", "end")
                self.url_text.insert("1.0", urls)
                self._seed_placeholder_active = False
            if meta.get("output") is not None:
                self.output_entry.delete(0, "end")
                self.output_entry.insert(0, str(meta.get("output") or ""))
                self._output_folder = str(meta.get("output") or self._output_folder)
            if meta.get("max_products") is not None:
                self.max_products_entry.delete(0, "end")
                self.max_products_entry.insert(0, str(meta.get("max_products") or ""))
            if meta.get("custom_vendor"):
                self.var_custom_vendor.set(True)
                self._on_vendor_toggle()
                self.vendor_entry.delete(0, "end")
                self.vendor_entry.insert(0, str(meta.get("vendor") or ""))
        except Exception:
            pass

        # Restore log (colored)
        try:
            self.log_box.configure(state="normal")
            tb = getattr(self.log_box, "_textbox", None)
            if tb is not None:
                tb.delete("1.0", "end")
                for line in job.log_lines:
                    tag = self._log_level_tag(line)
                    tb.insert("end", f"› {line}\n", tag)
                tb.see("end")
            else:
                self.log_box.delete("1.0", "end")
                for line in job.log_lines:
                    self.log_box.insert("end", f"› {line}\n")
                self.log_box.see("end")
            self.log_box.configure(state="disabled")
        except Exception:
            pass

        self._stop_requested = bool(job.stop_requested)
        self._extract_proc = job.proc
        self._apply_running_chrome(True)
        self._disable_actions()
        self.clear_btn.configure(state="disabled")

        loading = str(meta.get("loading") or "Running…")
        self._show_progress(loading[:80] if loading else "Running…")
        self._start_elapsed_tick()

    def _set_inputs_locked(self, locked: bool) -> None:
        """Disable seed/output/mode controls while a job is running."""
        state = "disabled" if locked else "normal"
        try:
            self.url_text.configure(state=state)
        except Exception:
            pass
        for widget in (
            self.mode_menu,
            self.output_entry,
            self.browse_btn,
            self.max_products_entry,
            self.vendor_checkbox,
            self.vendor_entry,
            self.fetch_categories_btn,
            self.category_menu,
        ):
            try:
                widget.configure(state=state)
            except Exception:
                pass

    def _stop_scrape(self) -> None:
        """Terminate the running extract subprocess and reset the UI."""
        store = getattr(self.app, "job_status", None)
        running = self._running or (
            store is not None and store.is_running(TOOL_URL_SCRAPER)
        )
        if not running:
            return
        self._stop_requested = True
        proc = self._extract_proc
        if store is not None:
            proc = store.request_stop(TOOL_URL_SCRAPER) or proc
        if proc is not None and getattr(proc, "poll", lambda: 0)() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._emit_log("Stopped by user")
        self._finish_stopped_ui()

    def _finish_stopped_ui(self) -> None:
        try:
            if not self.winfo_exists():
                store = getattr(self.app, "job_status", None)
                if store is not None:
                    store.set_stopped("URL Scraper", tool_id=TOOL_URL_SCRAPER)
                return
        except Exception:
            store = getattr(self.app, "job_status", None)
            if store is not None:
                store.set_stopped("URL Scraper", tool_id=TOOL_URL_SCRAPER)
            return
        self._hide_progress()
        self._set_running(False)
        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.set_stopped("URL Scraper", tool_id=TOOL_URL_SCRAPER)
        self.clear_btn.configure(state="normal")
        self._set_status_badge("Ready")
        self.loading_label.configure(text="")

    def _install_seed_url_placeholder(self) -> None:
        """Muted gray example text that is not a real seed URL."""
        try:
            tb = self.url_text._textbox  # noqa: SLF001
            tb.tag_configure("placeholder", foreground=T.TEXT_MUTED)
        except Exception:
            pass
        self.url_text.bind("<FocusIn>", self._on_seed_focus_in)
        self.url_text.bind("<FocusOut>", self._on_seed_focus_out)
        self._show_seed_placeholder()

    def _show_seed_placeholder(self) -> None:
        self.url_text.delete("1.0", "end")
        self.url_text.insert("1.0", SEED_URL_PLACEHOLDER)
        self._seed_placeholder_active = True
        try:
            tb = self.url_text._textbox  # noqa: SLF001
            tb.tag_add("placeholder", "1.0", "end")
            self.url_text.configure(text_color=T.TEXT_MUTED)
        except Exception:
            self.url_text.configure(text_color=T.TEXT_MUTED)

    def _clear_seed_placeholder(self) -> None:
        if not self._seed_placeholder_active:
            return
        self.url_text.delete("1.0", "end")
        self._seed_placeholder_active = False
        self.url_text.configure(text_color=T.INPUT_TEXT)
        try:
            tb = self.url_text._textbox  # noqa: SLF001
            tb.tag_remove("placeholder", "1.0", "end")
        except Exception:
            pass

    def _on_seed_focus_in(self, _event=None) -> None:
        if self._seed_placeholder_active:
            self._clear_seed_placeholder()

    def _on_seed_focus_out(self, _event=None) -> None:
        raw = self.url_text.get("1.0", "end").strip()
        if not raw:
            self._show_seed_placeholder()

    def _seed_text_is_placeholder(self) -> bool:
        if self._seed_placeholder_active:
            return True
        raw = self.url_text.get("1.0", "end").strip()
        return raw == SEED_URL_PLACEHOLDER

    def _parse_urls_from_text(self) -> list[str]:
        if self._seed_text_is_placeholder():
            return []
        raw = self.url_text.get("1.0", "end")
        urls: list[str] = []
        seen: set[str] = set()
        for line in raw.splitlines():
            u = line.strip()
            if not u or u.startswith("#"):
                continue
            if u == SEED_URL_PLACEHOLDER:
                continue
            if u in seen:
                continue
            seen.add(u)
            urls.append(u)
        return urls

    def _clear_results(self) -> None:
        """Reset scraper UI to initial empty state."""
        self.parsed_data = None
        self.source_url = ""
        self.suggested_filename = "shopify_products.csv"
        self._universal_output_dir = None
        self.error_label.configure(text="")
        self.strategy_label.configure(text="")
        self._set_status_badge("Ready")
        self.platform_label.configure(text="")
        self._reset_category_filter()
        self._hide_output_actions()
        self.log_box.configure(state="normal")
        try:
            tb = getattr(self.log_box, "_textbox", None)
            if tb is not None:
                tb.delete("1.0", "end")
            else:
                self.log_box.delete("1.0", "end")
        except Exception:
            self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self._clear_preview()
        self._disable_actions()
        self._hide_progress()
        self._stop_requested = False
        self._set_running(False)
        self._show_seed_placeholder()
        self._on_mode_changed(self.mode_var.get())

    def _reset_category_filter(self) -> None:
        self._category_options = {ALL_CATEGORIES_LABEL: ALL_CATEGORIES_KEY}
        try:
            self.category_menu.configure(values=[ALL_CATEGORIES_LABEL])
            self.category_menu.set(ALL_CATEGORIES_LABEL)
        except Exception:
            pass
        try:
            self.category_status.configure(text="")
        except Exception:
            pass

    def _on_category_selected(self, _label: str) -> None:
        self.error_label.configure(text="")

    def _selected_category_url(self) -> str | None:
        """Return selected category URL, or None for full-site (all categories)."""
        label = self.category_menu.get()
        value = self._category_options.get(label)
        if not value or value == ALL_CATEGORIES_KEY:
            return None
        return value

    def _fetch_categories(self) -> None:
        """Discover categories/collections from the first Seed URL."""
        if self._running or self._fetching_categories:
            return
        urls = self._parse_urls_from_text()
        if not urls:
            self.error_label.configure(text="Enter at least one Seed URL before fetching categories.")
            return
        seed = urls[0]
        if not is_valid_url(seed):
            self.error_label.configure(
                text="Seed URL must start with http:// or https://"
            )
            return

        self.error_label.configure(text="")
        self._fetching_categories = True
        try:
            self.fetch_categories_btn.configure(
                state="disabled", text="Fetching…"
            )
        except Exception:
            pass
        self.category_status.configure(text="Discovering categories…")
        self._append_log(f"Fetching categories from: {seed}")

        def _worker() -> None:
            try:
                from sentivo_extractor.core.category_discovery import (
                    discover_categories,
                )

                result = discover_categories(seed)
                self.after(0, lambda: self._on_categories_fetched(result, seed))
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                self.after(0, lambda m=msg: self._on_categories_fetch_error(m))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_categories_fetched(self, result: dict, seed: str) -> None:
        self._fetching_categories = False
        try:
            self.fetch_categories_btn.configure(
                state="disabled" if self._running else "normal",
                text="Fetch Categories →",
            )
        except Exception:
            pass

        platform = str((result or {}).get("platform") or "Unknown")
        categories = list((result or {}).get("categories") or [])
        source = str((result or {}).get("source") or "nav")

        self._category_options = {ALL_CATEGORIES_LABEL: ALL_CATEGORIES_KEY}
        labels = [ALL_CATEGORIES_LABEL]
        for cat in categories:
            label = str(cat.get("label") or "").strip()
            url = str(cat.get("url") or "").strip()
            if not label or not url:
                continue
            if label == ALL_CATEGORIES_LABEL:
                label = f"{label} (store)"
            # Disambiguate duplicate labels
            base_label = label
            n = 2
            while label in self._category_options:
                label = f"{base_label} ({n})"
                n += 1
            self._category_options[label] = url
            labels.append(label)

        self.category_menu.configure(values=labels)
        self.category_menu.set(ALL_CATEGORIES_LABEL)
        count = len(labels) - 1
        if count:
            self.category_status.configure(
                text=f"{platform} · {count} categories found ({source})"
            )
            self.platform_label.configure(
                text=f"Detected: {platform} · {count} categories"
            )
            self._append_log(
                f"Found {count} categories via {source} ({platform})"
            )
        else:
            self.category_status.configure(
                text=f"{platform} · no categories found — using full site"
            )
            self.platform_label.configure(text=f"Detected: {platform}")
            self._append_log(
                f"No categories found for {seed} ({platform}); defaulting to full site"
            )

    def _on_categories_fetch_error(self, message: str) -> None:
        self._fetching_categories = False
        try:
            self.fetch_categories_btn.configure(
                state="disabled" if self._running else "normal",
                text="Fetch Categories →",
            )
        except Exception:
            pass
        self.category_status.configure(text="Category fetch failed")
        self.error_label.configure(text=f"Category fetch failed: {message}")
        self._append_log(f"ERROR: category fetch failed — {message}")

    def _read_max_products(self) -> int | None:
        """Return max products, or None when unlimited (empty / 0)."""
        raw = (self.max_products_entry.get() or "").strip()
        if not raw:
            return None
        value = int(raw)
        if value < 0:
            raise ValueError("negative")
        if value == 0:
            return None
        return value

    # ── Start ─────────────────────────────────────────────

    def _start_scrape(self) -> None:
        store = getattr(self.app, "job_status", None)
        if store is not None and store.is_running(TOOL_URL_SCRAPER):
            self.error_label.configure(text="Already running")
            return

        self.error_label.configure(text="")
        self.strategy_label.configure(text="")
        self._set_status_badge("Ready")
        self.parsed_data = None
        self._universal_output_dir = None
        self._hide_output_actions()
        self._disable_actions()
        self._clear_preview()

        self.log_box.configure(state="normal")
        try:
            tb = getattr(self.log_box, "_textbox", None)
            if tb is not None:
                tb.delete("1.0", "end")
            else:
                self.log_box.delete("1.0", "end")
        except Exception:
            self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        self._start_universal()

    def _claim_scraper_job(self) -> bool:
        """Reserve the URL Scraper job slot. Shows Already running if busy."""
        store = getattr(self.app, "job_status", None)
        if store is None:
            return True
        if not store.try_begin(TOOL_URL_SCRAPER):
            self.error_label.configure(text="Already running")
            self.clear_btn.configure(state="normal")
            return False
        return True

    def _start_reprocess(self) -> None:
        """Pick a domain output folder and re-run merger/export on raw_json_backup."""
        if self._running or self._reprocessing:
            return
        folder = filedialog.askdirectory(
            title="Select domain output folder (e.g. directplastics)",
            initialdir=self._output_folder or str(DEFAULT_OUTPUT_DIR),
        )
        if not folder:
            return
        input_dir = Path(folder)
        raw_dir = input_dir / "raw_json_backup"
        if not raw_dir.is_dir():
            self.error_label.configure(
                text=f"raw_json_backup/ not found in: {input_dir}"
            )
            return

        if not self._claim_scraper_job():
            return

        self.error_label.configure(text="")
        self.scrape_btn.configure(state="disabled")
        try:
            self.reprocess_btn.configure(state="disabled")
        except Exception:
            pass
        self._reprocessing = True
        self._show_progress("Reprocessing existing data…")
        self._append_log(f"Reprocess input: {input_dir}")
        self._update_results(
            output=str(input_dir)[:48],
            status="Reprocessing",
            status_color=T.GOLD,
        )
        self._set_running(True)
        self.clear_btn.configure(state="disabled")
        thread = threading.Thread(
            target=self._run_reprocess,
            args=(input_dir,),
            daemon=True,
        )
        thread.start()

    def _run_reprocess(self, input_dir: Path) -> None:
        try:
            input_dir = Path(input_dir)
            cmd = [
                sys.executable,
                "-m",
                "sentivo_extractor.scripts.reprocess",
                "--input",
                str(input_dir),
            ]
            self._emit_log(f"CMD: {' '.join(cmd)}")

            env = os.environ.copy()
            env["PYTHONPATH"] = (
                str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            )
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            self._extract_proc = proc
            store = getattr(self.app, "job_status", None)
            if store is not None:
                store.set_proc(TOOL_URL_SCRAPER, proc)
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._job_stop_requested():
                    break
                text = line.rstrip()
                if text:
                    self._emit_log(text)
                    self._post_to_scraper_ui(
                        lambda s, m=text: s.loading_label.configure(text=m[:80])
                    )
            code = proc.wait()
            self._extract_proc = None
            if store is not None:
                store.set_proc(TOOL_URL_SCRAPER, None)
            if self._job_stop_requested():
                self._post_to_scraper_ui(
                    lambda s: s._finish_stopped_ui(),
                    fallback=self._store_mark_stopped,
                )
                return
            if code != 0:
                err = f"Reprocess exited with code {code}. See log."
                self._post_to_scraper_ui(
                    lambda s, m=err: s._on_error(m),
                    fallback=lambda m=err: self._store_mark_error(m),
                )
                return

            self._post_to_scraper_ui(
                lambda s, p=input_dir: s._on_reprocess_success(p),
                fallback=lambda p=input_dir: self._store_mark_complete(
                    self._count_reprocess_products(p)
                ),
            )
        except Exception as exc:  # noqa: BLE001
            if self._job_stop_requested():
                self._post_to_scraper_ui(
                    lambda s: s._finish_stopped_ui(),
                    fallback=self._store_mark_stopped,
                )
                return
            msg = f"Reprocess failed: {exc}"
            self._post_to_scraper_ui(
                lambda s, m=msg: s._on_error(m),
                fallback=lambda m=msg: self._store_mark_error(m),
            )

    @staticmethod
    def _count_reprocess_products(input_dir: Path) -> int:
        reprocessed = Path(input_dir) / "reprocessed"
        if not reprocessed.is_dir():
            return 0
        csvs = sorted(reprocessed.glob("*_shopify_import.csv"))
        if not csvs:
            csvs = sorted(reprocessed.glob("shopify_import.csv"))
        if not csvs:
            return 0
        return ScraperScreen._count_products_in_csv(csvs[0])

    def _on_reprocess_success(self, input_dir: Path) -> None:
        if self._job_stop_requested():
            self._finish_stopped_ui()
            return
        self._hide_progress()
        self._reprocessing = False
        self._set_running(False)
        reprocessed_dir = Path(input_dir) / "reprocessed"
        self._universal_output_dir = reprocessed_dir
        self._universal_domain_key = domain_folder_name(input_dir.name)

        product_count = self._count_reprocess_products(input_dir)
        csvs = sorted(reprocessed_dir.glob("*_shopify_import.csv"))
        csv_path = (
            csvs[0]
            if csvs
            else reprocessed_dir / "shopify_import.csv"
        )

        self.output_entry.delete(0, "end")
        self.output_entry.insert(0, str(input_dir))
        self._set_status_badge("Reprocess complete")
        self.strategy_label.configure(
            text=f"Reprocess complete  ·  {product_count} product(s)  ·  {reprocessed_dir}"
        )
        self._update_results(
            products=str(product_count),
            output=str(reprocessed_dir)[:48],
            status="Reprocess complete",
            status_color=T.SUCCESS,
        )
        self._append_log(f"Reprocess complete — {product_count} product(s)")
        if csv_path.exists():
            self._append_log(f"{csv_path.name} → {csv_path}")
        self._rebuild_output_buttons("full")
        self._show_output_actions()
        self._enable_actions()
        self.clear_btn.configure(state="normal")

        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.set_complete(
                product_count, "URL Scraper", tool_id=TOOL_URL_SCRAPER
            )

    def _start_universal(self) -> None:
        urls = self._parse_urls_from_text()
        if not urls:
            self.error_label.configure(text="Enter at least one URL (one per line).")
            self.clear_btn.configure(state="normal")
            return

        invalid = [u for u in urls if not is_valid_url(u)]
        if invalid:
            self.error_label.configure(
                text=f"Invalid URL (must start with http:// or https://): {invalid[0]}"
            )
            self.clear_btn.configure(state="normal")
            return

        # Optional category filter — scrape only the selected collection/category.
        selected_category = self._selected_category_url()
        category_label = self.category_menu.get()
        if selected_category:
            urls = [selected_category]

        out_folder = (self.output_entry.get() or "").strip() or self._output_folder
        if not out_folder:
            self.error_label.configure(text="Select an output folder.")
            self.clear_btn.configure(state="normal")
            return
        self._output_folder = out_folder

        try:
            max_products = self._read_max_products()
        except ValueError:
            self.error_label.configure(
                text="Max products per domain must be a non-negative integer."
            )
            self.clear_btn.configure(state="normal")
            return

        seed_urls = [{"url": u, "type": "auto", "department": ""} for u in urls]
        self.source_url = urls[0]
        self.suggested_filename = output_filename_from_url(urls[0])

        # Show domain subfolder path(s) in Output Path field.
        domain_keys = []
        for u in urls:
            key = domain_folder_name(u)
            if key not in domain_keys:
                domain_keys.append(key)
        primary_domain = domain_keys[0] if domain_keys else "unknown"
        domain_out = domain_output_dir(out_folder, primary_domain)
        ensure_domain_dir(out_folder, primary_domain)
        self.output_entry.delete(0, "end")
        self.output_entry.insert(0, str(domain_out))
        self._output_folder = str(Path(out_folder))  # keep base for CLI --output

        kind = "pilot" if self._is_universal_pilot() else "full"
        if kind == "pilot" and max_products is None:
            max_products = 20

        if not self._claim_scraper_job():
            return

        self.scrape_btn.configure(state="disabled")
        label = "Pilot" if kind == "pilot" else "Full"
        self._show_progress(f"Universal {label} running…")
        self._append_log(f"Mode: {self.mode_var.get()}")
        if selected_category:
            self._append_log(f"Category filter: {category_label}")
            self._append_log(f"Category URL: {selected_category}")
        else:
            self._append_log(f"Category filter: {ALL_CATEGORIES_LABEL}")
        self._append_log(f"URLs: {len(urls)}")
        self._append_log(f"Output base: {out_folder}")
        self._append_log(f"Domain folder: {domain_out}")
        if len(domain_keys) > 1:
            self._append_log(
                "Additional domain folders: "
                + ", ".join(str(domain_output_dir(out_folder, k)) for k in domain_keys[1:])
            )
        if max_products is None:
            self._append_log("Max products per domain: unlimited")
        else:
            self._append_log(f"Max products per domain: {max_products}")
        vendor = self._read_custom_vendor()
        if vendor:
            self._append_log(f"Custom vendor: {vendor}")

        self._stop_requested = False
        self._set_running(True)
        thread = threading.Thread(
            target=self._run_universal_extract,
            args=(
                kind,
                seed_urls,
                Path(out_folder),
                max_products,
                vendor,
                primary_domain,
            ),
            daemon=True,
        )
        thread.start()

    # ── Universal extract (subprocess CLI) ────────────────

    def _run_universal_extract(
        self,
        kind: str,
        seed_urls: list[dict[str, str]],
        out_dir: Path,
        max_products: int | None,
        vendor: str | None = None,
        primary_domain: str | None = None,
    ) -> None:
        label = "Pilot" if kind == "pilot" else "Full"
        try:
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            domain_key = primary_domain or domain_folder_name(
                str((seed_urls[0] or {}).get("url") or "")
            )
            domain_dir = ensure_domain_dir(out_dir, domain_key)
            input_csv = out_dir / "hashim_websites.csv"

            with input_csv.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["url", "type", "department"]
                )
                writer.writeheader()
                for row in seed_urls:
                    writer.writerow(
                        {
                            "url": row.get("url") or "",
                            "type": row.get("type") or "auto",
                            "department": row.get("department") or "",
                        }
                    )

            cmd = [
                sys.executable,
                "-m",
                "sentivo_extractor",
                "extract",
                "--input",
                str(input_csv),
                "--output",
                str(out_dir),
                "--overwrite",
                "true",
            ]
            if kind == "pilot":
                cmd.extend(
                    [
                        "--pilot",
                        "true",
                        "--pilot-size-per-domain",
                        str(max_products if max_products is not None else 20),
                    ]
                )
            # Unlimited → pass 0 so crawler skips the cap
            if max_products is None:
                cmd.extend(["--max-products-per-domain", "0"])
            else:
                cmd.extend(["--max-products-per-domain", str(max_products)])
            if vendor:
                cmd.extend(["--vendor", vendor])

            self._emit_log(f"Seed CSV: {input_csv}")
            self._emit_log(f"CMD: {' '.join(cmd)}")
            self._emit_log(f"Output base: {out_dir}")
            self._emit_log(f"Domain folder: {domain_dir}")

            env = os.environ.copy()
            env["PYTHONPATH"] = (
                str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            )

            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            self._extract_proc = proc
            store = getattr(self.app, "job_status", None)
            if store is not None:
                store.set_proc(TOOL_URL_SCRAPER, proc)
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._job_stop_requested():
                    break
                text = line.rstrip()
                if text:
                    self._emit_log(text)
                    self._post_to_scraper_ui(
                        lambda s, m=text: s.loading_label.configure(text=m[:80])
                    )
                    if store is not None:
                        meta = dict(store.get(TOOL_URL_SCRAPER).ui_meta or {})
                        meta["loading"] = text[:80]
                        store.set_ui_meta(TOOL_URL_SCRAPER, meta)
            code = proc.wait()
            self._extract_proc = None
            if store is not None:
                store.set_proc(TOOL_URL_SCRAPER, None)
            if self._job_stop_requested():
                self._post_to_scraper_ui(
                    lambda s: s._finish_stopped_ui(),
                    fallback=self._store_mark_stopped,
                )
                return
            if code != 0:
                err = f"Universal {label} exited with code {code}. See log."
                self._post_to_scraper_ui(
                    lambda s, m=err: s._on_error(m),
                    fallback=lambda m=err: self._store_mark_error(m),
                )
                return

            def _complete_fallback(o=domain_dir, d=domain_key) -> None:
                csv_path = domain_artifact_path(o.parent, d, "shopify_import.csv")
                if not csv_path.exists():
                    csv_path = Path(o) / "shopify_import.csv"
                count = self._count_products_in_csv(csv_path)
                self._store_mark_complete(count)

            self._post_to_scraper_ui(
                lambda s, o=domain_dir, k=kind, d=domain_key: s._on_universal_success(
                    o, k, d
                ),
                fallback=_complete_fallback,
            )
        except Exception as exc:  # noqa: BLE001
            if self._job_stop_requested():
                self._post_to_scraper_ui(
                    lambda s: s._finish_stopped_ui(),
                    fallback=self._store_mark_stopped,
                )
                return
            msg = f"Universal {label} failed: {exc}"
            self._post_to_scraper_ui(
                lambda s, m=msg: s._on_error(m),
                fallback=lambda m=msg: self._store_mark_error(m),
            )

    @staticmethod
    def _count_products_in_csv(csv_path: Path) -> int:
        if not csv_path.exists():
            return 0
        try:
            with csv_path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                fields = reader.fieldnames or []
                if "Handle" in fields:
                    handles = {
                        (row.get("Handle") or "").strip()
                        for row in reader
                        if (row.get("Handle") or "").strip()
                    }
                    return len(handles)
                return sum(1 for _ in reader)
        except Exception:
            return 0

    def _on_universal_success(
        self,
        out_dir: Path,
        kind: str = "full",
        domain_key: str | None = None,
    ) -> None:
        if self._job_stop_requested():
            self._finish_stopped_ui()
            return
        self._hide_progress()
        self._set_running(False)
        self.parsed_data = None  # never feed MappingScreen
        self._universal_output_dir = out_dir
        self._universal_run_kind = kind
        self._universal_domain_key = domain_key or domain_folder_name(
            getattr(self, "source_url", "") or out_dir.name
        )

        csv_path = domain_artifact_path(
            out_dir.parent, self._universal_domain_key, "shopify_import.csv"
        )
        if not csv_path.exists():
            # out_dir is already the domain folder
            csv_path = out_dir / f"{self._universal_domain_key}_shopify_import.csv"
        if not csv_path.exists():
            csv_path = out_dir / "shopify_import.csv"
        product_count = self._count_products_in_csv(csv_path)

        # Keep Output Path field on the domain folder
        self.output_entry.delete(0, "end")
        self.output_entry.insert(0, str(out_dir))

        mode_name = (
            "Universal Extractor Pilot"
            if kind == "pilot"
            else "Universal Extractor Full"
        )
        self._set_status_badge(f"Complete — {product_count} products")
        self.strategy_label.configure(
            text=f"{mode_name}  ·  {product_count} product(s)  ·  {out_dir}"
        )
        self.platform_label.configure(text=f"Mode: {mode_name}")
        seed_n = len(self._parse_urls_from_text()) if hasattr(self, "_parse_urls_from_text") else 0
        self._update_results(
            seeds=f"{seed_n} / {seed_n}" if seed_n else "—",
            products=str(product_count),
            output=str(out_dir)[:48],
            status="Complete",
            status_color=T.SUCCESS,
        )
        from app.utils.task_history import save_task

        save_task(
            "Universal Pilot" if kind == "pilot" else "Universal Full",
            str(csv_path.name),
            "Success",
        )
        self._append_log(
            f"Complete — {product_count} product(s) in {csv_path.name}"
        )
        self._append_log(f"{csv_path.name} → {csv_path}")
        self._rebuild_output_buttons(kind)
        self._show_output_actions()
        self._enable_actions()
        self.clear_btn.configure(state="normal")

        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.set_complete(product_count, "URL Scraper", tool_id=TOOL_URL_SCRAPER)

        from app.utils.job_status import notify_extraction_complete

        notify_extraction_complete(product_count, parent=self)

    def _open_universal_artifact(self, key: str) -> None:
        out = self._universal_output_dir
        if not out:
            self.error_label.configure(text="No universal output folder yet.")
            return
        domain_key = getattr(self, "_universal_domain_key", None) or out.name
        mapping = {
            "folder": out,
            "csv": out / f"{domain_key}_shopify_import.csv",
            "summary": out / f"{domain_key}_production_summary.xlsx",
            "qa": out / "qa" / "sample_review.xlsx",
            "preimport": out / f"{domain_key}_shopify_pre_import_validation.xlsx",
            "validation": out / f"{domain_key}_validation_report.xlsx",
            "images": out / f"{domain_key}_images_manifest.csv",
        }
        target = mapping.get(key)
        if target is None:
            return
        if key != "folder" and not Path(target).exists():
            # Fall back to unprefixed legacy names inside the domain folder
            legacy_names = {
                "csv": "shopify_import.csv",
                "summary": "production_summary.xlsx",
                "preimport": "shopify_pre_import_validation.xlsx",
                "validation": "validation_report.xlsx",
                "images": "images_manifest.csv",
            }
            legacy = out / legacy_names[key] if key in legacy_names else None
            alt = Path(str(target)).with_suffix(".csv")
            alt2 = Path(str(target)).with_suffix(".json")
            if legacy is not None and legacy.exists():
                target = legacy
            elif alt.exists():
                target = alt
            elif alt2.exists():
                target = alt2
            else:
                self.error_label.configure(text=f"File not found: {target}")
                self._append_log(f"Missing: {target}")
                return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception as exc:
            self.error_label.configure(text=f"Could not open: {exc}")

    def _on_error(self, message: str) -> None:
        if self._job_stop_requested():
            self._finish_stopped_ui()
            return
        self._hide_progress()
        self._set_running(False)
        self.error_label.configure(text=message)
        self._append_log(f"ERROR: {message}")
        self._disable_actions()
        self.clear_btn.configure(state="normal")
        self._set_status_badge("Ready")
        store = getattr(self.app, "job_status", None)
        if store is not None:
            # Release running slot after failed job
            job = store.get(TOOL_URL_SCRAPER)
            if job.state == "running":
                job.state = "idle"
                job.proc = None
                job.stop_requested = False
                job.message = "Ready"
                job.updated_at = time.time()
                store.message = "Ready"

    def destroy(self) -> None:
        self._stop_elapsed_tick()
        super().destroy()

    def _clear_preview(self) -> None:
        for child in self.preview_frame.winfo_children():
            child.destroy()

    def _go_mapping(self) -> None:
        self.error_label.configure(
            text="Universal Extractor already produced shopify_import.csv — open it from the buttons above."
        )
