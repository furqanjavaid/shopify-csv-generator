"""Detect CAPTCHA / bot-block pages. Manual intervention only — no bypass."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Special markers for CLI ↔ UI signalling
CAPTCHA_LOG_MARKER = "DECISION_ENGINE:CAPTCHA_BLOCKED"
CAPTCHA_EXIT_CODE = 75
CAPTCHA_PAUSE_FILENAME = "captcha_pause.json"

_CAPTCHA_KEYWORDS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cf-challenge",
    "cf-browser-check",
    "challenge-platform",
    "attention required",
    "verify you are human",
    "verify you are a human",
    "are you a robot",
    "i'm not a robot",
    "security check",
    "access denied",
    "just a moment",
    "checking your browser",
    "enable javascript and cookies",
    "ray id",
    "cloudflare",
    "ddos protection",
    "bot detection",
    "perimeterx",
    "datadome",
    "please complete the security check",
)

_BLOCK_STATUS = frozenset({403, 429, 503})


class CaptchaBlockedError(RuntimeError):
    """Raised when a CAPTCHA / rate-limit / Cloudflare challenge is detected."""

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        status_code: int | None = None,
        reason: str = "captcha_or_block",
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.reason = reason


class CaptchaDetector:
    """
    Detect status 403/429, CAPTCHA keywords, and Cloudflare challenges.
    On detection the caller must pause immediately — never auto-bypass.
    """

    def inspect(
        self,
        *,
        status_code: int | None = None,
        html: str = "",
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        code = int(status_code) if status_code is not None else None
        if code in _BLOCK_STATUS:
            reasons.append(f"http_{code}")

        hdrs = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
        server = hdrs.get("server", "")
        if "cloudflare" in server and code in (403, 429, 503):
            reasons.append("cloudflare_server")
        if "cf-ray" in hdrs and code in (403, 429, 503):
            reasons.append("cloudflare_ray")

        low = (html or "").lower()
        hits = [kw for kw in _CAPTCHA_KEYWORDS if kw in low]
        # Require stronger signal than a lone "cloudflare" footer on a normal page.
        strong = [
            h
            for h in hits
            if h
            not in (
                "cloudflare",
                "ray id",
            )
        ]
        if strong:
            reasons.append("captcha_keywords:" + ",".join(strong[:5]))
        elif hits and code in _BLOCK_STATUS:
            reasons.append("block_page_keywords:" + ",".join(hits[:5]))

        # Cloudflare interstitial often has challenge form with 403/503
        if ("cf-challenge" in low or "challenge-platform" in low) and (
            code in _BLOCK_STATUS or "just a moment" in low
        ):
            if "cloudflare_challenge" not in reasons:
                reasons.append("cloudflare_challenge")

        blocked = bool(reasons)
        return {
            "blocked": blocked,
            "reasons": reasons,
            "status_code": code,
            "url": url,
            "message": (
                f"CAPTCHA/block detected ({'; '.join(reasons)})"
                if blocked
                else ""
            ),
        }

    def raise_if_blocked(
        self,
        *,
        status_code: int | None = None,
        html: str = "",
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        result = self.inspect(
            status_code=status_code, html=html, url=url, headers=headers
        )
        if result["blocked"]:
            raise CaptchaBlockedError(
                result["message"],
                url=url,
                status_code=status_code,
                reason=";".join(result["reasons"]),
            )

    @staticmethod
    def write_pause_file(
        output_dir: Path,
        *,
        url: str,
        reason: str,
        status_code: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / CAPTCHA_PAUSE_FILENAME
        payload = {
            "paused": True,
            "url": url,
            "reason": reason,
            "status_code": status_code,
            "paused_at": datetime.now(timezone.utc).isoformat(),
            **(extra or {}),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8-sig")
        return path

    @staticmethod
    def clear_pause_file(output_dir: Path) -> None:
        path = Path(output_dir) / CAPTCHA_PAUSE_FILENAME
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    @staticmethod
    def read_pause_file(output_dir: Path) -> dict[str, Any] | None:
        path = Path(output_dir) / CAPTCHA_PAUSE_FILENAME
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None
