"""
Shared headed-Chrome Playwright session for Magento PDP fetches.

One browser, one tab — sequential only (avoids Sucuri IDS parallel blocks).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from sentivo_extractor.core.utils import BROWSER_HEADERS

DEFAULT_WORKERS = 1
_SETTLE_FIRST_MS = 2500
_SETTLE_WARM_MS = 800

_SUCURI_MARKERS = (
    "sucuri",
    "access denied",
    "website firewall",
)


def is_sucuri_block(html: str) -> bool:
    low = (html or "").lower()
    return any(m in low for m in _SUCURI_MARKERS)


class MagentoBrowserPool:
    """Process-wide Magento browser: one Chrome tab, sequential fetches."""

    def __init__(
        self,
        *,
        workers: int = DEFAULT_WORKERS,
        logger: logging.Logger | None = None,
    ) -> None:
        del workers  # always single-tab
        self.workers = 1
        self.logger = logger or logging.getLogger(__name__)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._warmed = False
        self._lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return (
            self._loop is not None
            and self._thread is not None
            and self._thread.is_alive()
            and self._context is not None
            and self._page is not None
            and self._start_error is None
        )

    def start(self) -> None:
        with self._lock:
            if self.alive:
                return
            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._thread_main,
                name="magento-playwright-pool",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=90):
            raise TimeoutError("Magento Playwright pool failed to start in time")
        if self._start_error is not None:
            raise RuntimeError(
                f"Magento Playwright pool failed: {self._start_error}"
            ) from self._start_error
        self.logger.info(
            "[Magento] Playwright session ready (single tab, sequential)"
        )

    def _thread_main(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._async_start())
            self._ready.set()
            self._loop.run_forever()
        except BaseException as exc:  # noqa: BLE001
            self._start_error = exc
            self._ready.set()
            self.logger.error("[Magento] Playwright pool thread crashed: %s", exc)

    async def _async_start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        try:
            self._browser = await self._pw.chromium.launch(
                channel="chrome", headless=False, args=args
            )
        except Exception:
            self._browser = await self._pw.chromium.launch(
                headless=False, args=args
            )
        extra = {
            k: v
            for k, v in BROWSER_HEADERS.items()
            if k.lower() != "user-agent"
        }
        self._context = await self._browser.new_context(
            extra_http_headers=extra,
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
        )
        self._page = await self._context.new_page()

    def fetch_one(self, url: str, *, timeout_ms: int = 45000) -> str:
        self.start()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(
            self._fetch_one(url, timeout_ms=timeout_ms), self._loop
        )
        try:
            html = fut.result(timeout=max(90.0, timeout_ms / 1000.0 + 30))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[Magento] Sequential fetch failed: %s", exc)
            return ""

        if is_sucuri_block(html):
            self.logger.warning(
                "[Magento] Sucuri block detected — pausing 5 minutes"
            )
            time.sleep(300)
            # Retry once after cooldown on the same single tab.
            fut2 = asyncio.run_coroutine_threadsafe(
                self._fetch_one(url, timeout_ms=timeout_ms), self._loop
            )
            try:
                html = fut2.result(timeout=max(90.0, timeout_ms / 1000.0 + 30))
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    "[Magento] Fetch after Sucuri pause failed: %s", exc
                )
                return ""
            if is_sucuri_block(html):
                self.logger.warning(
                    "[Magento] Sucuri still blocking after pause — continuing"
                )
                return ""
        return html or ""

    def fetch_many(
        self, urls: list[str], *, timeout_ms: int = 45000
    ) -> dict[str, str]:
        """Sequential single-tab fetch (no parallel)."""
        out: dict[str, str] = {}
        for url in urls:
            out[url] = self.fetch_one(url, timeout_ms=timeout_ms)
        return out

    async def _fetch_one(self, url: str, *, timeout_ms: int) -> str:
        assert self._page is not None
        settle = _SETTLE_WARM_MS if self._warmed else _SETTLE_FIRST_MS
        try:
            await self._page.goto(
                url, wait_until="domcontentloaded", timeout=timeout_ms
            )
            try:
                await self._page.wait_for_load_state(
                    "networkidle", timeout=min(12000, timeout_ms)
                )
            except Exception:
                pass
            await self._page.wait_for_timeout(settle)
            html = await self._page.content()
            if html:
                self._warmed = True
            return html or ""
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[Magento] page fetch failed %s: %s", url, exc)
            return ""

    def close(self) -> None:
        with self._lock:
            loop = self._loop
            if loop is not None and loop.is_running():
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        self._async_close(), loop
                    )
                    fut.result(timeout=20)
                except Exception:
                    pass
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except Exception:
                    pass
            if self._thread is not None:
                self._thread.join(timeout=15)
            self._thread = None
            self._loop = None
            self._pw = None
            self._browser = None
            self._context = None
            self._page = None
            self._warmed = False
            self._ready.clear()

    async def _async_close(self) -> None:
        for obj in (self._page, self._context, self._browser):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass


_SHARED_MAGENTO_POOL: MagentoBrowserPool | None = None
_POOL_LOCK = threading.Lock()


def shared_magento_pool(
    *,
    workers: int = DEFAULT_WORKERS,
    logger: logging.Logger | None = None,
) -> MagentoBrowserPool:
    global _SHARED_MAGENTO_POOL
    with _POOL_LOCK:
        if _SHARED_MAGENTO_POOL is None or not _SHARED_MAGENTO_POOL.alive:
            _SHARED_MAGENTO_POOL = MagentoBrowserPool(
                workers=1, logger=logger
            )
        return _SHARED_MAGENTO_POOL


def close_shared_magento_pool() -> None:
    global _SHARED_MAGENTO_POOL
    with _POOL_LOCK:
        if _SHARED_MAGENTO_POOL is not None:
            try:
                _SHARED_MAGENTO_POOL.close()
            except Exception:
                pass
            _SHARED_MAGENTO_POOL = None
