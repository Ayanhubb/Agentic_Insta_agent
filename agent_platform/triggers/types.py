"""Trigger definitions. A trigger names an agent and a binding key. It does not name code."""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_runtime.sanitize import sanitize_payload

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHORT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_DOTTED = re.compile(r"^[a-z][a-z0-9_.]{0,80}$")
_PUBLIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*$")
_PHASE = re.compile(r"^[a-z][a-z0-9-]{0,32}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_OCCURRENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")


class TriggerKind(str, Enum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    EVENT = "event"
    WEBHOOK = "webhook"
    CONDITION = "condition"


class ScheduleKind(str, Enum):
    DAILY = "daily"
    CALENDAR = "calendar"


class ConditionOperator(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    IN = "in"


class FireStatus(str, Enum):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class RetryPolicy(BaseModel):
    """How many times a failed occurrence may be started again.

    A code is retried only when it is listed here. A runtime flag on the error
    does not by itself start another execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=5)
    retryable_codes: frozenset[str] = Field(default_factory=frozenset)
    delay_seconds: float = Field(default=0, ge=0, le=3600)

    @field_validator("retryable_codes")
    @classmethod
    def _codes(cls, value: frozenset[str]) -> frozenset[str]:
        for item in value:
            if not _ERROR_CODE.fullmatch(item):
                raise ValueError("retry code is invalid")
        return frozenset(value)


class CalendarDate(BaseModel):
    """One civil date supplied by the caller. The scheduler does not invent dates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    occurrence_key: str
    occurs_on: date
    label: str = Field(min_length=1, max_length=120)

    @field_validator("occurrence_key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not _SHORT_KEY.fullmatch(value):
            raise ValueError("calendar occurrence key is invalid")
        return value


class ConditionSpec(BaseModel):
    """A closed comparison against a fact mapping. There is no expression string."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    operator: ConditionOperator
    value: Any = None

    @field_validator("field")
    @classmethod
    def _field(cls, value: str) -> str:
        if "__" in value or len(value) > 80 or not _FIELD.fullmatch(value):
            raise ValueError("condition field is invalid")
        return value

    @model_validator(mode="after")
    def _value_shape(self) -> ConditionSpec:
        if self.operator in {ConditionOperator.EXISTS, ConditionOperator.NOT_EXISTS}:
            if self.value is not None:
                raise ValueError("existence checks do not take a value")
            return self
        if self.operator == ConditionOperator.IN:
            if not isinstance(self.value, (list, tuple)) or not self.value or len(self.value) > 20:
                raise ValueError("condition value is invalid")
            copied = tuple(_scalar(item) for item in self.value)
            object.__setattr__(self, "value", copied)
            return self
        object.__setattr__(self, "value", _scalar(self.value))
        return self


class TriggerDefinition(BaseModel):
    """Tenant-scoped binding from a trigger kind to a published agent's trigger key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_id: str
    tenant_id: str
    agent_key: str
    trigger_key: str
    kind: TriggerKind
    enabled: bool = True
    timezone: str
    schedule_kind: ScheduleKind | None = None
    local_time: str | None = None
    lead_days: int = Field(default=0, ge=0, le=366)
    allow_same_day: bool = False
    occurrences: tuple[CalendarDate, ...] = ()
    lead_phase: str = "lead"
    on_phase: str = "on"
    condition: ConditionSpec | None = None
    registered_source: str | None = None
    static_payload: dict[str, Any] = Field(default_factory=dict)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    @field_validator("trigger_id", "agent_key")
    @classmethod
    def _keys(cls, value: str) -> str:
        if not _KEY.fullmatch(value):
            raise ValueError("key is invalid")
        return value

    @field_validator("trigger_key")
    @classmethod
    def _trigger_key(cls, value: str) -> str:
        if not _DOTTED.fullmatch(value):
            raise ValueError("trigger key is invalid")
        return value

    @field_validator("tenant_id")
    @classmethod
    def _tenant(cls, value: str) -> str:
        if not _PUBLIC.fullmatch(value):
            raise ValueError("tenant id is invalid")
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        name = value.strip()
        if not name or len(name) > 64:
            raise ValueError("timezone is not a known timezone")
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, KeyError, ValueError):
            raise ValueError("timezone is not a known timezone") from None
        return name

    @field_validator("local_time")
    @classmethod
    def _local_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _HHMM.fullmatch(value):
            raise ValueError("local time must be HH:MM")
        return value

    @field_validator("lead_phase", "on_phase")
    @classmethod
    def _phase(cls, value: str) -> str:
        if "__" in value or not _PHASE.fullmatch(value):
            raise ValueError("phase name is invalid")
        return value

    @field_validator("registered_source")
    @classmethod
    def _source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _KEY.fullmatch(value):
            raise ValueError("registered source is invalid")
        return value

    @field_validator("static_payload")
    @classmethod
    def _static_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 20:
            raise ValueError("static payload is too large")
        try:
            cleaned = sanitize_payload(value)
        except ValueError:
            raise ValueError("static payload was rejected") from None
        for key, item in cleaned.items():
            if not _KEY.fullmatch(str(key)):
                raise ValueError("static payload key is invalid")
            _static_value(item)
        return cleaned

    @model_validator(mode="after")
    def _shape(self) -> TriggerDefinition:
        keys = [item.occurrence_key for item in self.occurrences]
        if len(keys) != len(set(keys)):
            raise ValueError("calendar occurrence keys must be unique")
        if self.kind == TriggerKind.SCHEDULE:
            self._require_schedule()
        else:
            self._forbid_schedule()
        if self.kind == TriggerKind.CONDITION:
            if self.condition is None:
                raise ValueError("condition triggers require a condition")
        elif self.condition is not None:
            raise ValueError("only condition triggers accept a condition")
        if self.kind in {TriggerKind.EVENT, TriggerKind.WEBHOOK}:
            if self.registered_source is None:
                raise ValueError("event and webhook triggers require a registered source")
        elif self.registered_source is not None:
            raise ValueError("only event and webhook triggers accept a source")
        custom_phase = self.lead_phase != "lead" or self.on_phase != "on"
        if custom_phase and not (self.kind == TriggerKind.SCHEDULE and self.schedule_kind == ScheduleKind.CALENDAR):
            raise ValueError("phase names belong to a calendar schedule")
        return self

    def _require_schedule(self) -> None:
        if self.schedule_kind is None or self.local_time is None:
            raise ValueError("schedule triggers require a schedule kind and local time")
        if self.schedule_kind == ScheduleKind.DAILY and (self.occurrences or self.lead_days or self.allow_same_day):
            raise ValueError("a daily schedule does not take calendar dates")

    def _forbid_schedule(self) -> None:
        if self.schedule_kind is not None or self.local_time is not None:
            raise ValueError("only schedule triggers accept a schedule")
        if self.occurrences or self.lead_days or self.allow_same_day:
            raise ValueError("only calendar schedules accept dates")


def require_occurrence_key(value: str) -> str:
    if not isinstance(value, str) or not _OCCURRENCE.fullmatch(value):
        raise ValueError("occurrence key is invalid")
    return value


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 1_000_000_000_000:
            raise ValueError("condition value is invalid")
        return value
    if isinstance(value, str):
        if len(value) > 200:
            raise ValueError("condition value is invalid")
        return value
    raise ValueError("condition value is invalid")


def _static_value(value: Any) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, str) and len(value) <= 200:
        return
    raise ValueError("static payload value is invalid")
