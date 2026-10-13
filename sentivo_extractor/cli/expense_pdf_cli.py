"""
Generate an HMRC-friendly expense report PDF for Sentivo Tools.

Reads JSON from stdin:
  {
    "expenses": [...],
    "month": "October 2026",
    "output_path": "C:/Users/.../Downloads/expenses-october-2026.pdf"
  }

Prints: {"ok": true, "path": "..."} or {"ok": false, "errors": [...]}
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CURRENCY_SYMBOLS = {
    "GBP": "£",
    "USD": "$",
    "EUR": "€",
    "PKR": "₨",
    "AUD": "A$",
}


def _money(value: Any, currency: str = "GBP") -> str:
    code = (currency or "GBP").upper()
    symbol = _CURRENCY_SYMBOLS.get(code, f"{code} ")
    try:
        return f"{symbol}{float(value):,.2f}"
    except (TypeError, ValueError):
        return f"{symbol}0.00"


def _resolve_logo() -> Path | None:
    path = _ROOT / "assets" / "sentivo-logo-transparent.png"
    if path.is_file():
        return path
    return None


def get_logo_path(original_path: str | Path) -> str:
    from PIL import Image as PILImage

    img = PILImage.open(original_path).convert("RGBA")
    background = PILImage.new("RGB", img.size, (255, 255, 255))
    background.paste(img, mask=img.split()[3])
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    background.save(tmp.name, "PNG")
    tmp.close()
    return tmp.name


def generate_expense_pdf(
    expenses: list[dict[str, Any]],
    month_label: str,
    output_path: str,
) -> str:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
        HRFlowable,
        Image,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    brand = colors.HexColor("#8B1A2E")
    ink = colors.HexColor("#0D1B2A")
    gray = colors.HexColor("#666666")
    rule = colors.HexColor("#CCCCCC")
    alt_row = colors.HexColor("#F9F9F9")

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ExpTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=22,
            textColor=ink,
            alignment=TA_RIGHT,
            leading=26,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ExpMetaRight",
            parent=styles["Normal"],
            fontSize=9,
            textColor=gray,
            alignment=TA_RIGHT,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ExpCompanyGray",
            parent=styles["Normal"],
            fontSize=8,
            textColor=gray,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ExpSection",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=ink,
            spaceBefore=8,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ExpCell",
            parent=styles["Normal"],
            fontSize=8,
            textColor=ink,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ExpCellRight",
            parent=styles["Normal"],
            fontSize=8,
            textColor=ink,
            alignment=TA_RIGHT,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ExpHead",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=colors.white,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ExpHeadRight",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=colors.white,
            alignment=TA_RIGHT,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ExpFooter",
            parent=styles["Normal"],
            fontSize=8,
            textColor=gray,
            alignment=TA_CENTER,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ExpTotal",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=ink,
            alignment=TA_RIGHT,
            leading=14,
        )
    )

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40,
    )
    story: list[Any] = []
    tmp_logo_path: str | None = None

    left_rows: list[list[Any]] = []
    logo_path = _resolve_logo()
    if logo_path is not None:
        try:
            tmp_logo_path = get_logo_path(logo_path)
            logo = Image(tmp_logo_path, width=180, height=58)
            logo.hAlign = "LEFT"
            left_rows.append([logo])
        except Exception:
            tmp_logo_path = None
    left_rows.append([Paragraph("Sentivo Limited", styles["ExpCompanyGray"])])
    left_rows.append([Paragraph("Company No. 17388623", styles["ExpCompanyGray"])])
    left_rows.append(
        [
            Paragraph(
                "47 Hollinhall Street, Oldham, England, OL4 3EH",
                styles["ExpCompanyGray"],
            )
        ]
    )
    left_nested = Table(left_rows, colWidths=[300])
    left_nested.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    right_bits = [
        Paragraph("EXPENSE REPORT", styles["ExpTitle"]),
        Paragraph(str(month_label or ""), styles["ExpMetaRight"]),
        Paragraph(f"{len(expenses)} expense(s)", styles["ExpMetaRight"]),
    ]
    header = Table([[left_nested, right_bits]], colWidths=[300, 215])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 10))
    story.append(
        HRFlowable(width="100%", thickness=1, color=rule, spaceBefore=0, spaceAfter=12)
    )

    sorted_expenses = sorted(
        expenses or [],
        key=lambda e: str((e or {}).get("date") or ""),
    )

    table_data: list[list[Any]] = [
        [
            Paragraph("Date", styles["ExpHead"]),
            Paragraph("Description", styles["ExpHead"]),
            Paragraph("Category", styles["ExpHead"]),
            Paragraph("Amount", styles["ExpHeadRight"]),
            Paragraph("Currency", styles["ExpHead"]),
        ]
    ]
    for exp in sorted_expenses:
        e = exp or {}
        currency = str(e.get("currency") or "GBP").upper()
        table_data.append(
            [
                Paragraph(str(e.get("date") or "—"), styles["ExpCell"]),
                Paragraph(
                    str(e.get("description") or "—").replace("\n", "<br/>"),
                    styles["ExpCell"],
                ),
                Paragraph(str(e.get("category") or "—"), styles["ExpCell"]),
                Paragraph(_money(e.get("amount"), currency), styles["ExpCellRight"]),
                Paragraph(currency, styles["ExpCell"]),
            ]
        )

    items = Table(table_data, colWidths=[70, 185, 90, 80, 60])
    items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ink),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOX", (0, 0), (-1, -1), 0.5, rule),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, rule),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, alt_row]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(items)
    story.append(Spacer(1, 16))

    # Category subtotals (by currency+category)
    story.append(Paragraph("Category Subtotals", styles["ExpSection"]))
    cat_totals: dict[tuple[str, str], float] = defaultdict(float)
    currency_totals: dict[str, float] = defaultdict(float)
    for exp in sorted_expenses:
        e = exp or {}
        cat = str(e.get("category") or "Other")
        currency = str(e.get("currency") or "GBP").upper()
        try:
            amt = float(e.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        cat_totals[(cat, currency)] += amt
        currency_totals[currency] += amt

    sub_rows: list[list[Any]] = [
        [
            Paragraph("Category", styles["ExpHead"]),
            Paragraph("Currency", styles["ExpHead"]),
            Paragraph("Total", styles["ExpHeadRight"]),
        ]
    ]
    for (cat, currency), total in sorted(cat_totals.items(), key=lambda x: (x[0][0], x[0][1])):
        sub_rows.append(
            [
                Paragraph(cat, styles["ExpCell"]),
                Paragraph(currency, styles["ExpCell"]),
                Paragraph(_money(total, currency), styles["ExpCellRight"]),
            ]
        )
    sub_table = Table(sub_rows, colWidths=[220, 100, 165])
    sub_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), brand),
                ("BOX", (0, 0), (-1, -1), 0.5, rule),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, rule),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(sub_table)
    story.append(Spacer(1, 14))

    grand_bits = []
    for currency, total in sorted(currency_totals.items()):
        grand_bits.append(
            [
                Paragraph(f"Grand Total ({currency})", styles["ExpTotal"]),
                Paragraph(_money(total, currency), styles["ExpTotal"]),
            ]
        )
    if grand_bits:
        grand = Table(grand_bits, colWidths=[370, 115])
        grand.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (-1, 0), 1, rule),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(grand)

    story.append(Spacer(1, 28))
    story.append(
        HRFlowable(width="100%", thickness=1, color=rule, spaceBefore=0, spaceAfter=8)
    )
    story.append(
        Paragraph(
            "Generated for HMRC record keeping &nbsp;&nbsp;|&nbsp;&nbsp; "
            "Sentivo Limited &nbsp;&nbsp;|&nbsp;&nbsp; "
            '<link href="https://sentivo.co.uk" color="#8B1A2E">sentivo.co.uk</link>',
            styles["ExpFooter"],
        )
    )

    try:
        doc.build(story)
    finally:
        if tmp_logo_path:
            try:
                os.unlink(tmp_logo_path)
            except OSError:
                pass
    return str(out.resolve())


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [f"Invalid JSON: {exc}"]}), flush=True)
        return 2

    expenses = payload.get("expenses") or []
    if not isinstance(expenses, list):
        print(
            json.dumps({"ok": False, "errors": ["expenses must be an array."]}),
            flush=True,
        )
        return 2

    month = str(payload.get("month") or "Expenses")
    output_path = str(
        payload.get("output_path") or payload.get("outputPath") or ""
    ).strip()
    if not output_path:
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in month.lower())
        output_path = str(Path.home() / "Downloads" / f"expenses-{safe}.pdf")

    try:
        path = generate_expense_pdf(expenses, month, output_path)
        print(json.dumps({"ok": True, "path": path}), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [str(exc)]}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
