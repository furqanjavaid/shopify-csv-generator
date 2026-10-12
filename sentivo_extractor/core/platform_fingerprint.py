"""
Bulletproof platform fingerprinting, known-issue detection,
pre-flight validation, title blacklist, and domain cache.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

# ── Markers for UI (subprocess log scrape) ─────────────────────────────
PREFLIGHT_PASS_MARKER = "PREFLIGHT:PASSED"
PREFLIGHT_FAIL_MARKER = "PREFLIGHT:FAILED"
PLATFORM_CACHE_MARKER = "PLATFORM_CACHE:LOADED"
KNOWN_ISSUE_MARKER = "KNOWN_ISSUE:"

# In-run memory (never re-fingerprint same domain in one process).
domain_platform_cache: dict[str, dict[str, Any]] = {}

GetTextFn = Callable[[str], str]
ExtractFn = Callable[..., dict[str, Any]]

TITLE_BLACKLIST_EXACT = frozenset(
    {
        "sign up for",
        "sign up",
        "newsletter",
        "subscribe",
        "home",
        "page not found",
        "403",
        "404",
    }
)
TITLE_BLACKLIST_PREFIXES = (
    "sign up for",
    "sign up",
    "subscribe to",
    "newsletter",
)

# Strict chrome-only hints for pre-flight (path segments / filenames — not
# bare "logo"/"icon" substrings that appear in WooCommerce CDN product paths).
_NAV_IMAGE_HINTS = (
    "favicon",
    "sprite",
    "/1x1",
    "pixel.gif",
    "spacer.gif",
    "/avatar/",
    "profile-pic",
    "banner-nav",
    "/nav/",
    "cart.svg",
    "search.svg",
    "/icons/payment",
    "site-logo.",
    "header-logo.",
    "footer-logo.",
)

_THRESHOLDS = {
    "Shopify": 40,
    "WooCommerce": 40,
    "Magento": 40,
    "BigCommerce": 40,
    "Next.js": 40,
}


def clear_domain_platform_cache() -> None:
    domain_platform_cache.clear()


def domain_key(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_invalid_title(title: str | None) -> bool:
    """True when title is empty, too short, or matches the blacklist."""
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    if len(text) < 4:
        return True
    low = text.lower()
    if low in TITLE_BLACKLIST_EXACT:
        return True
    if any(low.startswith(p) for p in TITLE_BLACKLIST_PREFIXES):
        return True
    if "page not found" in low:
        return True
    return False


def is_valid_product_title(title: str | None) -> bool:
    return not is_invalid_title(title)


# ── Persistent cache ───────────────────────────────────────────────────

def platform_cache_path(output_dir: Path | str) -> Path:
    return Path(output_dir) / "platform_cache.json"


def load_platform_cache_file(output_dir: Path | str) -> dict[str, Any]:
    path = platform_cache_path(output_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _cache_public_fields(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": entry.get("platform"),
        "confidence": entry.get("confidence"),
        "signals": list(entry.get("signals") or []),
        "anti_bot": bool(entry.get("anti_bot")),
        "vue_pricing": bool(entry.get("vue_pricing")),
        "js_heavy": bool(entry.get("js_heavy")),
        "pre_flight_passed": entry.get("pre_flight_passed"),
        "detected_at": entry.get("detected_at"),
        "blockers": list(entry.get("blockers") or []),
    }


def save_platform_cache_entry(
    output_dir: Path | str,
    domain: str,
    entry: dict[str, Any],
) -> Path:
    path = platform_cache_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    store = load_platform_cache_file(output_dir)
    store[domain] = _cache_public_fields(entry)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def get_cached_platform(
    domain_or_url: str,
    output_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    domain = domain_key(domain_or_url) if "://" in (domain_or_url or "") else (
        (domain_or_url or "").lower().removeprefix("www.")
    )
    if not domain:
        return None
    if domain in domain_platform_cache:
        return domain_platform_cache[domain]
    if output_dir is not None:
        disk = load_platform_cache_file(output_dir).get(domain)
        if isinstance(disk, dict) and disk.get("platform"):
            domain_platform_cache[domain] = dict(disk)
            return domain_platform_cache[domain]
    return None


# ── HTTP helpers ───────────────────────────────────────────────────────

def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _raw_requests_get(url: str, *, timeout: float = 12.0) -> tuple[int | None, str]:
    """Plain requests GET (no cloudscraper) — used for anti-bot probes."""
    try:
        import requests

        resp = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        return resp.status_code, str(resp.text or "")
    except Exception as exc:
        resp = getattr(exc, "response", None)
        if resp is not None:
            return getattr(resp, "status_code", None), str(
                getattr(resp, "text", "") or ""
            )
        msg = str(exc).lower()
        if "403" in msg:
            return 403, ""
        if "502" in msg:
            return 502, ""
        return None, ""


def _http_probe(
    url: str,
    *,
    get_text: GetTextFn | None = None,
    session: Any = None,
    timeout: float = 12.0,
) -> tuple[int | None, str]:
    if get_text is not None:
        try:
            body = get_text(url) or ""
            return (200 if body.strip() else None), body
        except Exception as exc:
            msg = str(exc)
            m = re.search(r"\b(401|403|404|402|500|502)\b", msg)
            status = int(m.group(1)) if m else None
            resp = getattr(exc, "response", None)
            if resp is not None:
                status = getattr(resp, "status_code", status)
                try:
                    return status, str(getattr(resp, "text", "") or "")
                except Exception:
                    return status, ""
            return status, ""
    if session is not None:
        try:
            resp = session.get(url, timeout=timeout, allow_redirects=True)
            return resp.status_code, str(resp.text or "")
        except Exception as exc:
            resp = getattr(exc, "response", None)
            if resp is not None:
                return getattr(resp, "status_code", None), str(
                    getattr(resp, "text", "") or ""
                )
            return None, ""
    return _raw_requests_get(url, timeout=timeout)


def _probe_json_status(
    url: str,
    *,
    get_text: GetTextFn | None = None,
    session: Any = None,
) -> tuple[int | None, Any]:
    status, body = _http_probe(url, get_text=get_text, session=session)
    data: Any = None
    text = (body or "").strip()
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except Exception:
            data = None
    return status, data


def _strip_points(label: str) -> str:
    return re.sub(r"\(\+\d+\)$", "", label).strip()


# ── PART 2: Known issues ───────────────────────────────────────────────

def detect_known_issues(
    url: str,
    html: str = "",
    page_source: str = "",
    *,
    playwright_html: str = "",
    session: Any = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """
    Detect historically known extraction blockers:
      anti_bot / hard CF block / CF JS challenge
      Vue CutConfigurator pricing
      JS-heavy (requests HTML tiny vs Playwright rich)
    """
    log = logger or logging.getLogger("sentivo_extractor")
    origin = _origin(url)
    home = origin.rstrip("/") + "/"
    blob = page_source or html or ""
    low = blob.lower()
    blockers: list[str] = []
    notes: list[str] = []

    anti_bot = False
    hard_cf_block = False
    cf_js_challenge = False
    vue_pricing = False
    conf_data_missing = False
    js_heavy = False

    # Anti-bot: plain requests against homepage
    status, raw_body = _raw_requests_get(home)
    raw_low = (raw_body or "").lower()
    if status in (403, 502):
        anti_bot = True
        msg = (
            "⚠️ Anti-bot detected — Cloudflare/Sucuri protection active. "
            "Proxy or headed browser required."
        )
        notes.append(msg)
        blockers.append("anti_bot")
        log.warning("%s %s", KNOWN_ISSUE_MARKER, msg)
    if "sorry, you have been blocked" in raw_low or (
        "attention required" in raw_low and "cloudflare" in raw_low
    ):
        hard_cf_block = True
        anti_bot = True
        msg = "❌ Hard CF block — proxy required"
        notes.append(msg)
        blockers.append("hard_cf_block")
        log.error("%s %s", KNOWN_ISSUE_MARKER, msg)
    if "cf-browser-verification" in raw_low or "cf-browser-check" in raw_low:
        cf_js_challenge = True
        anti_bot = True
        msg = "⚠️ CF JS challenge — cloudscraper may bypass"
        notes.append(msg)
        blockers.append("cf_js_challenge")
        log.warning("%s %s", KNOWN_ISSUE_MARKER, msg)

    # Also inspect provided HTML (may already be a block page)
    if "sorry, you have been blocked" in low:
        hard_cf_block = True
        anti_bot = True
        if "hard_cf_block" not in blockers:
            msg = "❌ Hard CF block — proxy required"
            notes.append(msg)
            blockers.append("hard_cf_block")
            log.error("%s %s", KNOWN_ISSUE_MARKER, msg)

    # Vue.js CutConfigurator (sheetplastics / Magento pattern)
    if "cutconfiguratorwidget" in low or "CutConfiguratorWidget" in blob:
        vue_pricing = True
        msg = (
            "⚠️ Vue.js CutConfigurator detected — pricing is JS-calculated, "
            "not in static HTML. Playwright required for price extraction."
        )
        notes.append(msg)
        blockers.append("vue_pricing")
        log.warning("%s %s", KNOWN_ISSUE_MARKER, msg)
        # confData presence (string heuristics in HTML/JS dump)
        has_conf = bool(
            re.search(
                r"confData\s*[:=]",
                blob,
            )
            or "Configurator.confData" in blob
            or "configurator.confdata" in low
        )
        if not has_conf:
            conf_data_missing = True
            msg2 = (
                "❌ CutConfigurator confData not loaded — may need longer page wait"
            )
            notes.append(msg2)
            blockers.append("conf_data_missing")
            log.error("%s %s", KNOWN_ISSUE_MARKER, msg2)

    # JS-heavy: tiny requests body vs rich Playwright body
    req_len = len(raw_body or "") if raw_body is not None else len(blob)
    # Prefer dedicated playwright_html; else page_source if clearly richer
    pw_blob = playwright_html or ""
    if not pw_blob and page_source and html and len(page_source) > len(html) * 2:
        pw_blob = page_source
    pw_len = len(pw_blob)
    if req_len < 5000 and pw_len > 50000:
        js_heavy = True
        msg = (
            "⚠️ JS-rendered site — requests will fail, "
            "Playwright required for all fetches"
        )
        notes.append(msg)
        blockers.append("js_heavy")
        log.warning("%s %s", KNOWN_ISSUE_MARKER, msg)

    return {
        "anti_bot": anti_bot,
        "hard_cf_block": hard_cf_block,
        "cf_js_challenge": cf_js_challenge,
        "vue_pricing": vue_pricing,
        "conf_data_missing": conf_data_missing,
        "js_heavy": js_heavy,
        "blockers": blockers,
        "notes": notes,
        "requests_status": status,
        "requests_html_len": req_len,
        "playwright_html_len": pw_len,
    }


# ── PART 1: Fingerprint ────────────────────────────────────────────────

def fingerprint_platform(
    url: str,
    html: str = "",
    page_source: str = "",
    *,
    get_text: GetTextFn | None = None,
    session: Any = None,
    playwright_html: str = "",
    output_dir: Path | str | None = None,
    use_cache: bool = True,
    detect_blockers: bool = True,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """
    Score-based platform fingerprint. Highest score ≥ threshold wins.

    Returns platform, confidence, signals, known-issue flags, cache metadata.
    """
    log = logger or logging.getLogger("sentivo_extractor")
    domain = domain_key(url)
    blob = page_source or html or ""
    low = blob.lower()
    path = (urlparse(url).path or "").lower()
    origin = _origin(url)

    if use_cache and domain:
        cached = get_cached_platform(domain, output_dir)
        if cached and cached.get("platform"):
            detected_at = str(cached.get("detected_at") or "")[:10]
            pf = cached.get("pre_flight_passed")
            pf_note = (
                f" — pre-flight passed {detected_at}"
                if pf
                else (f" — detected {detected_at}" if detected_at else "")
            )
            msg = (
                f"Using cached: {cached.get('platform')} "
                f"({cached.get('confidence')}pts){pf_note}. Skipping detection."
            )
            log.info("%s %s", PLATFORM_CACHE_MARKER, msg)
            log.info(msg)
            out = dict(cached)
            out["domain"] = domain
            out["from_cache"] = True
            return out

    scores: dict[str, int] = {
        "Shopify": 0,
        "WooCommerce": 0,
        "Magento": 0,
        "BigCommerce": 0,
        "Next.js": 0,
    }
    fired: dict[str, list[str]] = {k: [] for k in scores}

    def hit(platform: str, label: str, points: int) -> None:
        scores[platform] += points
        fired[platform].append(f"{label}(+{points})")

    # ── Shopify ──
    shopify_json_url = f"{origin.rstrip('/')}/products.json"
    _st, data = _probe_json_status(
        shopify_json_url, get_text=get_text, session=session
    )
    if isinstance(data, dict) and "products" in data:
        hit("Shopify", "/products.json", 50)
    if re.search(r"\bwindow\.Shopify\b", blob) or "window.Shopify" in blob:
        hit("Shopify", "window.Shopify", 40)
    gen = re.search(
        r'<meta[^>]+name=["\']generator["\'][^>]*content=["\']([^"\']+)["\']',
        blob,
        re.I,
    ) or re.search(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']generator["\']',
        blob,
        re.I,
    )
    if gen and "shopify" in (gen.group(1) or "").lower():
        hit("Shopify", 'meta generator="Shopify"', 40)
    if "cdn.shopify.com" in low:
        hit("Shopify", "cdn.shopify.com", 30)
    if "/collections/" in path or "/products/" in path:
        hit("Shopify", "/collections/ or /products/ URL", 10)

    # ── WooCommerce ──
    woo_api = f"{origin.rstrip('/')}/wp-json/wc/v3/products"
    woo_st, _woo_data = _probe_json_status(
        woo_api, get_text=get_text, session=session
    )
    if woo_st in (200, 401):
        hit("WooCommerce", "/wp-json/wc/v3/products", 50)
    if "wc_add_to_cart_params" in blob or "window.wc_add_to_cart_params" in blob:
        hit("WooCommerce", "wc_add_to_cart_params", 40)
    if re.search(
        r"""<body[^>]+class=['"][^'"]*\bwoocommerce\b""",
        blob,
        re.I,
    ):
        hit("WooCommerce", "body.woocommerce", 40)
    if "wp-content/plugins/woocommerce" in low:
        hit("WooCommerce", "wp-content/plugins/woocommerce", 40)
    path_parts = [p for p in path.split("/") if p]
    if "/product-category/" in path or "/product_category/" in path:
        hit("WooCommerce", "/product-category/ or /product/ URL", 20)
    elif (
        "/catalog/product/" not in path
        and len(path_parts) >= 2
        and path_parts[0] == "product"
    ):
        hit("WooCommerce", "/product-category/ or /product/ URL", 20)
    if "wp-content/" in low:
        hit("WooCommerce", "wp-content/ assets", 10)

    # ── Magento ──
    has_require = bool(
        re.search(r"\bwindow\.require\b", blob) or "window.require" in blob
    )
    has_mage_path = "mage/" in low or "/mage/" in low
    if has_require and has_mage_path:
        hit("Magento", "window.require + mage/", 50)
    if "/static/frontend/" in low:
        hit("Magento", "/static/frontend/", 40)
    if "Magento_Ui" in blob or "Mage.Cookies" in blob:
        hit("Magento", "Magento_Ui or Mage.Cookies", 40)
    if "requirejs/require.js" in low or (
        "require.js" in low and "requirejs" in low
    ):
        hit("Magento", "requirejs/require.js", 30)
    if "data-mage-init" in low:
        hit("Magento", "data-mage-init", 30)
    if "cutconfiguratorwidget" in low or "CutConfiguratorWidget" in blob:
        hit("Magento", "CutConfiguratorWidget", 30)
    if "/catalog/product/" in path:
        hit("Magento", "/catalog/product/ URL", 20)

    # ── BigCommerce ──
    if "window.BCData" in blob:
        hit("BigCommerce", "window.BCData", 50)
    if "cdn11.bigcommerce.com" in low:
        hit("BigCommerce", "cdn11.bigcommerce.com", 50)

    # ── Next.js ──
    if "window.__NEXT_DATA__" in blob or "__NEXT_DATA__" in blob:
        hit("Next.js", "window.__NEXT_DATA__", 50)
    if "_next/static/" in low:
        hit("Next.js", "_next/static/", 40)

    above = {p: s for p, s in scores.items() if s >= _THRESHOLDS[p]}
    if not above:
        platform = "Custom"
        confidence = max(scores.values()) if scores else 0
        signal_labels: list[str] = []
    else:
        ranked = sorted(above.items(), key=lambda kv: kv[1], reverse=True)
        platform, confidence = ranked[0]
        if len(ranked) > 1:
            log.warning(
                "Platform fingerprint conflict: %s — picking %s (%spts)",
                ", ".join(f"{p}={s}" for p, s in ranked),
                platform,
                confidence,
            )
        signal_labels = list(fired.get(platform) or [])

    # Log ALL fired signals in the requested format
    for plat, parts in fired.items():
        if not parts:
            continue
        total = scores[plat]
        joined = " + ".join(parts)
        if plat == platform and platform != "Custom":
            log.info("%s confirmed: %s = %spts", plat, joined, total)
        else:
            log.info("%s signals: %s = %spts", plat, joined, total)

    log.info(
        "Platform fingerprint: %s (%spts) for %s",
        platform,
        confidence,
        domain or url,
    )

    issues: dict[str, Any] = {
        "anti_bot": False,
        "vue_pricing": False,
        "js_heavy": False,
        "blockers": [],
        "notes": [],
    }
    if detect_blockers:
        issues = detect_known_issues(
            url,
            html=html,
            page_source=page_source or html,
            playwright_html=playwright_html,
            session=session,
            logger=log,
        )

    entry = {
        "platform": platform,
        "confidence": int(confidence),
        "signals": [_strip_points(s) for s in signal_labels],
        "scores": scores,
        "fired": fired,
        "domain": domain,
        "from_cache": False,
        "detected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "pre_flight_passed": None,
        "anti_bot": bool(issues.get("anti_bot")),
        "vue_pricing": bool(issues.get("vue_pricing")),
        "js_heavy": bool(issues.get("js_heavy")),
        "blockers": list(issues.get("blockers") or []),
        "known_issues": issues,
    }

    if domain:
        domain_platform_cache[domain] = entry
        if output_dir is not None:
            try:
                save_platform_cache_entry(output_dir, domain, entry)
            except Exception as exc:
                log.warning("Failed to write platform_cache.json: %s", exc)

    return entry


