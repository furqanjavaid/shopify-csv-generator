"""
CRO Audit Word report — score /10, conversion-priority findings.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import urlparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

FONT = "Arial"
RGB_DARK = RGBColor(0x1A, 0x1A, 0x2E)
RGB_MID = RGBColor(0x2C, 0x3E, 0x50)
RGB_GREY = RGBColor(0x7F, 0x8C, 0x8D)
RGB_RED = RGBColor(0xC0, 0x39, 0x2B)
RGB_ORANGE = RGBColor(0xE6, 0x7E, 0x22)
RGB_GREEN = RGBColor(0x27, 0xAE, 0x60)
RGB_WHITE = RGBColor(0xFF, 0xFF, 0xFF)

CAT_LABELS = {
    "product_page": "Product Page",
    "cart_checkout": "Cart & Checkout",
    "mobile": "Mobile",
    "speed": "Speed",
    "marketing": "Marketing",
}


def set_cell_bg(cell, hex_color: str):
    hex_color = hex_color.lstrip("#")
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def add_run(para, text, size=10, bold=False, color=None, italic=False):
    r = para.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r.bold = bold
    r.italic = italic
    if color:
        r.font.color.rgb = color
    return r


def add_para(doc, text="", size=10, bold=False, color=None, align=None, space_after=6):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    if align:
        p.alignment = align
    if text:
        add_run(p, text, size=size, bold=bold, color=color)
    return p


def add_section_bar(doc, text, bg="1A1A2E"):
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.rows[0].cells[0]
    set_cell_bg(cell, bg)
    p = cell.paragraphs[0]
    r = p.add_run(f"  {text}")
    r.bold = True
    r.font.size = Pt(11)
    r.font.name = FONT
    r.font.color.rgb = RGB_WHITE
    doc.add_paragraph().paragraph_format.space_after = Pt(6)


def add_image_safe(doc, path, width=5.0):
    if path and os.path.exists(path):
        try:
            doc.add_picture(path, width=Inches(width))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            return True
        except Exception:
            pass
    return False


def score_color(score: float):
    if score >= 7.5:
        return RGB_GREEN, "27AE60"
    if score >= 5:
        return RGB_ORANGE, "E67E22"
    return RGB_RED, "C0392B"


def category_status(score: float) -> str:
    if score >= 7.5:
        return "✓ Good"
    if score >= 5:
        return "⚠ Fix"
    return "✗ Critical"


def make_category_chart(scores: dict, output_dir: str) -> str:
    cats = scores.get("categories") or {}
    labels = [CAT_LABELS.get(k, k) for k in cats]
    values = [cats[k] for k in cats]
    colors = ["#27AE60" if v >= 7.5 else "#E67E22" if v >= 5 else "#C0392B" for v in values]

    fig, ax = plt.subplots(figsize=(6.5, max(2.8, len(labels) * 0.55)))
    fig.patch.set_facecolor("white")
    ax.barh(labels, values, color=colors, height=0.55)
    ax.set_xlim(0, 10)
    ax.set_xlabel("Score / 10")
    ax.set_title("CRO Category Scores")
    for i, v in enumerate(values):
        ax.text(min(v + 0.15, 9.5), i, f"{v}", va="center", fontsize=9)
    path = os.path.join(output_dir, "chart_cro_scores.png")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def make_score_badge(score: float, output_dir: str) -> str:
    _, hex_c = score_color(score)
    fig, ax = plt.subplots(figsize=(2.4, 2.4))
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    circle = plt.Circle((0.5, 0.5), 0.42, color=f"#{hex_c}")
    ax.add_patch(circle)
    ax.text(0.5, 0.55, f"{score}", ha="center", va="center", fontsize=28, fontweight="bold", color="white")
    ax.text(0.5, 0.32, "/ 10", ha="center", va="center", fontsize=11, color="white")
    path = os.path.join(output_dir, "chart_health_score.png")
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return path


def generate_report(audit_data: dict, output_dir: str) -> str:
    store_url = audit_data.get("store_url", "")
    audit_date = audit_data.get("audit_date", datetime.now().isoformat())
    findings = audit_data.get("findings") or []
    scores = audit_data.get("scores") or {"categories": {}, "overall": 0}
    screenshot_dir = os.path.join(output_dir, "screenshots")

    domain = urlparse(store_url).netloc.replace("www.", "")
    store_name = domain.split(".")[0].title() if domain else "Store"
    try:
        date_str = datetime.fromisoformat(audit_date).strftime("%B %d, %Y")
        file_date = datetime.fromisoformat(audit_date).strftime("%Y-%m-%d")
    except Exception:
        date_str = audit_date
        file_date = datetime.now().strftime("%Y-%m-%d")

    overall = float(scores.get("overall") or 0)
    badge = make_score_badge(overall, output_dir)
    chart = make_category_chart(scores, output_dir)

    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # ── PAGE 1 Cover ──
    add_para(doc, "CRO AUDIT REPORT", size=26, bold=True, color=RGB_DARK, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, store_name, size=18, bold=True, color=RGB_MID, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, store_url, size=10, color=RGB_GREY, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, date_str, size=10, color=RGB_GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=12)

    sc, _ = score_color(overall)
    add_para(doc, f"Overall CRO Score: {overall}/10", size=20, bold=True, color=sc, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_image_safe(doc, badge, width=2.0)
    add_para(doc, "Prepared by: Sentivo Limited", size=10, color=RGB_GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=18)
    home_ss = os.path.join(screenshot_dir, "homepage_desktop.png")
    add_image_safe(doc, home_ss, width=5.5)
    doc.add_page_break()

    # ── PAGE 2 Executive Summary ──
    add_section_bar(doc, "EXECUTIVE SUMMARY")
    add_para(doc, f"Overall CRO Score: {overall}/10", size=14, bold=True, color=sc)

    highs = [f for f in findings if f.get("severity") == "HIGH" and not f.get("passed")]
    mediums = [f for f in findings if f.get("severity") == "MEDIUM" and not f.get("passed")]
    lows = [f for f in findings if f.get("severity") == "LOW" and not f.get("passed")]

    add_para(doc, "Top critical issues", size=12, bold=True, color=RGB_RED, space_after=4)
    if highs:
        for f in highs[:3]:
            add_para(doc, f"• {f.get('issue', '')}", size=10, space_after=2)
    else:
        add_para(doc, "• No HIGH severity issues found.", size=10, color=RGB_GREEN)

    add_para(doc, "Quick wins", size=12, bold=True, color=RGB_ORANGE, space_after=4)
    if mediums:
        for f in mediums[:4]:
            add_para(doc, f"• {f.get('issue', '')}", size=10, space_after=2)
    else:
        add_para(doc, "• No medium-priority gaps flagged.", size=10)

    # Health paragraph
    if overall >= 7.5:
        summary = (
            f"{store_name} shows a strong conversion foundation ({overall}/10). "
            "Focus remaining effort on polishing medium gaps and protecting page speed."
        )
    elif overall >= 5:
        summary = (
            f"{store_name} is conversion-capable but leaking revenue ({overall}/10). "
            "Prioritize HIGH issues (ATC, reviews, trust, checkout) before marketing polish."
        )
    else:
        summary = (
            f"{store_name} has critical conversion blockers ({overall}/10). "
            "Fix ATC visibility, trust/reviews, and checkout reliability before traffic spend."
        )
    add_para(doc, summary, size=10, space_after=12)
    doc.add_page_break()

    # ── PAGE 3 Score Breakdown ──
    add_section_bar(doc, "SCORE BREAKDOWN")
    add_image_safe(doc, chart, width=5.8)

    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = table.rows[0].cells
    for i, label in enumerate(("Category", "Score", "Status")):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        add_run(p, label, size=10, bold=True, color=RGB_WHITE)
        set_cell_bg(hdr[i], "1A1A2E")

    for key, val in (scores.get("categories") or {}).items():
        row = table.add_row().cells
        row[0].text = CAT_LABELS.get(key, key)
        row[1].text = f"{val}/10"
        row[2].text = category_status(val)

    doc.add_paragraph()
    doc.add_page_break()

    # ── Detailed findings ──
    add_section_bar(doc, "DETAILED FINDINGS", bg="C0392B")
    add_para(doc, "🔴 CRITICAL ISSUES (HIGH)", size=14, bold=True, color=RGB_RED)

    def write_full_issue(f: dict):
        add_para(doc, f.get("issue", ""), size=11, bold=True, space_after=2)
        add_para(doc, f"Evidence: {f.get('evidence', '')}", size=9, color=RGB_GREY, space_after=1)
        urls = f.get("urls") or []
        if urls:
            add_para(doc, f"URL(s): {', '.join(urls[:4])}", size=8, color=RGB_GREY, space_after=1)
        if f.get("impact"):
            add_para(doc, f"Why it matters: {f['impact']}", size=9, space_after=1)
        if f.get("fix"):
            add_para(doc, f"How to fix: {f['fix']}", size=9, space_after=4)
        shot = f.get("screenshot") or ""
        if shot:
            add_image_safe(doc, os.path.join(screenshot_dir, shot), width=4.8)

    if highs:
        for f in highs:
            write_full_issue(f)
    else:
        add_para(doc, "No critical issues.", size=10, color=RGB_GREEN)

    doc.add_page_break()
    add_section_bar(doc, "IMPROVEMENTS", bg="E67E22")
    add_para(doc, "🟡 IMPROVEMENTS (MEDIUM)", size=14, bold=True, color=RGB_ORANGE)
    if mediums:
        for f in mediums:
            write_full_issue(f)
    else:
        add_para(doc, "No medium issues.", size=10, color=RGB_GREEN)

    doc.add_page_break()
    add_section_bar(doc, "NICE TO HAVE", bg="27AE60")
    add_para(doc, "🟢 NICE TO HAVE (LOW)", size=14, bold=True, color=RGB_GREEN)
    if lows:
        for f in lows:
            add_para(doc, f"• {f.get('issue', '')} — {f.get('fix', '')}", size=9, space_after=3)
    else:
        add_para(doc, "• No low-priority gaps.", size=10)

    # Passed checks brief
    passes = [f for f in findings if f.get("passed") and f.get("status") == "pass"]
    if passes:
        add_para(doc, "Passing checks", size=12, bold=True, space_after=4)
        for f in passes:
            add_para(doc, f"✓ {f.get('check_name')}: {f.get('evidence', '')[:80]}", size=8, color=RGB_GREY, space_after=1)

    doc.add_page_break()
    add_section_bar(doc, "ABOUT SENTIVO")
    add_para(
        doc,
        "This audit was prepared by Sentivo Limited.\n"
        "For implementation support, contact: hello@sentivo.co",
        size=11,
        space_after=8,
    )
    add_para(doc, store_url, size=9, color=RGB_GREY)

    safe_domain = re.sub(r"[^\w\-]", "_", domain)
    out_name = f"CRO_Audit_{safe_domain}_{file_date}.docx"
    out_path = os.path.join(output_dir, out_name)
    doc.save(out_path)
    return out_path


# Back-compat aliases used by older code paths
def deduplicate_results(results: list) -> dict:
    return {}


def compute_cro_scores(deduped: dict) -> dict:
    return {"categories": {}, "overall": 0}
