"""In-memory registry of background book-ingestion jobs.

Single-process, single-uvicorn deployment, so a plain dict is enough; jobs are
ephemeral and reset on restart (which is fine — the ingested book itself is
persisted in books.db, only the transient progress is lost).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IngestJob:
    id: str
    title: str
    status: str = "queued"   # queued | running | done | error
    stage: str = ""          # parsing | chunking | embedding | summarizing | done
    progress: float = 0.0    # 0.0 .. 1.0
    detail: str = ""
    book_id: int | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class JobRegistry:
    """Tracks ingestion jobs and prunes old completed ones."""

    _MAX_FINISHED = 50

    def __init__(self) -> None:
        self._jobs: dict[str, IngestJob] = {}

    def create(self, title: str) -> IngestJob:
        job = IngestJob(id=uuid.uuid4().hex, title=title)
        self._jobs[job.id] = job
        self._prune()
        return job

    def get(self, job_id: str) -> IngestJob | None:
        return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        job.updated_at = time.time()

    def _prune(self) -> None:
        finished = [j for j in self._jobs.values() if j.status in ("done", "error")]
        if len(finished) > self._MAX_FINISHED:
            finished.sort(key=lambda j: j.updated_at)
            for job in finished[: len(finished) - self._MAX_FINISHED]:
                self._jobs.pop(job.id, None)
