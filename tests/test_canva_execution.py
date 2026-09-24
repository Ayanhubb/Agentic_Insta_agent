"""Canva capability execution inside content generation. No test contacts Canva or OpenAI."""

from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from agent.content_agent import ContentAgent
from agent.content_orchestrator import ContentOrchestrator
from backend.integrations.canva.adapter import CanvaAdapter
from backend.integrations.canva.catalog import DiscoveredTool, available_capabilities
from backend.integrations.canva.contracts import CanvaOwner
from models.content import ApprovalStatus, ContentMode, ContentStrategyRequest
from models.creative import ImageQAVerdict
from models.errors import AppError, ErrorCode
from tests.test_canva_mcp import (
    ACCESS,
    REFRESH,
    ScriptedCanvaSession,
    _adapter,
    _canva_settings,
    _tool,
    _users,
)
from tests.test_content_agent import FakeImageGenerator, FakeLLM, MemoryStore, jewelry_plan
from tests.test_content_orchestrator import FakeImages, FakeStore, ScriptedCreative, _mcp, _plan, _request

BRIEF = "Photorealistic Instagram still of the kundan necklace on linen in warm light, no text."


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (128, 128), (12, 24, 36)).save(buffer, format="PNG")
    return buffer.getvalue()


def _run(coro):
    return asyncio.run(coro)


def _db(tmp_path: Path):
    from db.session import create_engine_from_settings, init_db, session_factory

    settings = _canva_settings(tmp_path)
    engine = create_engine_from_settings(settings)
    init_db(engine)
    factory = session_factory(engine)
    _users(factory)
    return settings, factory


def _design_tools() -> list[DiscoveredTool]:
    return [
        _tool("search-brand-templates", ["query", "user_intent"], ["query"]),
        _tool("get-assets", ["asset_ids", "user_intent"], ["asset_ids"], extra={"asset_ids": {"type": "array"}}),
        _tool("generate-design", ["query", "design_type", "user_intent", "template_id"], ["query"]),
        _tool("create-design-from-candidate", ["job_id", "candidate_id", "user_intent"], ["job_id", "candidate_id"]),
        _tool("get-export-formats", ["design_id", "user_intent"], ["design_id"]),
        DiscoveredTool(
            name="export-design",
            input_schema={
                "type": "object",
                "properties": {
                    "design_id": {"type": "string"},
                    "user_intent": {"type": "string"},
                    "format": {"type": "object", "properties": {"type": {"type": "string"}}},
                },
                "required": ["design_id", "format"],
            },
        ),
    ]


def _design_results() -> dict:
    return {
        "search-brand-templates": {"brand_templates": [{"id": "tpl-1", "name": "Post"}]},
        "get-assets": {"assets": [{"id": "tpl-1", "name": "Post"}]},
        "generate-design": {
            "job": {
                "id": "job-1",
                "status": "success",
                "result": {"generated_designs": [{"candidate_id": "dg-1"}]},
            }
        },
        "create-design-from-candidate": {"design_summary": {"id": "DAF_1", "title": "Post", "page_count": 1}},
        "get-export-formats": {"formats": [{"type": "png"}]},
        "export-design": {"job": {"id": "export-1", "status": "success", "urls": ["https://export-download.canva.com/post.png"]}},
    }


def _ready(tmp_path: Path, results: dict | None = None):
    settings, factory = _db(tmp_path)
    session = ScriptedCanvaSession(_design_tools(), results or _design_results())
    adapter = _adapter(settings, session, factory)
    fetched: list[str] = []

    async def fetch(url: str) -> bytes:
        fetched.append(url)
        assert ACCESS not in url
        return _png()

    adapter.export_fetcher = fetch
    return adapter, session, fetched


