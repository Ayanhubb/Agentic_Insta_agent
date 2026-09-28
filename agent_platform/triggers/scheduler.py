"""Process tick for schedule and condition triggers.

Event and webhook triggers are not polled. A tick never publishes.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from agent_platform.triggers.dispatch import TriggerDispatcher
from agent_platform.triggers.errors import TriggerError
from agent_platform.triggers.history import FireResult
from agent_platform.triggers.schedule import due_condition, due_schedule
from agent_platform.triggers.types import TriggerKind

logger = logging.getLogger(__name__)


class TriggerScheduler:
    def __init__(self, dispatcher: TriggerDispatcher) -> None:
        self._dispatcher = dispatcher

    async def tick(
        self,
        now: datetime,
        *,
        tenant_id: str | None = None,
        facts: Mapping[str, Any] | None = None,
    ) -> list[FireResult]:
        if now.tzinfo is None:
            raise TriggerError("naive_timestamp", "Trigger time must be timezone-aware.")
        if facts is not None and not isinstance(facts, Mapping):
            raise TriggerError("invalid_request", "Condition facts must be data.")
        results: list[FireResult] = []
        for definition in self._dispatcher.catalog.list(tenant_id):
            if not definition.enabled:
                continue
            if definition.kind == TriggerKind.SCHEDULE:
                occurrences = due_schedule(definition, now)
            elif definition.kind == TriggerKind.CONDITION and facts is not None:
                match = due_condition(definition, facts)
                occurrences = [] if match is None else [match]
            else:
                continue
            for occurrence in occurrences:
                try:
                    results.append(
                        await self._dispatcher.fire(
                            definition,
                            occurrence_key=occurrence.occurrence_key,
                            payload=occurrence.payload,
                            now=now,
                        )
                    )
                except TriggerError as exc:
                    logger.warning("Trigger was not fired: %s", exc.code)
        return results
