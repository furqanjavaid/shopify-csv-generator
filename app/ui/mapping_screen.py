"""Column mapping screen — two-column table + Auto Map."""

from __future__ import annotations

from tkinter import filedialog
from typing import Any

import customtkinter as ctk

from app.core.column_mapper import SKIP_LABEL, SHOPIFY_FIELDS, ColumnMapper
from app.core.shopify_generator import ShopifyGenerator
from app.ui import theme as T
from app.ui.sidebar import attach_sidebar

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
        super().__init__(parent, fg_color=T.BG_PRIMARY, corner_radius=0)
        self.app = app
        self.parsed_data = parsed_data or {"headers": [], "rows": [], "row_count": 0}
        self.suggested_filename = suggested_filename or "shopify_products.csv"
        if not self.suggested_filename.lower().endswith(".csv"):
            self.suggested_filename += ".csv"
        self.mapper = ColumnMapper()
        self.auto_mapping = self.mapper.auto_map(self.parsed_data["headers"])
        self.dropdowns: list[dict[str, Any]] = []

        body = attach_sidebar(self, app, "upload")

        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x", pady=(0, T.GRID_GAP))

        title_wrap = ctk.CTkFrame(top, fg_color="transparent")
        title_wrap.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            title_wrap, text="Column Mapping",
            font=T.font_tuple(T.H1), text_color=T.TEXT_PRIMARY, anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            title_wrap,
            text="Map client columns to Shopify fields. Adjust any dropdown as needed.",
            font=T.font_tuple(T.BODY), text_color=T.TEXT_SECONDARY, anchor="w",
        ).pack(fill="x", pady=(4, 0))

        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right")
        T.step_indicator(right, current=2, total=4).pack(side="left", padx=(0, 12))
        T.secondary_button(right, "Auto Map", self._auto_map, width=110).pack(side="left")

        self.error_label = ctk.CTkLabel(
            body, text="", font=T.font_tuple(T.CAPTION), text_color=T.ERROR
        )
        self.error_label.pack()

        # Table header
        table_card = T.card_frame(body)
        table_card.pack(fill="both", expand=True, pady=(4, 12))

        header = ctk.CTkFrame(table_card, fg_color=T.BG_SURFACE_B, height=T.ROW_HEIGHT)
        header.pack(fill="x", padx=1, pady=(1, 0))
        header.pack_propagate(False)
        ctk.CTkLabel(
            header, text="Client Column", font=T.font(12, "bold"),
            text_color=T.TEXT_MUTED, width=220, anchor="w",
        ).pack(side="left", padx=16)
        ctk.CTkLabel(
            header, text="Shopify Column", font=T.font(12, "bold"),
            text_color=T.TEXT_MUTED, width=260, anchor="w",
        ).pack(side="left", padx=8)
        ctk.CTkLabel(
            header, text="Confidence", font=T.font(12, "bold"),
            text_color=T.TEXT_MUTED, anchor="e",
        ).pack(side="right", padx=16)

        self.table = ctk.CTkScrollableFrame(
            table_card, fg_color=T.BG_SURFACE_A, corner_radius=0,
        )
        self.table.pack(fill="both", expand=True, padx=1, pady=(0, 1))

        self._rebuild_rows()

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x", side="bottom")
        self.generate_btn = T.primary_button(
            actions, "Generate Shopify CSV →", self._generate, width=220
        )
        self.generate_btn.pack(side="right")

    def _rebuild_rows(self) -> None:
        for child in self.table.winfo_children():
            child.destroy()
        self.dropdowns.clear()
        for item in self.auto_mapping:
            self._add_mapping_row(item["client_col"], item["shopify_field"])

    def _auto_map(self) -> None:
        self.auto_mapping = self.mapper.auto_map(self.parsed_data["headers"])
        self.error_label.configure(text="")
        self._rebuild_rows()

    def _add_mapping_row(self, client_col: str, shopify_field: str | None) -> None:
        idx = len(self.dropdowns)
        bg = T.BG_SURFACE_A if idx % 2 == 0 else T.BG_SURFACE_B
        row = ctk.CTkFrame(self.table, fg_color=bg, height=T.ROW_HEIGHT, corner_radius=0)
        row.pack(fill="x")
        row.pack_propagate(False)

        ctk.CTkLabel(
            row, text=client_col[:40], font=T.font_tuple(T.LABEL),
            text_color=T.TEXT_PRIMARY, width=220, anchor="w",
        ).pack(side="left", padx=16)

        mode, options, selected, fixed_field = self._dropdown_config(
            client_col, shopify_field
        )

        combo = ctk.CTkComboBox(
            row,
            values=options,
            width=260,
            height=32,
            corner_radius=T.BORDER_RADIUS,
            fg_color=T.BG_SURFACE_B,
            border_color=T.BORDER,
            button_color=T.BORDER,
            button_hover_color=T.ACCENT_HOVER,
            dropdown_fg_color=T.BG_SURFACE_A,
            dropdown_hover_color=T.BG_SURFACE_B,
            text_color=T.TEXT_PRIMARY,
            font=T.font(12),
            state="readonly",
        )
        combo.set(selected)
        combo.pack(side="left", padx=8)

        conf = _confidence(
            client_col,
            fixed_field or (selected if selected != SKIP_LABEL else None),
        )
        T.confidence_badge(row, conf).pack(side="right", padx=16)

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