def test_canva_connected_lists_authorized_templates(tmp_path: Path) -> None:
    adapter, session, _fetched = _ready(tmp_path)
    owner = CanvaOwner(tenant_id="user-a", user_id="user-a")

    payload = _run(adapter.query(user_id=owner.user_id))

    assert payload["connected"] is True
    assert payload["code"] is None
    assert payload["action"] == "apply_template"
    assert payload["asset_ids"] == ["tpl-1"]
    assert "create_design" in payload["capabilities"]
    assert "publish" not in payload["capabilities"]
    assert ACCESS not in json.dumps(payload)
    assert REFRESH not in json.dumps(payload)
    assert any(name == "search-brand-templates" and arguments.get("query") == "brand template" for name, arguments in session.calls)


def test_canva_not_connected_is_structured(tmp_path: Path) -> None:
    settings, factory = _db(tmp_path)
    opened: list[str] = []

    def opener(token: str):
        opened.append(token)
        raise AssertionError("session opened")

    adapter = CanvaAdapter(settings, session_factory=factory, session_opener=opener)
    payload = _run(adapter.query(user_id="user-a"))
    assert payload["code"] == ErrorCode.CANVA_NOT_CONNECTED.value
    assert payload["connected"] is False
    assert payload["asset_ids"] == []
    assert opened == []
    with pytest.raises(AppError) as caught:
        _run(adapter.produce(user_id="user-a", action="apply_template", brief=BRIEF, asset_ids=["tpl-1"]))
    assert caught.value.code == ErrorCode.CANVA_NOT_CONNECTED
    assert ACCESS not in str(caught.value)


def test_canva_invalid_token_is_structured(tmp_path: Path) -> None:
    settings, factory = _db(tmp_path)

    class Expired:
        async def list_tools(self):
            raise AppError(ErrorCode.CANVA_AUTHORIZATION_FAILED, "Canva authorization expired.", http_status=401)

        async def call_tool(self, name: str, arguments: dict):
            raise AssertionError(name)

        async def aclose(self):
            return None

    opened: list[str] = []

    def opener(token: str):
        opened.append(token)
        return Expired()

    adapter = CanvaAdapter(settings, session_factory=factory, session_opener=opener)
    adapter._store.save_tokens(
        CanvaOwner(tenant_id="user-a", user_id="user-a"),
        access_token=ACCESS,
        refresh_token=REFRESH,
        expires_in=3600,
    )
    payload = _run(adapter.query(user_id="user-a"))
    assert payload["code"] == ErrorCode.CANVA_AUTHORIZATION_FAILED.value
    assert payload["connected"] is False
    assert ACCESS not in json.dumps(payload)
    assert opened == [ACCESS]
    again = _run(adapter.query(user_id="user-a"))
    assert again["code"] == ErrorCode.CANVA_AUTHORIZATION_FAILED.value
    assert opened == [ACCESS]


def test_template_lookup_asset_retrieval_generation_and_export(tmp_path: Path) -> None:
    adapter, session, fetched = _ready(tmp_path)

    result = _run(
        adapter.produce(user_id="user-a", action="apply_template", brief=BRIEF, asset_ids=["tpl-1"])
    )

    names = [name for name, _arguments in session.calls]
    assert "search-brand-templates" in names
    assert "get-assets" in names
    assert "generate-design" in names
    assert "create-design-from-candidate" in names
    assert "export-design" in names
    assert "media_publish" not in names
    generate = next(arguments for name, arguments in session.calls if name == "generate-design")
    assert generate["query"] == BRIEF
    assert generate["template_id"] == "tpl-1"
    assert "access_token" not in generate
    assets = next(arguments for name, arguments in session.calls if name == "get-assets")
    assert assets["asset_ids"] == ["tpl-1"]
    export = next(arguments for name, arguments in session.calls if name == "export-design")
    assert export["format"] == {"type": "png"}
    assert export["design_id"] == "DAF_1"
    assert fetched == ["https://export-download.canva.com/post.png"]
    assert result["provider"] == "canva"
    assert result["image_bytes"].startswith(b"\x89PNG")
    assert set(result) == {"provider", "image_bytes", "mime_type"}
    assert ACCESS not in json.dumps(session.calls)


