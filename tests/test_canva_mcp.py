"""Official Canva MCP adapter tests. No test contacts Canva or OpenAI."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from ai.image_generator import get_image_generation_provider
from ai.llm.deepseek import DeepSeekLLMProvider
from ai.llm_client import get_llm_provider
from ai.openai_image_generator import OpenAIImageGenerationProvider
from api.app import create_app
from backend.integrations.canva.adapter import CanvaAdapter, get_canva_client
from backend.integrations.canva.catalog import DiscoveredTool
from backend.integrations.canva.contracts import CanvaOwner
from backend.integrations.canva.mcp_client import CanvaMcpClient
from db.models import CanvaConnection, CanvaOAuthState, User
from db.session import create_engine_from_settings, init_db, session_factory
from models.errors import AppError, ErrorCode
from services.logging import redact_text
from tests.helpers import DummyInstagramClient, auth_client_headers, test_settings

ACCESS = "canva-user-access-token-SHOULD-NOT-LEAK"
REFRESH = "canva-user-refresh-token-SHOULD-NOT-LEAK"
CLIENT_SECRET = "canva-client-secret-SHOULD-NOT-LEAK"
OFFICIAL_MCP = "https://mcp.canva.com/mcp"


def _canva_settings(tmp_path, **overrides):
    values = {
        "canva_enabled": True,
        "canva_client_id": "canva-client-id",
        "canva_client_secret": CLIENT_SECRET,
        "canva_redirect_uri": "http://127.0.0.1:8000/api/v1/integrations/canva/callback",
    }
    values.update(overrides)
    return test_settings(tmp_path, **values)


def _users(factory) -> None:
    session = factory()
    session.add(User(id="user-a", email="a@example.com", password_hash="hashed"))
    session.add(User(id="user-b", email="b@example.com", password_hash="hashed"))
    session.commit()
    session.close()


def _tool(name: str, properties: list[str], required: list[str] | None = None, extra: dict | None = None) -> DiscoveredTool:
    props = {key: {"type": "string"} for key in properties}
    if extra:
        props.update(extra)
    return DiscoveredTool(
        name=name,
        input_schema={"type": "object", "properties": props, "required": required or []},
    )


class FakeHttp:
    """Test double for the MCP HTTP call. Avoids constructing httpx.AsyncClient."""

    def __init__(self, handler) -> None:
        self._handler = handler

    async def post(self, url: str, headers: dict | None = None, json: dict | None = None, timeout: float | None = None) -> httpx.Response:
        del timeout
        request = httpx.Request("POST", url, headers=headers or {}, json=json)
        result = self._handler(request)
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self) -> None:
        return None


class ScriptedCanvaSession:
    def __init__(self, tools: list[DiscoveredTool], results: dict) -> None:
        self._tools = tools
        self._results = results
        self.calls: list[tuple[str, dict]] = []
        self.tokens: list[str] = []

    async def list_tools(self) -> list[DiscoveredTool]:
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        known = {tool.name for tool in self._tools}
        if name not in known:
            raise AssertionError(f"undiscovered tool called: {name}")
        self.calls.append((name, arguments))
        result = self._results[name]
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(arguments)
        return result

    async def aclose(self) -> None:
        return None


def _adapter(settings, session, factory):
    def opener(token: str):
        session.tokens.append(token)
        return session

    adapter = CanvaAdapter(settings, session_factory=factory, session_opener=opener)
    adapter._store.save_tokens(
        CanvaOwner(tenant_id="user-a", user_id="user-a"),
        access_token=ACCESS,
        refresh_token=REFRESH,
        expires_in=3600,
    )
    return adapter


def test_canva_disabled_keeps_image_generation(tmp_path) -> None:
    settings = test_settings(
        tmp_path,
        canva_enabled=False,
        openai_api_key="sk-test-not-a-real-openai-key-xxxxx",
        llm_model="configured-llm",
        image_model="configured-image",
    )
    assert get_canva_client(settings) is None
    provider = get_image_generation_provider(settings)
    assert isinstance(provider, OpenAIImageGenerationProvider)
    deepseek_settings = test_settings(
        tmp_path / "deepseek",
        canva_enabled=False,
        llm_provider="deepseek",
        deepseek_api_key="sk-deepseek-test-not-real",
        deepseek_model="deepseek-flash",
        openai_api_key="sk-test-not-a-real-openai-key-xxxxx",
        image_model="configured-image",
    )
    assert isinstance(get_llm_provider(deepseek_settings), DeepSeekLLMProvider)
    assert isinstance(get_image_generation_provider(deepseek_settings), OpenAIImageGenerationProvider)
    assert get_canva_client(deepseek_settings) is None
    app = create_app(settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    assert isinstance(app.state.image_provider, OpenAIImageGenerationProvider)
    assert app.state.canva_client is None

    def opener(_token: str):
        raise AssertionError("Canva session opened while disabled")

    app.state.canva.session_opener = opener
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        headers = auth_client_headers(client)
        status = client.get("/api/v1/integrations/canva/status", headers=headers)
        assert status.status_code == 200
        body = status.json()
        assert body["enabled"] is False
        assert body["connected"] is False
        assert body["capabilities"] == []
        blocked = client.post("/api/v1/integrations/canva/connect", headers=headers)
        assert blocked.status_code == 503
        assert blocked.json()["error"]["code"] == "CANVA_DISABLED"
        assert CLIENT_SECRET not in status.text
        assert "sk-" not in client.get("/api/v1/health").text


def test_canva_connected_is_per_user_and_hides_tokens(tmp_path) -> None:
    settings = _canva_settings(tmp_path)
    app = create_app(settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    seen: dict[str, str] = {}

    async def exchange(form: dict[str, str]) -> dict[str, str | int]:
        seen["grant"] = form["grant_type"]
        assert form["client_secret"] == CLIENT_SECRET
        return {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600}

    tools = [_tool("search-designs", ["query", "user_intent"])]
    session = ScriptedCanvaSession(tools, {})
    app.state.canva.token_exchange = exchange
    app.state.canva.session_opener = lambda token: session
    with TestClient(app) as client:
        headers_a = auth_client_headers(client, email="a@example.com")
        started = client.post("/api/v1/integrations/canva/connect", headers=headers_a)
        assert started.status_code == 200
        authorize = started.json()["authorization_url"]
        assert authorize.startswith("https://mcp.canva.com/authorize?")
        assert CLIENT_SECRET not in authorize
        assert "code_verifier" not in authorize
        query = parse_qs(urlparse(authorize).query)
        assert query["code_challenge_method"] == ["S256"]
        state = query["state"][0]
        with app.state.session_factory() as db:
            row = db.get(CanvaOAuthState, state)
            assert row is not None
            assert ACCESS not in row.code_verifier_encrypted
            assert CLIENT_SECRET not in row.code_verifier_encrypted
        headers_b = auth_client_headers(client, email="b@example.com")
        stolen = client.get(
            f"/api/v1/integrations/canva/callback?code=auth-code&state={state}",
            headers=headers_b,
        )
        assert stolen.status_code == 401
        assert ACCESS not in stolen.text
        restarted = client.post("/api/v1/integrations/canva/connect", headers=headers_a)
        state = parse_qs(urlparse(restarted.json()["authorization_url"]).query)["state"][0]
        callback = client.get(
            f"/api/v1/integrations/canva/callback?code=auth-code&state={state}",
            headers=headers_a,
        )
        assert callback.status_code == 200
        assert "Canva is connected" in callback.text
        assert ACCESS not in callback.text
        assert REFRESH not in callback.text
        assert CLIENT_SECRET not in callback.text
        account = client.get("/api/v1/integrations/canva/status", headers=headers_a)
        assert account.status_code == 200
        assert account.json()["connected"] is True
        assert account.json()["capabilities"] == ["search_designs"]
        assert account.json()["tools"] == ["search-designs"]
        assert ACCESS not in account.text
        other = client.get("/api/v1/integrations/canva/status", headers=headers_b)
        assert other.json()["connected"] is False
        with app.state.session_factory() as db:
            stored = db.query(CanvaConnection).one()
            assert ACCESS not in stored.access_token_encrypted
            assert REFRESH not in (stored.refresh_token_encrypted or "")
    assert seen["grant"] == "authorization_code"
    assert session.tokens == []


def test_canva_tool_discovery_uses_official_mcp_response() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OFFICIAL_MCP
        body = json.loads(request.content.decode())
        calls.append(body["method"])
        assert request.headers["authorization"] == f"Bearer {ACCESS}"
        if body["method"] == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "canva"}},
                },
                headers={"mcp-session-id": "sess-1"},
            )
        if body["method"] == "notifications/initialized":
            assert request.headers["mcp-session-id"] == "sess-1"
            return httpx.Response(202)
        if body["method"] == "tools/list":
            payload = {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "tools": [
                        {
                            "name": "search-designs",
                            "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                        }
                    ]
                },
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream", "mcp-session-id": "sess-1"},
                text=f"data: {json.dumps(payload)}\n\n",
            )
        raise AssertionError(body["method"])

    http = FakeHttp(handler)
    client = CanvaMcpClient(
        endpoint=OFFICIAL_MCP,
        access_token=ACCESS,
        timeout_seconds=5,
        http_client=http,
    )

    async def run():
        tools = await client.list_tools()
        with pytest.raises(AppError) as exc:
            await client.call_tool("generate-design", {"query": "ignored"})
        await client.aclose()
        return tools, exc.value

    import asyncio

    tools, error = asyncio.run(run())
    assert [tool.name for tool in tools] == ["search-designs"]
    assert error.code == ErrorCode.CANVA_CAPABILITY_UNAVAILABLE
    assert "tools/call" not in calls
    assert calls == ["initialize", "notifications/initialized", "tools/list"]


def test_canva_design_creation_uses_only_discovered_tools(tmp_path) -> None:
    settings = _canva_settings(tmp_path)
    engine = create_engine_from_settings(settings)
    init_db(engine)
    factory = session_factory(engine)
    _users(factory)
    tools = [
        _tool("generate-design", ["query", "design_type", "user_intent", "brand_kit_id"], ["query"]),
        _tool("create-design-from-candidate", ["job_id", "candidate_id", "user_intent"], ["job_id", "candidate_id"]),
    ]
    session = ScriptedCanvaSession(
        tools,
        {
            "generate-design": {
                "job": {
                    "id": "job-1",
                    "status": "success",
                    "result": {
                        "generated_designs": [
                            {"candidate_id": "dg-1", "url": "https://www.canva.com/d/1", "thumbnails": [{"url": "https://design.canva.ai/1"}]}
                        ]
                    },
                }
            },
            "create-design-from-candidate": {
                "design_summary": {
                    "id": "DAF_1",
                    "title": "Summer sale",
                    "urls": {"edit_url": "https://www.canva.com/d/edit", "view_url": "https://www.canva.com/d/view"},
                    "page_count": 1,
                }
            },
        },
    )
    adapter = _adapter(settings, session, factory)
    owner = CanvaOwner(tenant_id="user-a", user_id="user-a")

    async def run():
        created = await adapter.create_design(owner, query="Instagram post for a summer sale", design_type="instagram_post")
        saved = await adapter.create_design(owner, query="ignored", candidate_id="dg-1", job_id="job-1")
        return created, saved

    import asyncio

    created, saved = asyncio.run(run())
    assert created.status == "candidates_ready"
    assert created.job_id == "job-1"
    assert created.candidates[0].candidate_id == "dg-1"
    assert saved.status == "created"
    assert saved.design is not None
    assert saved.design.id == "DAF_1"
    assert [name for name, _ in session.calls] == ["generate-design", "create-design-from-candidate"]
    assert session.calls[0][1]["query"] == "Instagram post for a summer sale"
    assert "access_token" not in session.calls[0][1]
    other = CanvaOwner(tenant_id="user-b", user_id="user-b")
    with pytest.raises(AppError) as exc:
        asyncio.run(adapter.search_designs(other, query="sale"))
    assert exc.value.code == ErrorCode.CANVA_NOT_CONNECTED


def test_canva_export_uses_only_discovered_tools(tmp_path) -> None:
    settings = _canva_settings(tmp_path)
    engine = create_engine_from_settings(settings)
    init_db(engine)
    factory = session_factory(engine)
    _users(factory)
    tools = [
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
    session = ScriptedCanvaSession(
        tools,
        {
            "get-export-formats": {"formats": [{"type": "pdf"}, {"type": "png"}]},
            "export-design": {"job": {"id": "export-1", "status": "success", "urls": ["https://export-download.canva.com/EXAMPLE.pdf"]}},
        },
    )
    adapter = _adapter(settings, session, factory)

    import asyncio

    result = asyncio.run(
        adapter.export_design(CanvaOwner(tenant_id="user-a", user_id="user-a"), design_id="DAF_1", format="pdf")
    )
    assert result.status == "success"
    assert result.download_urls == ["https://export-download.canva.com/EXAMPLE.pdf"]
    assert [name for name, _ in session.calls] == ["get-export-formats", "export-design"]
    assert session.calls[1][1]["format"] == {"type": "pdf"}
    assert ACCESS not in json.dumps(session.calls)


def test_canva_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("deadline exceeded")

    http = FakeHttp(handler)
    client = CanvaMcpClient(endpoint=OFFICIAL_MCP, access_token=ACCESS, timeout_seconds=1, http_client=http)

    async def run():
        with pytest.raises(AppError) as exc:
            await client.list_tools()
        await client.aclose()
        return exc.value

    import asyncio

    error = asyncio.run(run())
    assert error.code == ErrorCode.CANVA_TIMEOUT
    assert error.retryable is True
    assert ACCESS not in str(error)


def test_canva_authorization_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"access_token": ACCESS, "error": "invalid_token"})

    http = FakeHttp(handler)
    client = CanvaMcpClient(endpoint=OFFICIAL_MCP, access_token=ACCESS, timeout_seconds=1, http_client=http)

    async def run():
        with pytest.raises(AppError) as exc:
            await client.list_tools()
        await client.aclose()
        return exc.value

    import asyncio

    error = asyncio.run(run())
    assert error.code == ErrorCode.CANVA_AUTHORIZATION_FAILED
    assert ACCESS not in str(error)
    assert "invalid_token" not in str(error)


def test_canva_rejects_unofficial_endpoint() -> None:
    with pytest.raises(AppError) as exc:
        CanvaMcpClient(endpoint="https://evil.example/mcp", access_token=ACCESS, timeout_seconds=1)
    assert exc.value.code == ErrorCode.CONFIGURATION_ERROR


def test_canva_logs_redact_secrets() -> None:
    redacted = redact_text(
        f"CANVA_CLIENT_SECRET={CLIENT_SECRET} Authorization: Bearer {ACCESS} code_verifier={REFRESH}"
    )
    assert CLIENT_SECRET not in redacted
    assert ACCESS not in redacted
    assert REFRESH not in redacted
