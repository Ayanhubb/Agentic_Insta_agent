"""Generic workflow engine.

Steps name capabilities in a registry. The engine compares structured conditions
and calls those callables. It does not evaluate text, import a name, or read
secrets.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Mapping
from typing import Any

from agent_platform.workflow.errors import (
    CapabilityFailure,
    ExecutionNotFound,
    InvalidDefinition,
    InvalidExecutionState,
    SecretRejected,
    UnknownCapability,
    WorkflowError,
)
from agent_platform.workflow.guard import (
    bind_tenant,
    lookup_field,
    reject_secrets,
    require_capability_name,
    require_field_path,
    require_idempotency_key,
    require_step_id,
    require_workflow_id,
    strip_untrusted_tenant_keys,
)
from agent_platform.workflow.registry import BLOCKED_CAPABILITY_NAMES, CapabilityCall, CapabilityRegistry
from agent_platform.workflow.types import (
    CAPABILITY_STEP_TYPES,
    ConditionOperator,
    ExecutionStatus,
    StepCondition,
    StepOutput,
    StepRetryPolicy,
    StepStatus,
    StepTimeout,
    StepType,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowExecutionStep,
    WorkflowStep,
)

_MAX_ATTEMPTS = 5
_MAX_TIMEOUT_SECONDS = 120.0
_TERMINAL = frozenset(
    {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT,
    }
)


class _Abort(Exception):
    def __init__(self, code: str, *, timed_out: bool = False, attempts: int = 1) -> None:
        self.code = code
        self.timed_out = timed_out
        self.attempts = attempts


class _Pause(Exception):
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


class _ExecutionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, WorkflowExecution] = {}
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._payloads: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[tuple[str, str, str], str] = {}

    def begin(
        self,
        *,
        tenant_id: str,
        definition: WorkflowDefinition,
        payload: dict[str, Any],
        idempotency_key: str | None,
    ) -> tuple[WorkflowExecution, bool]:
        """Return the existing execution for this key, or insert one and report that it is new."""

        with self._lock:
            if idempotency_key:
                existing_id = self._idempotency.get((tenant_id, definition.id, idempotency_key))
                if existing_id is not None:
                    return self._by_id[existing_id], False
            execution = WorkflowExecution(
                id=str(uuid.uuid4()),
                workflow_id=definition.id,
                workflow_version=definition.version,
                tenant_id=tenant_id,
                status=ExecutionStatus.RUNNING,
                idempotency_key=idempotency_key,
                steps=[WorkflowExecutionStep(step_id=step.id, step_type=step.step_type) for step in definition.steps],
            )
            self._by_id[execution.id] = execution
            self._definitions[execution.id] = definition
            self._payloads[execution.id] = payload
            if idempotency_key:
                self._idempotency[(tenant_id, definition.id, idempotency_key)] = execution.id
            return execution, True

    def payload_for(self, execution_id: str) -> dict[str, Any]:
        with self._lock:
            return self._payloads[execution_id]

    def get_owned(self, tenant_id: str, execution_id: str) -> WorkflowExecution | None:
        with self._lock:
            execution = self._by_id.get(execution_id)
            if execution is None or execution.tenant_id != tenant_id:
                return None
            return execution

    def definition_for(self, execution_id: str) -> WorkflowDefinition:
        with self._lock:
            return self._definitions[execution_id]


class WorkflowEngine:
    """Runs one validated definition for one trusted tenant."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry
        self._store = _ExecutionStore()

    def run(
        self,
        definition: WorkflowDefinition,
        tenant: object,
        payload: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> WorkflowExecution:
        tenant_id, source = bind_tenant(tenant)
        checked = validate_definition(definition, self._registry)
        safe_payload = _sanitize_payload(payload)
        key = require_idempotency_key(idempotency_key) if idempotency_key is not None else None
        execution, created = self._store.begin(
            tenant_id=tenant_id,
            definition=checked,
            payload=safe_payload,
            idempotency_key=key,
        )
        if not created:
            return execution
        try:
            return self._drive(execution, checked, (tenant_id, source), safe_payload, _start_id(checked))
        except WorkflowError:
            if execution.status is ExecutionStatus.RUNNING:
                execution.status = ExecutionStatus.FAILED
                execution.error_code = execution.error_code or "workflow_error"
            raise

    def get(self, tenant: object, execution_id: str) -> WorkflowExecution:
        tenant_id, _source = bind_tenant(tenant)
        execution = self._store.get_owned(tenant_id, execution_id)
        if execution is None:
            raise ExecutionNotFound()
        return execution

    def resume(self, tenant: object, execution_id: str, *, approved: bool) -> WorkflowExecution:
        tenant_id, source = bind_tenant(tenant)
        execution = self._store.get_owned(tenant_id, execution_id)
        if execution is None:
            raise ExecutionNotFound()
        if execution.status != ExecutionStatus.WAITING_APPROVAL or not execution.resume_step_id:
            raise InvalidExecutionState("Execution is not waiting for approval.")
        if execution.current_step_id is None:
            raise InvalidExecutionState("Execution is not waiting for approval.")
        current = execution.step(execution.current_step_id)
        if not approved:
            current.status = StepStatus.FAILED
            current.output = StepOutput(data={}, error_code="approval_rejected", error_message="Approval was rejected.")
            execution.status = ExecutionStatus.FAILED
            execution.error_code = "approval_rejected"
            return execution
        current.status = StepStatus.SUCCEEDED
        current.output = StepOutput(data={"decision": "approved"})
        execution.status = ExecutionStatus.RUNNING
        definition = self._store.definition_for(execution.id)
        payload = self._store.payload_for(execution.id)
        resume_at = execution.resume_step_id
        execution.resume_step_id = None
        return self._drive(execution, definition, (tenant_id, source), payload, resume_at)

    def cancel(self, tenant: object, execution_id: str) -> WorkflowExecution:
        tenant_id, _source = bind_tenant(tenant)
        execution = self._store.get_owned(tenant_id, execution_id)
        if execution is None:
            raise ExecutionNotFound()
        if execution.status in _TERMINAL:
            return execution
        execution.cancel_requested = True
        if execution.status == ExecutionStatus.WAITING_APPROVAL:
            _finish_cancelled(execution)
        return execution

    def _drive(
        self,
        execution: WorkflowExecution,
        definition: WorkflowDefinition,
        tenant: tuple[str, str],
        payload: dict[str, Any],
        cursor: str,
    ) -> WorkflowExecution:
        by_id = {step.id: step for step in definition.steps}
        seen: set[str] = set()
        while cursor:
            if cursor in seen:
                return _fail(execution, "invalid_definition")
            seen.add(cursor)
            if execution.cancel_requested:
                _finish_cancelled(execution)
                return execution
            step = by_id[cursor]
            record = execution.step(step.id)
            execution.current_step_id = step.id
            if not _dependencies_satisfied(execution, step):
                return _fail(execution, "dependency_not_met", record)
            if step.step_type is StepType.END:
                record.status = StepStatus.SUCCEEDED
                record.output = StepOutput(data=_succeeded_outputs(execution))
                execution.status = ExecutionStatus.COMPLETED
                execution.error_code = None
                return execution
            if step.step_type is StepType.START:
                record.status = StepStatus.SUCCEEDED
                record.attempts = 1
                record.output = StepOutput(data=dict(payload))
                cursor = step.next_step or ""
                continue
            try:
                if step.step_type is StepType.CONDITION:
                    taken, other = _evaluate_condition(execution, step)
                    record.status = StepStatus.SUCCEEDED
                    record.attempts = 1
                    record.output = StepOutput(data={"taken": taken})
                    _skip_until(execution, definition, other, _reachable(definition, taken))
                    cursor = taken
                    continue
                data, attempts = self._call_step(execution, definition, tenant, step)
            except _Pause as paused:
                record.status = StepStatus.WAITING_APPROVAL
                record.attempts = 1
                record.output = StepOutput(data=paused.data)
                execution.status = ExecutionStatus.WAITING_APPROVAL
                execution.resume_step_id = step.next_step
                return execution
            except _Abort as aborted:
                record.attempts = aborted.attempts
                record.status = StepStatus.TIMED_OUT if aborted.timed_out else StepStatus.FAILED
                record.output = StepOutput(data={}, error_code=aborted.code, error_message="Step failed.")
                execution.status = ExecutionStatus.TIMED_OUT if aborted.timed_out else ExecutionStatus.FAILED
                execution.error_code = aborted.code
                return execution
            record.status = StepStatus.SUCCEEDED
            record.attempts = attempts
            record.output = StepOutput(data=data)
            cursor = step.next_step or ""
        return _fail(execution, "invalid_definition")

    def _call_step(
        self,
        execution: WorkflowExecution,
        definition: WorkflowDefinition,
        tenant: tuple[str, str],
        step: WorkflowStep,
    ) -> tuple[dict[str, Any], int]:
        if not step.capability:
            raise _Abort("unknown_capability")
        registered = self._registry.resolve(step.capability, step.step_type)
        data = _materialize_input(execution, step)
        call = CapabilityCall(
            tenant_id=tenant[0],
            tenant_source=tenant[1],
            execution_id=execution.id,
            workflow_id=definition.id,
            step_id=step.id,
            capability=registered.name,
            data=data,
        )
        attempts = 0
        while attempts < step.retry.max_attempts:
            if execution.cancel_requested:
                raise _Abort("cancelled", attempts=max(attempts, 1))
            attempts += 1
            try:
                raw = _invoke(registered.handler, call, step.timeout.seconds)
            except _Abort as aborted:
                aborted.attempts = attempts
                if aborted.timed_out and attempts < step.retry.max_attempts and "timeout" in step.retry.retry_on:
                    continue
                raise
            except CapabilityFailure as exc:
                if attempts < step.retry.max_attempts and exc.code in step.retry.retry_on:
                    continue
                raise _Abort(exc.code, attempts=attempts) from None
            except Exception:
                raise _Abort("handler_error", attempts=attempts) from None
            if not isinstance(raw, dict):
                raise _Abort("invalid_result", attempts=attempts)
            try:
                reject_secrets(raw)
            except SecretRejected:
                raise _Abort("secret_rejected", attempts=attempts) from None
            cleaned = dict(raw)
            if step.step_type is StepType.APPROVAL:
                decision = cleaned.get("decision")
                if decision == "pause":
                    raise _Pause({"decision": "pause"})
                if decision == "reject":
                    raise _Abort("approval_rejected")
                if decision != "approve":
                    raise _Abort("invalid_result")
            return cleaned, attempts
        raise _Abort("failed")


def validate_definition(definition: WorkflowDefinition, registry: CapabilityRegistry) -> WorkflowDefinition:
    """Reject a definition that names unknown capabilities or carries secrets."""

    require_workflow_id(definition.id)
    if not isinstance(definition.version, int) or isinstance(definition.version, bool) or definition.version < 1:
        raise InvalidDefinition("Workflow version is not valid.")
    if definition.agent_id is not None:
        require_workflow_id(definition.agent_id)
    if not definition.steps:
        raise InvalidDefinition("Workflow has no steps.")
    by_id: dict[str, WorkflowStep] = {}
    for step in definition.steps:
        require_step_id(step.id)
        if step.id in by_id:
            raise InvalidDefinition("Workflow step ids must be unique.")
        by_id[step.id] = step
        _validate_step_shape(step, registry)
    starts = [step.id for step in definition.steps if step.step_type is StepType.START]
    ends = [step.id for step in definition.steps if step.step_type is StepType.END]
    if len(starts) != 1 or len(ends) != 1:
        raise InvalidDefinition("Workflow must have one start step and one end step.")
    _assert_graph(by_id, starts[0])
    return definition


def _validate_step_shape(step: WorkflowStep, registry: CapabilityRegistry) -> None:
    _validate_policy(step.retry)
    _validate_timeout(step.timeout)
    for binding in step.input.from_steps:
        if len(binding) != 3:
            raise InvalidDefinition("Step input binding is not valid.")
        require_step_id(binding[0])
        require_step_id(binding[1])
        require_field_path(binding[2])
        reject_secrets({binding[0]: ""})
    reject_secrets(dict(step.input.values))
    for dep in step.depends_on:
        require_step_id(dep.step_id)
        if dep.step_id == step.id:
            raise InvalidDefinition("A step cannot depend on itself.")
    if step.step_type in {StepType.START, StepType.END, StepType.CONDITION}:
        if step.capability is not None:
            raise InvalidDefinition("This step cannot name a capability.")
    if step.step_type is StepType.CONDITION:
        if step.next_step is not None or step.condition is None:
            raise InvalidDefinition("Condition step is not valid.")
        _validate_condition(step.condition)
        return
    if step.step_type is StepType.END:
        if step.next_step is not None:
            raise InvalidDefinition("End step cannot have a next step.")
        return
    if not step.next_step:
        raise InvalidDefinition("Step is missing a next step.")
    require_step_id(step.next_step)
    if step.step_type not in CAPABILITY_STEP_TYPES and step.step_type is not StepType.START:
        raise InvalidDefinition("Step type is not valid.")
    if step.step_type is StepType.START:
        return
    if not step.capability:
        raise UnknownCapability()
    require_capability_name(step.capability)
    if step.capability in BLOCKED_CAPABILITY_NAMES:
        raise UnknownCapability()
    registry.resolve(step.capability, step.step_type)


def _validate_condition(condition: StepCondition) -> None:
    require_step_id(condition.source_step)
    require_step_id(condition.then_step)
    require_step_id(condition.else_step)
    require_field_path(condition.field)
    if condition.operator not in ConditionOperator:
        raise InvalidDefinition("Condition operator is not valid.")
    if condition.operator is ConditionOperator.IN and not isinstance(condition.value, (list, tuple)):
        raise InvalidDefinition("Condition value is not valid.")
    if condition.value is not None:
        reject_secrets(condition.value if isinstance(condition.value, (dict, list, tuple)) else {"value": condition.value})


def _validate_policy(policy: StepRetryPolicy) -> None:
    if (
        not isinstance(policy.max_attempts, int)
        or isinstance(policy.max_attempts, bool)
        or policy.max_attempts < 1
        or policy.max_attempts > _MAX_ATTEMPTS
    ):
        raise InvalidDefinition("Retry policy is not valid.")
    for code in policy.retry_on:
        if not isinstance(code, str) or not code.replace("_", "").isalnum():
            raise InvalidDefinition("Retry policy is not valid.")


def _validate_timeout(timeout: StepTimeout) -> None:
    if (
        isinstance(timeout.seconds, bool)
        or not isinstance(timeout.seconds, (int, float))
        or timeout.seconds <= 0
        or timeout.seconds > _MAX_TIMEOUT_SECONDS
    ):
        raise InvalidDefinition("Timeout is not valid.")


def _assert_graph(by_id: dict[str, WorkflowStep], start_id: str) -> None:
    paths = _paths(start_id, by_id)
    seen: set[str] = set()
    for path in paths:
        if by_id[path[-1]].step_type is not StepType.END:
            raise InvalidDefinition("Every path must end.")
        earlier: set[str] = set()
        for step_id in path:
            step = by_id[step_id]
            for dep in step.depends_on:
                if dep.step_id not in by_id:
                    raise InvalidDefinition("Step dependency is not valid.")
                if dep.step_id not in earlier:
                    raise InvalidDefinition("Step dependency is not ordered before the step.")
            earlier.add(step_id)
            seen.add(step_id)
            for nxt in _successors(step):
                if nxt not in by_id:
                    raise InvalidDefinition("Step points at an unknown step.")
    if seen != set(by_id):
        raise InvalidDefinition("Every step must be reachable from start.")


def _paths(start_id: str, by_id: dict[str, WorkflowStep]) -> list[list[str]]:
    found: list[list[str]] = []

    def walk(node: str, acc: list[str], stack: set[str]) -> None:
        if node in stack:
            raise InvalidDefinition("Workflow contains a cycle.")
        if len(found) > 64:
            raise InvalidDefinition("Workflow has too many paths.")
        step = by_id[node]
        nxts = _successors(step)
        if not nxts:
            found.append(acc + [node])
            return
        for nxt in nxts:
            if nxt not in by_id:
                raise InvalidDefinition("Step points at an unknown step.")
            walk(nxt, acc + [node], stack | {node})

    walk(start_id, [], set())
    return found


def _successors(step: WorkflowStep) -> list[str]:
    if step.step_type is StepType.END:
        return []
    if step.step_type is StepType.CONDITION:
        if step.condition is None:
            raise InvalidDefinition("Condition step is not valid.")
        return [step.condition.then_step, step.condition.else_step]
    if not step.next_step:
        raise InvalidDefinition("Step is missing a next step.")
    return [step.next_step]


def _start_id(definition: WorkflowDefinition) -> str:
    for step in definition.steps:
        if step.step_type is StepType.START:
            return step.id
    raise InvalidDefinition("Workflow must have one start step and one end step.")


def _sanitize_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise InvalidDefinition("Workflow payload must be an object.")
    safe = strip_untrusted_tenant_keys(payload)
    reject_secrets(safe)
    return safe


def _dependencies_satisfied(execution: WorkflowExecution, step: WorkflowStep) -> bool:
    allowed = {StepStatus.SUCCEEDED, StepStatus.SKIPPED}
    for dep in step.depends_on:
        if execution.step(dep.step_id).status not in allowed:
            return False
    return True


def _evaluate_condition(execution: WorkflowExecution, step: WorkflowStep) -> tuple[str, str]:
    condition = step.condition
    if condition is None:
        raise _Abort("invalid_definition")
    source = execution.step(condition.source_step)
    if source.status is not StepStatus.SUCCEEDED or source.output is None:
        raise _Abort("condition_source_missing")
    found, value = lookup_field(source.output.data, condition.field)
    matched = _matches(condition, found, value)
    if matched:
        return condition.then_step, condition.else_step
    return condition.else_step, condition.then_step


def _matches(condition: StepCondition, found: bool, value: Any) -> bool:
    operator = condition.operator
    if operator is ConditionOperator.EXISTS:
        return found
    if operator is ConditionOperator.NOT_EXISTS:
        return not found
    if not found:
        return False
    if operator is ConditionOperator.EQ:
        return value == condition.value
    if operator is ConditionOperator.NEQ:
        return value != condition.value
    if operator is ConditionOperator.IN:
        return isinstance(condition.value, (list, tuple)) and value in condition.value
    return False


def _reachable(definition: WorkflowDefinition, start_id: str) -> set[str]:
    by_id = {step.id: step for step in definition.steps}
    seen: set[str] = set()
    stack = [start_id]
    while stack:
        node = stack.pop()
        if node in seen or node not in by_id:
            continue
        seen.add(node)
        stack.extend(_successors(by_id[node]))
    return seen


def _skip_until(
    execution: WorkflowExecution,
    definition: WorkflowDefinition,
    start_id: str,
    keep: set[str],
) -> None:
    by_id = {step.id: step for step in definition.steps}
    stack = [start_id]
    walked: set[str] = set()
    while stack:
        node = stack.pop()
        if node in walked or node in keep or node not in by_id:
            continue
        walked.add(node)
        record = execution.step(node)
        if record.status is StepStatus.PENDING:
            record.status = StepStatus.SKIPPED
            record.output = StepOutput(data={})
        stack.extend(_successors(by_id[node]))


def _materialize_input(execution: WorkflowExecution, step: WorkflowStep) -> dict[str, Any]:
    data = dict(step.input.values)
    reject_secrets(data)
    for dest, source_id, field in step.input.from_steps:
        source = execution.step(source_id)
        if source.status is not StepStatus.SUCCEEDED or source.output is None:
            raise _Abort("missing_input")
        found, value = lookup_field(source.output.data, field)
        if not found:
            raise _Abort("missing_input")
        data[dest] = value
    reject_secrets(data)
    return data


def _invoke(handler: Any, call: CapabilityCall, timeout_s: float) -> Mapping[str, Any]:
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = handler(call)
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise _Abort("timeout", timed_out=True)
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _succeeded_outputs(execution: WorkflowExecution) -> dict[str, Any]:
    collected: dict[str, Any] = {}
    for record in execution.steps:
        if record.step_type is StepType.END:
            continue
        if record.status is StepStatus.SUCCEEDED and record.output is not None:
            collected[record.step_id] = dict(record.output.data)
    return {"steps": collected}


def _fail(
    execution: WorkflowExecution,
    code: str,
    record: WorkflowExecutionStep | None = None,
) -> WorkflowExecution:
    if record is not None and record.status is StepStatus.PENDING:
        record.status = StepStatus.FAILED
        record.output = StepOutput(data={}, error_code=code, error_message="Step failed.")
    execution.status = ExecutionStatus.FAILED
    execution.error_code = code
    return execution


def _finish_cancelled(execution: WorkflowExecution) -> None:
    for record in execution.steps:
        if record.status in {StepStatus.PENDING, StepStatus.WAITING_APPROVAL, StepStatus.RUNNING}:
            record.status = StepStatus.CANCELLED
    execution.status = ExecutionStatus.CANCELLED
    execution.error_code = "cancelled"
