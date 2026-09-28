"""Platform tool registry: grants, schema, tenant, secrets, audit, and the MCP adapter."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS, MCP_TOOL_ALLOWLIST
from backend.mcp.sources import RepositoryGateway
from backend.mcp.registry import object_schema
from backend.mcp.tenant_isolation import TenantContext, trusted_tenant
from backend.platform.tools import (
    AuditRequirements,
    Idempotency,
    ToolDefinition,
    ToolExecutionError,
    ToolExecutor,
    bind_agent,
    build_platform_executor,
    require_resource_tenant,
)
from backend.platform.tools.registry import PlatformToolRegistry
from db.schemas import BusinessProfileWrite
from db.session import create_engine_from_url, init_db, session_factory
from db.uow import Database

SECRET = "sk-proj-platform-registry-secret-value"
TOKEN = "EAAGmeta-platform-token-should-not-leak"
OTHER_TITLE = "SECRET_OTHER_NOTE"


def _assert_structured(exc: ToolExecutionError, code: str) -> None:
    body = exc.as_dict()
    assert body["ok"] is False
    assert set(body["error"]) == {"code", "message", "tool", "retryable"}
    assert body["error"]["code"] == code
    assert exc.code == code
    assert body["error"]["message"]
    assert SECRET not in json.dumps(body)
    assert TOKEN not in json.dumps(body)
    assert OTHER_TITLE not in json.dumps(body)


def _tool(
    *,
    name: str,
    handler: Any,
    classification: str,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    idempotent: bool | None = None,
) -> ToolDefinition:
    read = classification == "read"
    if output_schema is None:
        output_schema = (
            object_schema({"title": {"type": "string"}}, required=["title"])
            if read
            else object_schema({"saved": {"type": "boolean"}}, required=["saved"])
        )
    return ToolDefinition(
        name=name,
        version="1",
        description=f"{classification} {name}",
        input_schema=input_schema if input_schema is not None else object_schema(),
        output_schema=output_schema,
        permissions=frozenset({"read"} if read else {"write"}),
        tenant_scope="tenant",
        classification=classification,
        timeout_seconds=1,
        idempotency=Idempotency(idempotent if idempotent is not None else read),
        audit=AuditRequirements(),
        handler=handler,
        plane="service",
    )


class NoteBook:
    def __init__(self) -> None:
        self.rows = {
            "a-1": {"tenant_id": "tenant-a", "title": "Ayan note"},
            "b-1": {"tenant_id": "tenant-b", "title": OTHER_TITLE},
        }
        self.seen: list[dict[str, Any]] = []
        self.tenants: list[str] = []
        self.writes = 0

    async def get_note(self, tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        self.seen.append(dict(arguments))
        self.tenants.append(tenant.tenant_id)
        row = self.rows.get(str(arguments["note_id"]))
        owner = None if row is None else row["tenant_id"]
        require_resource_tenant(owner, tenant, tool="get_note")
        return {"title": row["title"]}

    async def save_note(self, tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        self.writes += 1
        self.tenants.append(tenant.tenant_id)
        self.seen.append(dict(arguments))
        return {"saved": True}


def _reader(book: NoteBook) -> tuple[PlatformToolRegistry, ToolExecutor, Any]:
    registry = PlatformToolRegistry()
    registry.register(
        _tool(
            name="get_note",
            handler=book.get_note,
            classification="read",
            input_schema=object_schema(
                {"note_id": {"type": "string", "minLength": 1, "maxLength": 64}},
                required=["note_id"],
            ),
        )
    )
    registry.register(
        _tool(
            name="save_note",
            handler=book.save_note,
            classification="write",
            idempotent=False,
            input_schema=object_schema({"title": {"type": "string", "minLength": 1}}, required=["title"]),
        )
    )
    binding = bind_agent(
        registry,
        agent_id="content_reader",
        tool_names={"get_note"},
        permissions={"read"},
    )
    return registry, ToolExecutor(registry), binding


async def test_unknown_tool() -> None:
    book = NoteBook()
    _registry, executor, binding = _reader(book)
    with pytest.raises(ToolExecutionError) as caught:
        await executor.execute(binding, "invented_tool", {}, trusted_tenant("tenant-a"))
    _assert_structured(caught.value, "UNKNOWN_TOOL")
    assert book.seen == []
    assert executor.audit.events[-1].status == "error"
    assert executor.audit.events[-1].error_code == "UNKNOWN_TOOL"


async def test_invalid_schema() -> None:
    book = NoteBook()
    _registry, executor, binding = _reader(book)
    tenant = trusted_tenant("tenant-a")
    for arguments in ({}, {"note_id": 5}, {"note_id": "a-1", "extra": True}, ["a-1"]):
        with pytest.raises(ToolExecutionError) as caught:
            await executor.execute(binding, "get_note", arguments, tenant)  # type: ignore[arg-type]
        _assert_structured(caught.value, "MALFORMED_ARGUMENTS")
    assert book.seen == []


async def test_cross_tenant_access() -> None:
    book = NoteBook()
    _registry, executor, binding = _reader(book)
    tenant_a = trusted_tenant("tenant-a")

    own = await executor.execute(
        binding,
        "get_note",
        {"note_id": "a-1", "tenant_id": "tenant-b", "user_id": "tenant-b"},
        tenant_a,
    )
    assert own.data["title"] == "Ayan note"
    assert own.data["tenant_id"] == "tenant-a"
    assert book.tenants == ["tenant-a"]
    assert "tenant_id" not in book.seen[0]
    assert "user_id" not in book.seen[0]
    assert executor.audit.events[-1].ignored_untrusted_tenant is True
    assert "tenant-b" not in json.dumps(executor.audit.events[-1].arguments)

    with pytest.raises(ToolExecutionError) as foreign:
        await executor.execute(binding, "get_note", {"note_id": "b-1"}, tenant_a)
    _assert_structured(foreign.value, "CROSS_TENANT_DENIED")
    assert OTHER_TITLE not in str(foreign.value)

    async def leak(_tenant: TenantContext, _arguments: dict[str, Any]) -> dict[str, Any]:
        return {"title": OTHER_TITLE, "tenant_id": "tenant-b", "access_token": TOKEN}

    registry = PlatformToolRegistry()
    registry.register(
        _tool(
            name="get_foreign",
            handler=leak,
            classification="read",
            output_schema={"type": "object", "additionalProperties": True},
        )
    )
    leak_binding = bind_agent(registry, agent_id="content_reader", tool_names={"get_foreign"}, permissions={"read"})
    leak_executor = ToolExecutor(registry)
    with pytest.raises(ToolExecutionError) as leaked:
        await leak_executor.execute(leak_binding, "get_foreign", {}, tenant_a)
    _assert_structured(leaked.value, "CROSS_TENANT_DENIED")
    assert OTHER_TITLE not in json.dumps(leak_executor.audit.events[-1].as_dict())
    assert TOKEN not in json.dumps(leak_executor.audit.events[-1].as_dict())


async def test_unauthorized_mutation() -> None:
    book = NoteBook()
    registry, executor, binding = _reader(book)
    tenant = trusted_tenant("tenant-a")
    with pytest.raises(ToolExecutionError) as denied:
        await executor.execute(binding, "save_note", {"title": "Hello"}, tenant)
    _assert_structured(denied.value, "UNAUTHORIZED_MUTATION")
    assert book.writes == 0

    with pytest.raises(ToolExecutionError) as publish:
        await executor.execute(binding, "publish_instagram_media", {"title": "Hello"}, tenant)
    _assert_structured(publish.value, "TOOL_NOT_ALLOWED")
    assert book.writes == 0

    writer = bind_agent(
        registry,
        agent_id="notes_writer",
        tool_names={"save_note"},
        permissions={"write"},
    )
    saved = await executor.execute(writer, "save_note", {"title": "Hello", "tenant_id": "tenant-b"}, tenant)
    assert saved.classification == "write"
    assert saved.data["saved"] is True
    assert saved.data["tenant_id"] == "tenant-a"
    assert book.writes == 1
    assert "tenant_id" not in book.seen[-1]


async def test_secret_leakage(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="backend.platform.tools")

    async def reveal(_tenant: TenantContext, _arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": "hello",
            "access_token": TOKEN,
            "api_key": SECRET,
            "blurb": f"token={TOKEN} sk-proj-platform-registry-secret-value",
            "nested": {"refresh_token": TOKEN, "note": SECRET},
        }

    registry = PlatformToolRegistry()
    registry.register(
        _tool(
            name="get_secret_note",
            handler=reveal,
            classification="read",
            output_schema={"type": "object", "additionalProperties": True},
        )
    )
    binding = bind_agent(registry, agent_id="content_reader", tool_names={"get_secret_note"}, permissions={"read"})
    executor = ToolExecutor(registry)
    result = await executor.execute(binding, "get_secret_note", {}, trusted_tenant("tenant-a"))
    rendered = json.dumps(result.data)
    assert SECRET not in rendered
    assert TOKEN not in rendered
    assert "access_token" not in result.data
    assert "api_key" not in result.data
    assert "refresh_token" not in json.dumps(result.data.get("nested"))

    with pytest.raises(ToolExecutionError) as caught:
        await executor.execute(
            binding,
            "get_secret_note",
            {"access_token": TOKEN, "api_key": SECRET},
            trusted_tenant("tenant-a"),
        )
    _assert_structured(caught.value, "MALFORMED_ARGUMENTS")

    logged = caplog.text + json.dumps([event.as_dict() for event in executor.audit.events])
    assert SECRET not in logged
    assert TOKEN not in logged
    assert "platform.tool.execute" in caplog.text


async def test_audit_events(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="backend.platform.tools")
    book = NoteBook()
    _registry, executor, binding = _reader(book)
    tenant = trusted_tenant("tenant-a")
    await executor.execute(binding, "get_note", {"note_id": "a-1"}, tenant)
    with pytest.raises(ToolExecutionError):
        await executor.execute(binding, "missing_tool", {}, tenant)

    assert len(executor.audit.events) == 2
    ok, failed = executor.audit.events
    assert ok.event == "platform.tool.execute"
    assert ok.status == "ok"
    assert ok.error_code is None
    assert ok.tool == "get_note"
    assert ok.version == "1"
    assert ok.agent_id == "content_reader"
    assert ok.tenant_id == "tenant-a"
    assert ok.classification == "read"
    assert ok.idempotent is True
    assert "title" in ok.result_keys
    assert failed.status == "error"
    assert failed.error_code == "UNKNOWN_TOOL"
    assert failed.tenant_id == "tenant-a"
    assert failed.result_keys == ()
    assert "platform.tool.execute" in caplog.text


async def test_valid_tool_execution() -> None:
    book = NoteBook()
    registry, executor, binding = _reader(book)
    result = await executor.execute(binding, "get_note", {"note_id": "a-1"}, trusted_tenant("tenant-a"))
    assert result.name == "get_note"
    assert result.version == "1"
    assert result.classification == "read"
    assert result.data == {"title": "Ayan note", "tenant_id": "tenant-a"}
    entry = registry.catalog()[0]
    assert "handler" not in entry
    assert entry["classification"] in {"read", "write"}
    assert entry["audit"]["required"] is True
    assert entry["tenant_scope"] == "tenant"
    assert executor.audit.events[-1].status == "ok"


def test_publishing_tools_are_rejected() -> None:
    async def _unused(_tenant: TenantContext, _arguments: dict[str, Any]) -> dict[str, Any]:
        return {}

    registry = PlatformToolRegistry()
    for name in sorted(INSTAGRAM_PUBLISHING_TOOLS):
        with pytest.raises(ToolExecutionError) as caught:
            registry.register(
                ToolDefinition(
                    name=name,
                    version="1",
                    description="publish",
                    input_schema=object_schema(),
                    output_schema=object_schema(),
                    permissions=frozenset({"read"}),
                    tenant_scope="tenant",
                    classification="read",
                    timeout_seconds=1,
                    idempotency=Idempotency(True),
                    audit=AuditRequirements(),
                    handler=_unused,
                    plane="service",
                )
            )
        _assert_structured(caught.value, "TOOL_NOT_ALLOWED")
        with pytest.raises(ToolExecutionError) as bound:
            bind_agent(registry, agent_id="instagram_marketing", tool_names={name}, permissions={"read", "write"})
        _assert_structured(bound.value, "TOOL_NOT_ALLOWED")


async def test_mcp_adapter_keeps_read_tools(tmp_path: Any) -> None:
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'registry.db').as_posix()}")
    init_db(engine)
    factory = session_factory(engine)
    session = factory()
    db = Database(session)
    user = db.users.create(email="a@example.com", password_hash="hashed-a")
    other = db.users.create(email="b@example.com", password_hash="hashed-b")
    db.business.upsert(user.id, BusinessProfileWrite(business_name="Ayan Jewels", products=["Gold ring"]))
    db.business.upsert(other.id, BusinessProfileWrite(business_name="Other Shop"))
    db.commit()
    user_id = user.id
    session.close()

    executor = build_platform_executor(RepositoryGateway(factory), timeout_seconds=2.0)
    catalog = executor.registry.catalog()
    names = {item["name"] for item in catalog}
    assert names == set(MCP_TOOL_ALLOWLIST)
    assert INSTAGRAM_PUBLISHING_TOOLS.isdisjoint(names)
    for item in catalog:
        assert item["classification"] == "read"
        assert item["plane"] == "mcp_read"
        assert item["tenant_scope"] == "tenant"
        assert item["permissions"] == ["read"]
        assert item["idempotency"] == {"idempotent": True}
        assert item["audit"]["required"] is True
        assert item["timeout_seconds"] == 2.0
        assert "handler" not in item
        assert item["input_schema"]["additionalProperties"] is False

    binding = bind_agent(
        executor.registry,
        agent_id="instagram_marketing",
        tool_names=names,
        permissions={"read"},
    )
    profile = await executor.execute(
        binding,
        "get_business_profile",
        {"tenant_id": other.id, "user_id": other.id},
        trusted_tenant(user_id),
    )
    assert profile.data["tenant_id"] == user_id
    assert profile.data["profile"]["business_name"] == "Ayan Jewels"
    assert "Other Shop" not in json.dumps(profile.data)
    assert profile.classification == "read"
    assert profile.version == "1"

    rules = await executor.execute(binding, "get_content_rules", {}, trusted_tenant(user_id))
    assert rules.data["publishing"]["available"] is False
    assert rules.data["publishing"]["user_prompt_auto_publish"] is False
    assert "publish_instagram_media" not in json.dumps(rules.data)
    assert TOKEN not in json.dumps(rules.data)
