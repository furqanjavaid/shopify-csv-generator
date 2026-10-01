"""Checkpoint / resume state for interrupted crawls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CheckpointStore:
    def __init__(self, path: Path, *, load_existing: bool = True) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {
            "completed_urls": [],
            "failed_urls": [],
            "products": [],
        }
        if load_existing:
            self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._data.update(data)
                self._data["completed_urls"] = list(self._data.get("completed_urls") or [])
                self._data["failed_urls"] = list(self._data.get("failed_urls") or [])
                self._data["products"] = list(self._data.get("products") or [])
        except Exception:
            pass

    def reset(self) -> None:
        """Delete checkpoint file and clear all in-memory state."""
        self._data = {
            "completed_urls": [],
            "failed_urls": [],
            "products": [],
        }
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @property
    def completed(self) -> set[str]:
        return set(self._data.get("completed_urls") or [])

    @property
    def failed(self) -> set[str]:
        out: set[str] = set()
        for item in self._data.get("failed_urls") or []:
            if isinstance(item, dict):
                url = str(item.get("url") or "").strip()
            else:
                url = str(item).strip()
            if url:
                out.add(url)
        return out

    def completed_count(self) -> int:
        return len(self._data.get("completed_urls") or [])

    def mark_done(self, url: str, product: dict[str, Any] | None = None) -> None:
        completed = list(self._data.get("completed_urls") or [])
        if url not in completed:
            completed.append(url)
        self._data["completed_urls"] = completed
        # Drop from failed if later succeeded
        failed = [
            item
            for item in (self._data.get("failed_urls") or [])
            if not (
                (isinstance(item, dict) and item.get("url") == url)
                or (not isinstance(item, dict) and item == url)
            )
        ]
        self._data["failed_urls"] = failed
        if product:
            products = list(self._data.get("products") or [])
            products.append(product)
            self._data["products"] = products
        self.save()

    def mark_failed(self, url: str, error: str) -> None:
        failed = list(self._data.get("failed_urls") or [])
        failed.append({"url": url, "error": error})
        self._data["failed_urls"] = failed
        self.save()

    def products(self) -> list[dict[str, Any]]:
        return list(self._data.get("products") or [])
