from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from enum import StrEnum

from app.data.market_cache import CacheWrite, MarketCacheStore


class CacheWriteMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"
    READ_ONLY = "read_only"
    DISABLED = "disabled"


@dataclass(frozen=True)
class CacheWriteResult:
    status: str
    job_id: str | None = None
    version_id: str | None = None


@dataclass(frozen=True)
class CacheFlushResult:
    succeeded: int
    failed: int


class CacheWriter:
    def __init__(
        self,
        store: MarketCacheStore,
        mode: CacheWriteMode = CacheWriteMode.ASYNC,
        allow_provider_correction: bool = True,
        max_attempts: int = 3,
        auto_flush: bool = False,
    ) -> None:
        self.store = store
        self.mode = CacheWriteMode(mode)
        self.allow_provider_correction = allow_provider_correction
        self.max_attempts = max(1, max_attempts)
        self.auto_flush = auto_flush
        self._flush_lock = threading.Lock()
        self._flush_thread: threading.Thread | None = None

    def write(self, write: CacheWrite) -> CacheWriteResult:
        if self.mode == CacheWriteMode.DISABLED:
            return CacheWriteResult(status="skipped_disabled")
        if self.mode == CacheWriteMode.READ_ONLY:
            return CacheWriteResult(status="skipped_read_only")
        if self.mode == CacheWriteMode.SYNC:
            result = self.store.upsert(write, allow_provider_correction=self.allow_provider_correction)
            if result.status == "conflict_rejected":
                return CacheWriteResult(status="conflict_rejected", version_id=result.version_id)
            return CacheWriteResult(status="written", version_id=result.version_id)
        if self.auto_flush:
            with self._flush_lock:
                job_id = self.store.enqueue_write_job(write)
                self._schedule_flush_locked()
        else:
            job_id = self.store.enqueue_write_job(write)
        return CacheWriteResult(status="enqueued", job_id=job_id)

    def flush(self) -> CacheFlushResult:
        with self._flush_lock:
            return self._flush_pending_jobs_unlocked()

    def _schedule_flush_locked(self) -> None:
        if self._flush_thread is not None and self._flush_thread.is_alive():
            return
        self._flush_thread = threading.Thread(target=self._flush_until_idle, daemon=True)
        self._flush_thread.start()

    def _flush_until_idle(self) -> None:
        while True:
            with self._flush_lock:
                self._flush_pending_jobs_unlocked()
                if self.store.count_pending_jobs() == 0:
                    self._flush_thread = None
                    return

    def _flush_pending_jobs_unlocked(self) -> CacheFlushResult:
        succeeded = 0
        failed = 0
        for job in self.store.pending_jobs():
            job_id = str(job["job_id"])
            last_error: Exception | None = None
            for _attempt in range(self.max_attempts):
                try:
                    write = self.store.cache_write_from_job(job)
                    result = self.store.upsert(write, allow_provider_correction=self.allow_provider_correction)
                except Exception as error:
                    last_error = error
                    continue
                if result.status == "conflict_rejected":
                    last_error = RuntimeError("cache write conflict rejected")
                    break
                succeeded += 1
                last_error = None
                self.store.mark_job_succeeded(job_id)
                break
            else:
                pass
            if last_error is not None:
                failed += 1
                self.store.mark_job_failed(job_id, _sanitize_error_message(str(last_error)))
        return CacheFlushResult(succeeded=succeeded, failed=failed)


def _sanitize_error_message(message: str, max_length: int = 500) -> str:
    cleaned = message.replace("\n", " ").replace("\r", " ")
    cleaned = re.sub(
        r"(?i)([\"']?\b(?:token|api[_-]?key|secret|password)\b[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)",
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)([\"']?\b(?:token|api[_-]?key|secret|password)\b[\"']?\s*[:=]\s*)(?![\"'])[^\s,;}]+",
        lambda match: f"{match.group(1)}[REDACTED]",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)(\b[A-Z0-9_]*(?:TOKEN|API_KEY|SECRET|PASSWORD)\b\s*=\s*)[^\s,;}]+",
        lambda match: f"{match.group(1)}[REDACTED]",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)(Authorization\s*:\s*Bearer\s+)[^\s,;}]+",
        lambda match: f"{match.group(1)}[REDACTED]",
        cleaned,
    )
    return cleaned[:max_length]
