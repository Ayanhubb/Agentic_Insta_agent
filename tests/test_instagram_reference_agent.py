"""Old and new Instagram paths both end at the Instagram Agent."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent.instagram_tasks import enqueue_instagram_publication
from agent.planner import ALLOWED_TOOL_SET
from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import (
    ExecutionMode,
    ExecutionStatus,
    StepStatus,
    TriggerContext,
)
from api.app import create_app
from backend.mcp.errors import ToolNotAllowed
from backend.mcp.permissions import (
    INSTAGRAM_PUBLISHING_TOOLS,
    MCP_TOOL_ALLOWLIST,
    assert_tool_allowed,
)
from instagram_reference.binding import build_reference_runtime, dispatch
from instagram_reference.definition import (
    AGENT_KEY,
    DISPLAY_NAME,
    REFERENCE_STEP_KEYS,
    TRIGGER_KEY,
    instagram_marketing_definition,
)
from instagram_reference.services import ReferenceToolService
from models.state import AgentState, TaskStatus

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROOT = ROOT / "instagram_reference"
RUNTIME_ROOT = ROOT / "agent_runtime"
IMAGE_PATH = "storage/generated/user-1/still.jpg"


class SpyGateway:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def enqueue_publication(self, request: object) -> AgentState:
        self.requests.append(request)
        return AgentState(
            user_id=getattr(request, "user_id", None),
            status=TaskStatus.COMPLETED,
            instagram_media_id="media-1",
            verified=True,
            db_task_id="db-task",
            db_post_id="db-post",
            task_id="task-1",
            skip_publish=False,
        )


class RecordingModels:
    def __init__(self) -> None:
        self.roles: list[str] = []

    async def complete(self, ctx: object, role: str, state: object) -> dict[str, object]:
        del ctx, state
        self.roles.append(role)
        if role == "content_planning":
            return {
                "planned": True,
                "provider": "deepseek",
                "plan": {"content_type": "BRAND", "caption": "Shop still", "image_prompt": "A shop still"},
            }
        if role == "image_generation":
            return {"generated": True, "edited": False, "provider": "openai", "image_id": "img-1"}
        if role == "vision_qa":
            return {"passed": True, "malformed": False, "reasons": [], "provider": "deepseek"}
        raise AssertionError(role)


class _Grants:
    def __init__(self, names: frozenset[str]) -> None:
        self.tool_grants = names


class _ToolContext:
    def __init__(self, names: frozenset[str]) -> None:
        self.snapshot = _Grants(names)
        self.tenant_id = "user-1"


@pytest.fixture
def spy_gateway(monkeypatch: pytest.MonkeyPatch) -> SpyGateway:
    constructed: list[object] = []

    def _reject_graph(self: object, *args: object, **kwargs: object) -> None:
        del self, args, kwargs
        constructed.append(True)
        raise AssertionError("Meta Graph client must not be constructed")

    monkeypatch.setattr("tools.instagram_media.InstagramGraphClient.__init__", _reject_graph)
    gateway = SpyGateway()
    from agent import instagram_tasks

    previous = instagram_tasks._gateway
    instagram_tasks.bind_publication_gateway(gateway)
    yield gateway
    instagram_tasks.bind_publication_gateway(previous)
    assert constructed == []


def _approved_payload() -> dict[str, object]:
    return {
        "user_id": "someone-else",
        "tenant_id": "someone-else",
        "publish": False,
        "image_path": IMAGE_PATH,
        "caption": "Shop still",
        "mode": "DAILY",
        "ownership_ok": True,
        "automation": {"auto_daily_publish": True},
        "profile": {"business_name": "Shop"},
    }


async def test_old_and_new_paths_publish_only_through_the_instagram_agent(spy_gateway: SpyGateway) -> None:
    await enqueue_instagram_publication(
        {
            "user_id": "user-1",
            "image_path": IMAGE_PATH,
            "source": "USER_PROMPT",
            "wait": True,
            "allow_environment_fallback": False,
        }
    )
    models = RecordingModels()
    runtime = build_reference_runtime(models=models)
    record = await dispatch("user-1", _approved_payload(), runtime=runtime)

    assert record.status == ExecutionStatus.COMPLETED
    assert record.agent_key == AGENT_KEY
    assert [step.step_key for step in record.steps] == list(REFERENCE_STEP_KEYS)
    assert {step.status for step in record.steps} == {StepStatus.COMPLETED}
    assert models.roles == ["content_planning", "image_generation", "vision_qa"]
    assert len(spy_gateway.requests) == 2
    for request in spy_gateway.requests:
        assert request.user_id == "user-1"
        assert request.allow_environment_fallback is False
        assert request.image_path == IMAGE_PATH
        assert getattr(request, "access_token", None) is None
    steps = record.output["steps"]
    assert steps["load_business_context"]["applicable"] is True
    assert steps["load_business_context"]["found"] is False
    assert steps["load_festival_context"]["applicable"] is False
    assert steps["load_instagram_intelligence"]["applicable"] is False
    assert steps["load_trend_evidence"]["applicable"] is False
    publication = steps["instagram_agent_publication"]
    assert publication["publisher"] == "instagram_agent"
    assert publication["published"] is True
    assert steps["meta_verification"]["verified"] is True
    assert steps["meta_verification"]["instagram_media_id"] == "media-1"
    assert steps["persist_result"]["accepted"] is True
    assert steps["persist_result"]["persisted"] is True
    assert "image_path" not in publication
    assert "image_path" not in steps["persist_result"]


async def test_new_path_holds_human_approval_and_rejects_a_publish_flag(spy_gateway: SpyGateway) -> None:
    runtime = build_reference_runtime()
    held = await dispatch(
        "user-1",
        {"image_path": IMAGE_PATH, "publish": True, "ownership_ok": True, "profile": {"business_name": "Shop"}},
        runtime=runtime,
    )
    denied = await dispatch(
        "user-1",
        {
            "image_path": IMAGE_PATH,
            "publish": True,
            "mode": "DAILY",
            "ownership_ok": True,
            "automation": {"auto_daily_publish": True},
            "profile": {"business_name": "Shop"},
        },
        runtime=runtime,
    )

    assert spy_gateway.requests == []
    assert held.status == ExecutionStatus.WAITING_APPROVAL
    assert held.error is not None and held.error.code == RuntimeErrorCode.APPROVAL_REQUIRED
    held_steps = {step.step_key: step.status for step in held.steps}
    assert held_steps["approval_policy"] == StepStatus.COMPLETED
    assert held_steps["instagram_agent_publication"] == StepStatus.WAITING
    assert held_steps["meta_verification"] == StepStatus.SKIPPED
    assert held_steps["persist_result"] == StepStatus.SKIPPED
    assert denied.status == ExecutionStatus.FAILED
    assert denied.error is not None and denied.error.code == RuntimeErrorCode.APPROVAL_DENIED
    denied_steps = {step.step_key: step.status for step in denied.steps}
    assert denied_steps["instagram_agent_publication"] == StepStatus.FAILED
    assert denied_steps["meta_verification"] == StepStatus.SKIPPED


async def test_publish_tool_names_never_reach_the_gateway(spy_gateway: SpyGateway) -> None:
    class PublishingModels(RecordingModels):
        async def complete(self, ctx: object, role: str, state: object) -> dict[str, object]:
            if role == "content_planning":
                return {"planned": True, "provider": "deepseek", "plan": {"tool": "publish_instagram_media"}}
            return await super().complete(ctx, role, state)

    runtime = build_reference_runtime(models=PublishingModels())
    record = await dispatch("user-1", _approved_payload(), runtime=runtime)

    assert spy_gateway.requests == []
    assert record.status == ExecutionStatus.FAILED
    assert record.error is not None and record.error.code == RuntimeErrorCode.APPROVAL_DENIED

    service = ReferenceToolService()
    for name in ("publish_instagram_media", "verify_publication", "media_publish", "publish"):
        with pytest.raises(RuntimeFailure) as blocked:
            await service.invoke(_ToolContext(frozenset({name})), name, {})
        assert blocked.value.code == RuntimeErrorCode.TOOL_NOT_GRANTED
    with pytest.raises(ToolNotAllowed):
        assert_tool_allowed("publish_instagram_media")
    with pytest.raises(RuntimeFailure) as unconfigured:
        await service.invoke(_ToolContext(frozenset({"get_business_profile"})), "get_business_profile", {})
    assert unconfigured.value.code == RuntimeErrorCode.STEP_FAILED


def test_definition_matches_the_studio_boundary_and_v1_routes() -> None:
    definition, version, workflow = instagram_marketing_definition()
    assert definition.display_name == DISPLAY_NAME
    assert definition.key == AGENT_KEY
    assert definition.owner.value == "system"
    assert definition.status.value == "active"
    assert definition.published_version_number == 1
    assert tuple(step.step_key for step in workflow.steps) == REFERENCE_STEP_KEYS
    assert set(version.tool_grants) == set(MCP_TOOL_ALLOWLIST)
    assert set(version.tool_grants).isdisjoint(INSTAGRAM_PUBLISHING_TOOLS)
    assert set(version.tool_grants).isdisjoint(ALLOWED_TOOL_SET)
    assert "publish_instagram_media" in ALLOWED_TOOL_SET
    assert "verify_publication" in ALLOWED_TOOL_SET
    assert MCP_TOOL_ALLOWLIST.isdisjoint(ALLOWED_TOOL_SET)

    routes = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    app = (ROOT / "api" / "app.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "scheduler" / "pipeline.py").read_text(encoding="utf-8")
    orchestrator = (ROOT / "agent" / "content_orchestrator.py").read_text(encoding="utf-8")
    creative = (ROOT / "ai" / "creative_model.py").read_text(encoding="utf-8")
    deepseek = (ROOT / "ai" / "llm" / "deepseek.py").read_text(encoding="utf-8")
    endpoints = (ROOT / "frontend" / "src" / "services" / "api" / "endpoints.ts").read_text(encoding="utf-8")
    assert '@router.post("/instagram/publish")' in routes
    assert "enqueue_publication" in routes
    assert "instagram_reference" not in routes
    assert "instagram_reference" not in app
    assert "bind_publication_gateway" in app
    assert "publish_generated_image" in pipeline
    assert "InstagramGraphClient" not in pipeline
    assert "media_publish" not in pipeline
    assert "published: Literal[False] = False" in orchestrator
    assert "InstagramGraphClient" not in orchestrator
    assert "InstagramGraphClient" not in creative
    assert "InstagramGraphClient" not in deepseek
    assert 'publish: "/api/v1/instagram/publish"' in endpoints

    assert callable(create_app)
    runtime = "\n".join(path.read_text(encoding="utf-8") for path in RUNTIME_ROOT.glob("*.py"))
    assert "enqueue_instagram_publication" not in runtime.lower()
    assert "instagramgraphclient" not in runtime.lower()
    importers: list[str] = []
    for path in sorted(REFERENCE_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "agent.instagram_tasks":
                importers.append(path.name)
    assert importers == ["handlers.py"]
    for path in REFERENCE_ROOT.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        lowered = source.lower()
        for needle in ("instagramgraphclient", "graph.facebook", "graph.instagram", "media_publish", "meta_access_token"):
            assert needle not in lowered, path.name
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "compile"}


def test_v1_trigger_is_not_replaced_by_the_reference_trigger() -> None:
    _definition, version, _workflow = instagram_marketing_definition()
    assert [item.trigger_key for item in version.trigger_bindings] == [TRIGGER_KEY]
    trigger = TriggerContext(
        trigger_key=TRIGGER_KEY,
        tenant_id="user-1",
        user_id="user-1",
        payload={"publish": True, "user_id": "other"},
        mode=ExecutionMode.PRODUCTION,
    )
    assert trigger.payload == {"publish": True}
    assert trigger.tenant_id == "user-1"
