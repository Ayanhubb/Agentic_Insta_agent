from models.errors import AppError, ErrorCode
from models.state import AgentState, TaskStatus
from agent.planner import ALLOWED_TOOLS, DeterministicPlanner, LLMPlanner, PLAN_PURPOSES
from agent.registry import ToolRegistry
from agent.state_machine import StateMachine
from tests.helpers import ScriptedTool


def test_deterministic_planner_output() -> None:
    planner = DeterministicPlanner()
    plan = planner.create_plan(AgentState(task_id="t1", image_path="photo.jpg"))
    assert [action.tool for action in plan.actions] == list(ALLOWED_TOOLS)
    assert [action.purpose for action in plan.actions] == [PLAN_PURPOSES[name] for name in ALLOWED_TOOLS]
    assert plan.actions[0].tool == "validate_image"
    assert plan.actions[0].purpose == "Validate uploaded image"
    assert plan.actions[-1].tool == "verify_publication"


def test_llm_planner_is_not_used_in_production() -> None:
    planner = LLMPlanner()
    try:
        planner.create_plan(AgentState(task_id="t1", image_path="photo.jpg"))
        raise AssertionError("LLMPlanner must not produce a production plan")
    except AppError as exc:
        assert exc.code == ErrorCode.CONFIGURATION_ERROR


def test_llm_planner_sanitizes_to_allowlisted_tools_only() -> None:
    planner = LLMPlanner()
    actions = planner.sanitize_actions(
        [
            {"tool": "validate_image", "purpose": "Validate uploaded image"},
            {"tool": "shell_exec", "command": "rm -rf /"},
            {"tool": "publish_instagram_media", "url": "https://evil.example/hook"},
            {"tool": "verify_publication"},
            {"tool": "create_instagram_media"},
        ]
    )
    assert [item.tool for item in actions] == ["validate_image", "verify_publication", "create_instagram_media"]


def test_llm_planner_rejects_unknown_proposed_tools() -> None:
    planner = LLMPlanner()
    try:
        planner.create_plan(
            AgentState(task_id="t1", image_path="photo.jpg"),
            proposed_actions=["run_python", "curl_random_url"],
        )
        raise AssertionError("expected allowlist rejection")
    except AppError as exc:
        assert exc.code == ErrorCode.TOOL_NOT_ALLOWED


def test_registry_register_and_get() -> None:
    registry = ToolRegistry()
    tool = ScriptedTool("validate_image", "Validate uploaded image", [])
    registry.register(tool)
    assert registry.get("validate_image") is tool
    assert registry.contains("validate_image")
    assert registry.names() == ["validate_image"]


def test_registry_rejects_unknown_tools() -> None:
    registry = ToolRegistry()

    class ShellTool(ScriptedTool):
        pass

    try:
        registry.register(ShellTool("shell_exec", "Run a shell command", []))
        raise AssertionError("non-allowlisted tools must be rejected")
    except AppError as exc:
        assert exc.code == ErrorCode.TOOL_NOT_ALLOWED

    try:
        registry.get("shell_exec")
        raise AssertionError("unknown tools must not be retrieved")
    except AppError as exc:
        assert exc.code == ErrorCode.TOOL_NOT_ALLOWED


def test_state_machine_records_from_to_aliases() -> None:
    machine = StateMachine()
    state = AgentState(task_id="t1", image_path="photo.jpg")
    machine.transition(state, TaskStatus.PLANNING, tool="Planner", result="started")
    machine.enter_tool(state, "validate_image")
    record = state.execution_history[-1].model_dump(by_alias=True)
    assert record["from"] == "PLANNING"
    assert record["to"] == "VALIDATING"
    assert record["tool"] == "ImageValidator"
    assert record["result"] == "started"
    assert "timestamp" in record


def test_illegal_transition_is_rejected() -> None:
    machine = StateMachine()
    state = AgentState(task_id="t1", image_path="photo.jpg")
    try:
        machine.transition(state, TaskStatus.PUBLISHING, tool="InstagramPublisher", result="started")
        raise AssertionError("illegal transition must fail")
    except AppError as exc:
        assert exc.code == ErrorCode.INTERNAL_ERROR
