import json
import os
import re
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# ─────────────────────────────────────────────
#  FONTS
# ─────────────────────────────────────────────
FONT_HEADING = "Arial"
FONT_BODY    = "Arial"
FONT_CODE    = "Courier New"

# ─────────────────────────────────────────────
#  COLORS
# ─────────────────────────────────────────────
C_RED    = "#C0392B"
C_ORANGE = "#E67E22"
C_GREEN  = "#27AE60"
C_DARK   = "#1A1A2E"
C_MID    = "#2C3E50"
C_GREY   = "#7F8C8D"
C_ACCENT = "#2980B9"

RGB_RED    = RGBColor(0xC0, 0x39, 0x2B)
RGB_ORANGE = RGBColor(0xE6, 0x7E, 0x22)
RGB_GREEN  = RGBColor(0x27, 0xAE, 0x60)
RGB_DARK   = RGBColor(0x1A, 0x1A, 0x2E)
RGB_MID    = RGBColor(0x2C, 0x3E, 0x50)
RGB_GREY   = RGBColor(0x7F, 0x8C, 0x8D)
RGB_ACCENT = RGBColor(0x29, 0x80, 0xB9)
RGB_WHITE  = RGBColor(0xFF, 0xFF, 0xFF)

CRO_CATEGORY_LABELS = {
    "cart_flow":    "Cart Flow",
    "trust":        "Trust Signals",
    "social_proof": "Social Proof",
    "urgency":      "Urgency",
    "aov":          "Avg Order Value",
    "product_page": "Product Page",
    "homepage":     "Homepage",
    "email_capture":"Email Capture",
    "mobile_ux":    "Mobile UX",
    "speed":        "Page Speed",
    "retention":    "Retention",
}

CHECK_LABELS = {
    "cro_product":  "Product Page CRO",
    "cro_homepage": "Homepage CRO",
    "cro_cart":     "Cart & Checkout Flow",
    "cro_mobile":   "Mobile UX",
    "cro_speed":    "Page Speed",
    "meta_seo":     "SEO — Meta & Headings",
    "images":       "Images & Alt Text",
    "speed":        "Page Speed Signals",
    "schema":       "Structured Data",
    "broken_links": "Broken Links",
    "navigation":   "Navigation & Footer",
}

SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


# ─────────────────────────────────────────────
#  DOCX HELPERS
# ─────────────────────────────────────────────

