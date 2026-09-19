"""Column mapping screen — premium card rows."""

from __future__ import annotations

from tkinter import filedialog
from typing import Any

import customtkinter as ctk

from app.core.column_mapper import SKIP_LABEL, SHOPIFY_FIELDS, ColumnMapper
from app.core.shopify_generator import ShopifyGenerator
from app.ui import theme as T

STATUS_VALUES = ["Active", "Draft", "Archived"]
PUBLISHED_VALUES = ["TRUE", "FALSE"]


def relevant_fields(client_column: str) -> list[str]:
    """Return likely Shopify fields for a client column + Skip last."""
    h = (client_column or "").strip().lower()

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


def _confidence(client_col: str, shopify_field: str | None) -> str:
    if not shopify_field or shopify_field == SKIP_LABEL:
        return "low"
    if client_col.strip().lower() == shopify_field.strip().lower():
        return "high"
    return "medium"


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
        super().__init__(parent, fg_color=T.BG, corner_radius=0)
        self.app = app
        self.parsed_data = parsed_data or {"headers": [], "rows": [], "row_count": 0}
        self.suggested_filename = suggested_filename or "shopify_products.csv"
        if not self.suggested_filename.lower().endswith(".csv"):
            self.suggested_filename += ".csv"
        self.mapper = ColumnMapper()
        self.auto_mapping = self.mapper.auto_map(self.parsed_data["headers"])
        self.dropdowns: list[dict[str, Any]] = []

        T.header_bar(self, "Column Mapping", self._go_home, "Step 2 of 3")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(12, 0))

        ctk.CTkLabel(
            body,
            text="AI-detected mappings shown below. Adjust any field using dropdowns.",
            font=T.font(13),
            text_color=T.TEXT_SECONDARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        self.error_label = ctk.CTkLabel(
            body, text="", font=T.font(12), text_color=T.DANGER
        )
        self.error_label.pack()

        self.table = ctk.CTkScrollableFrame(
            body,
            fg_color=T.SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=T.BORDER,
        )
        self.table.pack(fill="both", expand=True, pady=(4, 8))

        for item in self.auto_mapping:
            self._add_mapping_row(item["client_col"], item["shopify_field"])

        bottom = ctk.CTkFrame(self, fg_color=T.SURFACE, height=64, corner_radius=0)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)

        bottom_inner = ctk.CTkFrame(bottom, fg_color="transparent")
        bottom_inner.pack(fill="both", expand=True, padx=24)

        self.generate_btn = T.primary_button(
            bottom_inner,
            "Generate Shopify CSV →",
            self._generate,
            width=220,
        )
        self.generate_btn.pack(side="right", pady=12)

    def _add_mapping_row(self, client_col: str, shopify_field: str | None) -> None:
        card = ctk.CTkFrame(
            self.table,
            fg_color=T.CARD,
            corner_radius=10,
            border_width=1,
            border_color=T.BORDER,
            height=52,
        )
        card.pack(fill="x", padx=8, pady=4)
        card.pack_propagate(False)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=8)

        T.pill(inner, client_col[:36], T.AMBER_BG, T.AMBER).pack(side="left")

        ctk.CTkLabel(
            inner, text="→", font=T.font(14), text_color=T.TEXT_MUTED, width=30
        ).pack(side="left", padx=8)

        mode, options, selected, fixed_field = self._dropdown_config(
            client_col, shopify_field
        )

        combo = ctk.CTkComboBox(
            inner,
            values=options,
            width=260,
            height=30,
            corner_radius=6,
            fg_color=T.BLUE_DIM,
            border_color=T.BORDER,
            button_color=T.BORDER_HOVER,
            button_hover_color=T.ACCENT_HOVER,
            dropdown_fg_color=T.SURFACE,
            dropdown_hover_color=T.CARD,
            text_color=T.TEXT,
            font=T.font(12),
            state="readonly",
        )
        combo.set(selected)
        combo.pack(side="left", padx=(0, 10))

        conf = _confidence(
            client_col,
            fixed_field or (selected if selected != SKIP_LABEL else None),
        )
        T.confidence_badge(inner, conf).pack(side="right")

        if mode == "field":
            combo.configure(
                command=lambda choice, c=combo, col=client_col: self._on_field_choice(
                    c, col, choice
                )
            )

        self.dropdowns.append(
            {
                "widget": combo,
                "mode": mode,
                "shopify_field": fixed_field,
                "client_col": client_col,
            }
        )

    def _dropdown_config(
        self, client_col: str, shopify_field: str | None
    ) -> tuple[str, list[str], str, str | None]:
        selected_field = shopify_field if shopify_field else SKIP_LABEL
        col_lower = (client_col or "").strip().lower()

        if selected_field == "Status" or col_lower == "status":
            current = self._sample_column_value(client_col)
            selected = self._normalize_status(current) or "Active"
            return "status_value", list(STATUS_VALUES), selected, "Status"

        if (
            selected_field == "Published on online store"
            or col_lower == "published on online store"
        ):
            current = self._sample_column_value(client_col)
            selected = self._normalize_published(current) or "TRUE"
            return (
                "published_value",
                list(PUBLISHED_VALUES),
                selected,
                "Published on online store",
            )

        options = relevant_fields(client_col)
        if selected_field != SKIP_LABEL and selected_field not in options:
            options = [selected_field] + [o for o in options if o != selected_field]
        return "field", options, selected_field, None

    def _on_field_choice(
        self, combo: ctk.CTkComboBox, client_col: str, choice: str
    ) -> None:
        meta = next((d for d in self.dropdowns if d["widget"] is combo), None)
        if not meta:
            return

        if choice == "Status":
            combo.configure(values=list(STATUS_VALUES), command=None)
            current = self._normalize_status(self._sample_column_value(client_col))
            combo.set(current or "Active")
            meta["mode"] = "status_value"
            meta["shopify_field"] = "Status"
        elif choice == "Published on online store":
            combo.configure(values=list(PUBLISHED_VALUES), command=None)
            current = self._normalize_published(self._sample_column_value(client_col))
            combo.set(current or "TRUE")
            meta["mode"] = "published_value"
            meta["shopify_field"] = "Published on online store"

    def _sample_column_value(self, client_col: str) -> str:
        for row in self.parsed_data.get("rows") or []:
            value = str(row.get(client_col, "") or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _normalize_status(value: str) -> str | None:
        text = (value or "").strip().lower()
        if text in {"active", "draft", "archived"}:
            return text.capitalize()
        return None

    @staticmethod
    def _normalize_published(value: str) -> str | None:
        text = (value or "").strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return "TRUE"
        if text in {"false", "0", "no", "n"}:
            return "FALSE"
        return None

    def _go_home(self) -> None:
        from app.ui.home_screen import HomeScreen

        self.app.show_screen(HomeScreen)

    def _collect_mapping(self) -> list[dict]:
        mapping = []
        for item, meta in zip(self.auto_mapping, self.dropdowns):
            mode = meta["mode"]
            widget = meta["widget"]
            if mode == "status_value":
                mapping.append(
                    {
                        "client_col": item["client_col"],
                        "shopify_field": "Status",
                        "constant_value": widget.get(),
                    }
                )
            elif mode == "published_value":
                mapping.append(
                    {
                        "client_col": item["client_col"],
                        "shopify_field": "Published on online store",
                        "constant_value": widget.get(),
                    }
                )
            else:
                mapping.append(
                    {
                        "client_col": item["client_col"],
                        "shopify_field": widget.get(),
                    }
                )
        return mapping

    def _apply_constant_values(self, mapping: list[dict]) -> dict:
        rows = [dict(r) for r in self.parsed_data.get("rows") or []]
        for item in mapping:
            constant = item.get("constant_value")
            if not constant:
                continue
            col = item["client_col"]
            for row in rows:
                row[col] = constant
        return {
            "headers": list(self.parsed_data.get("headers") or []),
            "rows": rows,
            "row_count": len(rows),
        }

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

        data = self._apply_constant_values(mapping)

        try:
            result = ShopifyGenerator().generate(data, mapping, output_path)
        except Exception as exc:  # noqa: BLE001
            self.error_label.configure(text=f"Failed to generate CSV: {exc}")
            return

        from app.ui.success_screen import SuccessScreen

        self.app.show_screen(SuccessScreen, result=result)
