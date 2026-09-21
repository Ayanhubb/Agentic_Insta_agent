"""In-memory task store with fan-out for Server-Sent Events and optional DB durability."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy.orm import sessionmaker

from models.errors import AppError, ErrorCode
from models.state import AgentState, TaskStatus

logger = logging.getLogger(__name__)


class TaskStore:
    def __init__(self, session_factory: sessionmaker | None = None) -> None:
        self._tasks: dict[str, AgentState] = {}
        self._subscribers: dict[str, list[asyncio.Queue[AgentState]]] = {}
        self._lock = asyncio.Lock()
        self._session_factory = session_factory

    async def save(self, state: AgentState, *, persist: bool = False) -> AgentState:
        snapshot = state.model_copy(deep=True)
        async with self._lock:
            self._tasks[state.task_id] = snapshot
            queues = list(self._subscribers.get(state.task_id, []))
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(snapshot)
            except asyncio.QueueFull:
                continue
        if (
            persist
            and self._session_factory is not None
            and snapshot.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}
        ):
            try:
                from db.persist import persist_agent_state_with_factory

                persist_agent_state_with_factory(self._session_factory, snapshot)
            except Exception:
                logger.exception("Failed to persist task %s", state.task_id)
        return snapshot

    async def get(self, task_id: str) -> AgentState:
        async with self._lock:
            state = self._tasks.get(task_id)
        if state is not None:
            return state.model_copy(deep=True)
        if self._session_factory is not None:
            from db.persist import load_agent_state

            session = self._session_factory()
            try:
                loaded = load_agent_state(session, task_id)
            finally:
                session.close()
            if loaded is not None:
                async with self._lock:
                    self._tasks[task_id] = loaded
                return loaded.model_copy(deep=True)
        raise AppError(ErrorCode.TASK_NOT_FOUND, "Task was not found.", http_status=404)

    async def subscribe(self, task_id: str) -> AsyncIterator[AgentState]:
        queue: asyncio.Queue[AgentState] = asyncio.Queue(maxsize=32)
        async with self._lock:
            self._subscribers.setdefault(task_id, []).append(queue)
            current = self._tasks.get(task_id)
        try:
            if current is not None:
                yield current.model_copy(deep=True)
            while True:
                state = await queue.get()
                yield state
                if state.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                    break
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(task_id, [])
                if queue in subscribers:
                    subscribers.remove(queue)
                if not subscribers:
                    self._subscribers.pop(task_id, None)