# ── PART 3: Pre-flight ─────────────────────────────────────────────────

def _product_price_value(product: dict[str, Any]) -> float | None:
    candidates: list[str] = []
    if product.get("price"):
        candidates.append(str(product.get("price")))
    for v in product.get("variants") or []:
        if isinstance(v, dict) and v.get("price"):
            candidates.append(str(v.get("price")))
    for raw in candidates:
        cleaned = re.sub(r"[^\d.\-]", "", raw.replace(",", ""))
        try:
            val = float(cleaned)
            if val > 0:
                return val
        except Exception:
            continue
    return None


def _is_nav_image(src: str) -> bool:
    low = (src or "").lower()
    return any(h in low for h in _NAV_IMAGE_HINTS)


def _raw_image_urls(product: dict[str, Any]) -> list[str]:
    """All absolute image URLs on the product (no chrome filter)."""
    out: list[str] = []
    for img in product.get("images") or []:
        if isinstance(img, dict):
            src = str(img.get("src") or "").strip()
        else:
            src = str(img or "").strip()
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith(("http://", "https://")):
            out.append(src)
    return out


def _product_image_urls(
    product: dict[str, Any],
    *,
    logger: logging.Logger | None = None,
) -> list[str]:
    """Absolute product image URLs after chrome filter (with rejection logs)."""
    log = logger or logging.getLogger("sentivo_extractor")
    out: list[str] = []
    for src in _raw_image_urls(product):
        if _is_nav_image(src):
            hit = next(h for h in _NAV_IMAGE_HINTS if h in src.lower())
            log.info("Image rejected: %s — reason: nav_hint:%s", src, hit)
            continue
        out.append(src)
    return out


