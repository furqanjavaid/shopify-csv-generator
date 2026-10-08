"""Strategy fallback chain with per-strategy attempt limits."""

from __future__ import annotations

import logging
from typing import Any, Callable

from sentivo_extractor.decision_engine.confidence_scorer import ConfidenceScorer

# Each strategy gets this many attempts before moving to the next.
ATTEMPTS_PER_STRATEGY = 2


class FallbackManager:
    """
    Walk a strategy chain. Each strategy gets 2 attempts before moving on.
    Logs which strategy succeeded per URL.
    """

    def __init__(
        self,
        *,
        scorer: ConfidenceScorer | None = None,
        attempts_per_strategy: int = ATTEMPTS_PER_STRATEGY,
        logger: logging.Logger | None = None,
    ) -> None:
        self.scorer = scorer or ConfidenceScorer()
        self.attempts_per_strategy = max(1, int(attempts_per_strategy))
        self.logger = logger or logging.getLogger(__name__)
        # url → strategy that last succeeded
        self.success_log: dict[str, str] = {}

    def run_chain(
        self,
        url: str,
        strategies: list[str],
        runner: Callable[[str, int], dict[str, Any] | None],
        *,
        price_optional: bool = False,
    ) -> dict[str, Any]:
        """
        Execute strategies in order.

        ``runner(strategy, attempt_index)`` should return a product-like dict
        (or merged values) for scoring, or None on failure.
        """
        attempted: list[str] = []
        last_partial: dict[str, Any] | None = None
        last_score: dict[str, Any] | None = None

        for strategy in strategies:
            for attempt in range(1, self.attempts_per_strategy + 1):
                attempted.append(f"{strategy}#{attempt}")
                self.logger.info(
                    "DecisionEngine strategy=%s attempt=%s/%s url=%s",
                    strategy,
                    attempt,
                    self.attempts_per_strategy,
                    url,
                )
                try:
                    partial = runner(strategy, attempt)
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(
                        "DecisionEngine strategy %s failed: %s", strategy, exc
                    )
                    partial = None

                if not partial:
                    continue

                last_partial = partial
                score = self.scorer.score(partial, price_optional=price_optional)
                last_score = score
                self.logger.info(
                    "DecisionEngine strategy=%s confidence=%s%% (need %s%%) url=%s",
                    strategy,
                    score["percent"],
                    score["threshold"],
                    url,
                )
                if score["passed"]:
                    self.success_log[url] = strategy
                    self.logger.info(
                        "DecisionEngine SUCCESS strategy=%s url=%s", strategy, url
                    )
                    return {
                        "success": True,
                        "strategy": strategy,
                        "partial": partial,
                        "score": score,
                        "attempted": attempted,
                    }

                # Confidence < 70% → retry same strategy, then next.
                self.logger.info(
                    "DecisionEngine confidence below threshold — "
                    "will retry/fallback (strategy=%s)",
                    strategy,
                )

        return {
            "success": False,
            "strategy": None,
            "partial": last_partial,
            "score": last_score,
            "attempted": attempted,
        }
