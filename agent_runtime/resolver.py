"""Selects the agent version and workflow a trigger is allowed to run."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.catalog import AgentCatalog
from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import (
    AgentDefinition,
    AgentStatus,
    AgentVersion,
    ExecutionSnapshot,
    OwnerKind,
    TriggerContext,
    VersionStatus,
    WorkflowVersion,
    requires_published_version,
)


@dataclass(frozen=True)
class ResolvedRun:
    definition: AgentDefinition
    version: AgentVersion
    workflow: WorkflowVersion

    def snapshot(
        self,
        *,
        execution_id: str,
        correlation_id: str,
        trigger: TriggerContext,
    ) -> ExecutionSnapshot:
        policy_id = self.workflow.approval_policy_id or self.version.approval_policy_id
        return ExecutionSnapshot(
            execution_id=execution_id,
            correlation_id=correlation_id,
            tenant_id=trigger.tenant_id,
            user_id=trigger.user_id,
            agent_key=self.definition.key,
            agent_version_number=self.version.version_number,
            agent_version_status=self.version.status,
            workflow_key=self.workflow.workflow_key,
            workflow_version_number=self.workflow.version_number,
            steps=tuple(self.workflow.steps),
            trigger_key=trigger.trigger_key,
            mode=trigger.mode,
            idempotency_key=trigger.idempotency_key,
            execution_policy=self.version.execution_policy,
            approval_policy_id=policy_id,
            tool_grants=frozenset(self.version.tool_grants),
            model_roles=frozenset(self.version.model_roles),
            knowledge_sources=frozenset(self.version.knowledge_sources),
            asset_roles=frozenset(self.version.asset_roles),
        )


class AgentResolver:
    def __init__(self, catalog: AgentCatalog) -> None:
        self._catalog = catalog

    def resolve(self, agent_key: str, trigger: TriggerContext) -> ResolvedRun:
        definition = self._catalog.get_definition(agent_key, tenant_id=trigger.tenant_id)
        if definition is None:
            raise RuntimeFailure(RuntimeErrorCode.AGENT_NOT_FOUND, "Agent was not found.")
        version = self._select_version(definition, trigger)
        workflow = self._select_workflow(version, trigger)
        resolved = ResolvedRun(definition=definition, version=version, workflow=workflow)
        self.validate(resolved, trigger)
        return resolved

    def validate(self, resolved: ResolvedRun, trigger: TriggerContext) -> None:
        definition = resolved.definition
        version = resolved.version
        workflow = resolved.workflow
        self._assert_owner(definition, trigger)
        if definition.status == AgentStatus.PAUSED:
            raise RuntimeFailure(RuntimeErrorCode.AGENT_PAUSED, "Agent is paused.")
        if definition.status == AgentStatus.RETIRED:
            raise RuntimeFailure(RuntimeErrorCode.AGENT_RETIRED, "Agent is retired.")
        if definition.status != AgentStatus.ACTIVE:
            raise RuntimeFailure(RuntimeErrorCode.AGENT_NOT_FOUND, "Agent was not found.")
        if requires_published_version(trigger.mode):
            if version.status != VersionStatus.PUBLISHED:
                raise RuntimeFailure(
                    RuntimeErrorCode.DRAFT_NOT_ALLOWED,
                    "Draft agent versions cannot run in production.",
                )
            if definition.published_version_number != version.version_number:
                raise RuntimeFailure(
                    RuntimeErrorCode.PUBLISHED_POINTER_REQUIRED,
                    "Production runs the published agent version.",
                )
        if workflow.status != VersionStatus.PUBLISHED:
            raise RuntimeFailure(
                RuntimeErrorCode.VERSION_NOT_PUBLISHED,
                "Workflow version is not published.",
            )
        if workflow.workflow_key != self._bound_workflow_key(version, trigger):
            raise RuntimeFailure(
                RuntimeErrorCode.WORKFLOW_NOT_PINNED,
                "Trigger is not bound to this workflow.",
            )
        pin = self._pin(version, workflow.workflow_key)
        if pin.version_number != workflow.version_number:
            raise RuntimeFailure(
                RuntimeErrorCode.WORKFLOW_NOT_PINNED,
                "Workflow version is not pinned by the agent version.",
            )

    def _select_version(self, definition: AgentDefinition, trigger: TriggerContext) -> AgentVersion:
        requested = trigger.requested_version_number
        if requires_published_version(trigger.mode):
            if definition.published_version_number is None:
                raise RuntimeFailure(
                    RuntimeErrorCode.VERSION_NOT_PUBLISHED,
                    "Agent has no published version.",
                )
            if requested is not None and requested != definition.published_version_number:
                requested_version = self._catalog.get_version(definition, requested)
                if requested_version is not None and requested_version.status == VersionStatus.DRAFT:
                    raise RuntimeFailure(
                        RuntimeErrorCode.DRAFT_NOT_ALLOWED,
                        "Draft agent versions cannot run in production.",
                    )
                raise RuntimeFailure(
                    RuntimeErrorCode.PUBLISHED_POINTER_REQUIRED,
                    "Production runs the published agent version.",
                )
            version = self._catalog.get_version(definition, definition.published_version_number)
            if version is None or version.status != VersionStatus.PUBLISHED:
                raise RuntimeFailure(
                    RuntimeErrorCode.VERSION_NOT_PUBLISHED,
                    "Published agent version was not found.",
                )
            return version
        if requested is not None:
            version = self._catalog.get_version(definition, requested)
            if version is None:
                raise RuntimeFailure(RuntimeErrorCode.VERSION_NOT_PUBLISHED, "Agent version was not found.")
            return version
        if definition.published_version_number is not None:
            version = self._catalog.get_version(definition, definition.published_version_number)
            if version is not None and version.status == VersionStatus.PUBLISHED:
                return version
        draft = self._catalog.get_draft(definition)
        if draft is None:
            raise RuntimeFailure(RuntimeErrorCode.VERSION_NOT_PUBLISHED, "Agent has no version to test.")
        return draft

    def _select_workflow(self, version: AgentVersion, trigger: TriggerContext) -> WorkflowVersion:
        workflow_key = self._bound_workflow_key(version, trigger)
        pin = self._pin(version, workflow_key)
        workflow = self._catalog.get_workflow(pin.workflow_key, pin.version_number)
        if workflow is None:
            raise RuntimeFailure(RuntimeErrorCode.WORKFLOW_NOT_FOUND, "Workflow version was not found.")
        return workflow

    def _bound_workflow_key(self, version: AgentVersion, trigger: TriggerContext) -> str:
        for binding in version.trigger_bindings:
            if binding.trigger_key == trigger.trigger_key:
                return binding.workflow_key
        raise RuntimeFailure(
            RuntimeErrorCode.WORKFLOW_NOT_PINNED,
            "Trigger is not bound to this workflow.",
        )

    def _pin(self, version: AgentVersion, workflow_key: str):
        for pin in version.workflow_pins:
            if pin.workflow_key == workflow_key:
                return pin
        raise RuntimeFailure(
            RuntimeErrorCode.WORKFLOW_NOT_PINNED,
            "Workflow version is not pinned by the agent version.",
        )

    def _assert_owner(self, definition: AgentDefinition, trigger: TriggerContext) -> None:
        if definition.owner == OwnerKind.USER:
            if trigger.tenant_id != definition.user_id or trigger.user_id != definition.user_id:
                raise RuntimeFailure(RuntimeErrorCode.AGENT_NOT_FOUND, "Agent was not found.")
        elif definition.owner != OwnerKind.SYSTEM:
            raise RuntimeFailure(RuntimeErrorCode.INVALID_REQUEST, "Agent owner is invalid.")