def set_cell_bg(cell, hex_color: str):
    hex_color = hex_color.lstrip("#")
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd  = OxmlElement("w:shd")
    shd.set(qn("w:val"),   "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  hex_color)
    tcPr.append(shd)


def cell_valign(cell, align="center"):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    va   = OxmlElement("w:vAlign")
    va.set(qn("w:val"), align)
    tcPr.append(va)


def set_col_width(table, col_index, width_inches):
    for row in table.rows:
        row.cells[col_index].width = Inches(width_inches)


def add_run(para, text, size=10, bold=False, color=None, font=FONT_BODY, italic=False):
    r = para.add_run(text)
    r.font.name   = font
    r.font.size   = Pt(size)
    r.bold        = bold
    r.italic      = italic
    if color:
        r.font.color.rgb = color
    return r


def add_para(doc, text="", size=10, bold=False, color=None,
             align=None, space_before=0, space_after=6, font=FONT_BODY):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after  = Pt(space_after)
    if align:
        p.alignment = align
    if text:
        add_run(p, text, size=size, bold=bold, color=color, font=font)
    return p


def add_image_safe(doc, path, width=5.5):
    if path and os.path.exists(path):
        try:
            doc.add_picture(path, width=Inches(width))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            return True
        except Exception:
            pass
    return False


def find_screenshot(screenshot_dir, keywords):
    if not screenshot_dir or not os.path.exists(screenshot_dir):
        return ""
    files = os.listdir(screenshot_dir)
    for kw in keywords:
        for f in sorted(files):
            if kw.lower() in f.lower() and f.endswith(".png"):
                return os.path.join(screenshot_dir, f)
    return ""


def add_section_bar(doc, text, bg_hex="1A1A2E"):
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = table.rows[0].cells[0]
    set_cell_bg(cell, bg_hex)
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(5)
    p.paragraph_format.space_after  = Pt(5)
    r = p.add_run(f"  {text}")
    r.bold           = True
    r.font.size      = Pt(11)
    r.font.name      = FONT_HEADING
    r.font.color.rgb = RGB_WHITE
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return table


def hr(doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after  = Pt(8)
    pPr  = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bot  = OxmlElement("w:bottom")
    bot.set(qn("w:val"),   "single")
    bot.set(qn("w:sz"),    "4")
    bot.set(qn("w:space"), "1")
    bot.set(qn("w:color"), "CCCCCC")
    pBdr.append(bot)
    pPr.append(pBdr)


# ─────────────────────────────────────────────
#  DEDUPLICATION
# ─────────────────────────────────────────────

def deduplicate_results(results: list) -> dict:
    grouped = defaultdict(lambda: defaultdict(list))

    for result in results:
        check = result.get("check", "other")
        url   = result.get("url", "")
        for issue in result.get("issues", []):
            issue_text = issue.get("issue", "")
            # Strip numbers/measurements so same issue groups correctly
            normalized = re.sub(r'\d+px[^)]*\)', '', issue_text)
            normalized = re.sub(r'\s*\(\d+[^)]*\)?', '', normalized)
            normalized = normalized.strip().rstrip('(').strip()
            normalized = re.sub(r'^\d+\s+', '', normalized)
            normalized = normalized.strip()
            key = (normalized, issue.get("severity"), issue.get("label"))
            grouped[check][key].append({
                "url":          url,
                "original_text": issue_text,
                "detail":       issue.get("detail", []),
                "cro_category": issue.get("cro_category", ""),
                "impact":       issue.get("impact", ""),
                "fix":          issue.get("fix", ""),
                "screenshot":   issue.get("_screenshot", ""),
            })

    deduped = {}
    for check, issues_map in grouped.items():
        deduped[check] = []
        for (norm, sev, label), occs in issues_map.items():
            urls         = list(dict.fromkeys([o["url"] for o in occs]))
            all_detail   = []
            for o in occs:
                all_detail.extend(o.get("detail", []))
            unique_detail = list(dict.fromkeys(all_detail))
            screenshot    = next((o["screenshot"] for o in occs if o.get("screenshot")), "")

            # Use normalized text as canonical issue title (cleaner)
            canonical = norm if norm else occs[0]["original_text"]

            deduped[check].append({
                "issue":          canonical,
                "severity":       sev,
                "label":          label,
                "cro_category":   occs[0]["cro_category"],
                "impact":         occs[0]["impact"],
                "fix":            occs[0]["fix"],
                "affected_pages": urls,
                "affected_count": len(urls),
                "detail":         unique_detail[:3],
                "is_sitewide":    len(urls) >= 5,
                "_screenshot":    screenshot,
            })
        deduped[check].sort(key=lambda x: SEV_ORDER.get(x["severity"], 3))

    return deduped


# ─────────────────────────────────────────────
#  CRO SCORING
# ─────────────────────────────────────────────

def compute_cro_scores(deduped: dict) -> dict:
    category_issues = defaultdict(list)
    for check, issues in deduped.items():
        for issue in issues:
            cat = issue.get("cro_category", "")
            if cat:
                category_issues[cat].append(issue)

    categories  = {
        "cart_flow": 100, "social_proof": 100, "trust": 100,
        "homepage":  100, "mobile_ux":    100, "email_capture": 100,
        "urgency":   100, "aov":          100, "speed": 100,
    }
    deductions = {"HIGH": 35, "MEDIUM": 15, "LOW": 5}

    for cat in categories:
        for issue in category_issues.get(cat, []):
            categories[cat] = max(0, categories[cat] - deductions.get(issue.get("severity", "LOW"), 5))

    overall = round(sum(categories.values()) / len(categories))
    return {"categories": categories, "overall": overall}


# ─────────────────────────────────────────────
#  CHARTS
# ─────────────────────────────────────────────

def make_score_chart(scores: dict, output_dir: str) -> str:
    categories = scores["categories"]
    labels     = [CRO_CATEGORY_LABELS.get(k, k) for k in categories]
    values     = list(categories.values())
    colors     = [C_GREEN if v >= 70 else C_ORANGE if v >= 40 else C_RED for v in values]

    fig, ax = plt.subplots(figsize=(6.5, max(3, len(labels) * 0.55)))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#F8F9FA")

    bars = ax.barh(labels, values, color=colors, height=0.6, edgecolor="white", linewidth=0.5)
    for bar, val in zip(bars, values):
        ax.text(min(val + 2, 96), bar.get_y() + bar.get_height() / 2,
                f"{val}", va="center", ha="left", fontsize=9,
                fontweight="bold", color="#333333", fontfamily="Arial")

    ax.set_xlim(0, 110)
    ax.set_title("CRO Category Scores", fontsize=11, fontweight="bold",
                 color=C_DARK, pad=10, fontfamily="Arial")
    ax.axvline(x=70, color=C_GREEN, linestyle="--", linewidth=0.8, alpha=0.5)
    ax.tick_params(axis="y", labelsize=8)
    ax.tick_params(axis="x", labelsize=8)
    for label in ax.get_yticklabels():
        label.set_fontfamily("Arial")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)

    legend_patches = [
        mpatches.Patch(color=C_GREEN,  label="Good (70+)"),
        mpatches.Patch(color=C_ORANGE, label="Needs Work (40-69)"),
        mpatches.Patch(color=C_RED,    label="Critical (<40)"),
    ]
    ax.legend(handles=legend_patches, fontsize=8, loc="lower right",
              framealpha=0.9, edgecolor="none", prop={"family": "Arial"})

    plt.tight_layout()
    path = os.path.join(output_dir, "chart_cro_scores.png")
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close()
    return path


def make_issue_count_chart(deduped: dict, output_dir: str) -> str:
    check_order = ["cro_cart", "cro_homepage", "cro_product", "cro_mobile", "cro_speed"]
    labels, highs, meds, lows = [], [], [], []

    for check in check_order:
        if check not in deduped or not deduped[check]:
            continue
        issues = deduped[check]
        labels.append(CHECK_LABELS.get(check, check))
        highs.append(sum(1 for i in issues if i["severity"] == "HIGH"))
        meds.append(sum(1 for i in issues if i["severity"] == "MEDIUM"))
        lows.append(sum(1 for i in issues if i["severity"] == "LOW"))

    if not labels:
        return ""

    x     = np.arange(len(labels))
    width = 0.25

    fig, ax = plt.subplots(figsize=(6.5, 3.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#F8F9FA")

    b1 = ax.bar(x - width, highs, width, label="HIGH",   color=C_RED,    edgecolor="white")
    b2 = ax.bar(x,          meds,  width, label="MEDIUM", color=C_ORANGE, edgecolor="white")
    b3 = ax.bar(x + width,  lows,  width, label="LOW",    color=C_GREEN,  edgecolor="white")

    for bars in [b1, b2, b3]:
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, h + 0.05,
                        str(int(h)), ha="center", va="bottom",
                        fontsize=8, fontweight="bold", fontfamily="Arial")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8, rotation=12, ha="right", fontfamily="Arial")
    ax.set_ylabel("Issues Found", fontsize=9, color=C_GREY, fontfamily="Arial")
    ax.set_title("Issues by Category", fontsize=11, fontweight="bold",
                 color=C_DARK, pad=10, fontfamily="Arial")
    ax.legend(fontsize=8, framealpha=0.9, edgecolor="none", prop={"family": "Arial"})
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True))

    plt.tight_layout()
    path = os.path.join(output_dir, "chart_issue_counts.png")
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close()
    return path


