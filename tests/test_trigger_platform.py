"""Generic triggers can start any published agent. Instagram dates stay catalog data."""

from __future__ import annotations

import ast
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from agent_platform.triggers import (
    CalendarDate,
    ConditionOperator,
    ConditionSpec,
    RetryPolicy,
    ScheduleKind,
    TriggerCatalog,
    TriggerDefinition,
    TriggerDispatcher,
    TriggerError,
    TriggerHistory,
    TriggerKind,
    TriggerScheduler,
    due_schedule,
)
from agent_platform.triggers.types import FireStatus
from agent_runtime.catalog import InMemoryCatalog
from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import (
    AgentDefinition,
    AgentStatus,
    AgentVersion,
    ExecutionContext,
    OwnerKind,
    StepKind,
    TriggerBinding,
    VersionStatus,
    WorkflowPin,
    WorkflowStep,
    WorkflowVersion,
)
from agent_runtime.runtime import AgentRuntime
from agent_runtime.services import HandlerRegistry
from agent_runtime.store import InMemoryExecutionStore
from festivals.india_festivals import festivals_for_year, lunar_date_table
from scheduler.trigger_configuration import (
    PHASE_TO_CAMPAIGN_KIND,
    instagram_trigger_definitions,
)

ROOT = Path(__file__).resolve().parents[1]
KOLKATA = ZoneInfo("Asia/Kolkata")
DUE = datetime(2026, 9, 25, 10, 0, tzinfo=KOLKATA)
EARLY = datetime(2026, 9, 25, 9, 59, tzinfo=KOLKATA)


class Platform:
    def __init__(self) -> None:
        self.catalog = InMemoryCatalog()
        self.handlers = HandlerRegistry()
        self.calls: list[dict[str, Any]] = []
        self.executions = InMemoryExecutionStore()
        self.runtime = AgentRuntime(
            catalog=self.catalog,
            handlers=self.handlers,
            store=self.executions,
        )
        self.triggers = TriggerCatalog()
        self.history = TriggerHistory()
        self.dispatcher = TriggerDispatcher(
            runtime=self.runtime,
            catalog=self.triggers,
            history=self.history,
        )
        self.scheduler = TriggerScheduler(self.dispatcher)

    def publish(
        self,
        agent_key: str,
        trigger_key: str,
        handler_id: str,
        *,
        status: AgentStatus = AgentStatus.ACTIVE,
        owner: OwnerKind = OwnerKind.SYSTEM,
        user_id: str | None = None,
        published: int | None = 1,
        version_status: VersionStatus = VersionStatus.PUBLISHED,
        handler: Any | None = None,
    ) -> AgentDefinition:
        workflow = _workflow(f"{agent_key}.flow", handler_id)
        version = _version(workflow, trigger_key, number=1, status=version_status)
        definition = AgentDefinition(
            key=agent_key,
            owner=owner,
            status=status,
            display_name=agent_key,
            user_id=user_id,
            published_version_number=published,
        )
        self.handlers.register(handler_id, handler or self._recorder(handler_id))
        self.catalog.save_workflow(workflow)
        self.catalog.save_definition(definition.model_copy(update={"published_version_number": None}))
        self.catalog.save_version(definition, version)
        if published is not None:
            self.catalog.save_definition(definition)
        return definition

    def add_draft(self, definition: AgentDefinition, trigger_key: str, handler_id: str) -> None:
        workflow = _workflow(f"{definition.key}.draft", handler_id)
        version = _version(workflow, trigger_key, number=2, status=VersionStatus.DRAFT)
        self.handlers.register(handler_id, self._recorder(handler_id))
        self.catalog.save_workflow(workflow)
        self.catalog.save_version(definition, version)

    def _recorder(self, handler_id: str):
        async def handler(ctx: ExecutionContext, step: WorkflowStep, state: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(
                {
                    "tenant": ctx.tenant_id,
                    "handler": handler_id,
                    "agent": ctx.snapshot.agent_key,
                    "input": dict(state["input"]),
                }
            )
            return {"ok": True}

        return handler


def _workflow(key: str, handler_id: str) -> WorkflowVersion:
    step = WorkflowStep(step_key="record", position=1, kind=StepKind.STORE, handler_id=handler_id)
    return WorkflowVersion(
        workflow_key=key,
        version_number=1,
        status=VersionStatus.PUBLISHED,
        steps=(step,),
    )


def _version(
    workflow: WorkflowVersion,
    trigger_key: str,
    *,
    number: int,
    status: VersionStatus,
) -> AgentVersion:
    return AgentVersion(
        version_number=number,
        status=status,
        workflow_pins=(WorkflowPin(workflow_key=workflow.workflow_key, version_number=1),),
        trigger_bindings=(TriggerBinding(trigger_key=trigger_key, workflow_key=workflow.workflow_key),),
    )


def _daily(
    *,
    tenant: str = "tenant-1",
    trigger_id: str = "daily_note",
    agent: str = "notes",
    trigger_key: str = "scheduler.daily",
    enabled: bool = True,
    retry: RetryPolicy | None = None,
    payload: dict[str, Any] | None = None,
) -> TriggerDefinition:
    return TriggerDefinition(
        trigger_id=trigger_id,
        tenant_id=tenant,
        agent_key=agent,
        trigger_key=trigger_key,
        kind=TriggerKind.SCHEDULE,
        enabled=enabled,
        timezone="Asia/Kolkata",
        schedule_kind=ScheduleKind.DAILY,
        local_time="10:00",
        static_payload=payload or {},
        retry=retry or RetryPolicy(),
    )


def test_trigger_package_does_not_import_publishers_or_execute_code() -> None:
    package = ROOT / "agent_platform" / "triggers"
    files = list(package.glob("*.py"))
    assert files
    forbidden_modules = {"agent", "tools", "festivals", "services", "scheduler", "instagram", "importlib", "subprocess"}
    forbidden_calls = {"eval", "exec", "compile", "__import__"}
    forbidden_text = ("instagram", "graph.facebook", "graph.instagram", "meta_access_token", "enqueue_instagram")
    for path in files:
        source = path.read_text(encoding="utf-8")
        lowered = source.lower()
        for needle in forbidden_text:
            assert needle not in lowered, path.name
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".", 1)[0] not in forbidden_modules
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_modules
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls


