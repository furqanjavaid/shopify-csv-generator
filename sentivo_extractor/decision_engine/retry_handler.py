"""URL-level retries with exponential backoff."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypeVar

from sentivo_extractor.decision_engine.captcha_detector import (
    CaptchaBlockedError,
    CaptchaTimeoutError,
)

T = TypeVar("T")

# Max retries after the first attempt (total attempts = max_retries + 1 when counting
# retries; we enforce max_attempts = 3 total tries with backoff between them).
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 5.0, 10.0)


class RetryHandler:
    """
    Max 3 attempts per URL with exponential backoff: 2s → 5s → 10s.
    After all fails → mark as failed and continue. CAPTCHA errors are never retried.
    """

    def __init__(
        self,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_seconds: tuple[float, ...] = BACKOFF_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_seconds = tuple(backoff_seconds) or BACKOFF_SECONDS
        self.logger = logger or logging.getLogger(__name__)
        self.failure_log: list[dict[str, Any]] = []

    def run(
        self,
        url: str,
        fn: Callable[[], T],
        *,
        is_success: Callable[[T], bool] | None = None,
    ) -> tuple[T | None, str]:
        """
        Execute ``fn`` up to ``max_attempts`` times.

        Returns ``(result, reason)``. On success reason is "".
        CAPTCHA errors propagate immediately.
        """
        last_reason = "extraction_failed"
        last_result: T | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                result = fn()
            except (CaptchaBlockedError, CaptchaTimeoutError):
                raise
            except Exception as exc:  # noqa: BLE001
                last_reason = f"exception:{exc}"
                self.logger.warning(
                    "DecisionEngine retry attempt %s/%s failed for %s: %s",
                    attempt,
                    self.max_attempts,
                    url,
                    exc,
                )
                last_result = None
                if attempt < self.max_attempts:
                    self._sleep(attempt)
                continue

            last_result = result
            ok = is_success(result) if is_success else bool(result)
            if ok:
                return result, ""

            last_reason = "low_confidence_or_empty"
            if isinstance(result, tuple) and len(result) >= 2:
                maybe_reason = result[1]
                if isinstance(maybe_reason, str) and maybe_reason:
                    last_reason = maybe_reason
            elif isinstance(result, dict):
                last_reason = str(
                    result.get("reason") or result.get("_fail_reason") or last_reason
                )

            self.logger.warning(
                "DecisionEngine attempt %s/%s unsuccessful for %s: %s",
                attempt,
                self.max_attempts,
                url,
                last_reason,
            )
            if attempt < self.max_attempts:
                self._sleep(attempt)

        self.failure_log.append({"url": url, "reason": last_reason})
        self.logger.error(
            "DecisionEngine giving up after %s attempts: %s (%s)",
            self.max_attempts,
            url,
            last_reason,
        )
        return last_result, last_reason

    def _sleep(self, attempt: int) -> None:
        # attempt is 1-based completed attempt; sleep before next.
        idx = min(attempt - 1, len(self.backoff_seconds) - 1)
        delay = float(self.backoff_seconds[idx])
        self.logger.info("DecisionEngine backoff %.0fs before retry", delay)
        time.sleep(delay)
