"""Checkpoint / resume state for interrupted crawls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sentivo_extractor.core.output_layout import (
    domain_artifact_path,
    domain_folder_name,
    ensure_domain_dir,
)


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
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
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
            encoding="utf-8-sig",
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


class DomainCheckpointManager:
    """
    Route checkpoint state into per-domain files:

      base/<domain>/<domain>_checkpoint.json
    """

    def __init__(
        self,
        base_dir: Path,
        *,
        load_existing: bool = True,
        overwrite: bool = False,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.load_existing = bool(load_existing) and not bool(overwrite)
        self.overwrite = bool(overwrite)
        self._stores: dict[str, CheckpointStore] = {}
        if self.overwrite:
            self.reset()
        else:
            self._discover_existing()

    @property
    def path(self) -> Path:
        """Primary checkpoint path (first domain store, else base placeholder)."""
        if self._stores:
            return next(iter(self._stores.values())).path
        return self.base_dir / "checkpoint.json"

    def _checkpoint_path(self, domain_key: str) -> Path:
        ensure_domain_dir(self.base_dir, domain_key)
        return domain_artifact_path(self.base_dir, domain_key, "checkpoint.json")

    def _discover_existing(self) -> None:
        if not self.load_existing:
            return
        for path in sorted(self.base_dir.glob("*/*_checkpoint.json")):
            key = path.parent.name
            if key not in self._stores:
                self._stores[key] = CheckpointStore(
                    path, load_existing=True
                )
        legacy = self.base_dir / "checkpoint.json"
        if legacy.exists() and "unknown" not in self._stores:
            self._stores["unknown"] = CheckpointStore(
                legacy, load_existing=True
            )

    def for_domain(self, domain_key: str) -> CheckpointStore:
        key = domain_key or "unknown"
        if key not in self._stores:
            store = CheckpointStore(
                self._checkpoint_path(key),
                load_existing=self.load_existing,
            )
            if self.overwrite:
                store.reset()
            self._stores[key] = store
        return self._stores[key]

    def for_url(self, url: str) -> CheckpointStore:
        return self.for_domain(domain_folder_name(url))

    def mark_done(self, url: str, product: dict[str, Any] | None = None) -> None:
        self.for_url(url).mark_done(url, product)

    def mark_failed(self, url: str, error: str) -> None:
        self.for_url(url).mark_failed(url, error)

    @property
    def completed(self) -> set[str]:
        out: set[str] = set()
        for store in self._stores.values():
            out |= store.completed
        return out

    @property
    def failed(self) -> set[str]:
        out: set[str] = set()
        for store in self._stores.values():
            out |= store.failed
        return out

    def completed_count(self) -> int:
        return len(self.completed)

    def products(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for store in self._stores.values():
            out.extend(store.products())
        return out

    def reset(self) -> None:
        for store in list(self._stores.values()):
            store.reset()
        self._stores.clear()
        for path in self.base_dir.glob("*/*_checkpoint.json"):
            try:
                path.unlink()
            except Exception:
                pass
        legacy = self.base_dir / "checkpoint.json"
        if legacy.exists():
            try:
                legacy.unlink()
            except Exception:
                pass
