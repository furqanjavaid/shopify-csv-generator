"""Auto-updater — checks GitHub releases for new version."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import requests

GITHUB_API = (
    "https://api.github.com/repos/furqanjavaid/shopify-csv-generator/releases/latest"
)
VERSION_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "version.json")
)


def get_current_version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return json.load(f)["version"]
    except Exception:
        return "1.0.0"


def check_for_update(callback) -> None:
    """Run in background thread — calls callback(latest, url, notes) if update available."""

    def _check():
        try:
            print(f"[Updater] Current version: {get_current_version()}")
            resp = requests.get(GITHUB_API, timeout=5)
            print(f"[Updater] Status: {resp.status_code}")

            if resp.status_code != 200:
                print(f"[Updater] No releases found ({resp.status_code})")
                return

            data = resp.json()

            if "tag_name" not in data:
                print(f"[Updater] Invalid response: {data}")
                return

            latest = data["tag_name"].lstrip("v")
            current = get_current_version()
            print(f"[Updater] Latest: {latest}, Current: {current}")

            if _version_gt(latest, current):
                download_url = None
                for asset in data.get("assets", []):
                    if asset["name"].lower().endswith(".exe"):
                        download_url = asset["browser_download_url"]
                        break
                release_notes = data.get("body", "Bug fixes and improvements")
                release_notes = release_notes.split("\n")[0][:80]
                print(f"[Updater] Update available: v{latest}")
                callback(latest, download_url, release_notes)
        except Exception as e:
            print(f"[Updater] ERROR: {e}")

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
    """Download new installer and run it."""
    import tempfile
    import urllib.request

    tmp = tempfile.mktemp(suffix=".exe")

    def _reporthook(count, block_size, total_size):
        if progress_callback and total_size > 0:
            percent = int(count * block_size * 100 / total_size)
            progress_callback(min(percent, 100))

    urllib.request.urlretrieve(download_url, tmp, _reporthook)

    # Run installer silently
    subprocess.Popen([tmp, "/SILENT", "/NORESTART"])

    # Close current app
    sys.exit(0)
