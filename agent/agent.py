"""Instagram Image Posting Agent.

The Agent plans, selects allowlisted tools through a registry, observes
results, decides the next action, recovers when it is safe, verifies
publication, and never executes arbitrary user or LLM code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from config import Settings
from models.errors import AppError, ErrorCode, OperationCertainty, http_status_for
from models.requests import InstagramPublishTask
from models.state import AgentState, TaskStatus, TraceStep
from services.instagram_client import InstagramClient, InstagramGraphClient
from services.logging import log_step
from tools.image_preparer import ImagePreparer
from tools.image_storage import ImageStorageTool
from tools.image_validator import ImageValidator
from tools.instagram_media import InstagramMediaCreator, InstagramPublisher
from tools.instagram_verifier import InstagramVerifier

from agent.executor import Executor
from agent.planner import ALLOWED_TOOL_SET, DeterministicPlanner, Plan, Planner, PlannedAction
from agent.recovery import Decision, RecoveryPolicy
from agent.registry import ToolRegistry
from agent.state_machine import StateMachine

logger = logging.getLogger(__name__)

StateCallback = Callable[[AgentState], Awaitable[None]]

EVENT_BY_TOOL_SUCCESS = {
    "validate_image": "IMAGE_VALIDATED",
    "prepare_image": "IMAGE_PREPARED",
    "upload_image": "IMAGE_UPLOADED",
    "create_instagram_media": "INSTAGRAM_CONTAINER_CREATED",
    "publish_instagram_media": "INSTAGRAM_MEDIA_PUBLISHED",
    "verify_publication": "PUBLICATION_VERIFIED",
}


def build_registry(settings: Settings, instagram_client: InstagramClient) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ImageValidator(settings))
    registry.register(ImagePreparer(settings))
    registry.register(ImageStorageTool(settings))
    registry.register(InstagramMediaCreator(settings, instagram_client))
    registry.register(InstagramPublisher(instagram_client))
    registry.register(InstagramVerifier(settings, instagram_client))
    return registry


class InstagramAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        planner: Planner | None = None,
        registry: ToolRegistry | None = None,
        instagram_client: InstagramClient | None = None,
        on_change: StateCallback | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._planner = planner or DeterministicPlanner()
        self._client = instagram_client or InstagramGraphClient(settings)
        self._registry = registry or build_registry(settings, self._client)
        self._executor = Executor()
        self._state_machine = StateMachine()
        self._recovery = RecoveryPolicy(
            max_retry_after_seconds=settings.max_retry_after_seconds,
            storage_retry_attempts=settings.storage_retry_attempts,
            verification_retry_attempts=settings.verification_retry_attempts,
        )
        self._on_change = on_change
        self._sleep = sleeper or asyncio.sleep

    async def _emit(self, state: AgentState) -> None:
        if self._on_change:
            await self._on_change(state)

    async def run(self, task: AgentState | InstagramPublishTask) -> AgentState:
        state = (
            task
            if isinstance(task, AgentState)
            else AgentState(
                task_id=task.task_id,
                image_path=task.image_path,
                platform=task.platform,
            )
        )
        log_step(
            logger,
            event="TASK_CREATED",
            task_id=state.task_id,
            request_id=state.request_id,
            status="pending",
        )
        try:
            await asyncio.wait_for(self._run_inner(state), timeout=self._settings.agent_timeout_seconds)
        except asyncio.TimeoutError:
            self._fail(
                state,
                AppError(
                    ErrorCode.TIMEOUT,
                    "Agent execution timed out.",
                    http_status=504,
                    certainty=OperationCertainty.UNKNOWN
                    if state.status
                    in {TaskStatus.PUBLISHING, TaskStatus.VERIFYING, TaskStatus.CREATING_MEDIA}
                    else OperationCertainty.FAILED,
                ),
            )
            await self._emit(state)
        except AppError as exc:
            self._fail(state, exc)
            await self._emit(state)
        except Exception:
            logger.exception("Agent crashed", extra={"task_id": state.task_id})
            self._fail(
                state,
                AppError(ErrorCode.INTERNAL_ERROR, "Agent execution failed.", http_status=500),
            )
            await self._emit(state)
        finally:
            self._cleanup_temp_files(state)
        return state

    async def _run_inner(self, state: AgentState) -> None:
        self._state_machine.transition(state, TaskStatus.PLANNING, tool="Planner", result="started")
        await self._emit(state)

        plan = self._planner.create_plan(state)
        self._validate_plan(plan)
        state.plan = plan.step_names
        state.execution_trace = [
            TraceStep(step=index + 1, tool=action.tool, purpose=action.purpose, status="pending")
            for index, action in enumerate(plan.actions)
        ]
        log_step(
            logger,
            event="PLAN_CREATED",
            task_id=state.task_id,
            request_id=state.request_id,
            step="plan",
            status="success",
            tools=state.plan,
        )
        self._state_machine.transition(state, TaskStatus.PLANNING, tool="Planner", result="success")
        await self._emit(state)

        while state.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            action = self._select_next_action(plan, state)
            if action is None:
                if state.instagram_media_id and state.status != TaskStatus.FAILED:
                    self._state_machine.transition(
                        state,
                        TaskStatus.COMPLETED,
                        tool="InstagramVerifier",
                        result="success",
                    )
                    log_step(
                        logger,
                        event="TASK_COMPLETED",
                        task_id=state.task_id,
                        request_id=state.request_id,
                        status="success",
                        step="completed",
                    )
                elif state.status != TaskStatus.FAILED:
                    code = (
                        ErrorCode.AMBIGUOUS_PUBLICATION
                        if state.publish_certainty == OperationCertainty.UNKNOWN
                        else ErrorCode.VERIFICATION_FAILED
                    )
                    self._fail(
                        state,
                        AppError(
                            code,
                            "Workflow finished without a verified Instagram media ID.",
                            http_status=409 if code == ErrorCode.AMBIGUOUS_PUBLICATION else 502,
                            certainty=state.publish_certainty or OperationCertainty.FAILED,
                        ),
                    )
                await self._emit(state)
                return

            await self._execute_action(state, action, plan)
            await self._emit(state)

    def _select_next_action(self, plan: Plan, state: AgentState) -> PlannedAction | None:
        """Choose the next tool from the plan plus observations, not a hardcoded call list."""

        if state.force_next_tool:
            forced = state.force_next_tool
            state.force_next_tool = None
            for action in plan.actions:
                if action.tool == forced:
                    log_step(
                        logger,
                        event="DECISION",
                        task_id=state.task_id,
                        request_id=state.request_id,
                        step=forced,
                        tool=forced,
                        status="selected",
                        reason="forced_by_observation",
                    )
                    return action

        completed = set(state.completed_tools)
        for index, action in enumerate(plan.actions):
            if action.tool in completed:
                continue
            if action.tool == "publish_instagram_media" and self._must_not_publish(state):
                state.execution_trace[index].status = "skipped"
                state.execution_trace[index].decision = "skip_publish_duplicate_protection"
                state.completed_tools.append(action.tool)
                log_step(
                    logger,
                    event="DECISION",
                    task_id=state.task_id,
                    request_id=state.request_id,
                    step=action.tool,
                    tool=action.tool,
                    status="skipped",
                    reason="duplicate_protection",
                )
                continue
            if action.tool == "create_instagram_media" and state.instagram_container_id:
                state.execution_trace[index].status = "skipped"
                state.execution_trace[index].decision = "skip_create_already_have_container"
                state.completed_tools.append(action.tool)
                continue
            log_step(
                logger,
                event="DECISION",
                task_id=state.task_id,
                request_id=state.request_id,
                step=action.tool,
                tool=action.tool,
                status="selected",
                reason="next_planned_action",
            )
            return action
        return None

    def _must_not_publish(self, state: AgentState) -> bool:
        if state.skip_publish:
            return True
        if state.instagram_media_id:
            return True
        if state.publish_certainty == OperationCertainty.SUCCEEDED:
            return True
        if state.publish_certainty == OperationCertainty.UNKNOWN:
            return True
        return False

    async def _execute_action(self, state: AgentState, action: PlannedAction, plan: Plan) -> None:
        tool = self._registry.get(action.tool)
        index = next(i for i, item in enumerate(plan.actions) if item.tool == action.tool)
        self._state_machine.enter_tool(state, action.tool)
        state.execution_trace[index].status = "running"
        await self._emit(state)

        attempt = 1
        while True:
            observation = await self._executor.execute(tool, state)
            state.apply_observation(observation)
            if observation.success and action.tool == "publish_instagram_media" and state.instagram_media_id:
                state.publish_certainty = OperationCertainty.SUCCEEDED
            decision = self._recovery.decide(observation, state, attempt=attempt)
            state.execution_trace[index].decision = decision.action.value

            log_step(
                logger,
                event="OBSERVATION",
                task_id=state.task_id,
                request_id=state.request_id,
                step=action.tool,
                tool=action.tool,
                status="success" if observation.success else "failed",
                duration_ms=observation.duration_ms,
                error_code=observation.error.code.value if observation.error else None,
                decision=decision.action.value,
            )

            if decision.action == Decision.CONTINUE:
                state.execution_trace[index].status = "success"
                state.execution_trace[index].duration_ms = observation.duration_ms
                state.execution_trace[index].observation_summary = observation.summary()
                if action.tool not in state.completed_tools:
                    state.completed_tools.append(action.tool)
                self._state_machine.transition(
                    state,
                    state.status,
                    tool=action.tool,
                    result="success",
                )
                log_step(
                    logger,
                    event=EVENT_BY_TOOL_SUCCESS.get(action.tool, "TOOL_SUCCEEDED"),
                    task_id=state.task_id,
                    request_id=state.request_id,
                    step=action.tool,
                    tool=action.tool,
                    status="success",
                    duration_ms=observation.duration_ms,
                )
                return

            if decision.action == Decision.SKIP:
                state.execution_trace[index].status = "skipped"
                if action.tool not in state.completed_tools:
                    state.completed_tools.append(action.tool)
                if decision.next_tool:
                    state.force_next_tool = decision.next_tool
                return

            if decision.action == Decision.RETRY:
                attempt += 1
                if decision.delay_seconds:
                    await self._sleep(decision.delay_seconds)
                continue

            if decision.action == Decision.VERIFY_FIRST:
                state.execution_trace[index].status = "failed"
                state.execution_trace[index].duration_ms = observation.duration_ms
                state.execution_trace[index].error_code = (
                    observation.error.code.value if observation.error else None
                )
                if action.tool not in state.completed_tools:
                    state.completed_tools.append(action.tool)
                state.force_next_tool = decision.next_tool or "verify_publication"
                self._state_machine.transition(
                    state,
                    TaskStatus.VERIFYING,
                    tool=action.tool,
                    result="ambiguous",
                )
                return

            error = AppError(
                observation.error.code if observation.error else ErrorCode.INTERNAL_ERROR,
                observation.error.message if observation.error else "Tool failed.",
                http_status=http_status_for(
                    observation.error.code if observation.error else ErrorCode.INTERNAL_ERROR
                ),
                retryable=observation.error.retryable if observation.error else False,
                certainty=observation.error.certainty if observation.error else OperationCertainty.FAILED,
                details=observation.error.details if observation.error else {},
            )
            if (
                action.tool in {"publish_instagram_media", "verify_publication"}
                and state.publish_certainty == OperationCertainty.UNKNOWN
                and not state.instagram_media_id
            ):
                error = AppError(
                    ErrorCode.AMBIGUOUS_PUBLICATION,
                    "Publication state is unknown. The Agent refused to publish again.",
                    http_status=409,
                    certainty=OperationCertainty.UNKNOWN,
                )
            state.execution_trace[index].status = "failed"
            state.execution_trace[index].duration_ms = observation.duration_ms
            state.execution_trace[index].error_code = error.code.value
            self._fail(state, error, tool=action.tool)
            return

    def _validate_plan(self, plan: Plan) -> None:
        if not plan.actions:
            raise AppError(ErrorCode.INTERNAL_ERROR, "Planner produced no actions.", http_status=500)
        for action in plan.actions:
            if action.tool not in ALLOWED_TOOL_SET:
                raise AppError(
                    ErrorCode.TOOL_NOT_ALLOWED,
                    "Planner selected a tool that is not allowlisted.",
                    http_status=500,
                )
            self._registry.get(action.tool)

    def _fail(self, state: AgentState, error: AppError, *, tool: str | None = None) -> None:
        if state.status != TaskStatus.FAILED:
            try:
                self._state_machine.transition(state, TaskStatus.FAILED, tool=tool, result="failed")
            except AppError:
                state.status = TaskStatus.FAILED
                state.touch()
        state.error = error.to_body()
        if error.certainty == OperationCertainty.UNKNOWN:
            state.publish_certainty = OperationCertainty.UNKNOWN
        log_step(
            logger,
            event="TASK_FAILED",
            task_id=state.task_id,
            request_id=state.request_id,
            step=state.current_step,
            tool=tool,
            status="failed",
            error_code=error.code.value,
        )

    def _cleanup_temp_files(self, state: AgentState) -> None:
        for raw_path in list(state.temp_paths):
            path = Path(raw_path)
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                logger.warning("Failed to clean temporary file", extra={"task_id": state.task_id})
        if state.image_path:
            path = Path(state.image_path)
            try:
                if path.is_file() and path.resolve().is_relative_to(self._settings.temp_dir.resolve()):
                    path.unlink()
            except OSError:
                logger.warning("Failed to clean upload file", extra={"task_id": state.task_id})


def build_instagram_agent(
    settings: Settings,
    *,
    planner: Planner | None = None,
    registry: ToolRegistry | None = None,
    instagram_client: InstagramClient | None = None,
    on_change: StateCallback | None = None,
) -> InstagramAgent:
    return InstagramAgent(
        settings,
        planner=planner,
        registry=registry,
        instagram_client=instagram_client,
        on_change=on_change,
    )
