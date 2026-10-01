"""URL / collection scraper — themed UI with options + live log.

Modes:
  1. Universal Extractor Full (default) — multi-URL extract → shopify_import.csv
  2. Universal Extractor Pilot — capped pilot extract → shopify_import.csv
  3. Legacy Scraper — crawl() → MappingScreen → ShopifyGenerator
     Pilot/Full never open MappingScreen / ShopifyGenerator
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

from app.core.collection_crawler import CollectionCrawlError, crawl
from app.ui import theme as T
from app.ui.sidebar import attach_sidebar
from app.utils.helpers import is_valid_url, output_filename_from_url
from app.utils.job_status import TOOL_URL_SCRAPER

ALL_CATEGORIES_LABEL = "All Categories"
ALL_CATEGORIES_KEY = "__ALL__"

MODE_LEGACY = "Legacy Scraper"
MODE_UNIVERSAL = "Universal Extractor Pilot"
MODE_UNIVERSAL_FULL = "Universal Extractor Full"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "gui_extract"

LOG_COLOR_INFO = "#8B7340"
LOG_COLOR_WARN = "#E88C00"
LOG_COLOR_ERROR = "#CC3333"
LOG_MIN_HEIGHT = 300
PROGRESS_AMBER = "#C9A84C"

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
    """Scrape catalog URLs via Universal Extractor (default) or Legacy crawler."""

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
        self._elapsed_after = None

        # Persistent bottom status bar (mockup)
        self.bottom_bar = ctk.CTkFrame(
            self, fg_color="#E8E5E0", height=40, corner_radius=0, border_width=1, border_color=T.BORDER
        )
        self.bottom_bar.pack(side="bottom", fill="x")
        self.bottom_bar.pack_propagate(False)
        self.bottom_bar.grid_columnconfigure(0, weight=1)

        self.count_badge = ctk.CTkLabel(
            self.bottom_bar, text="Ready", font=T.font(12), text_color=T.TEXT_SECONDARY, anchor="w"
        )
        self.count_badge.grid(row=0, column=0, sticky="w", padx=16, pady=8)
        self.status_right = ctk.CTkLabel(
            self.bottom_bar, text="", font=T.font(12), text_color=T.TEXT_MUTED, anchor="e"
        )
        self.status_right.grid(row=0, column=1, sticky="e", padx=16, pady=8)
        # Placeholders reassigned when content cards are built
        self.next_btn = None
        self.clear_btn = None

        body = attach_sidebar(self, app, "scraper")
        body.grid_rowconfigure(2, weight=1)

        # Header
        header = ctk.CTkFrame(body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(
            header, text="URL Scraper", font=T.font_tuple(T.H1), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Scrape products and metadata from ecommerce stores using seed URLs.",
            font=T.font_tuple(T.BODY),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
            wraplength=720,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # ── Scraper Configuration card ────────────────────
        config = T.card_frame(body)
        config.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        config.grid_columnconfigure(0, weight=1)
        cfg = ctk.CTkFrame(config, fg_color="transparent")
        cfg.grid(row=0, column=0, sticky="ew", padx=18, pady=16)
        cfg.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(
            cfg, text="Scraper Configuration", font=T.font(16, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # Mode
        mode_row = ctk.CTkFrame(cfg, fg_color="transparent")
        mode_row.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 10))
        mode_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(mode_row, text="Mode", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w").grid(row=0, column=0, sticky="w")
        self.mode_var = ctk.StringVar(value=MODE_UNIVERSAL_FULL)
        self.mode_menu = ctk.CTkOptionMenu(
            mode_row,
            variable=self.mode_var,
            values=[MODE_UNIVERSAL_FULL, MODE_UNIVERSAL, MODE_LEGACY],
            width=280,
            command=self._on_mode_changed,
            **T.option_menu_style(),
        )
        self.mode_menu.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.mode_hint = ctk.CTkLabel(
            mode_row, text="", font=T.font_tuple(T.CAPTION), text_color=T.TEXT_MUTED, anchor="w"
        )
        self.mode_hint.grid(row=2, column=0, sticky="w", pady=(2, 0))

        # Output folder
        out_row = ctk.CTkFrame(cfg, fg_color="transparent")
        out_row.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 10))
        out_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(out_row, text="Output Folder", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w").grid(row=0, column=0, columnspan=2, sticky="w")
        self.output_entry = T.styled_entry(out_row, placeholder="Select output folder…")
        self.output_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 0))
        self.output_entry.insert(0, self._output_folder)
        self.browse_btn = T.primary_button(out_row, "Browse", self._browse_output_folder, width=90, height=36)
        self.browse_btn.grid(row=1, column=1, pady=(4, 0))

        # Universal opts host (max + vendor + pilot)
        self.universal_opts = ctk.CTkFrame(cfg, fg_color="transparent")
        self.universal_opts.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.universal_opts.grid_columnconfigure((0, 1), weight=1)

        max_row = ctk.CTkFrame(self.universal_opts, fg_color="transparent")
        max_row.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        max_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(max_row, text="Max Products (per store)", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w").grid(row=0, column=0, sticky="w")
        self.max_products_entry = T.styled_entry(max_row, placeholder="empty = unlimited")
        self.max_products_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        vendor_row = ctk.CTkFrame(self.universal_opts, fg_color="transparent")
        vendor_row.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        vendor_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(vendor_row, text="Vendor", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w").grid(row=0, column=0, columnspan=2, sticky="w")
        self.var_custom_vendor = ctk.BooleanVar(value=False)
        self.vendor_checkbox = ctk.CTkCheckBox(
            vendor_row,
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
        self.vendor_checkbox.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.vendor_entry = T.styled_entry(vendor_row, placeholder="Vendor name for all products…")
        self.vendor_entry.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(6, 0))
        self.vendor_entry.grid_remove()

        self.pilot_hint = ctk.CTkLabel(
            self.universal_opts,
            text="Pilot mode caps at 20 products per domain for QA review.",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_MUTED,
            anchor="w",
        )
        self.pilot_hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.pilot_hint.grid_remove()

        # Seed URLs
        ctk.CTkLabel(cfg, text="Seed URLs", font=T.font(12, "bold"), text_color=T.TEXT_MUTED, anchor="w").grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 4))
        self.url_text = ctk.CTkTextbox(
            cfg,
            height=100,
            wrap="none",
            **T.textbox_style(),
        )
        self.url_text.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.url_text.insert("1.0", "https://your-store.com/collections/all\n")

        self.url_entry = T.styled_entry(cfg, placeholder="Legacy: paste one collection URL here (optional)")
        self.url_entry.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.url_entry.grid_remove()
        self.url_entry.bind("<FocusOut>", self._on_url_changed)
        self.url_entry.bind("<Return>", self._on_url_changed)

        self.platform_label = ctk.CTkLabel(cfg, text="", font=T.font_tuple(T.CAPTION), text_color=T.GOLD, anchor="w")
        self.platform_label.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        self._category_options: dict[str, str] = {}
        self._discovered_categories: list[dict] = []
        self.category_row = ctk.CTkFrame(cfg, fg_color="transparent")
        self.category_row.grid(row=7, column=0, columnspan=2, sticky="ew")
        self.category_row.grid_columnconfigure(0, weight=1)
        self.category_row.grid_remove()
        self.category_hint = ctk.CTkLabel(
            self.category_row,
            text="Homepage detected — select a category to scrape:",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
        )
        self.category_hint.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.category_menu = ctk.CTkOptionMenu(
            self.category_row,
            values=["Select a category…"],
            width=420,
            command=self._on_category_selected,
            **T.option_menu_style(),
        )
        self.category_menu.grid(row=1, column=0, sticky="ew")
        self.category_menu.set("Select a category…")

        # Legacy checkboxes
        opts = ctk.CTkFrame(cfg, fg_color="transparent")
        opts.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.var_variants = ctk.BooleanVar(value=True)
        self.var_images = ctk.BooleanVar(value=True)
        self.var_compare = ctk.BooleanVar(value=True)
        self.legacy_opts = opts
        self._option_checkboxes: list[ctk.CTkCheckBox] = []
        for col, (text, var) in enumerate(
            (
                ("Include variants", self.var_variants),
                ("Include images", self.var_images),
                ("Include compare-at price", self.var_compare),
            )
        ):
            cb = ctk.CTkCheckBox(
                opts,
                text=text,
                variable=var,
                font=T.font_tuple(T.LABEL),
                text_color=T.TEXT_SECONDARY,
                fg_color=T.ACCENT,
                hover_color=T.ACCENT_HOVER,
                border_color=T.BORDER,
                checkmark_color=T.BTN_ON_ACCENT,
                corner_radius=T.BORDER_RADIUS,
            )
            cb.grid(row=0, column=col, sticky="w", padx=(0, 20))
            self._option_checkboxes.append(cb)
        opts.grid_remove()

        # Run / Stop
        btn_row = ctk.CTkFrame(cfg, fg_color="transparent")
        btn_row.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        btn_row.grid_columnconfigure((0, 1), weight=1)
        self.scrape_btn = T.primary_button(btn_row, "Run Scraper", self._start_scrape)
        self.scrape_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.stop_btn = ctk.CTkButton(
            btn_row,
            text="Stop",
            command=self._stop_scrape,
            state="disabled",
            fg_color="#CC3333",
            hover_color="#A82828",
            text_color="#F0EDE8",
            corner_radius=T.BORDER_RADIUS,
            font=T.font_tuple(T.BTN_TEXT),
            height=T.BTN_HEIGHT,
        )
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        # Progress
        self.progress_section = ctk.CTkFrame(cfg, fg_color="transparent")
        self.progress_section.grid(row=10, column=0, columnspan=2, sticky="ew")
        self.progress_section.grid_columnconfigure(0, weight=1)
        self.loading_label = ctk.CTkLabel(
            self.progress_section, text="", font=T.font_tuple(T.CAPTION), text_color=T.GOLD, anchor="w"
        )
        self.loading_label.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.loading_label.grid_remove()
        self.progress = ctk.CTkProgressBar(
            self.progress_section,
            height=12,
            mode="indeterminate",
            progress_color=PROGRESS_AMBER,
            fg_color=T.BORDER,
            corner_radius=6,
        )
        self.progress.grid(row=1, column=0, sticky="ew")
        self.progress.grid_remove()

        self.error_label = ctk.CTkLabel(cfg, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR, anchor="w")
        self.error_label.grid(row=11, column=0, columnspan=2, sticky="ew")
        self.strategy_label = ctk.CTkLabel(cfg, text="", font=T.font_tuple(T.CAPTION), text_color=T.TEXT_SECONDARY, anchor="w")
        self.strategy_label.grid(row=12, column=0, columnspan=2, sticky="ew")
        self.output_actions = ctk.CTkFrame(cfg, fg_color="transparent")
        self.output_actions.grid(row=13, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.output_actions.grid_remove()
        self._output_buttons: list[ctk.CTkButton] = []

        # ── Bottom: Log + Results ─────────────────────────
        bottom = ctk.CTkFrame(body, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="nsew")
        bottom.grid_columnconfigure(0, weight=7)
        bottom.grid_columnconfigure(1, weight=3)
        bottom.grid_rowconfigure(0, weight=1)

        log_card = T.card_frame(bottom)
        log_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)
        log_head = ctk.CTkFrame(log_card, fg_color="transparent")
        log_head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        log_head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_head, text="Log Area", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w").grid(row=0, column=0, sticky="w")
        self.clear_btn = T.secondary_button(log_head, "Clear Log", self._clear_results, width=100, height=28)
        self.clear_btn.grid(row=0, column=1, sticky="e")
        self.log_box = ctk.CTkTextbox(
            log_card,
            height=LOG_MIN_HEIGHT,
            text_color=LOG_COLOR_INFO,
            font=ctk.CTkFont(family=T.FONT_MONO, size=11),
            state="disabled",
            wrap="word",
            fg_color=T.get("INPUT_BG"),
            border_width=1,
            border_color=T.get("BORDER"),
            corner_radius=T.BORDER_RADIUS,
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self._configure_log_tags()

        results = T.card_frame(bottom)
        results.grid(row=0, column=1, sticky="nsew")
        results.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            results, text="Results & Status", font=T.font(14, "bold"), text_color=T.HEADING, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 10))
        self.result_labels = {}
        for i, (key, title) in enumerate(
            (
                ("seeds", "Seed URLs Processed"),
                ("products", "Products Found"),
                ("output", "Output Path"),
                ("elapsed", "Elapsed Time"),
                ("status", "Status"),
            )
        ):
            row = ctk.CTkFrame(results, fg_color="transparent")
            row.grid(row=i + 1, column=0, sticky="ew", padx=16, pady=3)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text=title, font=T.font(12), text_color=T.TEXT_MUTED, anchor="w").grid(row=0, column=0, sticky="w")
            lab = ctk.CTkLabel(row, text="—", font=T.font(12, "bold"), text_color=T.TEXT_PRIMARY, anchor="e")
            lab.grid(row=0, column=1, sticky="e")
            self.result_labels[key] = lab
        self.next_btn = T.primary_button(results, "Generate Final CSV", self._go_mapping)
        self.next_btn.grid(row=7, column=0, sticky="ew", padx=16, pady=(16, 16))

        # Hidden preview frame for legacy compatibility
        self.preview_frame = ctk.CTkScrollableFrame(body, fg_color=T.BG_SURFACE_B, height=1)
        self.preview_frame.grid_remove()

        self._disable_actions()
        self._on_mode_changed(MODE_UNIVERSAL_FULL)
        self._restore_running_job_ui()

    # ── Mode ──────────────────────────────────────────────

    def _is_universal_mode(self) -> bool:
        return self.mode_var.get() in (MODE_UNIVERSAL, MODE_UNIVERSAL_FULL)

    def _is_universal_pilot(self) -> bool:
        return self.mode_var.get() == MODE_UNIVERSAL

    def _is_universal_full(self) -> bool:
        return self.mode_var.get() == MODE_UNIVERSAL_FULL

    def _on_mode_changed(self, _value: str | None = None) -> None:
        if self._is_universal_full():
            self.scrape_btn.configure(text="Run →")
            self.next_btn.configure(text="Final CSV ready")
            self.mode_hint.configure(
                text="Full → shopify_import.csv (no MappingScreen)"
            )
            self._show_universal_opts(show_pilot_hint=False)
            self._hide_legacy_url_entry()
            self._disable_actions()
            self.clear_btn.configure(state="normal")
        elif self._is_universal_pilot():
            self.scrape_btn.configure(text="Run Pilot →")
            self.next_btn.configure(text="Final CSV ready")
            self.mode_hint.configure(
                text="Pilot → shopify_import.csv (no MappingScreen)"
            )
            self._show_universal_opts(show_pilot_hint=True)
            self._hide_legacy_url_entry()
            self._disable_actions()
            self.clear_btn.configure(state="normal")
        else:
            self.scrape_btn.configure(text="Scrape →")
            self.next_btn.configure(text="Generate Shopify CSV →")
            self.mode_hint.configure(
                text="Legacy → MappingScreen  ·  Pilot/Full → final Shopify CSV"
            )
            self._hide_universal_opts()
            self._show_legacy_url_entry()
            self._hide_output_actions()
            if self.parsed_data:
                self._enable_actions()
            else:
                self._disable_actions()

    def _show_universal_opts(self, *, show_pilot_hint: bool) -> None:
        self.universal_opts.grid()
        if show_pilot_hint:
            self.pilot_hint.grid()
        else:
            self.pilot_hint.grid_remove()

    def _hide_universal_opts(self) -> None:
        self.universal_opts.grid_remove()

    def _show_legacy_url_entry(self) -> None:
        self.url_entry.grid()
        self.legacy_opts.grid()

    def _hide_legacy_url_entry(self) -> None:
        self.url_entry.grid_remove()
        self._hide_category_picker()
        self.legacy_opts.grid_remove()

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
        self.next_btn.configure(
            state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED
        )
        self.clear_btn.configure(state="disabled")

    def _enable_actions(self) -> None:
        if self._is_universal_mode():
            self.next_btn.configure(
                state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED
            )
            self.clear_btn.configure(state="normal")
            return
        self.next_btn.configure(
            state="normal", fg_color=T.ACCENT, text_color=T.BG_PRIMARY
        )
        self.clear_btn.configure(state="normal")

    def _configure_log_tags(self) -> None:
        """Color tags for INFO / WARNING / ERROR log lines."""
        try:
            tb = self.log_box._textbox  # noqa: SLF001
            tb.tag_configure("info", foreground=LOG_COLOR_INFO)
            tb.tag_configure("warn", foreground=LOG_COLOR_WARN)
            tb.tag_configure("error", foreground=LOG_COLOR_ERROR)
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
        """Show full-width amber progress bar below checkboxes (above Log)."""
        if text:
            self.loading_label.configure(text=text[:120])
        self.loading_label.grid()
        self.progress.grid()
        try:
            self.progress.start()
        except Exception:
            pass

    def _hide_progress(self) -> None:
        try:
            self.progress.stop()
        except Exception:
            pass
        self.progress.grid_remove()
        self.loading_label.grid_remove()
        self.loading_label.configure(text="")

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
                "legacy_url": self.url_entry.get(),
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
        if running:
            self.scrape_btn.configure(state="disabled")
            self.stop_btn.configure(
                state="normal",
                fg_color=T.ERROR,
                hover_color=T.ACCENT_HOVER,
                text_color=T.BG_PRIMARY,
            )
        else:
            self.scrape_btn.configure(state="normal")
            self.stop_btn.configure(
                state="disabled",
                fg_color=T.BG_SURFACE_B,
                hover_color=T.BG_SURFACE_A,
                text_color=T.TEXT_MUTED,
            )

    def _set_running(self, running: bool) -> None:
        """Toggle Run/Stop button states and lock/unlock inputs."""
        self._apply_running_chrome(running)
        if running:
            self._snapshot_ui_meta()
            self._start_elapsed_tick()
        else:
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
        elapsed = store.get(TOOL_URL_SCRAPER).elapsed_text()
        try:
            self.count_badge.configure(text=f"Running · {elapsed}")
        except Exception:
            return
        self._update_results(
            elapsed=elapsed,
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
                self.url_text.configure(state="normal")
                self.url_text.delete("1.0", "end")
                self.url_text.insert("1.0", urls)
            if meta.get("legacy_url") is not None:
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, str(meta.get("legacy_url") or ""))
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
            self.output_entry,
            self.browse_btn,
            self.max_products_entry,
            self.mode_menu,
            self.url_entry,
            self.vendor_checkbox,
            self.vendor_entry,
        ):
            try:
                widget.configure(state=state)
            except Exception:
                pass
        for cb in getattr(self, "_option_checkboxes", []) or []:
            try:
                cb.configure(state=state)
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
    # ── Category picker (Legacy) ──────────────────────────

    def _hide_category_picker(self) -> None:
        self._category_options = {}
        self._discovered_categories = []
        try:
            self.category_menu.configure(values=["Select a category…"])
            self.category_menu.set("Select a category…")
        except Exception:
            pass
        if self.category_row.winfo_ismapped():
            self.category_row.grid_remove()

    def _show_category_picker(self, categories: list[dict]) -> None:
        if not categories:
            self._hide_category_picker()
            return
        self._discovered_categories = [
            c for c in categories if c.get("label") and c.get("url")
        ]
        self._category_options = {ALL_CATEGORIES_LABEL: ALL_CATEGORIES_KEY}
        for c in self._discovered_categories:
            label = c["label"]
            if label == ALL_CATEGORIES_LABEL:
                label = f"{label} (store)"
            self._category_options[label] = c["url"]
        labels = list(self._category_options.keys())
        self.category_menu.configure(values=labels)
        self.category_menu.set(ALL_CATEGORIES_LABEL)
        self.error_label.configure(text="")
        self.category_row.grid()

    def _on_category_selected(self, _label: str) -> None:
        self.error_label.configure(text="")

    def _selected_category_url(self) -> str | None:
        if not self._category_options:
            return None
        label = self.category_menu.get()
        value = self._category_options.get(label)
        if value == ALL_CATEGORIES_KEY:
            return ALL_CATEGORIES_KEY
        return value

    def _is_all_categories_selected(self) -> bool:
        return (
            bool(self._category_options)
            and self.category_menu.get() == ALL_CATEGORIES_LABEL
        )

    def _parse_urls_from_text(self) -> list[str]:
        raw = self.url_text.get("1.0", "end")
        urls: list[str] = []
        seen: set[str] = set()
        for line in raw.splitlines():
            u = line.strip()
            if not u or u.startswith("#"):
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
        self.url_text.delete("1.0", "end")
        self.url_entry.delete(0, "end")
        self.error_label.configure(text="")
        self.strategy_label.configure(text="")
        self._set_status_badge("Ready")
        self.platform_label.configure(text="")
        self._hide_category_picker()
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
        self._on_mode_changed(self.mode_var.get())

    def _on_url_changed(self, _event=None) -> None:
        """Legacy: detect platform / categories for single URL entry."""
        if self._is_universal_mode():
            return
        url = self.url_entry.get().strip()
        if not is_valid_url(url):
            self.platform_label.configure(text="")
            self._hide_category_picker()
            return
        self.platform_label.configure(text="Detecting platform…")
        self._hide_category_picker()

        def _detect() -> None:
            try:
                from app.core.html_catalog_scraper import (
                    detect_platform,
                    discover_category_links,
                    is_homepage_url,
                )

                platform = detect_platform(url)
                categories: list[dict] = []
                if is_homepage_url(url):
                    categories = discover_category_links(url)

                def _apply() -> None:
                    self.platform_label.configure(text=f"Detected: {platform}")
                    if categories:
                        self._show_category_picker(categories)
                        self.platform_label.configure(
                            text=f"Detected: {platform} · {len(categories)} categories found"
                        )
                    else:
                        self._hide_category_picker()

                self.after(0, _apply)
            except Exception:
                self.after(0, lambda: self.platform_label.configure(text=""))
                self.after(0, self._hide_category_picker)

        threading.Thread(target=_detect, daemon=True).start()

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

        if self._is_universal_mode():
            self._start_universal()
        else:
            self._start_legacy()

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

        kind = "pilot" if self._is_universal_pilot() else "full"
        if kind == "pilot" and max_products is None:
            max_products = 20

        if not self._claim_scraper_job():
            return

        self.scrape_btn.configure(state="disabled")
        label = "Pilot" if kind == "pilot" else "Full"
        self._show_progress(f"Universal {label} running…")
        self._append_log(f"Mode: {self.mode_var.get()}")
        self._append_log(f"URLs: {len(urls)}")
        self._append_log(f"Output: {out_folder}")
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
            args=(kind, seed_urls, Path(out_folder), max_products, vendor),
            daemon=True,
        )
        thread.start()

    def _start_legacy(self) -> None:
        url = self.url_entry.get().strip()
        if not url:
            # Fall back to first line of multi-line box
            urls = self._parse_urls_from_text()
            url = urls[0] if urls else ""
            if url:
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, url)

        if not is_valid_url(url):
            self.error_label.configure(text="URL must start with http:// or https://")
            self.clear_btn.configure(state="normal")
            return

        from app.core.html_catalog_scraper import is_homepage_url

        category_name = ""
        categories_arg = None

        if is_homepage_url(url):
            cat_url = self._selected_category_url()
            if self._category_options:
                if not cat_url:
                    self.error_label.configure(
                        text="Select a category from the dropdown before scraping."
                    )
                    self.clear_btn.configure(state="normal")
                    return
                if cat_url == ALL_CATEGORIES_KEY or self._is_all_categories_selected():
                    categories_arg = list(self._discovered_categories)
                    category_name = ALL_CATEGORIES_LABEL
                    self._append_log(
                        f"Using {ALL_CATEGORIES_LABEL} "
                        f"({len(categories_arg)} categories)"
                    )
                else:
                    url = cat_url
                    category_name = self.category_menu.get()
                    self._append_log(f"Using category: {category_name}")
            elif not self.category_row.winfo_ismapped():
                self.error_label.configure(
                    text="This looks like a homepage. Wait for categories to load, or paste a category URL."
                )
                self.clear_btn.configure(state="normal")
                self._on_url_changed()
                return

        self.source_url = url
        self.suggested_filename = output_filename_from_url(url)

        if not self._claim_scraper_job():
            return

        self.scrape_btn.configure(state="disabled")
        self._show_progress("Scraping...")
        self._append_log("Mode: Legacy Scraper")
        self._append_log(f"Starting crawl: {url}")
        self._stop_requested = False
        self._set_running(True)
        thread = threading.Thread(
            target=self._run_scrape,
            args=(url, category_name, categories_arg),
            daemon=True,
        )
        thread.start()

    def _on_progress(self, message: str) -> None:
        self._emit_log(message)
        self._post_to_scraper_ui(
            lambda s, m=message: s.loading_label.configure(text=m[:80])
        )
        store = getattr(self.app, "job_status", None)
        if store is not None:
            meta = dict(store.get(TOOL_URL_SCRAPER).ui_meta or {})
            meta["loading"] = message[:80]
            store.set_ui_meta(TOOL_URL_SCRAPER, meta)

    # ── Legacy scrape ─────────────────────────────────────

    def _run_scrape(
        self,
        url: str,
        category_name: str = "",
        categories: list | None = None,
    ) -> None:
        try:
            if self._job_stop_requested():
                self._post_to_scraper_ui(
                    lambda s: s._finish_stopped_ui(),
                    fallback=self._store_mark_stopped,
                )
                return
            data = crawl(
                url,
                progress=self._on_progress,
                category_name=category_name,
                categories=categories,
            )
            if self._job_stop_requested():
                self._post_to_scraper_ui(
                    lambda s: s._finish_stopped_ui(),
                    fallback=self._store_mark_stopped,
                )
                return
            self._post_to_scraper_ui(
                lambda s, d=data: s._on_success(d),
                fallback=lambda d=data: self._store_mark_complete(
                    int(d.get("row_count") or 0)
                ),
            )
        except CollectionCrawlError as exc:
            if self._job_stop_requested():
                self._post_to_scraper_ui(
                    lambda s: s._finish_stopped_ui(),
                    fallback=self._store_mark_stopped,
                )
                return
            msg = str(exc)
            self._post_to_scraper_ui(
                lambda s, m=msg: s._on_error(m),
                fallback=lambda m=msg: self._store_mark_error(m),
            )
        except Exception as exc:  # noqa: BLE001
            if self._job_stop_requested():
                self._post_to_scraper_ui(
                    lambda s: s._finish_stopped_ui(),
                    fallback=self._store_mark_stopped,
                )
                return
            msg = f"Unexpected error: {exc}"
            self._post_to_scraper_ui(
                lambda s, m=msg: s._on_error(m),
                fallback=lambda m=msg: self._store_mark_error(m),
            )

    def _on_success(self, data: dict) -> None:
        self._hide_progress()
        self._set_running(False)
        self.parsed_data = data

        count = data.get("row_count", 0)
        errors = data.get("errors") or []
        extra = f"  ·  {len(errors)} error(s)" if errors else ""
        self.suggested_filename = output_filename_from_url(self.source_url)
        platform = data.get("platform") or ""
        strategy = data.get("strategy_used", "")
        if platform:
            self.platform_label.configure(text=f"Detected: {platform}")
        self.strategy_label.configure(
            text=f"{strategy}  ·  → {self.suggested_filename}{extra}"
        )
        from app.utils.task_history import save_task

        save_task("Scrape", self.suggested_filename, "Success")
        self._append_log(f"Done — {count} rows ready")
        self._render_preview()
        self._enable_actions()
        self._set_status_badge(f"Complete — {int(count or 0)} products")
        out = self._output_folder or "—"
        self._update_results(
            products=str(int(count or 0)),
            output=str(out)[:48],
            status="Complete",
            status_color=T.SUCCESS,
        )
        store = getattr(self.app, "job_status", None)
        if store is not None:
            store.set_complete(int(count or 0), "URL Scraper", tool_id=TOOL_URL_SCRAPER)
        from app.utils.job_status import notify_extraction_complete

        notify_extraction_complete(int(count or 0), parent=self)

    # ── Universal extract (subprocess CLI) ────────────────

    def _run_universal_extract(
        self,
        kind: str,
        seed_urls: list[dict[str, str]],
        out_dir: Path,
        max_products: int | None,
        vendor: str | None = None,
    ) -> None:
        label = "Pilot" if kind == "pilot" else "Full"
        try:
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
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
            self._emit_log(f"Output: {out_dir}")

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

            def _complete_fallback(o=out_dir) -> None:
                count = self._count_products_in_csv(Path(o) / "shopify_import.csv")
                self._store_mark_complete(count)

            self._post_to_scraper_ui(
                lambda s, o=out_dir, k=kind: s._on_universal_success(o, k),
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
            with csv_path.open(encoding="utf-8", newline="") as f:
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

    def _on_universal_success(self, out_dir: Path, kind: str = "full") -> None:
        if self._job_stop_requested():
            self._finish_stopped_ui()
            return
        self._hide_progress()
        self._set_running(False)
        self.parsed_data = None  # never feed MappingScreen
        self._universal_output_dir = out_dir
        self._universal_run_kind = kind

        csv_path = out_dir / "shopify_import.csv"
        product_count = self._count_products_in_csv(csv_path)

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
            f"Complete — {product_count} product(s) in shopify_import.csv"
        )
        self._append_log(f"shopify_import.csv → {csv_path}")
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
        mapping = {
            "folder": out,
            "csv": out / "shopify_import.csv",
            "summary": out / "production_summary.xlsx",
            "qa": out / "qa" / "sample_review.xlsx",
            "preimport": out / "shopify_pre_import_validation.xlsx",
            "validation": out / "validation_report.xlsx",
            "images": out / "images_manifest.csv",
        }
        target = mapping.get(key)
        if target is None:
            return
        if key != "folder" and not Path(target).exists():
            alt = Path(str(target)).with_suffix(".csv")
            alt2 = Path(str(target)).with_suffix(".json")
            if alt.exists():
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

    def _render_preview(self) -> None:
        self._clear_preview()
        if not self.parsed_data:
            return

        headers = self.parsed_data["headers"]
        priority = [
            "Title",
            "Vendor",
            "Variant Price",
            "Variant SKU",
            "Image Src",
            "Option1 Name",
            "Option1 Value",
            "Price",
            "SKU",
            "Product image URL",
            "Option1 name",
            "Option1 value",
        ]
        preview_headers = [h for h in priority if h in headers]
        if not preview_headers:
            preview_headers = headers[:8]

        rows = self.parsed_data["rows"][:5]

        for col_idx, header in enumerate(preview_headers):
            col = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
            col.grid(row=0, column=col_idx, padx=4, sticky="nw")

            ctk.CTkLabel(
                col,
                text=header,
                font=T.font(11, "bold"),
                text_color=T.ACCENT,
                width=130,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", pady=(0, 4))

            for i, row in enumerate(rows):
                value = str(row.get(header, ""))[:45]
                bg = T.BG_SURFACE_A if i % 2 == 0 else T.BG_SURFACE_B
                ctk.CTkLabel(
                    col,
                    text=value or "—",
                    font=T.font(11),
                    text_color=T.TEXT_SECONDARY,
                    width=130,
                    anchor="w",
                    fg_color=bg,
                ).grid(row=i + 1, column=0, sticky="w", ipady=1)

    def _go_mapping(self) -> None:
        """Legacy only — Universal Pilot/Full never uses MappingScreen."""
        if self._is_universal_mode():
            self.error_label.configure(
                text="Universal Extractor already produced shopify_import.csv — open it from the buttons above."
            )
            return
        if not self.parsed_data:
            return
        from app.ui.mapping_screen import MappingScreen

        self.app.show_screen(
            MappingScreen,
            parsed_data=self.parsed_data,
            suggested_filename=self.suggested_filename,
        )
