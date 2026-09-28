"""Closed registry of workflow implementations.

Keys are registered by product packages. The runtime does not invent workflows
and does not execute model-supplied code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

WorkflowRunner = Callable[..., Awaitable[dict[str, Any]]]

_RUNNERS: dict[str, WorkflowRunner] = {}


class UnknownWorkflow(Exception):
    def __init__(self, workflow_key: str) -> None:
        self.workflow_key = workflow_key
        super().__init__(workflow_key)


def register_workflow(workflow_key: str, runner: WorkflowRunner) -> None:
    key = str(workflow_key or "").strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("workflow key is invalid")
    _RUNNERS[key] = runner


def resolve_workflow(workflow_key: str) -> WorkflowRunner:
    runner = _RUNNERS.get(str(workflow_key or "").strip())
    if runner is None:
        raise UnknownWorkflow(str(workflow_key or ""))
    return runner


def registered_workflow_keys() -> tuple[str, ...]:
    return tuple(sorted(_RUNNERS))
