"""Temporary download files. Each lives in its own folder and is deleted after the TTL."""

import os
import secrets
import shutil
import threading
import time
from dataclasses import dataclass

_ID_BYTES = 16


@dataclass
class Job:
    id: str
    directory: str
    path: str
    filename: str
    content_type: str
    size: int
    expires_at: float


class JobStore:
    def __init__(self, base_dir: str, ttl_seconds: int):
        self.base_dir = base_dir
        self.ttl = ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        os.makedirs(base_dir, exist_ok=True)

    def new_directory(self) -> tuple[str, str]:
        job_id = secrets.token_hex(_ID_BYTES)
        directory = os.path.join(self.base_dir, job_id)
        os.makedirs(directory, mode=0o700)
        return job_id, directory

    def register(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str, now: float | None = None) -> Job | None:
        if not isinstance(job_id, str) or len(job_id) != _ID_BYTES * 2 or not all(c in "0123456789abcdef" for c in job_id):
            return None
        with self._lock:
            job = self._jobs.get(job_id)
        if job and job.expires_at <= (time.time() if now is None else now):
            self.remove(job_id)
            return None
        return job

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
        # job_id is validated hex, so this join can't escape base_dir.
        shutil.rmtree(os.path.join(self.base_dir, job_id), ignore_errors=True)

    def sweep(self, now: float | None = None) -> int:
        """Deletes expired jobs and any orphaned folders (e.g. from a crash or failed download)."""
        now = time.time() if now is None else now
        removed = 0
        with self._lock:
            expired = [j.id for j in self._jobs.values() if j.expires_at <= now]
            known = set(self._jobs)
        for job_id in expired:
            self.remove(job_id)
            removed += 1
        for name in os.listdir(self.base_dir):
            path = os.path.join(self.base_dir, name)
            if name not in known and os.path.getmtime(path) < now - self.ttl:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        return removed

    def purge_all(self) -> None:
        with self._lock:
            self._jobs.clear()
        for name in os.listdir(self.base_dir):
            shutil.rmtree(os.path.join(self.base_dir, name), ignore_errors=True)
