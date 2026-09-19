"""Column mapping screen."""

from __future__ import annotations

from tkinter import filedialog

import customtkinter as ctk

from app.core.column_mapper import SKIP_LABEL, SHOPIFY_FIELDS, ColumnMapper
from app.core.shopify_generator import ShopifyGenerator


def relevant_fields(client_column: str) -> list[str]:
    """Return likely Shopify fields for a client column (max ~8) + Skip last."""
    h = (client_column or "").strip().lower()

    # Exact Shopify field name → that field first, full list available, Skip last
    for field in SHOPIFY_FIELDS:
        if field.lower() == h:
            rest = [f for f in SHOPIFY_FIELDS if f != field]
            return [field] + rest + [SKIP_LABEL]

    rules: list[tuple[list[str], list[str]]] = [
        (["title", "name", "product"], ["Title", "Description", "Vendor", "Type"]),
        (["price", "mrp", "cost", "rate"], ["Price", "Compare-at price", "Cost per item"]),
        (["sku", "code", "item", "article"], ["SKU", "Barcode"]),
        (["desc", "detail", "about", "body"], ["Description", "SEO description"]),
        (["vendor", "brand", "company"], ["Vendor", "Title"]),
        (["tag", "keyword"], ["Tags"]),
        (["type", "category"], ["Type", "Product category", "Tags"]),
        (["image", "photo", "img", "url"], ["Product image URL", "Variant image URL", "Image position"]),
        (["weight", "gram"], ["Weight value (grams)"]),
        (["qty", "stock", "inventory"], ["Inventory quantity"]),
        (["barcode", "ean", "upc"], ["Barcode", "SKU"]),
        (
            ["size", "colour", "color", "material", "option"],
            [
                "Option1 value",
                "Option2 value",
                "Option3 value",
                "Option1 name",
                "Option2 name",
                "Option3 name",
            ],
        ),
        (["status", "publish", "active"], ["Status", "Published on online store"]),
        (["seo", "meta"], ["SEO title", "SEO description"]),
    ]

    for keywords, fields in rules:
        if any(kw in h for kw in keywords):
            seen: set[str] = set()
            options: list[str] = []
            for field in fields:
                if field not in seen:
                    seen.add(field)
                    options.append(field)
                if len(options) >= 8:
                    break
            options.append(SKIP_LABEL)
            return options

    return list(SHOPIFY_FIELDS) + [SKIP_LABEL]


class MappingScreen(ctk.CTkFrame):
    """Map client columns to Shopify CSV fields and generate output."""

    def __init__(
        self,
        parent,
        app,
        parsed_data=None,
        suggested_filename: str | None = None,
        **kwargs,
    ):
        super().__init__(parent, fg_color="#1a1a2e", corner_radius=0)
        self.app = app
        self.parsed_data = parsed_data or {"headers": [], "rows": [], "row_count": 0}
        self.suggested_filename = suggested_filename or "shopify_products.csv"
        if not self.suggested_filename.lower().endswith(".csv"):
            self.suggested_filename += ".csv"
        self.mapper = ColumnMapper()
        self.auto_mapping = self.mapper.auto_map(self.parsed_data["headers"])
        self.dropdowns: list[ctk.CTkOptionMenu] = []

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
            text="Map Columns to Shopify Fields",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#ffffff",
        )
        title.pack(pady=(4, 4))

        subtitle = ctk.CTkLabel(
            self,
            text="Review the auto-mapping below. Change any field using the dropdowns.",
            font=ctk.CTkFont(size=13),
            text_color="#9ca3af",
        )
        subtitle.pack(pady=(0, 12))

        self.error_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=13),
            text_color="#ef4444",
        )
        self.error_label.pack()

        self.table = ctk.CTkScrollableFrame(
            self,
            width=820,
            height=400,
            fg_color="#0f172a",
        )
        self.table.pack(padx=20, pady=8, fill="both", expand=True)

        header_row = ctk.CTkFrame(self.table, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            header_row,
            text="Client Column",
            width=220,
            anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#93c5fd",
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            header_row,
            text="",
            width=40,
            text_color="#6b7280",
        ).pack(side="left")
        ctk.CTkLabel(
            header_row,
            text="Shopify Field",
            width=280,
            anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#93c5fd",
        ).pack(side="left")

        for item in self.auto_mapping:
            self._add_mapping_row(item["client_col"], item["shopify_field"])

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", side="bottom", padx=20, pady=16)

        self.generate_btn = ctk.CTkButton(
            bottom,
            text="Generate Shopify CSV →",
            width=200,
            height=36,
            command=self._generate,
        )
        self.generate_btn.pack(side="right")

    def _add_mapping_row(self, client_col: str, shopify_field: str | None) -> None:
        row = ctk.CTkFrame(self.table, fg_color="transparent")
        row.pack(fill="x", pady=4)

        ctk.CTkLabel(
            row,
            text=client_col,
            width=220,
            anchor="w",
            font=ctk.CTkFont(size=13),
            text_color="#9ca3af",
        ).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            row,
            text="→",
            width=40,
            text_color="#6b7280",
        ).pack(side="left")

        selected = shopify_field if shopify_field else SKIP_LABEL
        options = relevant_fields(client_col)

        # Ensure auto-mapped value is always selectable even if outside relevance list
        if selected != SKIP_LABEL and selected not in options:
            options = [selected] + [o for o in options if o != selected]

        menu = ctk.CTkOptionMenu(
            row,
            values=options,
            width=300,
            height=30,
            fg_color="#16213e",
            button_color="#1f2f54",
            button_hover_color="#2a3f6b",
        )
        menu.set(selected)
        menu.pack(side="left")
        self.dropdowns.append(menu)

    def _go_home(self) -> None:
        from app.ui.home_screen import HomeScreen

        self.app.show_screen(HomeScreen)

    def _collect_mapping(self) -> list[dict]:
        mapping = []
        for item, menu in zip(self.auto_mapping, self.dropdowns):
            mapping.append(
                {
                    "client_col": item["client_col"],
                    "shopify_field": menu.get(),
                }
            )
        return mapping

    def _generate(self) -> None:
        self.error_label.configure(text="")
        mapping = self._collect_mapping()

        mapped_fields = {
            m["shopify_field"]
            for m in mapping
            if m["shopify_field"] and m["shopify_field"] != SKIP_LABEL
        }
        if "Title" not in mapped_fields:
            self.error_label.configure(
                text="At least one column must map to Title before generating."
            )
            return

        output_path = filedialog.asksaveasfilename(
            title="Save Shopify CSV",
            defaultextension=".csv",
            initialfile=self.suggested_filename,
            filetypes=[("CSV files", "*.csv")],
        )
        if not output_path:
            return

        try:
            result = ShopifyGenerator().generate(
                self.parsed_data, mapping, output_path
            )
        except Exception as exc:  # noqa: BLE001
            self.error_label.configure(text=f"Failed to generate CSV: {exc}")
            return

        from app.ui.success_screen import SuccessScreen

        self.app.show_screen(SuccessScreen, result=result)
