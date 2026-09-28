"""Ports the runtime calls. Implementations are injected. None are loaded from strings."""

from __future__ import annotations

import builtins
import re
from collections.abc import Awaitable, Mapping
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import ExecutionContext, RuntimeEvent, WorkflowStep

_HANDLER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_POLICY_ID = re.compile(r"^[a-z][a-z0-9_.]{0,80}$")


class ToolService(Protocol):
    async def invoke(
        self,
        ctx: ExecutionContext,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class ModelService(Protocol):
    async def complete(
        self,
        ctx: ExecutionContext,
        role: str,
        state: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class KnowledgeService(Protocol):
    async def load(self, ctx: ExecutionContext, source_name: str) -> Mapping[str, Any]: ...


class AssetService(Protocol):
    async def resolve(
        self,
        ctx: ExecutionContext,
        role: str,
        asset_id: str | None,
    ) -> Mapping[str, Any]: ...


class ApprovalPolicy(Protocol):
    def evaluate(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        state: Mapping[str, Any],
    ) -> ApprovalDecision: ...


class StepHandler(Protocol):
    def __call__(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        state: Mapping[str, Any],
    ) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def execution_id(self) -> str: ...

    def correlation_id(self) -> str: ...


class ExecutionObserver(Protocol):
    def observe(self, event: RuntimeEvent) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class UuidIdGenerator:
    def execution_id(self) -> str:
        return f"exec_{uuid4().hex}"

    def correlation_id(self) -> str:
        return f"corr_{uuid4().hex}"


class NullObserver:
    def observe(self, event: RuntimeEvent) -> None:
        return None


class HandlerRegistry:
    """Maps a handler id to a callable registered by the host process."""

    def __init__(self) -> None:
        self._handlers: dict[str, StepHandler] = {}

    def register(self, handler_id: str, handler: StepHandler) -> None:
        if handler in {builtins.eval, builtins.exec, builtins.compile}:
            raise TypeError("arbitrary code execution is not a handler")
        if not isinstance(handler_id, str) or not _HANDLER_ID.fullmatch(handler_id):
            raise ValueError("handler id is not a valid key")
        if not callable(handler) or isinstance(handler, str):
            raise TypeError("handler must be a callable")
        if handler_id in self._handlers:
            raise ValueError("handler already registered")
        self._handlers[handler_id] = handler

    def get(self, handler_id: str) -> StepHandler | None:
        return self._handlers.get(handler_id)


class ApprovalRegistry:
    def __init__(self) -> None:
        self._policies: dict[str, ApprovalPolicy] = {}

    def register(self, policy_id: str, policy: ApprovalPolicy) -> None:
        if not isinstance(policy_id, str) or not _POLICY_ID.fullmatch(policy_id):
            raise ValueError("approval policy id is not a valid key")
        if not callable(getattr(policy, "evaluate", None)):
            raise TypeError("approval policy must be a registered object")
        if policy_id in self._policies:
            raise ValueError("approval policy already registered")
        self._policies[policy_id] = policy

    def get(self, policy_id: str) -> ApprovalPolicy | None:
        return self._policies.get(policy_id)


def _unavailable(service_name: str) -> RuntimeFailure:
    return RuntimeFailure(
        RuntimeErrorCode.STEP_FAILED,
        f"{service_name} is not configured.",
    )


class UnavailableToolService:
    async def invoke(
        self,
        ctx: ExecutionContext,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        raise _unavailable("Tool service")


class UnavailableModelService:
    async def complete(
        self,
        ctx: ExecutionContext,
        role: str,
        state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        raise _unavailable("Model service")


class UnavailableKnowledgeService:
    async def load(self, ctx: ExecutionContext, source_name: str) -> Mapping[str, Any]:
        raise _unavailable("Knowledge service")


class UnavailableAssetService:
    async def resolve(
        self,
        ctx: ExecutionContext,
        role: str,
        asset_id: str | None,
    ) -> Mapping[str, Any]:
        raise _unavailable("Asset service")
