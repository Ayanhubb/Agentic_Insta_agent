"""In-process pipeline counters. Values are counts only — never secrets or payloads."""

from __future__ import annotations

from threading import Lock


class PipelineMetrics:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = Lock()

    def increment(self, name: str, amount: int = 1) -> None:
        key = str(name or "").strip().lower()
        if not key:
            return
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + amount

    def get(self, name: str) -> int:
        return self._counts.get(str(name or "").strip().lower(), 0)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


pipeline_metrics = PipelineMetrics()
