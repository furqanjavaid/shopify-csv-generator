"""Shared per-tool job status (Home card + scraper mid-run restore)."""

from __future__ import annotations

import time
from typing import Any

TOOL_URL_SCRAPER = "url_scraper"
TOOL_STORE_AUDITOR = "store_auditor"
TOOL_FILE_UPLOAD = "file_upload"

TOOL_LABELS = {
    TOOL_URL_SCRAPER: "URL Scraper",
    TOOL_STORE_AUDITOR: "Store Auditor",
    TOOL_FILE_UPLOAD: "File Upload",
}


def format_elapsed(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, rem = divmod(s, 60)
    if m < 60:
        return f"{m}m {rem}s"
    h, rem_m = divmod(m, 60)
    return f"{h}h {rem_m}m {rem}s"


class ToolJob:
    """State for a single tool's background job."""

    def __init__(self, tool_id: str, label: str) -> None:
        self.tool_id = tool_id
        self.label = label
        self.state: str = "idle"  # idle | running | complete | stopped
        self.started_at: float | None = None
        self.product_count: int | None = None
        self.message: str = "Ready"
        self.log_lines: list[str] = []
        self.proc: Any = None
        self.stop_requested: bool = False
        self.ui_meta: dict[str, Any] = {}
        self.updated_at: float = time.time()

    def clear_log(self) -> None:
        self.log_lines.clear()

    def append_log(self, message: str) -> None:
        text = (message or "").rstrip()
        if text:
            self.log_lines.append(text)

    def begin(self) -> None:
        self.state = "running"
        self.started_at = time.time()
        self.product_count = None
        self.stop_requested = False
        self.proc = None
        self.log_lines.clear()
        self.ui_meta = {}
        self.message = f"{self.label} running..."
        self.updated_at = time.time()

    def complete(self, product_count: int | None = None) -> None:
        self.state = "complete"
        if product_count is not None:
            self.product_count = int(product_count)
        count = self.product_count if self.product_count is not None else 0
        self.message = f"{self.label} complete — {count} products extracted"
        self.proc = None
        self.stop_requested = False
        self.updated_at = time.time()

    def stopped(self) -> None:
        self.state = "stopped"
        self.message = f"{self.label} stopped by user"
        self.proc = None
        self.stop_requested = False
        self.updated_at = time.time()

    def idle(self) -> None:
        self.state = "idle"
        self.started_at = None
        self.product_count = None
        self.message = "Ready"
        self.proc = None
        self.stop_requested = False
        self.ui_meta = {}
        self.updated_at = time.time()

    def elapsed_text(self) -> str:
        if self.started_at is None:
            return ""
        return format_elapsed(time.time() - self.started_at)

    def display_text(self) -> str:
        if self.state == "running":
            elapsed = self.elapsed_text()
            base = f"{self.label} running..."
            return f"{base} ({elapsed})" if elapsed else base
        if self.state == "complete":
            if self.product_count is not None:
                return f"{self.label} complete — {self.product_count} products extracted"
            return self.message or f"{self.label} complete"
        if self.state == "stopped":
            return f"{self.label} stopped by user"
        return self.message or "Ready"


class JobStatusStore:
    """Process-wide status for tools (survives screen destroy/recreate)."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolJob] = {
            tid: ToolJob(tid, label) for tid, label in TOOL_LABELS.items()
        }
        # Back-compat aliases used by older Home/scraper code
        self.job_name: str = TOOL_LABELS[TOOL_URL_SCRAPER]
        self.product_count: int | None = None
        self.message: str = "Ready"

    def get(self, tool_id: str) -> ToolJob:
        if tool_id not in self._tools:
            self._tools[tool_id] = ToolJob(tool_id, tool_id)
        return self._tools[tool_id]

    def is_running(self, tool_id: str) -> bool:
        return self.get(tool_id).state == "running"

    def try_begin(self, tool_id: str) -> bool:
        """Start a job for this tool. Returns False if already running."""
        job = self.get(tool_id)
        if job.state == "running":
            return False
        job.begin()
        self.job_name = job.label
        self.product_count = None
        self.message = job.message
        return True

    def append_log(self, tool_id: str, message: str) -> None:
        self.get(tool_id).append_log(message)

    def get_log(self, tool_id: str) -> list[str]:
        return list(self.get(tool_id).log_lines)

    def set_proc(self, tool_id: str, proc: Any) -> None:
        self.get(tool_id).proc = proc

    def request_stop(self, tool_id: str) -> Any:
        """Mark stop requested; return the stored subprocess if any."""
        job = self.get(tool_id)
        job.stop_requested = True
        return job.proc

    def stop_requested(self, tool_id: str) -> bool:
        return bool(self.get(tool_id).stop_requested)

    def set_ui_meta(self, tool_id: str, meta: dict[str, Any]) -> None:
        self.get(tool_id).ui_meta = dict(meta or {})

    def set_complete(
        self, product_count: int, job_name: str | None = None, *, tool_id: str | None = None
    ) -> None:
        tid = tool_id or self._resolve_tool_id(job_name)
        job = self.get(tid)
        if job_name:
            job.label = job_name
        job.complete(product_count)
        self.job_name = job.label
        self.product_count = job.product_count
        self.message = job.message

    def set_stopped(self, job_name: str | None = None, *, tool_id: str | None = None) -> None:
        tid = tool_id or self._resolve_tool_id(job_name)
        job = self.get(tid)
        if job_name:
            job.label = job_name
        job.stopped()
        self.job_name = job.label
        self.message = job.message

    def set_running(self, job_name: str = "URL Scraper") -> None:
        """Back-compat: begin URL Scraper (or named tool) if not already running."""
        tid = self._resolve_tool_id(job_name)
        job = self.get(tid)
        if job.state == "running":
            # Keep existing started_at / log — do not reset mid-run
            self.job_name = job.label
            self.message = job.message
            return
        job.label = job_name or job.label
        job.begin()
        self.job_name = job.label
        self.product_count = None
        self.message = job.message

    def set_idle(self, *, tool_id: str | None = None) -> None:
        if tool_id:
            self.get(tool_id).idle()
        else:
            for job in self._tools.values():
                if job.state != "running":
                    job.idle()
        self.message = "Ready"

    @property
    def state(self) -> str:
        running = [j for j in self._tools.values() if j.state == "running"]
        if running:
            return "running"
        # Most recently updated non-idle
        recent = sorted(
            (j for j in self._tools.values() if j.state != "idle"),
            key=lambda j: j.updated_at,
            reverse=True,
        )
        if recent:
            return recent[0].state
        return "idle"

    @property
    def started_at(self) -> float | None:
        for j in self._tools.values():
            if j.state == "running" and j.started_at is not None:
                return j.started_at
        scraper = self.get(TOOL_URL_SCRAPER)
        return scraper.started_at

    def display_text(self) -> str:
        running = [j for j in self._tools.values() if j.state == "running"]
        if running:
            return " · ".join(j.display_text() for j in running)
        recent = sorted(
            (j for j in self._tools.values() if j.state != "idle"),
            key=lambda j: j.updated_at,
            reverse=True,
        )
        if recent:
            return recent[0].display_text()
        return "Ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "job_name": self.job_name,
            "message": self.display_text(),
            "product_count": self.product_count,
        }

    @staticmethod
    def _resolve_tool_id(job_name: str | None) -> str:
        name = (job_name or "").strip().lower()
        if "audit" in name:
            return TOOL_STORE_AUDITOR
        if "upload" in name or "file" in name:
            return TOOL_FILE_UPLOAD
        return TOOL_URL_SCRAPER


def notify_extraction_complete(product_count: int, *, parent=None) -> None:
    """Windows/system tray notification; falls back to in-app dialog."""
    title = "Sentivo Tools"
    message = f"Extraction complete — {product_count} products extracted"
    try:
        from plyer import notification

        notification.notify(
            title=title,
            message=message,
            app_name="Sentivo Tools",
            timeout=10,
        )
        return
    except Exception:
        pass
    try:
        import tkinter.messagebox as messagebox

        messagebox.showinfo(title, message, parent=parent)
    except Exception:
        pass
