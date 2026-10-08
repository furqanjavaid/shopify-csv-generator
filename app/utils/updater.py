"""Auto-updater — checks GitHub releases for new version."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from typing import Any

import requests

GITHUB_API_LATEST = (
    "https://api.github.com/repos/furqanjavaid/shopify-csv-generator/releases/latest"
)
GITHUB_API_RELEASES = (
    "https://api.github.com/repos/furqanjavaid/shopify-csv-generator/releases"
)
GITHUB_RELEASES_PAGE = (
    "https://github.com/furqanjavaid/shopify-csv-generator/releases"
)


def get_current_version() -> str:
    # Check next to .exe first (installed version)
    if getattr(sys, "frozen", False):
        # Running as PyInstaller .exe
        exe_dir = os.path.dirname(sys.executable)
        version_file = os.path.join(exe_dir, "version.json")
    else:
        # Running as Python script
        version_file = os.path.join(
            os.path.dirname(__file__), "../../version.json"
        )

    try:
        with open(version_file, encoding="utf-8") as f:
            return json.load(f)["version"]
    except Exception:
        return "1.0.0"


def _exe_asset_url(release: dict[str, Any]) -> str | None:
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "").lower()
        if name.endswith(".exe"):
            url = asset.get("browser_download_url")
            if url:
                return str(url)
    return None


def check_for_update(callback) -> None:
    """Run in background thread — calls callback(latest, url, notes) if update available."""

    def _check():
        try:
            resp = requests.get(GITHUB_API_LATEST, timeout=8)
            if resp.status_code != 200:
                return

            data = resp.json()
            if "tag_name" not in data:
                return

            latest = str(data["tag_name"]).lstrip("v")
            current = get_current_version()
            if not _version_gt(latest, current):
                return

            download_url = _exe_asset_url(data)
            # Latest release may be notes-only (build still running). Fall back to
            # the newest newer-than-current release that has an installer asset.
            if not download_url:
                try:
                    listing = requests.get(GITHUB_API_RELEASES, timeout=8)
                    if listing.status_code == 200:
                        for rel in listing.json() or []:
                            tag = str(rel.get("tag_name") or "").lstrip("v")
                            if not _version_gt(tag, current):
                                continue
                            asset_url = _exe_asset_url(rel)
                            if asset_url:
                                download_url = asset_url
                                latest = tag
                                data = rel
                                break
                except Exception:
                    pass

            # Always provide a clickable target: exe URL or release page.
            if not download_url:
                download_url = (
                    str(data.get("html_url") or "").strip() or GITHUB_RELEASES_PAGE
                )

            release_notes = data.get("body", "Bug fixes and improvements") or (
                "Bug fixes and improvements"
            )
            release_notes = str(release_notes).split("\n")[0][:80]
            callback(latest, download_url, release_notes)
        except Exception:
            pass

    thread = threading.Thread(target=_check, daemon=True)
    thread.start()


def _version_gt(a: str, b: str) -> bool:
    """Returns True if version a > version b."""
    try:
        a_parts = [int(x) for x in a.split(".")]
        b_parts = [int(x) for x in b.split(".")]
        return a_parts > b_parts
    except Exception:
        return False


def download_and_install(download_url: str, progress_callback=None) -> None:
    """Download new installer and run it. Opens browser if URL is not an .exe."""
    url = (download_url or "").strip()
    if not url:
        webbrowser.open(GITHUB_RELEASES_PAGE)
        return

    # No installer asset yet — open the release page instead of failing silently.
    if not url.lower().endswith(".exe"):
        webbrowser.open(url)
        return

    import tempfile
    import urllib.request

    tmp = tempfile.mktemp(suffix=".exe")

    def _reporthook(count, block_size, total_size):
        if progress_callback and total_size > 0:
            percent = int(count * block_size * 100 / total_size)
            progress_callback(min(percent, 100))

    urllib.request.urlretrieve(url, tmp, _reporthook)

    # Run installer silently
    subprocess.Popen([tmp, "/SILENT", "/NORESTART"])

    # Close current app
    sys.exit(0)