def _description_text(product: dict[str, Any]) -> str:
    raw = str(
        product.get("description_html")
        or product.get("description")
        or product.get("body_html")
        or ""
    )
    return re.sub(r"<[^>]+>", " ", raw).strip()


def _extraction_confidence(product: dict[str, Any]) -> float:
    de = product.get("decision_engine") or {}
    final = de.get("final_confidence") if isinstance(de, dict) else None
    if isinstance(final, dict) and final.get("percent") is not None:
        try:
            return float(final["percent"])
        except Exception:
            pass
    try:
        return float(product.get("confidence_score") or 0) * (
            100.0 if float(product.get("confidence_score") or 0) <= 1.0 else 1.0
        )
    except Exception:
        return 0.0


def _jsonld_sourced(product: dict[str, Any]) -> bool:
    sources = product.get("field_sources") or {}
    if isinstance(sources, dict):
        vals = [str(v) for v in sources.values() if v]
        if vals and all("json-ld" in v.lower() or v == "JSON-LD" for v in vals):
            return True
    method = str(product.get("extraction_method") or "").lower()
    return "jsonld" in method or "json-ld" in method


def validate_preflight_product(
    product: dict[str, Any] | None,
    *,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Validate title / price / images / description for pre-flight gate."""
    log = logger or logging.getLogger("sentivo_extractor")
    missing: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {
        "title": False,
        "price": False,
        "images": False,
        "description": False,
    }
    product = product if isinstance(product, dict) else {}
    title = str(product.get("title") or "").strip()
    if is_valid_product_title(title):
        checks["title"] = True
    else:
        missing.append("title")
    if _product_price_value(product) is not None:
        checks["price"] = True
    else:
        missing.append("price")

    filtered = _product_image_urls(product, logger=log)
    raw_imgs = _raw_image_urls(product)
    if filtered:
        checks["images"] = True
    elif raw_imgs:
        # Validator rejected URLs that still exist on the product — warn, don't fail
        # when the rest of the extraction is solid (esp. JSON-LD).
        checks["images"] = True
        warnings.append(
            f"image_filter_soft_pass:{len(raw_imgs)}_raw_url(s)_kept_despite_filter"
        )
        log.warning(
            "Pre-flight: image filter rejected candidates but product still has "
            "%s raw image URL(s) — treating images as present",
            len(raw_imgs),
        )
        for src in raw_imgs:
            log.info(
                "Image rejected: %s — reason: preflight_nav_filter "
                "(kept for soft-pass)",
                src,
            )
    else:
        missing.append("images")

    if _description_text(product):
        checks["description"] = True
    else:
        missing.append("description")

    conf = _extraction_confidence(product)
    # High-confidence JSON-LD with all core fields + ≥1 raw image URL → pass.
    if missing == ["images"] and raw_imgs and conf >= 99.0 and _jsonld_sourced(product):
        checks["images"] = True
        missing = []
        warnings.append("jsonld_100_soft_pass_images")
        log.warning(
            "Pre-flight: JSON-LD confidence≈100%% with title/price/description "
            "+ %s image URL(s) — PASSED with warning (strict image filter bypassed)",
            len(raw_imgs),
        )

    return {
        "passed": not missing,
        "missing": missing,
        "warnings": warnings,
        "checks": checks,
        "title": title,
        "price": _product_price_value(product),
        "images": len(filtered) or len(raw_imgs),
        "description_len": len(_description_text(product)),
        "confidence": conf,
    }


def _check_mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def run_preflight(
    *,
    seed_url: str,
    product_url: str,
    platform: str,
    confidence: int,
    extract_fn: ExtractFn,
    output_dir: Path | str | None = None,
    fingerprint: dict[str, Any] | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Extract one PDP with platform strategies and gate the full run."""
    log = logger or logging.getLogger("sentivo_extractor")
    domain = domain_key(seed_url) or domain_key(product_url)
    log.info(
        "[Pre-flight] Testing 1 product on %s as %s (%spts): %s",
        domain,
        platform,
        confidence,
        product_url,
    )
    product: dict[str, Any] | None = None
    reason = ""
    try:
        outcome = extract_fn(product_url, platform=platform)
        if isinstance(outcome, dict):
            if outcome.get("product"):
                product = outcome["product"]
            elif outcome.get("title") or outcome.get("variants"):
                product = outcome
            if not outcome.get("success", True) and not product:
                reason = str(outcome.get("reason") or "extraction_failed")
    except Exception as exc:
        reason = str(exc)
        log.warning("[Pre-flight] Extraction error: %s", exc)

    check = validate_preflight_product(product, logger=log)
    if reason and not product:
        check = {
            "passed": False,
            "missing": ["title", "price", "images", "description"],
            "checks": {
                "title": False,
                "price": False,
                "images": False,
                "description": False,
            },
            "warnings": [],
            "error": reason,
        }

    # Extra soft-pass: extraction reported success / 100% confidence with core fields
    # and at least one image URL, even if the image engine later emptied images.
    if (
        not check.get("passed")
        and isinstance(product, dict)
        and is_valid_product_title(product.get("title"))
        and _product_price_value(product) is not None
        and _description_text(product)
        and _raw_image_urls(product)
    ):
        conf = _extraction_confidence(product)
        de = product.get("decision_engine") or {}
        passed_flag = bool(
            conf >= 99.0
            or (isinstance(de, dict) and (de.get("final_confidence") or {}).get("passed"))
        )
        if passed_flag or _jsonld_sourced(product):
            check = dict(check)
            check["passed"] = True
            check["missing"] = []
            check["checks"] = {
                "title": True,
                "price": True,
                "images": True,
                "description": True,
            }
            warns = list(check.get("warnings") or [])
            warns.append("successful_extraction_soft_pass_images")
            check["warnings"] = warns
            log.warning(
                "Pre-flight PASSED with warning — extraction looks complete "
                "(confidence=%s) but image validator was strict; not blocking run",
                conf,
            )

    result = {
        "passed": bool(check.get("passed")),
        "platform": platform,
        "confidence": confidence,
        "domain": domain,
        "product_url": product_url,
        "missing": list(check.get("missing") or []),
        "warnings": list(check.get("warnings") or []),
        "checks": dict(check.get("checks") or {}),
        "product": product,
        "check": check,
    }

    checks = result["checks"]
    if result["passed"]:
        warn_note = ""
        if result["warnings"]:
            warn_note = f" ⚠️ ({', '.join(result['warnings'])})"
        msg = (
            f"✅ Pre-flight PASSED — {platform} | "
            f"title {_check_mark(checks.get('title'))} | "
            f"price {_check_mark(checks.get('price'))} | "
            f"images {_check_mark(checks.get('images'))} | "
            f"description {_check_mark(checks.get('description'))} "
            f"— proceeding with full run{warn_note}"
        )
        log.info("%s %s confidence=%s", PREFLIGHT_PASS_MARKER, platform, confidence)
        log.info(msg)
        print(msg)
    else:
        missing = ", ".join(result["missing"]) or "unknown"
        msg = (
            f"❌ Pre-flight FAILED — missing: {missing} — "
            f"full run BLOCKED. Fix extraction first."
        )
        log.error("%s %s missing=%s", PREFLIGHT_FAIL_MARKER, platform, missing)
        log.error(msg)
        print(msg)

    # Update caches
    base = dict(fingerprint or domain_platform_cache.get(domain) or {})
    if domain:
        entry = {
            **base,
            "platform": platform,
            "confidence": confidence,
            "signals": list(base.get("signals") or []),
            "anti_bot": bool(base.get("anti_bot")),
            "vue_pricing": bool(base.get("vue_pricing")),
            "js_heavy": bool(base.get("js_heavy")),
            "blockers": list(base.get("blockers") or []),
            "pre_flight_passed": result["passed"],
            "detected_at": base.get("detected_at")
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        domain_platform_cache[domain] = entry
        if output_dir is not None:
            try:
                save_platform_cache_entry(output_dir, domain, entry)
            except Exception as exc:
                log.warning("Failed to update platform_cache.json: %s", exc)

    return result


class PreflightFailedError(RuntimeError):
    """Raised when pre-flight validation fails — full run must stop."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}