def make_health_score_badge(score: int, output_dir: str) -> str:
    fig, ax = plt.subplots(figsize=(3.2, 3.2))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_aspect("equal")
    ax.axis("off")

    color = C_GREEN if score >= 70 else C_ORANGE if score >= 40 else C_RED
    label = "Good" if score >= 70 else "Needs Work" if score >= 40 else "Critical"

    bg  = plt.Circle((0.5, 0.5), 0.42, color="#F0F0F0", zorder=1, transform=ax.transAxes)
    ax.add_patch(bg)

    theta2 = 90 - (360 * score / 100)
    arc = mpatches.Arc((0.5, 0.5), 0.78, 0.78, angle=0,
                        theta1=theta2, theta2=90,
                        color=color, linewidth=18, zorder=2,
                        transform=ax.transAxes)
    ax.add_patch(arc)

    ax.text(0.5, 0.56, str(score), ha="center", va="center",
            fontsize=36, fontweight="bold", color=C_DARK, zorder=3,
            transform=ax.transAxes, fontfamily="Arial")
    ax.text(0.5, 0.36, "/ 100", ha="center", va="center",
            fontsize=12, color=C_GREY, zorder=3,
            transform=ax.transAxes, fontfamily="Arial")
    ax.text(0.5, 0.22, label, ha="center", va="center",
            fontsize=11, fontweight="bold", color=color, zorder=3,
            transform=ax.transAxes, fontfamily="Arial")
    ax.text(0.5, 0.78, "CRO HEALTH", ha="center", va="center",
            fontsize=8, color=C_GREY, zorder=3,
            transform=ax.transAxes, fontfamily="Arial")

    plt.tight_layout(pad=0)
    path = os.path.join(output_dir, "chart_health_score.png")
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close()
    return path


