"""Persistent agents, closed workflow execution, shadow mode, and scheduler lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.instagram_tasks import enqueue_instagram_publication
from agent_runtime.errors import RuntimeFailure
from agent_runtime.workflows import UnknownWorkflow, resolve_workflow
from backend.platform.builder.runner import run_declared
from config import Settings
from db.platform_agents import (
    PlatformStoreError,
    ensure_reference_agent,
    get_agent,
    get_execution,
    published_version,
    reference_snapshot,
    save_version,
    upsert_agent,
    validate_snapshot,
)
from db.session import bind_runtime, create_engine_from_url, init_db, session_factory
from instagram_reference.services import ReferenceToolService
from instagram_reference.shadow import execution_for, run_shadow, shadow_publication
from models.errors import AppError
from scheduler.leader import SchedulerLeader
from services.instagram_client import InstagramGraphClient
from tests.helpers import test_settings


def _factory(tmp_path: Path):
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'platform.db').as_posix()}")
    init_db(engine)
    factory = session_factory(engine)
    bind_runtime(engine, factory, None)
    return factory


def test_published_version_is_immutable_and_tenant_scoped(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        agent = upsert_agent(
            session,
            tenant_id="user-a",
            agent_key="studio",
            display_name="Studio",
            description="",
            owner="user",
            status="active",
            published_version_number=1,
        )
        version = save_version(
            session,
            tenant_id="user-a",
            agent=agent,
            version_number=1,
            status="published",
            snapshot=reference_snapshot(),
        )
        session.commit()
        assert version.snapshot["workflow_key"] == "instagram.reference"
        assert "api_key" not in str(version.snapshot)
        with pytest.raises(PlatformStoreError) as raised:
            save_version(
                session,
                tenant_id="user-a",
                agent=agent,
                version_number=1,
                status="published",
                snapshot={**reference_snapshot(), "trigger_key": "other.run"},
            )
        assert raised.value.code == "IMMUTABLE_VERSION"
        assert get_agent(session, "user-b", "studio") is None
        assert published_version(session, agent).id == version.id


def test_snapshot_rejects_provider_secrets() -> None:
    snapshot = reference_snapshot()
    assert snapshot["models"][0]["implementation"] == "deepseek"
    assert snapshot["models"][1]["implementation"] == "openai"
    with pytest.raises(PlatformStoreError):
        validate_snapshot({**snapshot, "models": [{"role": "content_planning", "implementation": "deepseek", "api_key": "sk-abcdefghij"}]})


def test_unknown_workflow_is_rejected() -> None:
    with pytest.raises(UnknownWorkflow):
        resolve_workflow("studio.generate")


@pytest.mark.asyncio
async def test_run_declared_executes_registered_workflow_and_rejects_unknown(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    settings = test_settings(tmp_path)
    with pytest.raises(AppError) as raised:
        await run_declared(
            execution_id="exec-unknown",
            workflow_id="studio.approve",
            trigger_key="manual",
            tenant_id="user-a",
            settings=settings,
            session_factory=factory,
        )
    assert raised.value.code.value == "INVALID_REQUEST"
    view = await run_declared(
        execution_id="exec-reference",
        workflow_id="instagram.reference",
        trigger_key="reference.run",
        tenant_id="user-a",
        settings=settings,
        session_factory=factory,
    )
    assert view["published"] is False
    assert any(step["step_key"] == "load_business_context" and step["status"] == "completed" for step in view["steps"])
    with factory() as session:
        stored = get_execution(session, "user-a", view["execution_id"])
        assert stored is not None
        assert stored.workflow_key == "instagram.reference"
        assert stored.tenant_id == "user-a"
        assert get_execution(session, "user-b", view["execution_id"]) is None


@pytest.mark.asyncio
async def test_shadow_execution_cannot_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _publish(*_args, **_kwargs):
        calls.append("enqueue")
        raise AssertionError("shadow execution published")

    def _graph(*_args, **_kwargs):
        calls.append("graph")
        raise AssertionError("shadow execution constructed a Graph client")

    monkeypatch.setattr("agent.instagram_tasks.enqueue_instagram_publication", _publish)
    monkeypatch.setattr(InstagramGraphClient, "__init__", _graph)
    factory = _factory(tmp_path)
    settings = test_settings(tmp_path)
    view = await run_shadow("user-a", {"studio_preview": {"planned": False, "published": False}}, settings=settings, session_factory=factory)
    assert view["published"] is False
    assert view["output"]["published"] is False
    assert view["output"]["handler_published"] is False
    assert view["output"]["comparison"]["shadow_published"] is False
    assert calls == []
    stored = execution_for(factory, "user-a", view["execution_id"])
    assert stored is not None
    assert stored["workflow"] == "instagram.reference"
    assert stored["mode"] == "shadow"
    assert stored["started_at"]
    assert stored["completed_at"]
    assert execution_for(factory, "user-b", view["execution_id"]) is None
    direct = await shadow_publication(object(), object(), {})
    assert direct["published"] is False
    assert enqueue_instagram_publication is not shadow_publication


@pytest.mark.asyncio
async def test_draft_and_paused_agents_do_not_execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _factory(tmp_path)
    settings = test_settings(tmp_path)

    async def _unexpected(*_args, **_kwargs):
        raise AssertionError("runtime should not start")

    monkeypatch.setattr("instagram_reference.shadow.AgentRuntime.run", _unexpected)
    with factory() as session:
        draft = upsert_agent(
            session,
            tenant_id="user-a",
            agent_key="draft-agent",
            display_name="Draft",
            description="",
            owner="user",
            status="active",
            published_version_number=None,
        )
        save_version(
            session,
            tenant_id="user-a",
            agent=draft,
            version_number=1,
            status="draft",
            snapshot=reference_snapshot(),
        )
        paused = upsert_agent(
            session,
            tenant_id="user-a",
            agent_key="paused-agent",
            display_name="Paused",
            description="",
            owner="user",
            status="paused",
            published_version_number=1,
        )
        save_version(
            session,
            tenant_id="user-a",
            agent=paused,
            version_number=1,
            status="published",
            snapshot=reference_snapshot(),
        )
        session.commit()
    with pytest.raises(AppError):
        await run_shadow("user-a", settings=settings, session_factory=factory, agent_key="draft-agent")
    with pytest.raises(AppError):
        await run_shadow("user-a", settings=settings, session_factory=factory, agent_key="paused-agent")
    with factory() as session:
        from sqlalchemy import func, select

        from db.models import DurableAgentExecution

        count = session.scalar(select(func.count()).select_from(DurableAgentExecution))
        assert count == 0


@pytest.mark.asyncio
async def test_shadow_tool_service_rejects_publishing_names() -> None:
    called: list[str] = []

    async def _invoke(*_args, **_kwargs):
        called.append("tool")
        return {}

    service = ReferenceToolService(invoker=_invoke)
    with pytest.raises(RuntimeFailure):
        await service.invoke(object(), "media_publish", {})
    assert called == []


def test_second_scheduler_leader_does_not_acquire(tmp_path: Path) -> None:
    path = tmp_path / "scheduler.leader.lock"
    first = SchedulerLeader(path)
    second = SchedulerLeader(path)
    assert first.try_acquire() is True
    assert second.try_acquire() is False
    first.release()
    assert second.try_acquire() is True
    second.release()


def test_production_refuses_known_jwt_defaults() -> None:
    with pytest.raises(RuntimeError):
        Settings(app_env="production", jwt_secret="dev-change-me")._refuse_known_production_jwt()
    with pytest.raises(RuntimeError):
        Settings(app_env="production", jwt_secret="docker-dev-jwt-secret-change-me-32b")._refuse_known_production_jwt()
    Settings(app_env="development", jwt_secret="dev-change-me")._refuse_known_production_jwt()
    Settings(app_env="production", jwt_secret="a-unique-production-secret-32chars")._refuse_known_production_jwt()


def test_from_env_refuses_default_jwt_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "dev-change-me")
    with pytest.raises(RuntimeError):
        Settings.from_env()


def test_reference_agent_row_is_created_for_shadow(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        agent = ensure_reference_agent(session)
        session.commit()
        assert agent.tenant_id == "system"
        assert agent.agent_key == "instagram_marketing"
        assert agent.status == "active"
        version = published_version(session, agent)
        assert version is not None
        assert version.status == "published"
        assert version.snapshot["models"][1]["implementation"] == "openai"