def test_foreign_asset_is_rejected(tmp_path: Path) -> None:
    results = _design_results()
    results["get-assets"] = {"assets": []}
    results["search-brand-templates"] = {"brand_templates": []}
    adapter, session, _fetched = _ready(tmp_path, results)

    with pytest.raises(AppError) as caught:
        _run(adapter.produce(user_id="user-a", action="use_reference", brief=BRIEF, asset_ids=["foreign-logo"]))
    assert caught.value.code == ErrorCode.PERMISSION_ERROR
    assert "generate-design" not in [name for name, _arguments in session.calls]


def test_other_tenant_cannot_use_the_connection(tmp_path: Path) -> None:
    adapter, session, _fetched = _ready(tmp_path)
    with pytest.raises(AppError) as caught:
        _run(adapter.produce(user_id="user-b", action="apply_template", brief=BRIEF, asset_ids=["tpl-1"]))
    assert caught.value.code == ErrorCode.CANVA_NOT_CONNECTED
    assert session.calls == []


def test_canva_refuses_publish_tools(tmp_path: Path) -> None:
    settings, factory = _db(tmp_path)
    tool = _tool("media_publish", ["image_id"])
    session = ScriptedCanvaSession([tool], {"media_publish": {"ok": True}})
    adapter = _adapter(settings, session, factory)
    assert "publish" not in available_capabilities({tool.name: tool})
    with pytest.raises(AppError) as caught:
        _run(adapter._call(session, tool, {"image_id": "img"}))
    assert caught.value.code == ErrorCode.CANVA_CAPABILITY_UNAVAILABLE
    assert session.calls == []
    observed = _run(adapter.apply(user_id="user-a", action="apply_template"))
    assert observed["applied"] is False
    assert observed["connected"] is True
    assert "publish" not in observed


def test_disabled_canva_does_not_connect_on_startup(tmp_path: Path) -> None:
    from backend.integrations.canva.adapter import get_canva_client

    settings = _canva_settings(tmp_path, canva_enabled=False)

    def opener(_token: str):
        raise AssertionError("session opened")

    assert get_canva_client(settings) is None
    adapter = CanvaAdapter(settings, session_opener=opener)
    payload = _run(adapter.query(user_id="user-1"))
    assert payload["code"] == ErrorCode.CANVA_DISABLED.value
    assert payload["connected"] is False


def test_export_url_outside_canva_is_rejected(tmp_path: Path) -> None:
    results = _design_results()
    results["export-design"] = {"job": {"id": "export-1", "status": "success", "urls": ["https://evil.example/post.png"]}}
    adapter, _session, fetched = _ready(tmp_path, results)
    with pytest.raises(AppError) as caught:
        _run(adapter.produce(user_id="user-a", action="apply_template", brief=BRIEF, asset_ids=["tpl-1"]))
    assert caught.value.code == ErrorCode.CANVA_UNAVAILABLE
    assert fetched == []


class _PlanCanva:
    def __init__(self, png: bytes, *, connected: bool = True, code: str | None = None) -> None:
        self.png = png
        self.connected = connected
        self.code = code
        self.produce_calls = 0

    async def query(self, *, user_id: str) -> dict:
        del user_id
        return {
            "queried": True,
            "connected": self.connected,
            "action": "apply_template" if self.connected else "none",
            "asset_ids": ["tpl-1"] if self.connected else [],
            "code": self.code,
        }

    async def produce(self, **kwargs) -> dict:
        self.produce_calls += 1
        assert kwargs["user_id"] == "user-1"
        assert kwargs["asset_ids"] == ["tpl-1"]
        assert ACCESS not in kwargs["brief"]
        return {"provider": "canva", "image_bytes": self.png, "mime_type": "image/png"}


class _ReviewCreative(ScriptedCreative):
    def __init__(self, plans) -> None:
        super().__init__(plans)
        self.reviewed: list[bytes] = []

    async def review_image(self, **kwargs) -> ImageQAVerdict:
        self.reviewed.append(kwargs["image_bytes"])
        return ImageQAVerdict(passed=True, issues=[])


