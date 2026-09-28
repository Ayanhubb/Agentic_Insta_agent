"""Definitions, versions, and execution records for the generic runtime."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure, StructuredError
from agent_runtime.sanitize import sanitize_payload

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DOTTED_KEY = re.compile(r"^[a-z][a-z0-9_.]{0,80}$")
_HANDLER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")

_SERVICE_KINDS = frozenset(
    {"load_context", "reason", "invoke_tool", "resolve_asset", "decide_approval"}
)
_HANDLER_KINDS = frozenset({"validate", "store", "human_decision", "effect"})


class ExecutionMode(str, Enum):
    PRODUCTION = "production"
    TEST = "test"
    SHADOW = "shadow"


def requires_published_version(mode: ExecutionMode) -> bool:
    """Production and shadow both run the published version."""

    return mode in {ExecutionMode.PRODUCTION, ExecutionMode.SHADOW}


class AgentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class VersionStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"
    WAITING = "waiting"


class SideEffect(str, Enum):
    NONE = "none"
    LOCAL_WRITE = "local_write"
    PROVIDER_CALL = "provider_call"
    EXTERNAL_EFFECT = "external_effect"


class StepKind(str, Enum):
    LOAD_CONTEXT = "load_context"
    REASON = "reason"
    VALIDATE = "validate"
    INVOKE_TOOL = "invoke_tool"
    RESOLVE_ASSET = "resolve_asset"
    DECIDE_APPROVAL = "decide_approval"
    EFFECT = "effect"
    STORE = "store"
    HUMAN_DECISION = "human_decision"


class OwnerKind(str, Enum):
    SYSTEM = "system"
    USER = "user"


def _require_key(value: str, pattern: re.Pattern[str], field_name: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"{field_name} is not a valid key")
    return value


def _optional_id(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not _PUBLIC_ID.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")
    return value


class ApprovalDecision(BaseModel):
    """Result of a registered policy. The runtime does not publish on its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reason: str = Field(min_length=1, max_length=300)
    requires_human: bool = False


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout_seconds: float = 120
    step_timeout_seconds: float | None = None

    @field_validator("timeout_seconds", "step_timeout_seconds")
    @classmethod
    def _positive(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0 or value > 3600:
            raise ValueError("timeout must be greater than 0 and at most 3600 seconds")
        return float(value)


class WorkflowPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_key: str
    version_number: int = Field(ge=1)

    @field_validator("workflow_key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _require_key(value, _DOTTED_KEY, "workflow_key")


class TriggerBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_key: str
    workflow_key: str

    @field_validator("trigger_key", "workflow_key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _require_key(value, _DOTTED_KEY, "trigger_key")


class WorkflowStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_key: str
    position: int = Field(ge=1)
    kind: StepKind
    side_effect: SideEffect = SideEffect.NONE
    handler_id: str | None = None
    tool_name: str | None = None
    model_role: str | None = None
    knowledge_source: str | None = None
    asset_role: str | None = None
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()

    @field_validator("step_key")
    @classmethod
    def _step_key(cls, value: str) -> str:
        return _require_key(value, _HANDLER_ID, "step_key")

    @field_validator("handler_id")
    @classmethod
    def _handler_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_key(value, _HANDLER_ID, "handler_id")

    @field_validator("tool_name", "model_role", "knowledge_source", "asset_role")
    @classmethod
    def _names(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_key(value, _KEY, "name")

    @field_validator("reads", "writes")
    @classmethod
    def _tables(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _require_key(item, _KEY, "table")
        return value

    @model_validator(mode="after")
    def _kind_shape(self) -> WorkflowStep:
        kind = self.kind.value
        if kind in _SERVICE_KINDS and self.handler_id is not None:
            raise ValueError("service steps do not accept a handler")
        if kind in _HANDLER_KINDS and self.handler_id is None:
            raise ValueError("this step requires a registered handler id")
        if kind == StepKind.INVOKE_TOOL.value and not self.tool_name:
            raise ValueError("invoke_tool requires tool_name")
        if kind == StepKind.REASON.value and not self.model_role:
            raise ValueError("reason requires model_role")
        if kind == StepKind.LOAD_CONTEXT.value and not self.knowledge_source:
            raise ValueError("load_context requires knowledge_source")
        if kind == StepKind.RESOLVE_ASSET.value and not self.asset_role:
            raise ValueError("resolve_asset requires asset_role")
        if kind == StepKind.EFFECT.value and self.side_effect != SideEffect.EXTERNAL_EFFECT:
            raise ValueError("effect steps must declare an external side effect")
        return self


class WorkflowVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_key: str
    version_number: int = Field(ge=1)
    status: VersionStatus
    steps: tuple[WorkflowStep, ...]
    output_schema: str | None = None
    approval_policy_id: str | None = None
    side_effects: tuple[SideEffect, ...] = ()

    @field_validator("workflow_key")
    @classmethod
    def _workflow_key(cls, value: str) -> str:
        return _require_key(value, _DOTTED_KEY, "workflow_key")

    @field_validator("approval_policy_id")
    @classmethod
    def _policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_key(value, _DOTTED_KEY, "approval_policy_id")

    @field_validator("output_schema")
    @classmethod
    def _schema(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_key(value, _DOTTED_KEY, "output_schema")

    @model_validator(mode="after")
    def _steps_consistent(self) -> WorkflowVersion:
        positions = [step.position for step in self.steps]
        keys = [step.step_key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("step keys must be unique")
        if positions != list(range(1, len(positions) + 1)):
            raise ValueError("step positions must be 1..n in order")
        declared = set(self.side_effects)
        for step in self.steps:
            if step.side_effect != SideEffect.NONE and step.side_effect not in declared:
                raise ValueError("workflow side_effects must include every step side effect")
        return self


class AgentVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version_number: int = Field(ge=1)
    status: VersionStatus
    workflow_pins: tuple[WorkflowPin, ...] = ()
    trigger_bindings: tuple[TriggerBinding, ...] = ()
    tool_grants: frozenset[str] = Field(default_factory=frozenset)
    model_roles: frozenset[str] = Field(default_factory=frozenset)
    knowledge_sources: frozenset[str] = Field(default_factory=frozenset)
    asset_roles: frozenset[str] = Field(default_factory=frozenset)
    approval_policy_id: str | None = None
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)

    @field_validator("approval_policy_id")
    @classmethod
    def _policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_key(value, _DOTTED_KEY, "approval_policy_id")

    @field_validator("tool_grants", "model_roles", "knowledge_sources", "asset_roles")
    @classmethod
    def _grants(cls, value: frozenset[str]) -> frozenset[str]:
        for item in value:
            _require_key(item, _KEY, "grant")
        return frozenset(value)

    @model_validator(mode="after")
    def _unique_bindings(self) -> AgentVersion:
        triggers = [item.trigger_key for item in self.trigger_bindings]
        workflows = [item.workflow_key for item in self.workflow_pins]
        if len(triggers) != len(set(triggers)):
            raise ValueError("trigger bindings must be unique")
        if len(workflows) != len(set(workflows)):
            raise ValueError("workflow pins must be unique")
        pinned = {item.workflow_key: item.version_number for item in self.workflow_pins}
        for binding in self.trigger_bindings:
            if binding.workflow_key not in pinned:
                raise ValueError("every trigger must pin a workflow")
        return self


class AgentDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    owner: OwnerKind
    status: AgentStatus
    display_name: str = Field(min_length=1, max_length=120)
    user_id: str | None = None
    published_version_number: int | None = Field(default=None, ge=1)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _require_key(value, _KEY, "key")

    @field_validator("user_id")
    @classmethod
    def _user(cls, value: str | None) -> str | None:
        return _optional_id(value, "user_id")

    @model_validator(mode="after")
    def _owner_shape(self) -> AgentDefinition:
        if self.owner == OwnerKind.SYSTEM and self.user_id is not None:
            raise ValueError("a system agent has no user id")
        if self.owner == OwnerKind.USER and self.user_id is None:
            raise ValueError("a user agent requires user_id")
        return self


class TriggerContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_key: str
    tenant_id: str
    user_id: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    mode: ExecutionMode = ExecutionMode.PRODUCTION
    requested_version_number: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None

    @field_validator("trigger_key")
    @classmethod
    def _trigger_key(cls, value: str) -> str:
        return _require_key(value, _DOTTED_KEY, "trigger_key")

    @field_validator("tenant_id")
    @classmethod
    def _tenant(cls, value: str) -> str:
        checked = _optional_id(value, "tenant_id")
        if checked is None:
            raise ValueError("tenant_id is required")
        return checked

    @field_validator("user_id", "correlation_id", "idempotency_key")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return _optional_id(value, "id")

    @field_validator("payload")
    @classmethod
    def _payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return sanitize_payload(value)

    @field_validator("occurred_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class ExecutionSnapshot(BaseModel):
    """Immutable copy of the versions a run started with."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str
    correlation_id: str
    tenant_id: str
    user_id: str | None
    agent_key: str
    agent_version_number: int
    agent_version_status: VersionStatus
    workflow_key: str
    workflow_version_number: int
    steps: tuple[WorkflowStep, ...]
    trigger_key: str
    mode: ExecutionMode
    idempotency_key: str | None
    execution_policy: ExecutionPolicy
    approval_policy_id: str | None
    tool_grants: frozenset[str]
    model_roles: frozenset[str]
    knowledge_sources: frozenset[str]
    asset_roles: frozenset[str]


class CancellationToken:
    """Cooperative cancel flag checked between steps and during a step."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


@dataclass(frozen=True)
class ExecutionContext:
    execution_id: str
    correlation_id: str
    tenant_id: str
    user_id: str | None
    mode: ExecutionMode
    trigger: TriggerContext
    snapshot: ExecutionSnapshot
    cancellation: CancellationToken
    payload: Mapping[str, Any]


def freeze_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(payload))


@dataclass
class StepRecord:
    step_key: str
    position: int
    status: StepStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: StructuredError | None = None


@dataclass
class ExecutionRecord:
    execution_id: str
    correlation_id: str
    tenant_id: str
    user_id: str | None
    trigger_key: str
    mode: ExecutionMode
    status: ExecutionStatus
    agent_key: str
    agent_version_number: int | None = None
    agent_version_status: VersionStatus | None = None
    workflow_key: str | None = None
    workflow_version_number: int | None = None
    idempotency_key: str | None = None
    steps: list[StepRecord] = field(default_factory=list)
    error: StructuredError | None = None
    output: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    occurred_at: datetime | None = None
    snapshot: ExecutionSnapshot | None = None
    _sealed: bool = field(default=False, repr=False)

    def seal_snapshot(self, snapshot: ExecutionSnapshot) -> None:
        if self._sealed:
            raise RuntimeFailure(
                RuntimeErrorCode.INTERNAL_ERROR,
                "Execution snapshot is already sealed.",
            )
        if snapshot.execution_id != self.execution_id:
            raise RuntimeFailure(
                RuntimeErrorCode.INVALID_REQUEST,
                "Execution snapshot does not match the execution.",
            )
        self.snapshot = snapshot
        self.agent_key = snapshot.agent_key
        self.agent_version_number = snapshot.agent_version_number
        self.agent_version_status = snapshot.agent_version_status
        self.workflow_key = snapshot.workflow_key
        self.workflow_version_number = snapshot.workflow_version_number
        self._sealed = True

    def public_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "correlation_id": self.correlation_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "agent_key": self.agent_key,
            "agent_version_number": self.agent_version_number,
            "agent_version_status": self.agent_version_status.value if self.agent_version_status else None,
            "workflow_key": self.workflow_key,
            "workflow_version_number": self.workflow_version_number,
            "trigger_key": self.trigger_key,
            "mode": self.mode.value,
            "status": self.status.value,
            "idempotency_key": self.idempotency_key,
            "error": self.error.model_dump(mode="json") if self.error else None,
            "output": self.output,
            "steps": [
                {
                    "step_key": step.step_key,
                    "position": step.position,
                    "status": step.status.value,
                    "error": step.error.model_dump(mode="json") if step.error else None,
                }
                for step in self.steps
            ],
        }


@dataclass(frozen=True)
class RuntimeEvent:
    name: str
    execution_id: str
    correlation_id: str
    tenant_id: str
    user_id: str | None
    agent_key: str
    agent_version_number: int | None
    workflow_key: str | None
    workflow_version_number: int | None
    status: str
    trigger_key: str
    step_key: str | None = None
    error_code: str | None = None

