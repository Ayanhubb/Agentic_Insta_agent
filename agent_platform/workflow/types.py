"""Structured workflow records.

A definition names registered capabilities. It does not contain callable source,
module paths, or secret material.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StepType(str, Enum):
    START = "start"
    CONTEXT = "context"
    LLM = "llm"
    TOOL = "tool"
    MCP = "mcp"
    SERVICE = "service"
    CONDITION = "condition"
    APPROVAL = "approval"
    ACTION = "action"
    VERIFICATION = "verification"
    END = "end"


CAPABILITY_STEP_TYPES = frozenset(
    {
        StepType.CONTEXT,
        StepType.LLM,
        StepType.TOOL,
        StepType.MCP,
        StepType.SERVICE,
        StepType.APPROVAL,
        StepType.ACTION,
        StepType.VERIFICATION,
    }
)


class ConditionOperator(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    IN = "in"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_APPROVAL = "waiting_approval"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class StepRetryPolicy:
    """Total attempts, including the first call. Only listed codes are retried."""

    max_attempts: int = 1
    retry_on: frozenset[str] = field(default_factory=lambda: frozenset({"failed"}))


@dataclass(frozen=True, slots=True)
class StepTimeout:
    """Wall-clock limit for one handler call. The engine does not kill the handler process."""

    seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class StepDependency:
    step_id: str


@dataclass(frozen=True, slots=True)
class StepCondition:
    """Comparison against a prior step output. This is data, not an expression."""

    source_step: str
    field: str
    operator: ConditionOperator
    then_step: str
    else_step: str
    value: Any = None


@dataclass(frozen=True, slots=True)
class StepInput:
    """Literal values plus copies of fields from earlier step outputs."""

    values: tuple[tuple[str, Any], ...] = ()
    from_steps: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    id: str
    step_type: StepType
    capability: str | None = None
    input: StepInput = field(default_factory=StepInput)
    condition: StepCondition | None = None
    retry: StepRetryPolicy = field(default_factory=StepRetryPolicy)
    timeout: StepTimeout = field(default_factory=StepTimeout)
    depends_on: tuple[StepDependency, ...] = ()
    next_step: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    id: str
    steps: tuple[WorkflowStep, ...]
    version: int = 1
    agent_id: str | None = None

    def step(self, step_id: str) -> WorkflowStep:
        for item in self.steps:
            if item.id == step_id:
                return item
        raise KeyError(step_id)


@dataclass(slots=True)
class StepOutput:
    data: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class WorkflowExecutionStep:
    step_id: str
    step_type: StepType
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    output: StepOutput | None = None


@dataclass(slots=True)
class WorkflowExecution:
    id: str
    workflow_id: str
    workflow_version: int
    tenant_id: str
    status: ExecutionStatus
    steps: list[WorkflowExecutionStep]
    idempotency_key: str | None = None
    current_step_id: str | None = None
    resume_step_id: str | None = None
    error_code: str | None = None
    cancel_requested: bool = False

    def step(self, step_id: str) -> WorkflowExecutionStep:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        raise KeyError(step_id)
