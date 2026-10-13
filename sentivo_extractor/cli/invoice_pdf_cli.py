"""
Generate a professional invoice PDF for Sentivo Tools.

Reads one JSON object from stdin with at least:
  {
    "output_path": "C:/Users/.../Downloads/INV-001.pdf",
    "invoice": { ... invoice fields ... }
  }

Prints a JSON result object to stdout: {"ok": true, "path": "..."} or errors.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
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
    "CAD": "C$",
    "SAR": "SAR ",
    "AED": "AED ",
}

_CURRENCY_LABELS = {
    "GBP": "GBP (£)",
    "USD": "USD ($)",
    "EUR": "EUR (€)",
    "PKR": "PKR (₨)",
    "AUD": "AUD (A$)",
    "CAD": "CAD (C$)",
    "SAR": "SAR",
    "AED": "AED",
}


def _load_invoice_settings() -> dict[str, Any]:
    path = _ROOT / "data" / "invoice-settings.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


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


def generate_invoice_pdf(invoice: dict[str, Any], output_path: str) -> str:
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
    soft_bg = colors.HexColor("#F5F5F5")
    alt_row = colors.HexColor("#F9F9F9")
    notes_color = colors.HexColor("#333333")

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="InvTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=28,
            textColor=ink,
            alignment=TA_RIGHT,
            leading=32,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvNumber",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=brand,
            alignment=TA_RIGHT,
            leading=14,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvMetaRight",
            parent=styles["Normal"],
            fontSize=9,
            textColor=gray,
            alignment=TA_RIGHT,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvCompanyGray",
            parent=styles["Normal"],
            fontSize=8,
            textColor=gray,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvSectionLabel",
            parent=styles["Normal"],
            fontSize=7,
            textColor=gray,
            fontName="Helvetica-Bold",
            spaceAfter=4,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvClientName",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=ink,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvBody",
            parent=styles["Normal"],
            fontSize=10,
            textColor=ink,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvNotes",
            parent=styles["Normal"],
            fontSize=10,
            textColor=notes_color,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvCell",
            parent=styles["Normal"],
            fontSize=9,
            textColor=ink,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvCellRight",
            parent=styles["Normal"],
            fontSize=9,
            textColor=ink,
            alignment=TA_RIGHT,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvHeadCell",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=colors.white,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvHeadCellRight",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=colors.white,
            alignment=TA_RIGHT,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvTotalLabel",
            parent=styles["Normal"],
            fontSize=10,
            textColor=ink,
            alignment=TA_RIGHT,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvTotalValue",
            parent=styles["Normal"],
            fontSize=10,
            textColor=ink,
            alignment=TA_RIGHT,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvTotalLabelBold",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=ink,
            alignment=TA_RIGHT,
            leading=15,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvTotalValueBold",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=ink,
            alignment=TA_RIGHT,
            leading=15,
        )
    )
    styles.add(
        ParagraphStyle(
            name="InvFooter",
            parent=styles["Normal"],
            fontSize=8,
            textColor=gray,
            alignment=TA_CENTER,
            leading=11,
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

    number = str(invoice.get("number") or "INV-000")
    inv_date = str(invoice.get("date") or "")
    due_date = str(invoice.get("dueDate") or invoice.get("due_date") or "")
    currency = str(invoice.get("currency") or "GBP").upper()
    currency_label = _CURRENCY_LABELS.get(currency, currency)

    # --- Section 1: Header ---
    left_rows: list[list[Any]] = []
    logo_path = _resolve_logo()
    if logo_path is not None:
        try:
            tmp_logo_path = get_logo_path(logo_path)
            logo = Image(tmp_logo_path, width=200, height=65)
            logo.hAlign = "LEFT"
            left_rows.append([logo])
        except Exception:
            tmp_logo_path = None
    left_rows.append([Paragraph("Company No. 17388623", styles["InvCompanyGray"])])
    left_rows.append(
        [
            Paragraph(
                "47 Hollinhall Street, Oldham, England, OL4 3EH",
                styles["InvCompanyGray"],
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

    right_bits: list[Any] = [
        Paragraph("INVOICE", styles["InvTitle"]),
        Paragraph(number, styles["InvNumber"]),
        Paragraph(f"Date: {inv_date}", styles["InvMetaRight"]),
        Paragraph(f"Due: {due_date}", styles["InvMetaRight"]),
    ]

    header = Table([[left_nested, right_bits]], colWidths=[300, 215])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 10))
    story.append(
        HRFlowable(width="100%", thickness=1, color=rule, spaceBefore=0, spaceAfter=0)
    )
    story.append(Spacer(1, 10))

    # --- Section 2: Bill To / Details ---
    client_name = str(invoice.get("clientName") or invoice.get("client_name") or "")
    client_company = str(
        invoice.get("clientCompany") or invoice.get("client_company") or ""
    )
    client_email = str(invoice.get("clientEmail") or invoice.get("client_email") or "")
    client_address = str(
        invoice.get("clientAddress") or invoice.get("client_address") or ""
    )
    address_html = "<br/>".join(
        line.strip() for line in client_address.splitlines() if line.strip()
    )

    bill_to: list[Any] = [Paragraph("BILL TO", styles["InvSectionLabel"])]
    bill_to.append(Paragraph(client_name or "—", styles["InvClientName"]))
    if client_company:
        bill_to.append(Paragraph(client_company, styles["InvBody"]))
    if client_email:
        bill_to.append(Paragraph(client_email, styles["InvBody"]))
    if address_html:
        bill_to.append(Paragraph(address_html, styles["InvBody"]))

    details: list[Any] = [
        Paragraph("DETAILS", styles["InvSectionLabel"]),
        Paragraph(f"Invoice: {number}", styles["InvBody"]),
        Paragraph(f"Invoice date: {inv_date}", styles["InvBody"]),
        Paragraph(f"Due date: {due_date or '—'}", styles["InvBody"]),
        Paragraph(f"Currency: {currency_label}", styles["InvBody"]),
    ]

    info = Table([[bill_to, details]], colWidths=[257, 257])
    info.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), soft_bg),
                ("BOX", (0, 0), (-1, -1), 0.5, rule),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(info)
    story.append(Spacer(1, 16))

    # --- Section 3: Line items ---
    items = invoice.get("lineItems") or invoice.get("line_items") or []
    table_data: list[list[Any]] = [
        [
            Paragraph("Description", styles["InvHeadCell"]),
            Paragraph("Qty", styles["InvHeadCellRight"]),
            Paragraph("Unit Price", styles["InvHeadCellRight"]),
            Paragraph("Amount", styles["InvHeadCellRight"]),
        ]
    ]
    for item in items:
        desc = str((item or {}).get("description") or "")
        qty = (item or {}).get("qty", 0)
        unit = (item or {}).get("unitPrice", (item or {}).get("unit_price", 0))
        amount = (item or {}).get("amount", 0)
        try:
            qty_s = f"{float(qty):g}"
        except (TypeError, ValueError):
            qty_s = str(qty)
        table_data.append(
            [
                Paragraph(desc.replace("\n", "<br/>") or "—", styles["InvCell"]),
                Paragraph(qty_s, styles["InvCellRight"]),
                Paragraph(_money(unit, currency), styles["InvCellRight"]),
                Paragraph(_money(amount, currency), styles["InvCellRight"]),
            ]
        )

    items_table = Table(table_data, colWidths=[295, 50, 85, 85])
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ink),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOX", (0, 0), (-1, -1), 0.5, rule),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, rule),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, alt_row]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(items_table)
    story.append(Spacer(1, 8))

    # --- Section 4: Totals ---
    subtotal = invoice.get("subtotal", 0)
    vat_enabled = bool(
        invoice.get("vat")
        or invoice.get("vatEnabled")
        or invoice.get("vat_enabled")
    )
    vat_amount = invoice.get("vatAmount", invoice.get("vat_amount", 0))
    total = invoice.get("total", 0)

    totals_rows: list[list[Any]] = [
        [
            Paragraph("Subtotal", styles["InvTotalLabel"]),
            Paragraph(_money(subtotal, currency), styles["InvTotalValue"]),
        ]
    ]
    if vat_enabled:
        totals_rows.append(
            [
                Paragraph("VAT (20%)", styles["InvTotalLabel"]),
                Paragraph(_money(vat_amount, currency), styles["InvTotalValue"]),
            ]
        )
    totals_rows.append(
        [
            Paragraph("Total", styles["InvTotalLabelBold"]),
            Paragraph(_money(total, currency), styles["InvTotalValueBold"]),
        ]
    )
    totals = Table(totals_rows, colWidths=[370, 145])
    totals.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEABOVE", (0, -1), (-1, -1), 1, rule),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(totals)
    story.append(Spacer(1, 20))

    # --- Section 5: Notes ---
    notes = str(invoice.get("notes") or "").strip()
    if notes:
        story.append(Paragraph("NOTES", styles["InvSectionLabel"]))
        story.append(Paragraph(notes.replace("\n", "<br/>"), styles["InvNotes"]))
        story.append(Spacer(1, 12))

    # --- Section 6: Payment ---
    settings = _load_invoice_settings()
    bank_name = str(settings.get("bankName") or "").strip()
    account_name = str(settings.get("accountName") or "").strip()
    sort_code = str(settings.get("sortCode") or "").strip()
    account_number = str(settings.get("accountNumber") or "").strip()
    iban = str(settings.get("iban") or "").strip()
    payoneer = str(settings.get("payoneerLink") or "").strip()
    settings_paypal = str(settings.get("paypalLink") or "").strip()
    invoice_paypal = str(
        invoice.get("paypalLink") or invoice.get("paypal_link") or ""
    ).strip()
    paypal = settings_paypal or invoice_paypal

    payment_lines: list[Any] = []
    has_bank = any([bank_name, account_name, sort_code, account_number, iban])
    if has_bank:
        payment_lines.append(Paragraph("PAYMENT DETAILS", styles["InvSectionLabel"]))
        if bank_name:
            payment_lines.append(Paragraph(f"Bank: {bank_name}", styles["InvBody"]))
        if account_name:
            payment_lines.append(
                Paragraph(f"Account Name: {account_name}", styles["InvBody"])
            )
        if sort_code:
            payment_lines.append(
                Paragraph(f"Sort Code: {sort_code}", styles["InvBody"])
            )
        if account_number:
            payment_lines.append(
                Paragraph(f"Account Number: {account_number}", styles["InvBody"])
            )
        if iban:
            payment_lines.append(Paragraph(f"IBAN: {iban}", styles["InvBody"]))

    if payoneer or paypal:
        if not has_bank:
            payment_lines.append(Paragraph("PAYMENT", styles["InvSectionLabel"]))
        else:
            payment_lines.append(Spacer(1, 8))
        if payoneer:
            payment_lines.append(
                Paragraph(
                    f'Pay via Payoneer: <link href="{payoneer}" color="#8B1A2E">'
                    f"<u>{payoneer}</u></link>",
                    styles["InvBody"],
                )
            )
        if paypal:
            payment_lines.append(
                Paragraph(
                    f'Pay via PayPal: <link href="{paypal}" color="#8B1A2E">'
                    f"<u>{paypal}</u></link>",
                    styles["InvBody"],
                )
            )

    if payment_lines:
        for bit in payment_lines:
            story.append(bit)
        story.append(Spacer(1, 30))
    else:
        story.append(Spacer(1, 30))

    # --- Section 7: Footer ---
    story.append(
        HRFlowable(width="100%", thickness=1, color=rule, spaceBefore=0, spaceAfter=8)
    )
    story.append(
        Paragraph(
            "Sentivo Limited &nbsp;&nbsp;|&nbsp;&nbsp; "
            '<link href="https://sentivo.co.uk" color="#8B1A2E">sentivo.co.uk</link>'
            " &nbsp;&nbsp;|&nbsp;&nbsp; Registered in England &amp; Wales",
            styles["InvFooter"],
        )
    )

    invoice_data = invoice

    def add_watermark(canvas, doc_):
        if str(invoice_data.get("status") or "") == "Paid":
            canvas.saveState()
            canvas.setFillColorRGB(0, 0.5, 0, alpha=0.12)
            canvas.setFont("Helvetica-Bold", 90)
            canvas.translate(doc_.pagesize[0] / 2, doc_.pagesize[1] / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, "PAID")
            canvas.restoreState()

    try:
        doc.build(story, onFirstPage=add_watermark, onLaterPages=add_watermark)
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

    invoice = payload.get("invoice") or payload
    output_path = str(
        payload.get("output_path")
        or payload.get("outputPath")
        or ""
    ).strip()
    if not output_path:
        number = str((invoice or {}).get("number") or "INV-000")
        output_path = str(Path.home() / "Downloads" / f"{number}.pdf")

    if not isinstance(invoice, dict) or not invoice.get("number"):
        print(
            json.dumps({"ok": False, "errors": ["Invoice payload missing number."]}),
            flush=True,
        )
        return 2

    try:
        path = generate_invoice_pdf(invoice, output_path)
        print(json.dumps({"ok": True, "path": path}), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [str(exc)]}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
