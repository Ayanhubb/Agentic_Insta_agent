"""Trigger fire history and occurrence claims.

A claim is scoped by tenant, trigger, and occurrence. A second claim for a
finished occurrence returns the original fire.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from agent_platform.triggers.types import FireStatus, RetryPolicy, TriggerDefinition


@dataclass
class TriggerFire:
    fire_id: str
    tenant_id: str
    trigger_id: str
    agent_key: str
    kind: str
    occurrence_key: str
    attempt: int
    status: FireStatus
    idempotency_key: str
    execution_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    occurred_at: datetime | None = None
    completed_at: datetime | None = None
    next_retry_at: datetime | None = None


@dataclass(frozen=True)
class FireResult:
    fire: TriggerFire
    duplicate: bool


class TriggerHistory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._by_id: dict[str, TriggerFire] = {}
        self._order: list[str] = []
        self._by_occurrence: dict[tuple[str, str, str], list[str]] = {}

    def claim(
        self,
        definition: TriggerDefinition,
        occurrence_key: str,
        *,
        now: datetime,
        idempotency_key: str,
    ) -> tuple[TriggerFire, bool]:
        with self._lock:
            latest = self._latest_blocking(definition.tenant_id, definition.trigger_id, occurrence_key)
            if latest is not None:
                if latest.status == FireStatus.FAILED and _retry_due(latest, definition.retry, now):
                    return self._insert(
                        definition,
                        occurrence_key,
                        attempt=latest.attempt + 1,
                        status=FireStatus.CLAIMED,
                        idempotency_key=idempotency_key,
                        occurred_at=now,
                    ), True
                return latest, False
            return self._insert(
                definition,
                occurrence_key,
                attempt=1,
                status=FireStatus.CLAIMED,
                idempotency_key=idempotency_key,
                occurred_at=now,
            ), True

    def skip(
        self,
        definition: TriggerDefinition,
        occurrence_key: str,
        *,
        now: datetime,
    ) -> tuple[TriggerFire, bool]:
        return self._once(
            definition,
            occurrence_key,
            now=now,
            status=FireStatus.SKIPPED,
            error_code="TRIGGER_DISABLED",
            error_message="Trigger is disabled.",
        )

    def reject(
        self,
        definition: TriggerDefinition,
        occurrence_key: str,
        *,
        now: datetime,
        error_code: str,
        error_message: str,
    ) -> tuple[TriggerFire, bool]:
        return self._once(
            definition,
            occurrence_key,
            now=now,
            status=FireStatus.REJECTED,
            error_code=error_code,
            error_message=error_message,
        )

    def set_idempotency_key(self, fire: TriggerFire, key: str) -> None:
        with self._lock:
            current = self._by_id.get(fire.fire_id)
            if current is fire and current.status == FireStatus.CLAIMED:
                current.idempotency_key = key

    def finish(
        self,
        fire: TriggerFire,
        *,
        status: FireStatus,
        execution_id: str | None,
        error_code: str | None,
        error_message: str | None,
        completed_at: datetime | None,
        next_retry_at: datetime | None,
    ) -> None:
        with self._lock:
            current = self._by_id.get(fire.fire_id)
            if current is not fire or current.status != FireStatus.CLAIMED:
                return
            current.status = status
            current.execution_id = execution_id
            current.error_code = error_code
            current.error_message = error_message
            current.completed_at = completed_at
            current.next_retry_at = next_retry_at

    def get(self, tenant_id: str, fire_id: str) -> TriggerFire | None:
        with self._lock:
            fire = self._by_id.get(fire_id)
            if fire is None or fire.tenant_id != tenant_id:
                return None
            return fire

    def list(self, tenant_id: str, trigger_id: str | None = None) -> list[TriggerFire]:
        with self._lock:
            rows: list[TriggerFire] = []
            for fire_id in self._order:
                fire = self._by_id[fire_id]
                if fire.tenant_id != tenant_id:
                    continue
                if trigger_id is not None and fire.trigger_id != trigger_id:
                    continue
                rows.append(fire)
            return rows

    def _once(
        self,
        definition: TriggerDefinition,
        occurrence_key: str,
        *,
        now: datetime,
        status: FireStatus,
        error_code: str,
        error_message: str,
    ) -> tuple[TriggerFire, bool]:
        with self._lock:
            for fire_id in self._by_occurrence.get((definition.tenant_id, definition.trigger_id, occurrence_key), []):
                fire = self._by_id[fire_id]
                if fire.status == status and fire.error_code == error_code:
                    return fire, False
            created = self._insert(
                definition,
                occurrence_key,
                attempt=0,
                status=status,
                idempotency_key="",
                occurred_at=now,
            )
            created.error_code = error_code
            created.error_message = error_message
            created.completed_at = now
            return created, True

    def _latest_blocking(self, tenant_id: str, trigger_id: str, occurrence_key: str) -> TriggerFire | None:
        blocking = {
            FireStatus.CLAIMED,
            FireStatus.COMPLETED,
            FireStatus.WAITING,
            FireStatus.FAILED,
        }
        latest: TriggerFire | None = None
        for fire_id in self._by_occurrence.get((tenant_id, trigger_id, occurrence_key), []):
            fire = self._by_id[fire_id]
            if fire.status not in blocking:
                continue
            if latest is None or fire.attempt >= latest.attempt:
                latest = fire
        return latest

    def _insert(
        self,
        definition: TriggerDefinition,
        occurrence_key: str,
        *,
        attempt: int,
        status: FireStatus,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> TriggerFire:
        self._seq += 1
        fire = TriggerFire(
            fire_id=f"fire_{self._seq}",
            tenant_id=definition.tenant_id,
            trigger_id=definition.trigger_id,
            agent_key=definition.agent_key,
            kind=definition.kind.value,
            occurrence_key=occurrence_key,
            attempt=attempt,
            status=status,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )
        self._by_id[fire.fire_id] = fire
        self._order.append(fire.fire_id)
        slot = (definition.tenant_id, definition.trigger_id, occurrence_key)
        self._by_occurrence.setdefault(slot, []).append(fire.fire_id)
        return fire


def next_retry_at(
    *,
    error_code: str | None,
    attempt: int,
    policy: RetryPolicy,
    now: datetime,
) -> datetime | None:
    if error_code not in policy.retryable_codes:
        return None
    if attempt >= policy.max_attempts:
        return None
    if now.tzinfo is None:
        return None
    return now + timedelta(seconds=policy.delay_seconds * attempt)


def _retry_due(fire: TriggerFire, policy: RetryPolicy, now: datetime) -> bool:
    if fire.error_code not in policy.retryable_codes:
        return False
    if fire.attempt >= policy.max_attempts:
        return False
    if fire.next_retry_at is None or now.tzinfo is None or fire.next_retry_at.tzinfo is None:
        return False
    return now >= fire.next_retry_at
