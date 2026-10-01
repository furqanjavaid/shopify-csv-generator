"""HTTP client with rate limiting, retries, and robots awareness."""

from __future__ import annotations

import logging
from typing import Any

import requests

from sentivo_extractor.core.utils import (
    DEFAULT_USER_AGENT,
    RateLimiter,
    RobotsCache,
    retry_call,
)


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 25.0,
        delay_sec: float = 1.0,
        retries: int = 3,
        respect_robots: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.respect_robots = respect_robots
        self.logger = logger or logging.getLogger("sentivo_extractor")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.limiter = RateLimiter(delay_sec)
        self.robots = RobotsCache(user_agent)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        if not self.robots.allowed(url, self.respect_robots):
            raise PermissionError(f"Blocked by robots.txt: {url}")

        def _do() -> requests.Response:
            self.limiter.wait()
            opts = {"timeout": self.timeout, "allow_redirects": True}
            opts.update(kwargs)
            resp = self.session.get(url, **opts)
            resp.raise_for_status()
            return resp

        return retry_call(_do, retries=self.retries, logger=self.logger)

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.get(url, **kwargs).text

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.get(url, **kwargs).json()
