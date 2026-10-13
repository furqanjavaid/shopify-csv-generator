"""
Robots.txt + Sitemap checker for Sentivo Tools.

Reads JSON from stdin: {"url": "https://example.com"}
Streams JSON lines to stdout:
  {"type": "log", "level": "INFO", "message": "..."}
  {"type": "robots", "found": true, "content": "...", "disallowed": [...], "sitemap_url": "..."}
  {"type": "sitemap", "found": true, "total": 50}
  {"type": "sitemap_url", "url": "...", "status": 200, "current": 1, "total": 50}
  {"type": "done", "total_urls": 50}
  {"type": "error", "error": "..."}
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TIMEOUT = 10
_UA = "SentivoTools/1.0 (+https://sentivo.co.uk)"


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _log(level: str, message: str) -> None:
    _emit({"type": "log", "level": level, "message": message})


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("No JSON payload on stdin.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Payload must be a JSON object.")
    return data


def _normalize_base(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise ValueError("Store URL is required.")
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if not parsed.netloc:
        raise ValueError("Invalid store URL.")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _session():
    import requests

    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept": "*/*"})
    return s


def _fetch_text(session: Any, url: str) -> tuple[bool, str, int | None]:
    try:
        resp = session.get(url, timeout=_TIMEOUT, allow_redirects=True)
        if resp.status_code >= 400:
            return False, "", resp.status_code
        return True, resp.text or "", resp.status_code
    except Exception as exc:  # noqa: BLE001
        _log("WARNING", f"Fetch failed for {url}: {exc}")
        return False, "", None


def _parse_robots(content: str) -> tuple[list[str], str]:
    disallowed: list[str] = []
    sitemap_url = ""
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key_l = key.strip().lower()
        value = value.strip()
        if key_l == "disallow" and value:
            disallowed.append(value)
        elif key_l == "sitemap" and value and not sitemap_url:
            sitemap_url = value
    # unique preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for path in disallowed:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique, sitemap_url


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _parse_sitemap_xml(content: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, child_sitemap_urls)."""
    page_urls: list[str] = []
    child_sitemaps: list[str] = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # Fallback: regex for <loc>
        locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", content, flags=re.I)
        return [u.strip() for u in locs if u.strip()], []

    root_name = _local_name(root.tag).lower()
    for el in root.iter():
        name = _local_name(el.tag).lower()
        if name != "loc":
            continue
        loc = (el.text or "").strip()
        if not loc:
            continue
        parent = None
        # ElementTree doesn't give parent easily; infer by root type
        if root_name == "sitemapindex":
            child_sitemaps.append(loc)
        else:
            page_urls.append(loc)

    if root_name == "sitemapindex" and not child_sitemaps:
        # Unusual structure — treat all locs as children
        locs = [
            (el.text or "").strip()
            for el in root.iter()
            if _local_name(el.tag).lower() == "loc"
        ]
        child_sitemaps = [u for u in locs if u]
        page_urls = []

    return page_urls, child_sitemaps


def _collect_sitemap_urls(session: Any, sitemap_url: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    queue = [sitemap_url]
    visited_maps: set[str] = set()

    while queue:
        current = queue.pop(0)
        if current in visited_maps:
            continue
        visited_maps.add(current)
        _log("INFO", f"Fetching sitemap: {current}")
        ok, content, status = _fetch_text(session, current)
        if not ok:
            _log("WARNING", f"Sitemap not reachable ({status}): {current}")
            continue
        pages, children = _parse_sitemap_xml(content)
        for child in children:
            if child not in visited_maps:
                queue.append(child)
        for page in pages:
            if page not in seen:
                seen.add(page)
                found.append(page)

    return found


def _head_status(session: Any, url: str) -> int | str:
    try:
        resp = session.head(url, timeout=_TIMEOUT, allow_redirects=False)
        # Some servers reject HEAD — fallback GET
        if resp.status_code in {405, 501}:
            resp = session.get(url, timeout=_TIMEOUT, allow_redirects=False, stream=True)
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        return int(resp.status_code)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"


def main() -> int:
    try:
        payload = _read_request()
        base = _normalize_base(str(payload.get("url") or ""))
    except Exception as exc:  # noqa: BLE001
        _emit({"type": "error", "error": str(exc)})
        return 2

    session = _session()
    robots_url = urljoin(base + "/", "robots.txt")
    _log("INFO", f"Checking store: {base}")
    _log("INFO", f"Fetching robots.txt: {robots_url}")

    robots_ok, robots_content, robots_status = _fetch_text(session, robots_url)
    disallowed: list[str] = []
    sitemap_from_robots = ""
    if robots_ok:
        disallowed, sitemap_from_robots = _parse_robots(robots_content)
        _log(
            "INFO",
            f"robots.txt found ({robots_status}). "
            f"{len(disallowed)} disallow rule(s).",
        )
    else:
        robots_content = ""
        _log("WARNING", f"robots.txt not found (status={robots_status}).")

    _emit(
        {
            "type": "robots",
            "found": bool(robots_ok),
            "content": robots_content,
            "disallowed": disallowed,
            "sitemap_url": sitemap_from_robots,
            "status": robots_status,
        }
    )

    sitemap_url = sitemap_from_robots or urljoin(base + "/", "sitemap.xml")
    if not sitemap_from_robots:
        _log("INFO", "No Sitemap directive in robots.txt — trying /sitemap.xml")

    sitemap_ok, sitemap_probe, sitemap_status = _fetch_text(session, sitemap_url)
    urls: list[str] = []
    if sitemap_ok:
        # Re-parse via collector (handles index)
        urls = _collect_sitemap_urls(session, sitemap_url)
        _log("INFO", f"Sitemap found. {len(urls)} URL(s) discovered.")
    else:
        # Try default if robots sitemap failed
        if sitemap_from_robots:
            fallback = urljoin(base + "/", "sitemap.xml")
            _log("INFO", f"Trying fallback sitemap: {fallback}")
            fb_ok, _, _ = _fetch_text(session, fallback)
            if fb_ok:
                sitemap_url = fallback
                sitemap_ok = True
                urls = _collect_sitemap_urls(session, fallback)
                _log("INFO", f"Fallback sitemap found. {len(urls)} URL(s).")
            else:
                _log("WARNING", f"Sitemap not found (status={sitemap_status}).")
        else:
            _log("WARNING", f"Sitemap not found (status={sitemap_status}).")

    _emit(
        {
            "type": "sitemap",
            "found": bool(sitemap_ok and urls),
            "total": len(urls),
            "sitemap_url": sitemap_url if sitemap_ok else "",
        }
    )

    total = len(urls)
    for i, page_url in enumerate(urls, start=1):
        status = _head_status(session, page_url)
        _emit(
            {
                "type": "sitemap_url",
                "url": page_url,
                "status": status,
                "current": i,
                "total": total,
            }
        )
        if i == 1 or i == total or i % 10 == 0:
            _log("INFO", f"Checked {i}/{total} sitemap URLs…")

    _emit({"type": "done", "total_urls": total})
    _log("INFO", "Check complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
