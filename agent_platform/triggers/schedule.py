"""Decide which schedule and condition occurrences are due.

Dates on a calendar schedule are read from the definition. This module does
not compute a festival calendar and does not call a model.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from agent_platform.triggers.types import (
    ConditionOperator,
    ConditionSpec,
    ScheduleKind,
    TriggerDefinition,
    TriggerKind,
)

_MISSING = object()


@dataclass(frozen=True)
class DueOccurrence:
    occurrence_key: str
    payload: dict[str, Any]


def due_schedule(definition: TriggerDefinition, now: datetime) -> list[DueOccurrence]:
    if definition.kind != TriggerKind.SCHEDULE or definition.schedule_kind is None or definition.local_time is None:
        return []
    if now.tzinfo is None:
        return []
    local = now.astimezone(ZoneInfo(definition.timezone))
    if local.timetz().replace(tzinfo=None) < _parse_hhmm(definition.local_time):
        return []
    today = local.date()
    if definition.schedule_kind == ScheduleKind.DAILY:
        key = f"d{today.isoformat()}"
        return [
            DueOccurrence(
                occurrence_key=key,
                payload={"local_date": today.isoformat(), "phase": "daily", "occurrence_key": key},
            )
        ]
    due: list[DueOccurrence] = []
    for item in definition.occurrences:
        pre = item.occurs_on - timedelta(days=definition.lead_days)
        phases: list[tuple[str, str]] = []
        if today == pre:
            phases.append(("lead", definition.lead_phase))
        if today == item.occurs_on:
            phases.append(("on", definition.on_phase))
        if len(phases) > 1 and not definition.allow_same_day:
            phases = [("on", definition.on_phase)]
        for code, phase_name in phases:
            key = f"c{item.occurrence_key}_{code}_{today.isoformat()}"
            due.append(
                DueOccurrence(
                    occurrence_key=key,
                    payload={
                        "local_date": today.isoformat(),
                        "phase": phase_name,
                        "occurrence_key": key,
                        "calendar_key": item.occurrence_key,
                        "calendar_label": item.label,
                        "calendar_date": item.occurs_on.isoformat(),
                    },
                )
            )
    return due


def due_condition(definition: TriggerDefinition, facts: Mapping[str, Any]) -> DueOccurrence | None:
    spec = definition.condition
    if definition.kind != TriggerKind.CONDITION or spec is None:
        return None
    if not _matches(spec, facts):
        return None
    found = _lookup(facts, spec.field)
    material = f"{spec.field}|{spec.operator.value}|{_token(found)}"
    key = "c" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]
    return DueOccurrence(
        occurrence_key=key,
        payload={
            "condition_field": spec.field,
            "condition_operator": spec.operator.value,
            "occurrence_key": key,
        },
    )


def _matches(spec: ConditionSpec, facts: Mapping[str, Any]) -> bool:
    found = _lookup(facts, spec.field)
    operator = spec.operator
    if operator == ConditionOperator.EQ:
        return found is not _MISSING and _same(found, spec.value)
    if operator == ConditionOperator.NEQ:
        return found is not _MISSING and not _same(found, spec.value)
    if operator == ConditionOperator.EXISTS:
        return found is not _MISSING and found is not None
    if operator == ConditionOperator.NOT_EXISTS:
        return found is _MISSING or found is None
    if operator == ConditionOperator.IN:
        return found is not _MISSING and any(_same(found, item) for item in spec.value)
    return False


def _lookup(facts: Mapping[str, Any], field: str) -> Any:
    current: Any = facts
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _same(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    return left == right


def _token(value: Any) -> str:
    if value is _MISSING or value is None:
        encoded = "null"
    else:
        try:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            encoded = type(value).__name__
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))
