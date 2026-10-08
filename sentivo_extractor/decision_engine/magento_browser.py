"""
Shared headed-Chrome Playwright pool for Magento PDP fetches.

One browser stays alive for the whole crawl. Up to N pages fetch in parallel
via asyncio (Playwright sync API is not thread-safe for concurrent gotos).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from sentivo_extractor.core.utils import BROWSER_HEADERS

DEFAULT_WORKERS = 4
_SETTLE_FIRST_MS = 2500
_SETTLE_WARM_MS = 600


class MagentoBrowserPool:
    """Process-wide Magento browser: one Chrome, parallel page fetches."""

    def __init__(
        self,
        *,
        workers: int = DEFAULT_WORKERS,
        logger: logging.Logger | None = None,
    ) -> None:
        self.workers = max(1, min(5, int(workers)))
        self.logger = logger or logging.getLogger(__name__)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
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
            "[Magento] Playwright pool ready (workers=%s, session reused for all PDPs)",
            self.workers,
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

    def fetch_one(self, url: str, *, timeout_ms: int = 45000) -> str:
        result = self.fetch_many([url], timeout_ms=timeout_ms)
        return str(result.get(url) or "")

    def fetch_many(
        self, urls: list[str], *, timeout_ms: int = 45000
    ) -> dict[str, str]:
        if not urls:
            return {}
        self.start()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(
            self._fetch_many(urls, timeout_ms=timeout_ms), self._loop
        )
        # Per-URL budget + overhead for the batch
        budget = max(60.0, (timeout_ms / 1000.0) * max(2, len(urls) / self.workers))
        try:
            return fut.result(timeout=budget)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[Magento] Parallel fetch failed: %s", exc)
            return {u: "" for u in urls}

    async def _fetch_many(
        self, urls: list[str], *, timeout_ms: int
    ) -> dict[str, str]:
        assert self._context is not None
        sem = asyncio.Semaphore(self.workers)
        settle = _SETTLE_WARM_MS if self._warmed else _SETTLE_FIRST_MS
        out: dict[str, str] = {}

        async def one(url: str) -> None:
            async with sem:
                page = await self._context.new_page()
                try:
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=timeout_ms
                    )
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=min(12000, timeout_ms)
                        )
                    except Exception:
                        pass
                    await page.wait_for_timeout(settle)
                    html = await page.content()
                    out[url] = html or ""
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(
                        "[Magento] page fetch failed %s: %s", url, exc
                    )
                    out[url] = ""
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass

        await asyncio.gather(*[one(u) for u in urls])
        if any(out.values()):
            self._warmed = True
        return out

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
            self._warmed = False
            self._ready.clear()

    async def _async_close(self) -> None:
        for obj in (self._context, self._browser):
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
                workers=workers, logger=logger
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
