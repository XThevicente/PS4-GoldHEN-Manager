"""Thread-safe, in-memory FIFO for actual FTP transfer jobs.

The queue intentionally does not claim interrupted-transfer resume: an FTP
upload is only complete when its final callback succeeds. Jobs are not persisted
and the UI offers cancellation only for jobs that have not started.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TransferJob:
    id: int
    direction: str
    source: str
    target: str
    state: str = "pending"
    done: int = 0
    total: int | None = None
    detail: str = ""
    error: str = ""

    @property
    def percent(self) -> int | None:
        if not self.total:
            return None
        return min(100, max(0, round(self.done * 100 / self.total)))


class TransferQueue:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[int, TransferJob] = {}
        self._next_id = 1

    def _replace(self, job_id: int, **changes) -> TransferJob:
        current = self._jobs[job_id]
        updated = replace(current, **changes)
        self._jobs[job_id] = updated
        return updated

    def add(self, direction: str, source: str, target: str) -> TransferJob:
        if direction not in ("upload", "download"):
            raise ValueError("Dirección de transferencia desconocida")
        if not source or not target:
            raise ValueError("Origen y destino son obligatorios")
        with self._lock:
            job = TransferJob(self._next_id, direction, source, target)
            self._jobs[job.id] = job
            self._next_id += 1
            return job

    def start_next(self) -> TransferJob | None:
        with self._lock:
            if any(job.state == "running" for job in self._jobs.values()):
                return None
            for job in self._jobs.values():
                if job.state == "pending":
                    return self._replace(job.id, state="running")
        return None

    def progress(self, job_id: int, done: int, total: int | None, detail: str) -> None:
        with self._lock:
            if self._jobs[job_id].state != "running":
                return
            self._replace(job_id, done=max(0, done),
                          total=max(0, total) if total is not None else None,
                          detail=detail)

    def finish(self, job_id: int, error: str = "") -> TransferJob:
        with self._lock:
            if self._jobs[job_id].state != "running":
                raise ValueError("Sólo se puede finalizar una tarea activa")
            return self._replace(
                job_id, state="failed" if error else "completed", error=error,
                detail="" if not error else self._jobs[job_id].detail
            )

    def cancel_pending(self) -> int:
        with self._lock:
            pending = [job.id for job in self._jobs.values() if job.state == "pending"]
            for job_id in pending:
                self._replace(job_id, state="cancelled")
            return len(pending)

    def snapshot(self) -> tuple[TransferJob, ...]:
        with self._lock:
            return tuple(self._jobs.values())
