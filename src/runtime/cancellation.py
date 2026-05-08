"""Run-scoped cancellation registry for Web Chat tasks."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.process_sessions import ProcessKillSummary


class TaskCancelledError(asyncio.CancelledError):
    """Raised when the current run has been cancelled."""

    def __init__(self, runtime_session_id: str = "", reason: str = "user_stop") -> None:
        super().__init__(f"Task cancelled (session={runtime_session_id}, reason={reason})")
        self.runtime_session_id = runtime_session_id
        self.reason = reason


@dataclass(slots=True)
class CancellationRecord:
    """Cancellation state for one Web Chat run."""

    run_id: str
    channel: str
    chat_id: str
    runtime_session_id: str | None = None
    requested: bool = False
    requested_at: float | None = None
    reason: str = "user_stop"
    completed: bool = False
    completed_at: float | None = None


class CancellationManager:
    """Track run cancellation and map Web run ids to ADK runtime sessions."""

    _RETENTION_SEC = 60.0

    def __init__(self) -> None:
        self._records: dict[str, CancellationRecord] = {}
        self._session_to_run: dict[str, str] = {}
        self._lock = threading.RLock()

    def register_run(
        self,
        *,
        run_id: str,
        channel: str,
        chat_id: str,
        runtime_session_id: str | None = None,
    ) -> None:
        """Register one run before it enters the runtime."""
        cleaned_run_id = str(run_id or "").strip()
        if not cleaned_run_id:
            return
        cleaned_runtime_session_id = str(runtime_session_id or "").strip() or None
        with self._lock:
            record = CancellationRecord(
                run_id=cleaned_run_id,
                channel=str(channel or "").strip(),
                chat_id=str(chat_id or "").strip(),
                runtime_session_id=cleaned_runtime_session_id,
            )
            self._records[cleaned_run_id] = record
            if cleaned_runtime_session_id:
                self._session_to_run[cleaned_runtime_session_id] = cleaned_run_id
            self._gc_locked()

    def bind_runtime_session(self, run_id: str, runtime_session_id: str) -> CancellationRecord | None:
        """Bind a Web run id to the ADK runtime session id."""
        cleaned_run_id = str(run_id or "").strip()
        cleaned_runtime_session_id = str(runtime_session_id or "").strip()
        if not cleaned_run_id or not cleaned_runtime_session_id:
            return None
        with self._lock:
            record = self._records.get(cleaned_run_id)
            if record is None:
                return None
            if record.runtime_session_id and record.runtime_session_id != cleaned_runtime_session_id:
                self._session_to_run.pop(record.runtime_session_id, None)
            record.runtime_session_id = cleaned_runtime_session_id
            self._session_to_run[cleaned_runtime_session_id] = cleaned_run_id
            return record

    def request_cancel_by_run_id(
        self,
        run_id: str,
        reason: str = "user_stop",
    ) -> ProcessKillSummary | None:
        """Mark one run cancelled and kill its runtime-scoped processes if bound."""
        cleaned_run_id = str(run_id or "").strip()
        if not cleaned_run_id:
            return None
        runtime_session_id: str | None = None
        with self._lock:
            record = self._records.get(cleaned_run_id)
            if record is None or record.completed:
                return None
            self._mark_cancelled_locked(record, reason=reason)
            runtime_session_id = record.runtime_session_id
        if not runtime_session_id:
            return None
        return self.request_cancel_by_session(runtime_session_id, reason=reason)

    def request_cancel_by_session(
        self,
        runtime_session_id: str,
        reason: str = "user_stop",
    ) -> ProcessKillSummary:
        """Mark one runtime session cancelled and kill its managed processes."""
        cleaned_runtime_session_id = str(runtime_session_id or "").strip()
        with self._lock:
            run_id = self._session_to_run.get(cleaned_runtime_session_id)
            record = self._records.get(run_id) if run_id else None
            if record is not None:
                self._mark_cancelled_locked(record, reason=reason)

        from src.runtime.process_sessions import get_process_session_manager

        return get_process_session_manager().kill_sessions(scope_key=cleaned_runtime_session_id)

    def is_cancel_requested(self, runtime_session_id: str) -> bool:
        """Return whether cancellation has been requested for a runtime session."""
        cleaned_runtime_session_id = str(runtime_session_id or "").strip()
        if not cleaned_runtime_session_id:
            return False
        with self._lock:
            run_id = self._session_to_run.get(cleaned_runtime_session_id)
            record = self._records.get(run_id) if run_id else None
            return bool(record and record.requested and not record.completed)

    def raise_if_cancelled(self, runtime_session_id: str) -> None:
        """Raise TaskCancelledError if the runtime session is cancelled."""
        cleaned_runtime_session_id = str(runtime_session_id or "").strip()
        if not cleaned_runtime_session_id:
            return
        with self._lock:
            run_id = self._session_to_run.get(cleaned_runtime_session_id)
            record = self._records.get(run_id) if run_id else None
            if not record or not record.requested or record.completed:
                return
            reason = record.reason
        raise TaskCancelledError(cleaned_runtime_session_id, reason=reason)

    def complete_run(self, run_id: str) -> None:
        """Mark one run complete and clear its runtime session mapping."""
        cleaned_run_id = str(run_id or "").strip()
        if not cleaned_run_id:
            return
        with self._lock:
            record = self._records.get(cleaned_run_id)
            if record is None:
                return
            record.completed = True
            record.completed_at = time.time()
            if record.runtime_session_id:
                self._session_to_run.pop(record.runtime_session_id, None)
            self._gc_locked()

    def get_record(self, run_id: str) -> CancellationRecord | None:
        """Return a record snapshot for tests and diagnostics."""
        cleaned_run_id = str(run_id or "").strip()
        if not cleaned_run_id:
            return None
        with self._lock:
            record = self._records.get(cleaned_run_id)
            if record is None:
                return None
            return CancellationRecord(
                run_id=record.run_id,
                channel=record.channel,
                chat_id=record.chat_id,
                runtime_session_id=record.runtime_session_id,
                requested=record.requested,
                requested_at=record.requested_at,
                reason=record.reason,
                completed=record.completed,
                completed_at=record.completed_at,
            )

    @staticmethod
    def _mark_cancelled_locked(record: CancellationRecord, *, reason: str) -> None:
        """Mark one record as cancelled while the manager lock is held."""
        if record.requested:
            return
        record.requested = True
        record.reason = str(reason or "user_stop").strip() or "user_stop"
        record.requested_at = time.time()

    def _gc_locked(self) -> None:
        """Remove completed records after a short retention period."""
        cutoff = time.time() - self._RETENTION_SEC
        stale = [
            run_id
            for run_id, record in self._records.items()
            if record.completed and (record.completed_at or 0) < cutoff
        ]
        for run_id in stale:
            record = self._records.pop(run_id, None)
            if record and record.runtime_session_id:
                self._session_to_run.pop(record.runtime_session_id, None)


_CANCELLATION_MANAGER: CancellationManager | None = None
_CANCELLATION_MANAGER_LOCK = threading.Lock()


def get_cancellation_manager() -> CancellationManager:
    """Return the process-wide cancellation manager singleton."""
    global _CANCELLATION_MANAGER
    if _CANCELLATION_MANAGER is None:
        with _CANCELLATION_MANAGER_LOCK:
            if _CANCELLATION_MANAGER is None:
                _CANCELLATION_MANAGER = CancellationManager()
    return _CANCELLATION_MANAGER