@pytest.mark.asyncio
async def test_canva_output_goes_through_qa_and_approval(tmp_path: Path) -> None:
    png = _png()
    canva = _PlanCanva(png)
    images = FakeImages(tmp_path)
    creative = _ReviewCreative([_plan(canva_action="apply_template", asset_ids=["tpl-1"])])
    store = FakeStore()
    orchestrator = ContentOrchestrator(creative, images, _mcp(), store=store, canva=canva)
    result = await orchestrator.run(_request(use_canva=True))

    assert images.calls == 0
    assert canva.produce_calls == 1
    assert creative.reviewed == [png]
    assert result.qa.passed is True
    assert result.qa_attempts == 1
    assert result.published is False
    assert result.strategy.published is False
    assert result.strategy.approval_status == ApprovalStatus.PENDING_APPROVAL
    assert result.strategy.handoff.ready is False
    assert result.strategy.handoff.requires_instagram_agent is True
    assert store.images[0].provider == "canva"
    assert store.images[0].approval_status == "PENDING_APPROVAL"
    assert ACCESS not in json.dumps(creative.payloads[0])


@pytest.mark.asyncio
async def test_openai_is_used_when_the_plan_does_not_select_canva(tmp_path: Path) -> None:
    canva = _PlanCanva(_png())
    images = FakeImages(tmp_path)
    _result, _creative, images, _mcp_client, _store, canva = await _run_openai(tmp_path, canva, images)
    assert images.calls == 1
    assert canva.produce_calls == 0
    assert images.requests[0].prompt


async def _run_openai(tmp_path: Path, canva, images):
    orchestrator = ContentOrchestrator(
        ScriptedCreative([_plan()]),
        images,
        _mcp(),
        store=FakeStore(),
        canva=canva,
    )
    result = await orchestrator.run(_request())
    return result, None, images, None, None, canva


@pytest.mark.asyncio
async def test_requested_canva_without_a_connection_does_not_generate(tmp_path: Path) -> None:
    images = FakeImages(tmp_path)
    canva = _PlanCanva(_png(), connected=False, code="CANVA_NOT_CONNECTED")
    orchestrator = ContentOrchestrator(ScriptedCreative([_plan()]), images, _mcp(), canva=canva)
    with pytest.raises(AppError) as caught:
        await orchestrator.run(_request(use_canva=True))
    assert caught.value.code == ErrorCode.CANVA_NOT_CONNECTED
    assert images.calls == 0
    assert canva.produce_calls == 0


@pytest.mark.asyncio
async def test_content_agent_canva_asset_is_pending_and_cannot_publish() -> None:
    png = _png()
    canva = _PlanCanva(png)
    images = FakeImageGenerator()
    agent = ContentAgent(
        FakeLLM([jewelry_plan(canva_action="apply_template", asset_ids=["tpl-1"])]),
        images,
        context_store=MemoryStore(),
        canva=canva,
    )
    result = await agent.run(
        ContentStrategyRequest(
            user_id="user-1",
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show the kundan necklace on linen.",
        )
    )
    assert images.prompts == []
    assert canva.produce_calls == 1
    assert result.published is False
    assert result.approval_status == ApprovalStatus.PENDING_APPROVAL
    assert result.handoff.ready is False
    assert result.generated_image is not None
    assert result.generated_image.provider == "canva"
    assert result.generated_image.approval_status == ApprovalStatus.PENDING_APPROVAL.value


@pytest.mark.asyncio
async def test_content_agent_without_canva_action_uses_openai() -> None:
    canva = _PlanCanva(_png())
    images = FakeImageGenerator()
    agent = ContentAgent(
        FakeLLM([jewelry_plan()]),
        images,
        context_store=MemoryStore(),
        canva=canva,
    )
    result = await agent.run(
        ContentStrategyRequest(
            user_id="user-1",
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show the kundan necklace on linen.",
        )
    )
    assert images.prompts
    assert canva.produce_calls == 0
    assert result.generated_image is not None
    assert result.published is False


def test_canva_package_has_no_publisher() -> None:
    root = Path("backend/integrations/canva")
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "PublicationService" not in text
    assert "publish_generated_image" not in text
    assert "instagram_client" not in text