# ─────────────────────────────────────────────
#  REPORT BUILDER
# ─────────────────────────────────────────────

def generate_report(audit_data: dict, output_dir: str) -> str:
    store_url     = audit_data.get("store_url", "Unknown")
    audit_date    = audit_data.get("audit_date", datetime.now().isoformat())
    results       = audit_data.get("results", [])
    pages_crawled = audit_data.get("pages_crawled", [])
    load_times    = audit_data.get("load_times", {})
    mode          = audit_data.get("audit_mode", "full")
    screenshot_dir = os.path.join(output_dir, "screenshots")

    parsed     = urlparse(store_url)
    store_name = parsed.netloc.replace("www.", "").split(".")[0].title()
    date_str   = datetime.fromisoformat(audit_date).strftime("%B %d, %Y")

    print("  Deduplicating results...")
    deduped      = deduplicate_results(results)
    total_unique = sum(len(v) for v in deduped.values())
    print(f"  {total_unique} unique issues.")

    print("  Computing CRO scores...")
    scores        = compute_cro_scores(deduped)
    overall_score = scores["overall"]

    print("  Generating charts...")
    chart_badge  = make_health_score_badge(overall_score, output_dir)
    chart_score  = make_score_chart(scores, output_dir)
    chart_counts = make_issue_count_chart(deduped, output_dir)

    # ── Document setup ──
    doc = Document()
    for section in doc.sections:
        section.top_margin    = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin   = Inches(1.0)
        section.right_margin  = Inches(1.0)

    # Set default font for Normal style
    from docx.oxml.ns import qn as oxqn
    normal_style = doc.styles["Normal"]
    normal_style.font.name = FONT_BODY
    normal_style.font.size = Pt(10)

    # ══════════════════════════════════
    #  COVER PAGE
    # ══════════════════════════════════
    doc.add_paragraph()

    p = add_para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    add_run(p, "CRO AUDIT REPORT", size=28, bold=True, color=RGB_DARK, font=FONT_HEADING)

    p = add_para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
    add_run(p, store_name, size=18, bold=True, color=RGB_MID, font=FONT_HEADING)

    p = add_para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
    add_run(p, store_url, size=10, color=RGB_GREY)

    p = add_para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=16)
    add_run(p, f"{date_str}  |  Prepared by Abdullah — Sentivo Limited", size=9, color=RGB_GREY)

    # Badge + Homepage screenshot
    cover_tbl = doc.add_table(rows=1, cols=2)
    cover_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

    badge_cell = cover_tbl.rows[0].cells[0]
    bp = badge_cell.paragraphs[0]
    bp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if os.path.exists(chart_badge):
        try:
            bp.add_run().add_picture(chart_badge, width=Inches(2.2))
        except Exception:
            pass

    ss_cell = cover_tbl.rows[0].cells[1]
    ss_path = find_screenshot(screenshot_dir, ["homepage_desktop"])
    if ss_path:
        sp = ss_cell.paragraphs[0]
        sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        try:
            sp.add_run().add_picture(ss_path, width=Inches(3.8))
        except Exception:
            pass

    doc.add_page_break()

    # ══════════════════════════════════
    #  EXECUTIVE SUMMARY
    # ══════════════════════════════════
    add_section_bar(doc, "EXECUTIVE SUMMARY")

    high_count = sum(1 for v in deduped.values() for i in v if i["severity"] == "HIGH")
    med_count  = sum(1 for v in deduped.values() for i in v if i["severity"] == "MEDIUM")
    low_count  = sum(1 for v in deduped.values() for i in v if i["severity"] == "LOW")

    # Metric cards
    metric_tbl = doc.add_table(rows=1, cols=3)
    metric_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    metric_data = [
        (str(high_count), "Critical Issues", "C0392B"),
        (str(med_count),  "Issues to Fix",   "E67E22"),
        (str(low_count),  "Low Priority",    "27AE60"),
    ]
    for i, (num, lbl, color) in enumerate(metric_data):
        cell = metric_tbl.rows[0].cells[i]
        set_cell_bg(cell, color)
        cell_valign(cell, "center")
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(12)
        r = p.add_run(num)
        r.font.name      = FONT_HEADING
        r.font.size      = Pt(34)
        r.font.bold      = True
        r.font.color.rgb = RGB_WHITE
        p2 = cell.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.paragraph_format.space_after = Pt(12)
        r2 = p2.add_run(lbl)
        r2.font.name      = FONT_BODY
        r2.font.size      = Pt(9)
        r2.font.color.rgb = RGB_WHITE

    doc.add_paragraph()

    homepage_lt  = load_times.get(store_url)
    lt_text      = f" Homepage loaded in {homepage_lt}s." if homepage_lt else ""
    summary_text = (
        f"This CRO audit covers {len(pages_crawled)} pages of {store_url}, conducted on {date_str}. "
        f"{total_unique} unique conversion issues were identified with an overall CRO Health Score of {overall_score}/100.{lt_text} "
        f"Findings are prioritised by revenue impact — address HIGH severity issues first."
    )
    p = add_para(doc, summary_text, size=10, space_after=12)
    hr(doc)

    # ══════════════════════════════════
    #  CHARTS
    # ══════════════════════════════════
    add_section_bar(doc, "CRO PERFORMANCE OVERVIEW", bg_hex="2C3E50")

    chart_tbl = doc.add_table(rows=1, cols=2)
    chart_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

    lc = chart_tbl.rows[0].cells[0]
    rc = chart_tbl.rows[0].cells[1]

    if os.path.exists(chart_score):
        lp = lc.paragraphs[0]
        lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        try:
            lp.add_run().add_picture(chart_score, width=Inches(3.5))
        except Exception:
            pass

    if chart_counts and os.path.exists(chart_counts):
        rp = rc.paragraphs[0]
        rp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        try:
            rp.add_run().add_picture(chart_counts, width=Inches(3.5))
        except Exception:
            pass

    doc.add_paragraph()
    doc.add_page_break()

    # ══════════════════════════════════
    #  DETAILED FINDINGS
    # ══════════════════════════════════
    add_section_bar(doc, "DETAILED FINDINGS")
    doc.add_paragraph()

    cro_checks = ["cro_cart", "cro_homepage", "cro_product", "cro_mobile", "cro_speed"]
    seo_checks = ["meta_seo", "images", "speed", "schema", "broken_links", "navigation"]
    checks_to_show = cro_checks if mode == "cro" else cro_checks + seo_checks

    for check in checks_to_show:
        if check not in deduped or not deduped[check]:
            continue

        add_section_bar(doc, CHECK_LABELS.get(check, check), bg_hex="2C3E50")

        for issue in deduped[check]:
            sev       = issue["severity"]
            label     = issue["label"]
            issue_txt = issue["issue"]
            impact    = issue.get("impact", "")
            fix       = issue.get("fix", "")
            pages     = issue["affected_pages"]
            count     = issue["affected_count"]
            sitewide  = issue["is_sitewide"]

            sev_color = {"HIGH": RGB_RED, "MEDIUM": RGB_ORANGE, "LOW": RGB_GREEN}.get(sev, RGB_GREY)
            sev_hex   = {"HIGH": "FADBD8", "MEDIUM": "FDEBD0", "LOW": "D5F5E3"}.get(sev, "F5F5F5")

            # Issue card
            card      = doc.add_table(rows=1, cols=1)
            card.style = "Table Grid"
            card_cell = card.rows[0].cells[0]
            set_cell_bg(card_cell, sev_hex)

            # Severity + label
            p = card_cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after  = Pt(2)
            r = p.add_run(f"[{sev}]  [{label}]")
            r.font.name      = FONT_BODY
            r.font.size      = Pt(8)
            r.font.bold      = True
            r.font.color.rgb = sev_color

            # Issue title
            p2 = card_cell.add_paragraph()
            p2.paragraph_format.space_after = Pt(5)
            r2 = p2.add_run(issue_txt)
            r2.font.name      = FONT_HEADING
            r2.font.size      = Pt(11)
            r2.font.bold      = True
            r2.font.color.rgb = RGB_DARK

            # Impact
            if impact:
                p3 = card_cell.add_paragraph()
                p3.paragraph_format.space_after = Pt(3)
                ra = p3.add_run("Impact: ")
                ra.font.name = FONT_BODY; ra.font.size = Pt(9); ra.font.bold = True; ra.font.color.rgb = RGB_MID
                rb = p3.add_run(impact)
                rb.font.name = FONT_BODY; rb.font.size = Pt(9); rb.font.color.rgb = RGB_MID

            # Fix
            if fix:
                p4 = card_cell.add_paragraph()
                p4.paragraph_format.space_after = Pt(3)
                ra = p4.add_run("Fix: ")
                ra.font.name = FONT_BODY; ra.font.size = Pt(9); ra.font.bold = True; ra.font.color.rgb = RGB_ACCENT
                rb = p4.add_run(fix)
                rb.font.name = FONT_BODY; rb.font.size = Pt(9); rb.font.color.rgb = RGB_ACCENT

            # Scope
            if sitewide:
                scope_txt = f"Sitewide — affects all {count} crawled pages"
            else:
                slug_list = ", ".join(
                    u.replace(store_url, "") or "/" for u in pages[:3]
                )
                if count > 3:
                    slug_list += f" (+{count - 3} more)"
                scope_txt = f"Affected pages: {slug_list}"

            p5 = card_cell.add_paragraph()
            p5.paragraph_format.space_after = Pt(6)
            r5 = p5.add_run(scope_txt)
            r5.font.name      = FONT_BODY
            r5.font.size      = Pt(8)
            r5.font.color.rgb = RGB_GREY
            r5.italic         = True

            doc.add_paragraph().paragraph_format.space_after = Pt(2)

            # Screenshot — HIGH issues only
            if sev == "HIGH":
                kw_map = {
                    "cro_cart":    ["cro_cart_drawer", "cro_cart_after", "cro_cart_page", "cro_cart_product"],
                    "cro_mobile":  ["cro_mobile_scroll", "cro_mobile"],
                    "cro_product": ["cro_atc_below_fold"],
                }
                kws = kw_map.get(check, ["homepage_desktop"])

                # Use issue-specific screenshot if available
                if issue.get("_screenshot"):
                    ss = os.path.join(screenshot_dir, issue["_screenshot"])
                    if not os.path.exists(ss):
                        ss = find_screenshot(screenshot_dir, kws)
                else:
                    ss = find_screenshot(screenshot_dir, kws)

                if ss:
                    cap = doc.add_paragraph()
                    cap.paragraph_format.space_before = Pt(2)
                    cap.paragraph_format.space_after  = Pt(2)
                    cr = cap.add_run("Screenshot:")
                    cr.font.name      = FONT_BODY
                    cr.font.size      = Pt(8)
                    cr.font.bold      = True
                    cr.font.color.rgb = RGB_GREY
                    add_image_safe(doc, ss, width=5.0)
                    doc.add_paragraph().paragraph_format.space_after = Pt(4)

        doc.add_paragraph()

    doc.add_page_break()

    # ══════════════════════════════════
    #  PRIORITY ACTION PLAN
    # ══════════════════════════════════
    add_section_bar(doc, "PRIORITY ACTION PLAN")
    doc.add_paragraph()

    all_flat = []
    for check in checks_to_show:
        if check in deduped:
            for issue in deduped[check]:
                all_flat.append((check, issue))
    all_flat.sort(key=lambda x: SEV_ORDER.get(x[1]["severity"], 3))

    action_tbl = doc.add_table(rows=1, cols=4)
    action_tbl.style = "Table Grid"
    action_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

    headers = ["#", "Issue", "Priority", "Category"]
    for i, h in enumerate(headers):
        cell = action_tbl.rows[0].cells[i]
        set_cell_bg(cell, "1A1A2E")
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after  = Pt(4)
        r = p.add_run(h)
        r.font.name      = FONT_HEADING
        r.font.bold      = True
        r.font.size      = Pt(9)
        r.font.color.rgb = RGB_WHITE

    sev_hex_map = {"HIGH": "FADBD8", "MEDIUM": "FDEBD0", "LOW": "D5F5E3"}

    for idx, (check, issue) in enumerate(all_flat[:12], 1):
        sev     = issue["severity"]
        cat_lbl = CRO_CATEGORY_LABELS.get(issue.get("cro_category", ""), CHECK_LABELS.get(check, check))
        row     = action_tbl.add_row().cells

        row[0].text = str(idx)
        row[1].text = issue["issue"][:90]
        row[2].text = sev
        row[3].text = cat_lbl

        set_cell_bg(row[2], sev_hex_map.get(sev, "F5F5F5"))

        for cell in row:
            for para in cell.paragraphs:
                para.paragraph_format.space_before = Pt(4)
                para.paragraph_format.space_after  = Pt(4)
                for run in para.runs:
                    run.font.name = FONT_BODY
                    run.font.size = Pt(9)

    doc.add_page_break()

    # ══════════════════════════════════
    #  CURSOR PROMPTS
    # ══════════════════════════════════
    add_section_bar(doc, "CURSOR IMPLEMENTATION PROMPTS", bg_hex="2C3E50")
    p = add_para(doc, "Copy each prompt directly into Cursor Agent to implement the fix.", size=9, color=RGB_GREY, space_after=10)

    cursor_prompts = {
        "cro_cart": """Fix the cart and checkout flow on this Shopify store:
1. Verify Add to Cart button works on all product page templates
2. If using drawer/slide-out cart, ensure it triggers correctly on ATC click
3. If using /cart page, verify checkout button is above the fold
4. Test subscription products separately — Seal Subscriptions may need its own ATC selector
5. Check browser console for JS errors on product pages""",

        "cro_homepage": """Improve CRO on the homepage of this Shopify store:
1. Add a clear hero CTA button ('Shop Now' / 'Shop Best Sellers') linking to main collection
2. Add or make visible a testimonials or star-rating section
3. Add a 'Best Sellers' or featured product grid if not already present
4. Add an email capture section or exit-intent popup with a discount incentive (10% off first order)
5. Ensure value proposition (why choose us) is visible above the fold""",

        "cro_product": """Improve CRO on all product pages of this Shopify store:
1. Move Add to Cart button above the fold — product image and ATC should both be visible without scrolling
2. Add a sticky ATC bar that appears on scroll for longer product pages
3. Add trust badge strip below ATC: Secure Checkout, Money-Back Guarantee, Free Returns
4. Ensure reviews app (Judge.me / Okendo) displays star rating and review count on all product templates
5. Add low stock indicator: {% if product.selected_variant.inventory_quantity < 10 and product.selected_variant.inventory_quantity > 0 %}Only {{ product.selected_variant.inventory_quantity }} left{% endif %}
6. Add 'You May Also Like' or 'Frequently Bought Together' section below product description""",

        "cro_mobile": """Fix mobile UX issues on this Shopify store:
1. Find elements causing horizontal scroll — add max-width: 100% and overflow-x: hidden
2. Set min-height: 48px on Add to Cart button for mobile touch targets
3. Test all product pages at 390px width in DevTools after fixes
4. Ensure sticky header does not overlap ATC button on mobile""",

        "cro_speed": """Improve page speed on this Shopify store:
1. Go to Shopify Admin > Apps — remove any apps not actively used
2. In theme.liquid, add defer or async to non-critical third-party scripts
3. Add loading='lazy' to all below-fold images in section Liquid files
4. Run Google PageSpeed Insights and fix any LCP or CLS issues flagged""",
    }

    for check, prompt in cursor_prompts.items():
        if check not in deduped or not deduped[check]:
            continue
        p = add_para(doc, CHECK_LABELS.get(check, check), size=11, bold=True, color=RGB_MID,
                     font=FONT_HEADING, space_before=8, space_after=4)
        p2 = doc.add_paragraph()
        p2.paragraph_format.left_indent = Inches(0.2)
        p2.paragraph_format.space_after = Pt(10)
        r = p2.add_run(prompt)
        r.font.name      = FONT_CODE
        r.font.size      = Pt(8.5)
        r.font.color.rgb = RGB_DARK

    # ── Save ──
    domain   = parsed.netloc.replace("www.", "").replace(".", "_")
    date_tag = datetime.now().strftime("%Y-%m-%d")
    prefix   = "cro_" if mode == "cro" else ""
    fname    = f"{prefix}audit_report_{domain}_{date_tag}.docx"
    path     = os.path.join(output_dir, fname)
    doc.save(path)
    print(f"  Report saved: {path}")
    return path
