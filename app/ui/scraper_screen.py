"""URL / collection scraper screen."""

from __future__ import annotations

import threading

import customtkinter as ctk

from app.core.collection_crawler import CollectionCrawlError, crawl
from app.utils.helpers import is_valid_url, output_filename_from_url


class ScraperScreen(ctk.CTkFrame):
    """Scrape a Shopify collection URL and preview results."""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color="#1a1a2e", corner_radius=0)
        self.app = app
        self.parsed_data: dict | None = None
        self.source_url: str = ""
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
            text="Scrape Collection URL",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#ffffff",
        )
        title.pack(pady=(8, 8))

        hint = ctk.CTkLabel(
            self,
            text="Paste a Shopify collection URL (e.g. …/collections/door-closers)",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
        )
        hint.pack(pady=(0, 16))

        input_row = ctk.CTkFrame(self, fg_color="transparent")
        input_row.pack(fill="x", padx=40)

        self.url_entry = ctk.CTkEntry(
            input_row,
            placeholder_text="https://www.aluspec.co.uk/collections/door-closers",
            height=40,
            font=ctk.CTkFont(size=13),
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.scrape_btn = ctk.CTkButton(
            input_row,
            text="Scrape",
            width=110,
            height=40,
            command=self._start_scrape,
        )
        self.scrape_btn.pack(side="right")

        self.loading_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=13),
            text_color="#93c5fd",
        )
        self.loading_label.pack(pady=(12, 4))

        self.progress = ctk.CTkProgressBar(self, width=400, mode="indeterminate")
        self.progress.pack(pady=4)
        self.progress.pack_forget()

        self.strategy_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#6b7280",
        )
        self.strategy_label.pack()

        self.error_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=13),
            text_color="#ef4444",
        )
        self.error_label.pack(pady=4)

        self.preview_frame = ctk.CTkScrollableFrame(
            self,
            width=820,
            height=200,
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

    def _start_scrape(self) -> None:
        url = self.url_entry.get().strip()
        self.error_label.configure(text="")
        self.strategy_label.configure(text="")
        self.parsed_data = None
        self.source_url = url
        self.suggested_filename = output_filename_from_url(url)
        self.next_btn.configure(state="disabled")
        self._clear_preview()

        if not is_valid_url(url):
            self.error_label.configure(
                text="URL must start with http:// or https://"
            )
            return

        self.scrape_btn.configure(state="disabled")
        self.loading_label.configure(text="Starting crawl...")
        self.progress.pack(pady=4)
        self.progress.start()

        thread = threading.Thread(target=self._run_scrape, args=(url,), daemon=True)
        thread.start()

    def _on_progress(self, message: str) -> None:
        self.after(0, lambda m=message: self.loading_label.configure(text=m))

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
        self.loading_label.configure(text="")
        self.scrape_btn.configure(state="normal")
        self.parsed_data = data

        count = data.get("row_count", 0)
        errors = data.get("errors") or []
        extra = f"  ·  {len(errors)} product error(s)" if errors else ""
        self.suggested_filename = output_filename_from_url(self.source_url)
        self.strategy_label.configure(
            text=(
                f"{count} products found{extra}  ·  {data.get('strategy_used', '')}"
                f"  ·  → {self.suggested_filename}"
            )
        )
        self._render_preview()
        self.next_btn.configure(state="normal")

    def _on_error(self, message: str) -> None:
        self.progress.stop()
        self.progress.pack_forget()
        self.loading_label.configure(text="")
        self.scrape_btn.configure(state="normal")
        self.error_label.configure(text=message)
        self.next_btn.configure(state="disabled")

    def _clear_preview(self) -> None:
        for child in self.preview_frame.winfo_children():
            child.destroy()

    def _render_preview(self) -> None:
        self._clear_preview()
        if not self.parsed_data:
            return

        headers = self.parsed_data["headers"]
        # Show a useful subset in preview
        preview_headers = [
            h
            for h in (
                "Title",
                "Vendor",
                "SKU",
                "Price",
                "Compare-at price",
                "Option1 name",
                "Option1 value",
                "Option2 name",
                "Option2 value",
                "Product image URL",
            )
            if h in headers
        ] or headers[:8]

        rows = self.parsed_data["rows"][:5]

        for col_idx, header in enumerate(preview_headers):
            col = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
            col.grid(row=0, column=col_idx, padx=6, sticky="nw")

            ctk.CTkLabel(
                col,
                text=header,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#93c5fd",
                width=140,
                anchor="w",
            ).pack(anchor="w")

            for row in rows:
                value = str(row.get(header, ""))[:50]
                ctk.CTkLabel(
                    col,
                    text=value or "—",
                    font=ctk.CTkFont(size=11),
                    text_color="#d1d5db",
                    width=140,
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