def test_configuration_module_does_not_publish_and_festival_scheduler_remains() -> None:
    source = (ROOT / "scheduler" / "trigger_configuration.py").read_text(encoding="utf-8")
    lowered = source.lower()
    for needle in ("enqueue_instagram", "graph.facebook", "instagramgraphclient", "meta_access_token", "eval(", "exec("):
        assert needle not in lowered
    assert "festivals_for_year" in source
    festival = (ROOT / "scheduler" / "festival_scheduler.py").read_text(encoding="utf-8")
    assert "class FestivalScheduler" in festival
    assert "async def run_user" in festival
    assert "FestivalService" in festival
    assert "due_campaigns" in festival
    assert "lunar_dates.json" in (ROOT / "festivals" / "india_festivals.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_scheduler_fires_each_published_agent_once() -> None:
    platform = Platform()
    platform.publish("notes", "scheduler.daily", "notes_handler")
    platform.publish("billing", "scheduler.daily", "billing_handler")
    platform.triggers.save(_daily(agent="notes", trigger_id="notes_daily"))
    platform.triggers.save(_daily(agent="billing", trigger_id="billing_daily", trigger_key="scheduler.daily"))
    first = await platform.scheduler.tick(DUE)
    second = await platform.scheduler.tick(DUE)
    assert {item.fire.agent_key for item in first} == {"notes", "billing"}
    assert all(item.fire.status == FireStatus.COMPLETED for item in first)
    assert all(item.duplicate for item in second)
    assert [call["handler"] for call in platform.calls] == ["notes_handler", "billing_handler"]
    assert {call["agent"] for call in platform.calls} == {"notes", "billing"}


@pytest.mark.asyncio
async def test_tenants_do_not_share_history_or_executions() -> None:
    platform = Platform()
    platform.publish("notes", "scheduler.daily", "notes_handler")
    platform.triggers.save(_daily(tenant="tenant-a", trigger_id="daily_note"))
    platform.triggers.save(_daily(tenant="tenant-b", trigger_id="daily_note"))
    await platform.scheduler.tick(DUE, tenant_id="tenant-a")
    await platform.scheduler.tick(DUE, tenant_id="tenant-b")
    await platform.scheduler.tick(DUE, tenant_id="tenant-a")
    left = platform.history.list("tenant-a")
    right = platform.history.list("tenant-b")
    assert len(left) == 1 and len(right) == 1
    assert left[0].execution_id != right[0].execution_id
    assert platform.history.get("tenant-b", left[0].fire_id) is None
    assert [call["tenant"] for call in platform.calls] == ["tenant-a", "tenant-b"]


@pytest.mark.asyncio
async def test_paused_and_retired_agents_record_a_failure_and_do_not_run() -> None:
    platform = Platform()
    for status, code in (
        (AgentStatus.PAUSED, "AGENT_PAUSED"),
        (AgentStatus.RETIRED, "AGENT_RETIRED"),
    ):
        agent = f"agent_{status.value}"
        platform.publish(agent, "scheduler.daily", f"handler_{status.value}", status=status)
        platform.triggers.save(_daily(agent=agent, trigger_id=f"trig_{status.value}"))
        result = await platform.scheduler.tick(DUE)
        matched = [item for item in result if item.fire.agent_key == agent]
        assert matched[0].fire.status == FireStatus.FAILED
        assert matched[0].fire.error_code == code
        assert matched[0].fire.execution_id
        again = await platform.scheduler.tick(DUE)
        repeated = [item for item in again if item.fire.agent_key == agent]
        assert repeated[0].duplicate
    assert platform.calls == []
    assert len(platform.history.list("tenant-1", "trig_paused")) == 1


@pytest.mark.asyncio
async def test_draft_version_cannot_run_production_automation() -> None:
    platform = Platform()
    published = platform.publish("notes", "scheduler.daily", "published_handler")
    platform.add_draft(published, "scheduler.daily", "draft_handler")
    platform.triggers.save(_daily())
    result = await platform.scheduler.tick(DUE)
    assert result[0].fire.status == FireStatus.COMPLETED
    assert [call["handler"] for call in platform.calls] == ["published_handler"]

    draft_only = Platform()
    draft_only.publish(
        "notes",
        "scheduler.daily",
        "draft_handler",
        published=None,
        version_status=VersionStatus.DRAFT,
    )
    draft_only.triggers.save(_daily())
    failed = await draft_only.scheduler.tick(DUE)
    assert failed[0].fire.status == FireStatus.FAILED
    assert failed[0].fire.error_code == "VERSION_NOT_PUBLISHED"
    assert draft_only.calls == []
    assert len(draft_only.history.list("tenant-1")) == 1


@pytest.mark.asyncio
async def test_other_tenants_user_agent_is_not_found() -> None:
    platform = Platform()
    platform.publish(
        "shop",
        "manual.run",
        "shop_handler",
        owner=OwnerKind.USER,
        user_id="tenant-a",
    )
    platform.triggers.save(
        TriggerDefinition(
            trigger_id="run_shop",
            tenant_id="tenant-b",
            agent_key="shop",
            trigger_key="manual.run",
            kind=TriggerKind.MANUAL,
            timezone="Asia/Kolkata",
        )
    )
    result = await platform.dispatcher.manual(
        "tenant-b",
        "run_shop",
        idempotency_key="click-1",
        payload={},
        now=DUE,
    )
    assert result.fire.status == FireStatus.FAILED
    assert result.fire.error_code == "AGENT_NOT_FOUND"
    assert platform.calls == []
    with pytest.raises(TriggerError) as caught:
        await platform.dispatcher.manual("tenant-a", "run_shop", idempotency_key="click-1", payload={}, now=DUE)
    assert caught.value.code == "trigger_not_found"


@pytest.mark.asyncio
async def test_schedule_is_timezone_aware_and_idempotent() -> None:
    platform = Platform()
    platform.publish("notes", "scheduler.daily", "notes_handler")
    platform.triggers.save(_daily(payload={"mode": "daily"}))
    early = await platform.scheduler.tick(EARLY)
    due = await platform.scheduler.tick(DUE)
    again = await platform.scheduler.tick(DUE)
    assert early == []
    assert due[0].fire.status == FireStatus.COMPLETED
    assert again[0].duplicate
    assert again[0].fire.execution_id == due[0].fire.execution_id
    assert platform.calls[0]["input"]["mode"] == "daily"
    assert len(platform.calls) == 1


@pytest.mark.asyncio
async def test_naive_time_does_not_execute() -> None:
    platform = Platform()
    platform.publish("notes", "scheduler.daily", "notes_handler")
    platform.triggers.save(_daily())
    with pytest.raises(TriggerError) as caught:
        await platform.scheduler.tick(datetime(2026, 9, 25, 10, 0))  # noqa: DTZ001
    assert caught.value.code == "naive_timestamp"
    assert platform.calls == []


@pytest.mark.asyncio
async def test_disabled_trigger_does_not_run() -> None:
    platform = Platform()
    platform.publish("notes", "manual.run", "notes_handler")
    platform.triggers.save(
        TriggerDefinition(
            trigger_id="run_notes",
            tenant_id="tenant-1",
            agent_key="notes",
            trigger_key="manual.run",
            kind=TriggerKind.MANUAL,
            enabled=False,
            timezone="Asia/Kolkata",
        )
    )
    platform.triggers.save(_daily(enabled=False))
    assert await platform.scheduler.tick(DUE) == []
    skipped = await platform.dispatcher.manual(
        "tenant-1",
        "run_notes",
        idempotency_key="click-1",
        payload={},
        now=DUE,
    )
    assert skipped.fire.status == FireStatus.SKIPPED
    assert skipped.fire.error_code == "TRIGGER_DISABLED"
    assert platform.calls == []


@pytest.mark.asyncio
async def test_retry_policy_retries_listed_codes_only() -> None:
    platform = Platform()
    attempts: list[int] = []

    async def flaky(ctx: ExecutionContext, step: WorkflowStep, state: dict[str, Any]) -> dict[str, Any]:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeFailure(RuntimeErrorCode.STEP_FAILED, "Step failed.", retryable=True)
        return {"ok": True}

    platform.publish("notes", "scheduler.daily", "flaky", handler=flaky)
    platform.triggers.save(
        _daily(retry=RetryPolicy(max_attempts=2, retryable_codes=frozenset({"STEP_FAILED"}), delay_seconds=3600))
    )
    failed = await platform.scheduler.tick(DUE)
    assert failed[0].fire.status == FireStatus.FAILED
    assert failed[0].fire.error_code == "STEP_FAILED"
    held = await platform.scheduler.tick(DUE)
    assert held[0].duplicate
    assert len(attempts) == 1
    retried = await platform.scheduler.tick(DUE + timedelta(hours=2))
    assert retried[0].fire.status == FireStatus.COMPLETED
    assert retried[0].fire.attempt == 2
    assert len(attempts) == 2
    stored = platform.executions.get(failed[0].fire.execution_id or "")
    assert stored is not None and stored.error is not None and stored.error.retryable is True

    quiet = Platform()

    async def once(ctx: ExecutionContext, step: WorkflowStep, state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeFailure(RuntimeErrorCode.STEP_FAILED, "Step failed.", retryable=True)

    quiet.publish("notes", "scheduler.daily", "once", handler=once)
    quiet.triggers.save(_daily(retry=RetryPolicy(max_attempts=3)))
    await quiet.scheduler.tick(DUE)
    await quiet.scheduler.tick(DUE + timedelta(hours=5))
    assert len(quiet.history.list("tenant-1")) == 1


@pytest.mark.asyncio
async def test_event_and_webhook_are_not_polled_and_reject_unknown_sources() -> None:
    platform = Platform()
    platform.publish("notes", "ingress.event", "notes_handler")
    platform.triggers.save(
        TriggerDefinition(
            trigger_id="shop_event",
            tenant_id="tenant-1",
            agent_key="notes",
            trigger_key="ingress.event",
            kind=TriggerKind.EVENT,
            timezone="Asia/Kolkata",
            registered_source="catalog_refresh",
        )
    )
    platform.triggers.save(
        TriggerDefinition(
            trigger_id="shop_hook",
            tenant_id="tenant-1",
            agent_key="notes",
            trigger_key="ingress.event",
            kind=TriggerKind.WEBHOOK,
            timezone="Asia/Kolkata",
            registered_source="billing_hook",
        )
    )
    assert await platform.scheduler.tick(DUE) == []
    rejected = await platform.dispatcher.accept(
        "tenant-1",
        "shop_hook",
        source_id="someone_else",
        idempotency_key="hook-1",
        payload={"script": "print(1)"},
        now=DUE,
    )
    assert rejected.fire.status == FireStatus.REJECTED
    assert rejected.fire.execution_id is None
    accepted = await platform.dispatcher.accept(
        "tenant-1",
        "shop_hook",
        source_id="billing_hook",
        idempotency_key="hook-1",
        payload={"invoice": "100"},
        now=DUE,
    )
    assert accepted.fire.status == FireStatus.COMPLETED
    duplicate = await platform.dispatcher.accept(
        "tenant-1",
        "shop_hook",
        source_id="billing_hook",
        idempotency_key="hook-1",
        payload={"invoice": "100"},
        now=DUE,
    )
    assert duplicate.duplicate
    assert len(platform.calls) == 1
    with pytest.raises(TriggerError) as caught:
        await platform.dispatcher.accept(
            "tenant-1",
            "shop_hook",
            source_id="billing_hook",
            idempotency_key="hook-2",
            payload={"command": "rm"},
            now=DUE,
        )
    assert caught.value.code == "invalid_request"
    assert len(platform.calls) == 1


@pytest.mark.asyncio
async def test_condition_fires_once_per_matched_fact() -> None:
    platform = Platform()
    platform.publish("notes", "rule.ready", "notes_handler")
    platform.triggers.save(
        TriggerDefinition(
            trigger_id="ready_rule",
            tenant_id="tenant-1",
            agent_key="notes",
            trigger_key="rule.ready",
            kind=TriggerKind.CONDITION,
            timezone="Asia/Kolkata",
            condition=ConditionSpec(field="ready", operator=ConditionOperator.EQ, value=True),
        )
    )
    assert await platform.scheduler.tick(DUE, facts={"ready": False, "code": "import os"}) == []
    first = await platform.scheduler.tick(DUE, facts={"ready": True})
    second = await platform.scheduler.tick(DUE, facts={"ready": True})
    assert first[0].fire.status == FireStatus.COMPLETED
    assert second[0].duplicate
    assert len(platform.calls) == 1
    assert "import os" not in str(platform.calls)
    with pytest.raises(ValidationError):
        ConditionSpec(field="__class__", operator=ConditionOperator.EXISTS)


@pytest.mark.asyncio
async def test_calendar_uses_only_supplied_dates() -> None:
    platform = Platform()
    platform.publish("notes", "scheduler.calendar", "notes_handler")
    supplied = CalendarDate(occurrence_key="diwali", occurs_on=date(2026, 11, 8), label="Diwali")
    definition = TriggerDefinition(
        trigger_id="calendar_note",
        tenant_id="tenant-1",
        agent_key="notes",
        trigger_key="scheduler.calendar",
        kind=TriggerKind.SCHEDULE,
        timezone="Asia/Kolkata",
        schedule_kind=ScheduleKind.CALENDAR,
        local_time="10:00",
        lead_days=1,
        occurrences=(supplied,),
    )
    on_day = datetime(2026, 11, 8, 10, 0, tzinfo=KOLKATA)
    lead_day = datetime(2026, 11, 7, 10, 0, tzinfo=KOLKATA)
    after = datetime(2026, 11, 9, 10, 0, tzinfo=KOLKATA)
    assert due_schedule(definition, datetime(2026, 11, 8, 9, 59, tzinfo=KOLKATA)) == []
    assert due_schedule(definition, after) == []
    lead = due_schedule(definition, lead_day)
    assert lead[0].payload["phase"] == "lead"
    assert lead[0].payload["calendar_date"] == "2026-11-08"
    collapsed = definition.model_copy(update={"lead_days": 0})
    assert [item.payload["phase"] for item in due_schedule(collapsed, on_day)] == ["on"]
    both = collapsed.model_copy(update={"allow_same_day": True})
    assert [item.payload["phase"] for item in due_schedule(both, on_day)] == ["lead", "on"]
    empty = definition.model_copy(update={"occurrences": ()})
    assert due_schedule(empty, on_day) == []
    platform.triggers.save(definition)
    fired = await platform.scheduler.tick(on_day)
    assert fired[0].fire.status == FireStatus.COMPLETED
    assert platform.calls[0]["input"]["calendar_date"] == "2026-11-08"
    assert platform.calls[0]["input"]["calendar_label"] == "Diwali"


def test_instagram_configuration_copies_catalog_dates() -> None:
    daily, festival = instagram_trigger_definitions(
        tenant_id="user-1",
        year=2026,
        daily_enabled=True,
        festival_enabled=True,
        daily_post_time="09:15",
        pre_festival_days=1,
    )
    catalog = festivals_for_year(2026)
    assert daily.agent_key == "instagram_marketing"
    assert daily.kind == TriggerKind.SCHEDULE
    assert daily.local_time == "09:15"
    assert daily.static_payload["mode"] == "daily"
    assert festival.trigger_key == "scheduler.festival"
    assert festival.lead_phase == PHASE_TO_CAMPAIGN_KIND["lead"]
    assert festival.on_phase == PHASE_TO_CAMPAIGN_KIND["on"]
    assert len(festival.occurrences) == len(catalog)
    by_label = {item.label: item.occurs_on.isoformat() for item in festival.occurrences}
    for row in catalog:
        assert by_label[row["festival_name"]] == row["date"]
    table = lunar_date_table()
    if table.get("diwali", {}).get(2026):
        assert by_label["Diwali"] == table["diwali"][2026]
    diwali_on = date.fromisoformat(by_label["Diwali"])
    due = due_schedule(festival, datetime.combine(diwali_on, time(10, 0), tzinfo=KOLKATA))
    diwali_hits = [item for item in due if item.payload["calendar_label"] == "Diwali"]
    assert len(diwali_hits) == 1
    assert diwali_hits[0].payload["phase"] == "festival-day"
    assert diwali_hits[0].payload["calendar_date"] == by_label["Diwali"]
    if by_label.get("Kali Puja") == by_label["Diwali"]:
        assert any(item.payload["calendar_label"] == "Kali Puja" for item in due)
    lead = due_schedule(
        festival,
        datetime.combine(diwali_on - timedelta(days=1), time(10, 0), tzinfo=KOLKATA),
    )
    diwali_lead = [item for item in lead if item.payload["calendar_label"] == "Diwali"]
    assert diwali_lead[0].payload["phase"] == "pre-festival"
    assert diwali_lead[0].payload["calendar_date"] == by_label["Diwali"]
    day_after = due_schedule(
        festival,
        datetime.combine(diwali_on + timedelta(days=1), time(10, 0), tzinfo=KOLKATA),
    )
    assert all(item.payload["calendar_label"] != "Diwali" for item in day_after)
    assert all(item.payload["calendar_date"] in by_label.values() for item in due)


@pytest.mark.asyncio
async def test_instagram_daily_configuration_triggers_the_published_agent() -> None:
    platform = Platform()
    platform.publish("instagram_marketing", "scheduler.daily", "campaign_handler")
    daily, festival = instagram_trigger_definitions(
        tenant_id="tenant-1",
        year=2026,
        daily_enabled=True,
        festival_enabled=False,
    )
    platform.triggers.save(daily)
    platform.triggers.save(festival)
    result = await platform.scheduler.tick(DUE)
    assert len(result) == 1
    assert result[0].fire.agent_key == "instagram_marketing"
    assert result[0].fire.status == FireStatus.COMPLETED
    assert platform.calls[0]["input"]["post_type"] == "DAILY_RETAIL_POST"
    assert "enqueue_instagram" not in str(platform.calls)


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TriggerDefinition(
            trigger_id="daily_note",
            tenant_id="tenant-1",
            agent_key="notes",
            trigger_key="scheduler.daily",
            kind=TriggerKind.SCHEDULE,
            timezone="Not/AZone",
            schedule_kind=ScheduleKind.DAILY,
            local_time="10:00",
        )


@pytest.mark.asyncio
async def test_manual_is_idempotent_for_one_key() -> None:
    platform = Platform()
    platform.publish("notes", "manual.run", "notes_handler")
    platform.triggers.save(
        TriggerDefinition(
            trigger_id="run_notes",
            tenant_id="tenant-1",
            agent_key="notes",
            trigger_key="manual.run",
            kind=TriggerKind.MANUAL,
            timezone="UTC",
        )
    )
    now = datetime(2026, 9, 25, 10, 0, tzinfo=ZoneInfo("UTC"))
    first = await platform.dispatcher.manual("tenant-1", "run_notes", idempotency_key="click-1", payload={}, now=now)
    second = await platform.dispatcher.manual("tenant-1", "run_notes", idempotency_key="click-1", payload={}, now=now)
    assert first.fire.status == FireStatus.COMPLETED
    assert second.duplicate
    assert len(platform.calls) == 1
