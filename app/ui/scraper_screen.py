"""URL / collection scraper — themed UI with options + live log."""

from __future__ import annotations

import threading

import customtkinter as ctk

from app.core.collection_crawler import CollectionCrawlError, crawl
from app.ui import theme as T
from app.ui.sidebar import attach_sidebar
from app.utils.helpers import is_valid_url, output_filename_from_url


class ScraperScreen(ctk.CTkFrame):
    """Scrape a Shopify collection URL and preview results."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app
        self.parsed_data: dict | None = None
        self.source_url: str = ""
        self.suggested_filename: str = "shopify_products.csv"

        # Persistent bottom bar FIRST (always visible)
        self.bottom_bar = ctk.CTkFrame(
            self, fg_color=T.BG_SURFACE_A, height=64, corner_radius=0
        )
        self.bottom_bar.pack(side="bottom", fill="x")
        self.bottom_bar.pack_propagate(False)

        self.count_badge = ctk.CTkLabel(
            self.bottom_bar, text="", font=T.font(12, "bold"), text_color=T.ACCENT
        )
        self.count_badge.pack(side="left", padx=16, pady=12)

        self.next_btn = ctk.CTkButton(
            self.bottom_bar,
            text="Generate Shopify CSV →",
            command=self._go_mapping,
            width=200,
            **T.primary_btn(),
        )
        self.clear_btn = ctk.CTkButton(
            self.bottom_bar,
            text="Clear",
            command=self._clear_results,
            width=100,
            **T.secondary_btn(),
        )
        self.clear_btn.pack(side="right", padx=8, pady=12)
        self.next_btn.pack(side="right", padx=(0, 16), pady=12)
        self._disable_actions()

        body = attach_sidebar(self, app, "scraper")

        T.page_title(
            body,
            "URL Scraper",
            "Extract products from Shopify, WooCommerce, or any catalog URL",
        )

        main_area = ctk.CTkFrame(body, fg_color="transparent")
        main_area.pack(fill="both", expand=True)

        middle = ctk.CTkFrame(main_area, fg_color="transparent")
        middle.pack(fill="both", expand=True)

        top = ctk.CTkFrame(main_area, fg_color="transparent")
        top.pack(fill="x", before=middle)

        # URL + Scrape
        url_card = T.card_frame(top)
        url_card.pack(fill="x", pady=(0, T.GRID_GAP))
        url_inner = ctk.CTkFrame(url_card, fg_color="transparent")
        url_inner.pack(fill="x", padx=T.CARD_PADDING, pady=T.CARD_PADDING)

        self.url_entry = T.styled_entry(
            url_inner,
            placeholder="https://your-store.com/collections/all or /shop",
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.url_entry.bind("<FocusOut>", self._on_url_changed)
        self.url_entry.bind("<Return>", self._on_url_changed)

        self.scrape_btn = T.primary_button(
            url_inner, "Scrape →", self._start_scrape, width=120
        )
        self.scrape_btn.pack(side="right")

        self.platform_label = ctk.CTkLabel(
            url_card,
            text="",
            font=T.font_tuple(T.CAPTION),
            text_color=T.ACCENT,
            anchor="w",
        )
        self.platform_label.pack(fill="x", padx=T.CARD_PADDING, pady=(0, 6))

        # Category picker (shown when homepage URL is entered)
        self._category_options: dict[str, str] = {}  # label -> url
        self.category_row = ctk.CTkFrame(url_card, fg_color="transparent")
        self.category_hint = ctk.CTkLabel(
            self.category_row,
            text="Homepage detected — select a category to scrape:",
            font=T.font_tuple(T.CAPTION),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
        )
        self.category_hint.pack(fill="x", pady=(0, 4))
        self.category_menu = ctk.CTkOptionMenu(
            self.category_row,
            values=["Select a category…"],
            font=T.font_tuple(T.LABEL),
            fg_color=T.BG_SURFACE_B,
            button_color=T.ACCENT,
            button_hover_color=T.ACCENT_HOVER,
            text_color=T.TEXT_PRIMARY,
            dropdown_fg_color=T.BG_SURFACE_A,
            dropdown_hover_color=T.BG_SURFACE_B,
            dropdown_text_color=T.TEXT_PRIMARY,
            width=420,
            command=self._on_category_selected,
        )
        self.category_menu.pack(fill="x", pady=(0, 10))
        self.category_menu.set("Select a category…")
        # Hidden until homepage + categories found
        # (packed by _show_category_picker)

        opts = ctk.CTkFrame(top, fg_color="transparent")
        opts.pack(fill="x", pady=(0, 8))
        self.var_variants = ctk.BooleanVar(value=True)
        self.var_images = ctk.BooleanVar(value=True)
        self.var_compare = ctk.BooleanVar(value=True)
        for text, var in (
            ("Include variants", self.var_variants),
            ("Include images", self.var_images),
            ("Include compare-at price", self.var_compare),
        ):
            ctk.CTkCheckBox(
                opts,
                text=text,
                variable=var,
                font=T.font_tuple(T.LABEL),
                text_color=T.TEXT_SECONDARY,
                fg_color=T.ACCENT,
                hover_color=T.ACCENT_HOVER,
                border_color=T.BORDER,
                checkmark_color=T.BG_PRIMARY,
                corner_radius=T.BORDER_RADIUS,
            ).pack(side="left", padx=(0, 20))

        self.error_label = ctk.CTkLabel(
            top, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR
        )
        self.error_label.pack()

        self.progress_section = ctk.CTkFrame(top, fg_color="transparent")
        self.progress_section.pack(fill="x")
        self.progress = T.progress_bar(self.progress_section)
        self.loading_label = ctk.CTkLabel(
            self.progress_section, text="", font=T.font_tuple(T.CAPTION),
            text_color=T.ACCENT,
        )

        self.log_box = T.log_box(top, height=110)

        self.strategy_label = ctk.CTkLabel(
            top, text="", font=T.font_tuple(T.CAPTION), text_color=T.TEXT_SECONDARY
        )
        self.strategy_label.pack()

        preview_card = T.card_frame(middle)
        preview_card.pack(fill="both", expand=True, pady=(8, 0))
        ctk.CTkLabel(
            preview_card, text="Results Preview",
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

    def _disable_actions(self) -> None:
        self.next_btn.configure(
            state="disabled", fg_color=T.BG_SURFACE_B, text_color=T.TEXT_MUTED
        )
        self.clear_btn.configure(state="disabled")

    def _enable_actions(self) -> None:
        self.next_btn.configure(
            state="normal", fg_color=T.ACCENT, text_color=T.BG_PRIMARY
        )
        self.clear_btn.configure(state="normal")

    def _show_action_bar(self) -> None:
        self._enable_actions()

    def _hide_action_bar(self) -> None:
        self._disable_actions()

    def _append_log(self, message: str) -> None:
        if not self.log_box.winfo_ismapped():
            self.log_box.pack(fill="x", pady=(8, 8), after=self.strategy_label)
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"› {message.rstrip()}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _hide_category_picker(self) -> None:
        self._category_options = {}
        try:
            self.category_menu.configure(values=["Select a category…"])
            self.category_menu.set("Select a category…")
        except Exception:
            pass
        if self.category_row.winfo_ismapped():
            self.category_row.pack_forget()

    def _show_category_picker(self, categories: list[dict]) -> None:
        if not categories:
            self._hide_category_picker()
            return
        self._category_options = {
            c["label"]: c["url"] for c in categories if c.get("label") and c.get("url")
        }
        labels = list(self._category_options.keys())
        self.category_menu.configure(values=labels)
        self.category_menu.set(labels[0])
        if not self.category_row.winfo_ismapped():
            self.category_row.pack(fill="x", padx=T.CARD_PADDING, after=self.platform_label)

    def _on_category_selected(self, _label: str) -> None:
        # Selection is read at scrape time; no-op otherwise
        pass

    def _selected_category_url(self) -> str | None:
        if not self._category_options:
            return None
        label = self.category_menu.get()
        return self._category_options.get(label)

    def _clear_results(self) -> None:
        """Reset scraper UI to initial empty state."""
        self.parsed_data = None
        self.source_url = ""
        self.suggested_filename = "shopify_products.csv"
        self.url_entry.delete(0, "end")
        self.error_label.configure(text="")
        self.strategy_label.configure(text="")
        self.count_badge.configure(text="")
        self.platform_label.configure(text="")
        self._hide_category_picker()
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        if self.log_box.winfo_ismapped():
            self.log_box.pack_forget()
        self._clear_preview()
        self._disable_actions()
        self.progress.stop()
        self.progress.pack_forget()
        self.loading_label.pack_forget()
        self.scrape_btn.configure(state="normal")

    def _on_url_changed(self, _event=None) -> None:
        """Detect platform after URL entry; discover categories on homepage."""
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

    def _start_scrape(self) -> None:
        url = self.url_entry.get().strip()
        self.error_label.configure(text="")
        self.strategy_label.configure(text="")
        self.count_badge.configure(text="")
        self.parsed_data = None
        self._disable_actions()
        self._clear_preview()

        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        if not is_valid_url(url):
            self.error_label.configure(text="URL must start with http:// or https://")
            return

        # Homepage → require category selection
        from app.core.html_catalog_scraper import is_homepage_url

        if is_homepage_url(url):
            cat_url = self._selected_category_url()
            if self._category_options:
                if not cat_url:
                    self.error_label.configure(
                        text="Select a category from the dropdown before scraping."
                    )
                    return
                url = cat_url
                self._append_log(f"Using category: {self.category_menu.get()}")
            elif not self.category_row.winfo_ismapped():
                # Categories still loading or none found — try discover sync message
                self.error_label.configure(
                    text="This looks like a homepage. Wait for categories to load, or paste a category URL."
                )
                # Kick detection if needed
                self._on_url_changed()
                return

        self.source_url = url
        self.suggested_filename = output_filename_from_url(url)

        if not self.platform_label.cget("text"):
            self.platform_label.configure(text="Detecting platform…")

        self.scrape_btn.configure(state="disabled")
        self.loading_label.pack(pady=(0, 4))
        self.loading_label.configure(text="Scraping...")
        self.progress.pack(pady=4)
        self.progress.start()
        self._append_log(f"Starting crawl: {url}")

        thread = threading.Thread(target=self._run_scrape, args=(url,), daemon=True)
        thread.start()

    def _on_progress(self, message: str) -> None:
        self.after(0, lambda m=message: self._append_log(m))
        self.after(0, lambda m=message: self.loading_label.configure(text=m[:80]))

    def _run_scrape(self, url: str) -> None:
        try:
            data = crawl(url, progress=self._on_progress)
            self.after(0, lambda: self._on_success(data))
        except CollectionCrawlError as exc:
            msg = str(exc)
            self.after(0, lambda: self._on_error(msg))
        except Exception as exc:  # noqa: BLE001
            msg = f"Unexpected error: {exc}"
            self.after(0, lambda: self._on_error(msg))

    def _on_success(self, data: dict) -> None:
        self.progress.stop()
        self.progress.pack_forget()
        self.loading_label.pack_forget()
        self.scrape_btn.configure(state="normal")
        self.parsed_data = data

        count = data.get("row_count", 0)
        errors = data.get("errors") or []
        extra = f"  ·  {len(errors)} error(s)" if errors else ""
        self.suggested_filename = output_filename_from_url(self.source_url)
        self.count_badge.configure(text=f"{count} products found{extra}")
        platform = data.get("platform") or ""
        strategy = data.get("strategy_used", "")
        if platform:
            self.platform_label.configure(text=f"Detected: {platform}")
        self.strategy_label.configure(
            text=f"{strategy}  ·  → {self.suggested_filename}"
        )
        from app.utils.task_history import save_task

        save_task("Scrape", self.suggested_filename, "Success")
        self._append_log(f"Done — {count} rows ready")
        self._render_preview()
        self._enable_actions()

    def _on_error(self, message: str) -> None:
        self.progress.stop()
        self.progress.pack_forget()
        self.loading_label.pack_forget()
        self.scrape_btn.configure(state="normal")
        self.error_label.configure(text=message)
        self._append_log(f"ERROR: {message}")
        self._disable_actions()

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
                col, text=header, font=T.font(11, "bold"),
                text_color=T.ACCENT, width=130, anchor="w",
            ).pack(anchor="w", pady=(0, 4))

            for i, row in enumerate(rows):
                value = str(row.get(header, ""))[:45]
                bg = T.BG_SURFACE_A if i % 2 == 0 else T.BG_SURFACE_B
                ctk.CTkLabel(
                    col, text=value or "—", font=T.font(11),
                    text_color=T.TEXT_SECONDARY, width=130, anchor="w",
                    fg_color=bg,
                ).pack(anchor="w", ipady=1)

    def _go_mapping(self) -> None:
        if not self.parsed_data:
            return
        from app.ui.mapping_screen import MappingScreen

        self.app.show_screen(
            MappingScreen,
            parsed_data=self.parsed_data,
            suggested_filename=self.suggested_filename,
        )
