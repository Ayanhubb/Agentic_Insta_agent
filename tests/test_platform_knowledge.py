"""Platform knowledge: optional sources, versions, and tenant isolation."""

from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.mcp.tenant_isolation import trusted_tenant
from backend.platform.assets import AssetRole, AssetStore, bind_assets
from backend.platform.knowledge import (
    KnowledgeAccessError,
    KnowledgeSource,
    KnowledgeStore,
    McpContextAssembler,
    PlatformKnowledgeService,
    bind_knowledge,
    instagram_marketing_knowledge,
)

TENANT_A = "user-a"
TENANT_B = "user-b"
SECRET_NOTE = "TENANT_A_SECRET_NOTE"


def _document(store: KnowledgeStore, tenant: str = TENANT_A):
    return store.put(
        tenant,
        source=KnowledgeSource.DOCUMENT,
        title="Desk notes",
        body={"text": SECRET_NOTE, "access_token": "should-not-stick", "storage_key": "assets/secret"},
    )


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), (12, 40, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_instagram_knowledge_does_not_include_every_source() -> None:
    instagram = instagram_marketing_knowledge()
    assert KnowledgeSource.BUSINESS in instagram.sources
    assert KnowledgeSource.DOCUMENT not in instagram.sources
    operations = bind_knowledge("operations_assistant", 1, ["business", "document"])
    assert operations.sources == frozenset({KnowledgeSource.BUSINESS, KnowledgeSource.DOCUMENT})
    assert bind_knowledge("echo", 1, []).sources == frozenset()


def test_ungranted_source_is_not_loaded() -> None:
    store = KnowledgeStore()
    _document(store)
    service = PlatformKnowledgeService(instagram_marketing_knowledge(), store)
    with pytest.raises(KnowledgeAccessError) as exc:
        asyncio.run(service.load_for_tenant(TENANT_A, "document"))
    assert exc.value.code == "NOT_GRANTED"
    assert SECRET_NOTE not in json.dumps(exc.value.as_dict())
    empty = PlatformKnowledgeService(bind_knowledge("echo", 1, []), store)
    with pytest.raises(KnowledgeAccessError) as denied:
        asyncio.run(empty.load_for_tenant(TENANT_A, "business"))
    assert denied.value.code == "NOT_GRANTED"


def test_documents_and_business_snapshots_are_versioned_per_tenant() -> None:
    store = KnowledgeStore()
    first = _document(store)
    assert "access_token" not in first.body
    assert "storage_key" not in first.body
    assert first.body["text"] == SECRET_NOTE
    second = store.revise(TENANT_A, first.id, {"text": "revised note"})
    assert second.version == 2
    assert store.get(TENANT_A, first.id, version=1).body["text"] == SECRET_NOTE
    assert store.get(TENANT_A, first.id).version == 2
    assert store.get(TENANT_B, first.id) is None
    assert store.get(TENANT_B, first.id, version=1) is None
    with pytest.raises(KnowledgeAccessError) as exc:
        store.revise(TENANT_B, first.id, {"text": "nope"})
    assert exc.value.code == "NOT_FOUND"
    store.delete(TENANT_A, first.id)
    assert store.get(TENANT_A, first.id, version=1) is None
    assert store.get(TENANT_A, first.id, version=2) is None
    audit = json.dumps([event.as_dict() for event in store.audit.events])
    assert SECRET_NOTE not in audit
    assert "storage_key" not in audit
    with pytest.raises(KnowledgeAccessError) as live:
        store.put(TENANT_A, source=KnowledgeSource.FESTIVAL, title="Dates", body={"name": "Diwali"})
    assert live.value.code == "NOT_VERSIONED"
    with pytest.raises(KnowledgeAccessError):
        store.put(TENANT_A, source=KnowledgeSource.DOCUMENT, title="../notes", body={"text": "x"})


def test_structured_business_context_stays_on_the_owning_tenant() -> None:
    store = KnowledgeStore()
    record = store.put(
        TENANT_A,
        source=KnowledgeSource.BUSINESS,
        title="Shop",
        body={"business_name": "North Shop", "city": "Kolkata"},
    )
    assert store.latest(TENANT_B, KnowledgeSource.BUSINESS) is None
    assert store.latest(TENANT_A, KnowledgeSource.BUSINESS).id == record.id
    service = PlatformKnowledgeService(bind_knowledge("operations_assistant", 1, ["business", "document"]), store)
    loaded = asyncio.run(service.load_for_tenant(TENANT_A, "business"))
    assert loaded["found"] is True
    assert loaded["record"]["body"]["business_name"] == "North Shop"
    hidden = asyncio.run(service.load_for_tenant(TENANT_B, "business"))
    assert hidden == {"found": False, "source": "business"}


def test_live_loader_uses_the_execution_tenant(tmp_path: Path) -> None:
    del tmp_path
    calls: list[tuple[str, str]] = []

    async def invoke(tool: str, arguments: dict, tenant) -> dict:
        calls.append((tool, tenant.tenant_id))
        if tool == "explode":
            raise RuntimeError("assets/user-b/secret.png")
        return {"found": True, "profile": {"business_name": "North Shop"}, "tenant_id": "ignored"}

    service = PlatformKnowledgeService(instagram_marketing_knowledge(), KnowledgeStore(), invoke)
    loaded = asyncio.run(service.load_for_tenant(TENANT_A, "business"))
    assert calls == [("get_business_profile", TENANT_A)]
    assert loaded["live"]["profile"]["business_name"] == "North Shop"
    assert "tenant_id" not in loaded["live"]

    async def explode(tool: str, arguments: dict, tenant) -> dict:
        del tool, arguments, tenant
        raise RuntimeError("assets/user-b/secret.png")

    failing = PlatformKnowledgeService(instagram_marketing_knowledge(), KnowledgeStore(), explode)
    missed = asyncio.run(failing.load_for_tenant(TENANT_A, "brand"))
    assert missed["found"] is False
    assert "secret.png" not in json.dumps(missed)


def test_mcp_context_reads_only_granted_sources_and_roles() -> None:
    knowledge = KnowledgeStore()
    _document(knowledge)
    assets = AssetStore()
    assets.put(
        TENANT_A,
        filename="BlueLogo.png",
        data=_png(),
        role=AssetRole.COMPANY_LOGO,
        claimed_mime="image/png",
    )
    calls: list[tuple[str, dict, str]] = []

    async def invoke(tool: str, arguments: dict, tenant) -> dict:
        calls.append((tool, dict(arguments), tenant.tenant_id))
        return {
            "found": True,
            "access_token": "EAAGshould-not-leak",
            "storage_key": "assets/user-a/brand/secret.png",
            "profile": {"business_name": "North Shop"},
        }

    assembler = McpContextAssembler(invoke, knowledge=knowledge, assets=assets)
    packet = asyncio.run(
        assembler.build(
            trusted_tenant(TENANT_A),
            bind_knowledge("operations_assistant", 1, ["business", "document"]),
            bind_assets("operations_assistant", 1, [AssetRole.DOCUMENT]),
            arguments={"tenant_id": TENANT_B, "user_id": TENANT_B, "festival_name": "Diwali"},
        )
    )
    tools = [item[0] for item in calls]
    assert tools == ["get_business_profile"]
    assert calls[0][1] == {}
    assert calls[0][2] == TENANT_A
    assert "get_company_logo" not in tools
    assert "get_upcoming_festivals" not in tools
    assert "festival" not in packet["knowledge"]
    assert packet["knowledge"]["document"]["snapshot"]["found"] is True
    assert packet["knowledge"]["document"]["snapshot"]["record"]["body"]["text"] == SECRET_NOTE
    dumped = json.dumps(packet)
    assert "access_token" not in dumped
    assert "storage_key" not in dumped
    assert "BlueLogo" not in dumped
    assert "EAAGshould-not-leak" not in dumped
    foreign = asyncio.run(
        assembler.build(
            trusted_tenant(TENANT_B),
            bind_knowledge("operations_assistant", 1, ["document"]),
            bind_assets("operations_assistant", 1, []),
            arguments={"tenant_id": TENANT_A},
        )
    )
    assert foreign["knowledge"]["document"]["snapshot"]["found"] is False
    assert SECRET_NOTE not in json.dumps(foreign)
    assert foreign["assets"] == {}


def test_execution_context_ignores_payload_tenant() -> None:
    store = KnowledgeStore()
    _document(store, TENANT_A)
    service = PlatformKnowledgeService(bind_knowledge("operations_assistant", 1, ["document"]), store)
    ctx = SimpleNamespace(
        tenant_id=TENANT_B,
        snapshot=SimpleNamespace(knowledge_sources=frozenset({"document"})),
        payload={"tenant_id": TENANT_A},
    )
    loaded = asyncio.run(service.load(ctx, "document"))
    assert loaded == {"found": False, "source": "document"}
    ctx.tenant_id = TENANT_A
    visible = asyncio.run(service.load(ctx, "document"))
    assert visible["record"]["body"]["text"] == SECRET_NOTE


def test_instagram_context_includes_logo_only_when_the_role_is_bound() -> None:
    calls: list[str] = []

    async def invoke(tool: str, arguments: dict, tenant) -> dict:
        del arguments, tenant
        calls.append(tool)
        return {"found": False}

    assembler = McpContextAssembler(invoke)
    asyncio.run(
        assembler.build(
            trusted_tenant(TENANT_A),
            instagram_marketing_knowledge(),
            bind_assets("instagram_marketing", 1, [AssetRole.COMPANY_LOGO, AssetRole.PRODUCT_IMAGE]),
            arguments={"product": "Gold ring"},
        )
    )
    assert "get_company_logo" in calls
    assert "get_product_image" in calls
    assert "get_business_profile" in calls
    calls.clear()
    asyncio.run(
        assembler.build(
            trusted_tenant(TENANT_A),
            instagram_marketing_knowledge(),
            bind_assets("instagram_marketing", 1, []),
        )
    )
    assert "get_company_logo" not in calls
    assert "get_product_image" not in calls


def test_platform_knowledge_package_does_not_name_the_publisher() -> None:
    root = Path(__file__).resolve().parents[1] / "backend" / "platform" / "knowledge"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "InstagramAgent" not in text
    assert "enqueue_instagram" not in text
