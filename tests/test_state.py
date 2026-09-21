from models.state import AgentState, TaskStatus
from agent.state_machine import StateMachine
from models.errors import AppError


def test_recorded_transitions_use_from_to_aliases() -> None:
    machine = StateMachine()
    state = AgentState(task_id="t1", image_path="photo.jpg")
    machine.transition(state, TaskStatus.PLANNING, tool="Planner", result="started")
    for tool in (
        "validate_image",
        "prepare_image",
        "upload_image",
        "create_instagram_media",
        "publish_instagram_media",
        "verify_publication",
    ):
        machine.enter_tool(state, tool)
    machine.transition(state, TaskStatus.COMPLETED, tool="verify_publication", result="success")
    dumped = [item.model_dump(by_alias=True) for item in state.execution_history]
    assert dumped[0]["from"] == "PENDING"
    assert dumped[0]["to"] == "PLANNING"
    assert dumped[-1]["to"] == "COMPLETED"
    assert [item["to"] for item in dumped] == [
        "PLANNING",
        "VALIDATING",
        "PREPARING",
        "UPLOADING",
        "CREATING_MEDIA",
        "PUBLISHING",
        "VERIFYING",
        "COMPLETED",
    ]


def test_failure_transition() -> None:
    machine = StateMachine()
    state = AgentState(task_id="t1", image_path="photo.jpg")
    machine.transition(state, TaskStatus.PLANNING, result="started")
    machine.enter_tool(state, "validate_image")
    machine.transition(state, TaskStatus.FAILED, tool="validate_image", result="failed")
    assert state.status == TaskStatus.FAILED
    assert state.execution_history[-1].model_dump(by_alias=True)["to"] == "FAILED"
