"""Dispatch one trigger occurrence through the agent runtime.

The path is trigger, agent resolver, agent runtime, workflow, execution.
This module does not import a product publisher.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from agent_platform.triggers.catalog import TriggerCatalog
from agent_platform.triggers.errors import TriggerError
from agent_platform.triggers.history import (
    FireResult,
    TriggerFire,
    TriggerHistory,
    next_retry_at,
)
from agent_platform.triggers.types import (
    FireStatus,
    TriggerDefinition,
    TriggerKind,
    require_occurrence_key,
)
from agent_runtime.errors import RuntimeFailure
from agent_runtime.models import (
    ExecutionMode,
    ExecutionRecord,
    ExecutionStatus,
    TriggerContext,
)
from agent_runtime.runtime import AgentRuntime
from agent_runtime.sanitize import sanitize_payload

_SAFE_FAILURE = "The execution failed."


class TriggerDispatcher:
    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        catalog: TriggerCatalog,
        history: TriggerHistory | None = None,
    ) -> None:
        self._runtime = runtime
        self._catalog = catalog
        self._history = history if history is not None else TriggerHistory()

    @property
    def catalog(self) -> TriggerCatalog:
        return self._catalog

    @property
    def history(self) -> TriggerHistory:
        return self._history

    async def manual(
        self,
        tenant_id: str,
        trigger_id: str,
        *,
        idempotency_key: str,
        payload: dict[str, Any] | None,
        now: datetime,
    ) -> FireResult:
        definition = self._require(tenant_id, trigger_id)
        if definition.kind != TriggerKind.MANUAL:
            raise TriggerError("kind_mismatch", "Trigger kind does not accept this call.")
        return await self.fire(
            definition,
            occurrence_key=_checked_occurrence(idempotency_key),
            payload=payload or {},
            now=now,
        )

    async def accept(
        self,
        tenant_id: str,
        trigger_id: str,
        *,
        source_id: str,
        idempotency_key: str,
        payload: dict[str, Any] | None,
        now: datetime,
    ) -> FireResult:
        """Accept an in-process event or webhook whose source was registered in advance.

        There is no HTTP listener and no external subscription in this method.
        """
        definition = self._require(tenant_id, trigger_id)
        if definition.kind not in {TriggerKind.EVENT, TriggerKind.WEBHOOK}:
            raise TriggerError("kind_mismatch", "Trigger kind does not accept this call.")
        return await self.fire(
            definition,
            occurrence_key=_checked_occurrence(idempotency_key),
            payload=payload or {},
            now=now,
            source_id=source_id,
        )

    async def fire(
        self,
        definition: TriggerDefinition,
        *,
        occurrence_key: str,
        payload: dict[str, Any],
        now: datetime,
        source_id: str | None = None,
    ) -> FireResult:
        _require_aware(now)
        try:
            occurrence_key = require_occurrence_key(occurrence_key)
        except ValueError:
            raise TriggerError("invalid_request", "Occurrence key is invalid.") from None
        if definition.kind in {TriggerKind.EVENT, TriggerKind.WEBHOOK} and source_id != definition.registered_source:
            fire, created = self._history.reject(
                definition,
                occurrence_key,
                now=now,
                error_code="SOURCE_REJECTED",
                error_message="Trigger source is not registered.",
            )
            return FireResult(fire, duplicate=not created)
        if not definition.enabled:
            fire, created = self._history.skip(definition, occurrence_key, now=now)
            return FireResult(fire, duplicate=not created)
        merged = {**definition.static_payload, **payload}
        try:
            cleaned = sanitize_payload(merged)
        except ValueError:
            raise TriggerError("invalid_request", "Trigger payload was rejected.") from None
        fire, created = self._history.claim(
            definition,
            occurrence_key,
            now=now,
            idempotency_key="",
        )
        if not created:
            return FireResult(fire, duplicate=True)
        idempotency_key = _runtime_key(definition, occurrence_key, fire.attempt)
        self._history.set_idempotency_key(fire, idempotency_key)
        try:
            trigger = TriggerContext(
                trigger_key=definition.trigger_key,
                tenant_id=definition.tenant_id,
                user_id=definition.tenant_id,
                idempotency_key=idempotency_key,
                mode=ExecutionMode.PRODUCTION,
                payload=cleaned,
                occurred_at=now,
            )
            record = await self._runtime.run(definition.agent_key, trigger)
        except (ValidationError, ValueError):
            self._finish_failure(fire, "INVALID_REQUEST", now, definition)
            return FireResult(fire, duplicate=False)
        except RuntimeFailure as failure:
            self._finish_failure(fire, failure.code.value, now, definition)
            return FireResult(fire, duplicate=False)
        except Exception:  # noqa: BLE001 — record the fire; do not store the traceback
            self._finish_failure(fire, "INTERNAL_ERROR", now, definition)
            return FireResult(fire, duplicate=False)
        self._finish_record(fire, record, now, definition)
        return FireResult(fire, duplicate=False)

    def _require(self, tenant_id: str, trigger_id: str) -> TriggerDefinition:
        definition = self._catalog.get(tenant_id, trigger_id)
        if definition is None:
            raise TriggerError("trigger_not_found", "Trigger was not found.")
        return definition

    def _finish_record(
        self,
        fire: TriggerFire,
        record: ExecutionRecord,
        now: datetime,
        definition: TriggerDefinition,
    ) -> None:
        status = _fire_status(record)
        error_code = None if record.error is None else record.error.code.value
        error_message = None if record.error is None else record.error.message[:300]
        if status == FireStatus.COMPLETED:
            error_code = None
            error_message = None
        elif status == FireStatus.FAILED and error_code is None:
            error_code = "INTERNAL_ERROR"
            error_message = _SAFE_FAILURE
        retry_at = None
        completed_at: datetime | None = now
        if status == FireStatus.WAITING:
            completed_at = None
        if status == FireStatus.FAILED:
            retry_at = next_retry_at(
                error_code=error_code,
                attempt=fire.attempt,
                policy=definition.retry,
                now=now,
            )
        self._history.finish(
            fire,
            status=status,
            execution_id=record.execution_id,
            error_code=error_code,
            error_message=error_message,
            completed_at=completed_at,
            next_retry_at=retry_at,
        )

    def _finish_failure(
        self,
        fire: TriggerFire,
        code: str,
        now: datetime,
        definition: TriggerDefinition,
    ) -> None:
        retry_at = next_retry_at(
            error_code=code,
            attempt=fire.attempt,
            policy=definition.retry,
            now=now,
        )
        self._history.finish(
            fire,
            status=FireStatus.FAILED,
            execution_id=None,
            error_code=code,
            error_message=_SAFE_FAILURE,
            completed_at=now,
            next_retry_at=retry_at,
        )


def _fire_status(record: ExecutionRecord) -> FireStatus:
    if record.status == ExecutionStatus.COMPLETED:
        return FireStatus.COMPLETED
    if record.status == ExecutionStatus.WAITING_APPROVAL:
        return FireStatus.WAITING
    return FireStatus.FAILED


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise TriggerError("naive_timestamp", "Trigger time must be timezone-aware.")


def _checked_occurrence(value: str) -> str:
    try:
        return require_occurrence_key(value)
    except ValueError:
        raise TriggerError("invalid_request", "Occurrence key is invalid.") from None


def _runtime_key(definition: TriggerDefinition, occurrence_key: str, attempt: int) -> str:
    material = f"{definition.tenant_id}|{definition.trigger_id}|{occurrence_key}|{attempt}"
    return "t" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]
