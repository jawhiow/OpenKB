"""In-process background job tracking for the local client."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Job:
    id: str
    type: str
    status: str
    created_at: str
    updated_at: str
    message: str = ""
    progress_current: int = 0
    progress_total: int = 0
    logs: list[dict[str, str]] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    _sequence: int = 0
    _thread: threading.Thread | None = field(default=None, repr=False, compare=False)

    def set_message(self, message: str) -> None:
        self.message = message
        self.updated_at = _utcnow_iso()
        self.add_log(message)

    def set_progress(self, current: int, total: int) -> None:
        self.progress_current = max(int(current), 0)
        self.progress_total = max(int(total), 0)
        self.updated_at = _utcnow_iso()

    def add_log(self, message: str, level: str = "info") -> None:
        self.logs.append(
            {
                "time": _utcnow_iso(),
                "level": level,
                "message": message,
            }
        )
        self.updated_at = _utcnow_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message": self.message,
            "progress": {
                "current": self.progress_current,
                "total": self.progress_total,
            },
            "logs": list(self.logs),
            "result": self.result,
            "error": self.error,
        }


JobFunction = Callable[[Job], Any]


class JobRegistry:
    """Thread-backed job registry for short-lived local client tasks."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._sequence = 0

    def submit(self, job_type: str, fn: JobFunction, *, message: str = "") -> Job:
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        now = _utcnow_iso()
        job = Job(
            id=f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            type=job_type,
            status="running",
            created_at=now,
            updated_at=now,
            message=message,
            _sequence=sequence,
        )
        if message:
            job.add_log(message)

        def run() -> None:
            try:
                result = fn(job)
            except Exception as exc:  # pragma: no cover - exact exception type comes from caller
                with self._lock:
                    job.status = "failed"
                    job.error = str(exc)
                    job.add_log(str(exc), level="error")
                    job.updated_at = _utcnow_iso()
            else:
                with self._lock:
                    job.status = "succeeded"
                    job.result = result
                    job.add_log("Job succeeded")
                    job.updated_at = _utcnow_iso()

        thread = threading.Thread(target=run, name=f"openkb-client-{job.type}-{job.id}", daemon=True)
        job._thread = thread
        with self._lock:
            self._jobs[job.id] = job
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda item: item._sequence, reverse=True)

    def wait(self, job_id: str, timeout: float | None = None) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        if job._thread is not None:
            job._thread.join(timeout)
        return self.get(job_id)


default_registry = JobRegistry()
