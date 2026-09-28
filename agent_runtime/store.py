"""Execution records and idempotency claims. Replay returns the original run."""

from __future__ import annotations

import threading
from typing import Protocol

from agent_runtime.models import ExecutionRecord


class ExecutionStore(Protocol):
    def begin(self, record: ExecutionRecord) -> ExecutionRecord: ...

    def save(self, record: ExecutionRecord) -> None: ...

    def get(self, execution_id: str) -> ExecutionRecord | None: ...


class InMemoryExecutionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, ExecutionRecord] = {}
        self._by_key: dict[tuple[str, str], str] = {}

    def begin(self, record: ExecutionRecord) -> ExecutionRecord:
        with self._lock:
            if record.idempotency_key:
                slot = (record.tenant_id, record.idempotency_key)
                existing_id = self._by_key.get(slot)
                if existing_id is not None:
                    return self._by_id[existing_id]
                self._by_key[slot] = record.execution_id
            current = self._by_id.get(record.execution_id)
            if current is not None:
                return current
            self._by_id[record.execution_id] = record
            return record

    def save(self, record: ExecutionRecord) -> None:
        with self._lock:
            current = self._by_id.get(record.execution_id)
            if current is not None and current is not record:
                return
            self._by_id[record.execution_id] = record

    def get(self, execution_id: str) -> ExecutionRecord | None:
        with self._lock:
            return self._by_id.get(execution_id)
