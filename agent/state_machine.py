"""Explicit Agent state-machine transitions."""

from __future__ import annotations

from models.errors import AppError, ErrorCode
from models.state import TOOL_CLASS_NAMES, TOOL_TO_STATUS, AgentState, TaskStatus, Transition

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.PLANNING, TaskStatus.FAILED}),
    TaskStatus.PLANNING: frozenset({TaskStatus.VALIDATING, TaskStatus.FAILED}),
    TaskStatus.VALIDATING: frozenset({TaskStatus.PREPARING, TaskStatus.FAILED}),
    TaskStatus.PREPARING: frozenset({TaskStatus.UPLOADING, TaskStatus.FAILED}),
    TaskStatus.UPLOADING: frozenset({TaskStatus.CREATING_MEDIA, TaskStatus.FAILED}),
    TaskStatus.CREATING_MEDIA: frozenset({TaskStatus.PUBLISHING, TaskStatus.VERIFYING, TaskStatus.FAILED}),
    TaskStatus.PUBLISHING: frozenset({TaskStatus.VERIFYING, TaskStatus.FAILED}),
    TaskStatus.VERIFYING: frozenset({TaskStatus.COMPLETED, TaskStatus.VERIFYING, TaskStatus.FAILED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


def _transition_view(
    source: TaskStatus,
    target: TaskStatus,
    *,
    tool: str | None = None,
    result: str | None = None,
) -> Transition:
    display_tool = TOOL_CLASS_NAMES.get(tool, tool) if tool else tool
    return Transition(source=source, target=target, tool=display_tool, result=result)


class StateMachine:
    def transition(
        self,
        state: AgentState,
        target: TaskStatus,
        *,
        tool: str | None = None,
        result: str | None = None,
    ) -> Transition:
        allowed = ALLOWED_TRANSITIONS.get(state.status, frozenset())
        if target != state.status and target not in allowed:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                f"Illegal state transition: {state.status.value} -> {target.value}",
                http_status=500,
            )
        if target == state.status:
            return _transition_view(state.status, target, tool=tool, result=result)
        return state.record_transition(target, tool=tool, result=result)

    def enter_tool(self, state: AgentState, tool_name: str) -> Transition:
        target = TOOL_TO_STATUS.get(tool_name)
        if target is None:
            raise AppError(ErrorCode.TOOL_NOT_ALLOWED, "Unknown tool cannot enter the state machine.")
        return self.transition(state, target, tool=tool_name, result="started")
