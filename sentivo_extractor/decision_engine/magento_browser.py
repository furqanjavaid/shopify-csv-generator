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
        """Back-compat: HTML only."""
        return str(self.fetch_page(url, timeout_ms=timeout_ms).get("html") or "")

    def fetch_page(
        self, url: str, *, timeout_ms: int = 45000
    ) -> dict[str, Any]:
        """
        Fetch PDP HTML + optional CutConfiguratorWidget Vue pricing state.
        Returns {html, configurator_pricing}.
        """
        self.start()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(
            self._fetch_page(url, timeout_ms=timeout_ms), self._loop
        )
        try:
            result = fut.result(timeout=max(90.0, timeout_ms / 1000.0 + 45))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[Magento] Sequential fetch failed: %s", exc)
            return {"html": "", "configurator_pricing": None}

        html = str((result or {}).get("html") or "")
        pricing = (result or {}).get("configurator_pricing")

        if is_sucuri_block(html):
            self.logger.warning(
                "[Magento] Sucuri block detected — pausing 5 minutes"
            )
            time.sleep(300)
            fut2 = asyncio.run_coroutine_threadsafe(
                self._fetch_page(url, timeout_ms=timeout_ms), self._loop
            )
            try:
                result = fut2.result(
                    timeout=max(90.0, timeout_ms / 1000.0 + 45)
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    "[Magento] Fetch after Sucuri pause failed: %s", exc
                )
                return {"html": "", "configurator_pricing": None}
            html = str((result or {}).get("html") or "")
            pricing = (result or {}).get("configurator_pricing")
            if is_sucuri_block(html):
                self.logger.warning(
                    "[Magento] Sucuri still blocking after pause — continuing"
                )
                return {"html": "", "configurator_pricing": None}
        return {"html": html or "", "configurator_pricing": pricing}

    def fetch_many(
        self, urls: list[str], *, timeout_ms: int = 45000
    ) -> dict[str, str]:
        """Sequential single-tab fetch (no parallel)."""
        out: dict[str, str] = {}
        for url in urls:
            out[url] = self.fetch_one(url, timeout_ms=timeout_ms)
        return out

    async def _fetch_page(self, url: str, *, timeout_ms: int) -> dict[str, Any]:
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
            # Give Vue CutConfiguratorWidget time to hydrate store.
            try:
                await self._page.wait_for_function(
                    """() => {
                        const w = window.CutConfiguratorWidget;
                        return !!(w && w.vm && w.vm.$store
                          && w.vm.$store.state
                          && w.vm.$store.state.Configurator
                          && w.vm.$store.state.Configurator.confData);
                    }""",
                    timeout=15000,
                )
                # Extra beat so selectedPathWay / thickness price settle.
                await self._page.wait_for_timeout(1500)
            except Exception:
                self.logger.warning(
                    "[Magento] CutConfiguratorWidget not ready after wait — probing keys"
                )
            html = await self._page.content()
            pricing = await self._read_cut_configurator_pricing()
            if html:
                self._warmed = True
            return {"html": html or "", "configurator_pricing": pricing}
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[Magento] page fetch failed %s: %s", url, exc)
            return {"html": "", "configurator_pricing": None}

    async def _read_cut_configurator_pricing(self) -> dict[str, Any] | None:
        """Evaluate Vue store → structured pricePerSqm / size / polish / holes."""
        assert self._page is not None
        try:
            from sentivo_extractor.extractors.cut_configurator import (
                CUT_CONFIGURATOR_EVAL_JS,
                parse_cut_configurator_payload,
            )

            # Probe confData first (helps diagnose undefined widget).
            probe = await self._page.evaluate(
                """() => {
                  try {
                    const conf = window.CutConfiguratorWidget
                      && window.CutConfiguratorWidget.vm
                      && window.CutConfiguratorWidget.vm.$store
                      && window.CutConfiguratorWidget.vm.$store.state
                      && window.CutConfiguratorWidget.vm.$store.state.Configurator;
                    if (!conf) {
                      const keys = Object.keys(window).filter(
                        k => /Cut|Config|Widget/i.test(k)
                      );
                      return { ok: false, keys };
                    }
                    const cd = conf.confData;
                    return {
                      ok: !!cd,
                      confDataKeys: cd ? Object.keys(cd) : [],
                      entityLen: cd && cd.entities ? cd.entities.length : 0,
                      hasPath: Array.isArray(conf.selectedPathWay)
                        && conf.selectedPathWay.length > 0,
                    };
                  } catch (e) {
                    return { ok: false, error: String(e) };
                  }
                }"""
            )
            self.logger.info("[Magento] CutConfigurator probe: %s", probe)

            if not (isinstance(probe, dict) and probe.get("ok")):
                keys = (probe or {}).get("keys") if isinstance(probe, dict) else None
                self.logger.warning(
                    "[Magento] CutConfigurator confData missing; window keys=%s",
                    keys,
                )
                return None

            raw = await self._page.evaluate(CUT_CONFIGURATOR_EVAL_JS)
            pricing = parse_cut_configurator_payload(raw)
            if pricing:
                self.logger.info(
                    "[Magento] CutConfigurator state: pricePerSqm=%s sku=%s",
                    pricing.get("pricePerSqm"),
                    pricing.get("sku") or "-",
                )
            else:
                self.logger.warning(
                    "[Magento] CutConfigurator evaluate returned no pricing: %s",
                    str(raw)[:300] if raw is not None else None,
                )
            return pricing
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "[Magento] CutConfigurator evaluate skipped: %s", exc
            )
            return None

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
