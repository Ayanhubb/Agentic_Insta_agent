"""Errors for the generic workflow engine.

Messages are fixed phrases. They do not include handler tracebacks or secret values.
"""

from __future__ import annotations


class WorkflowError(Exception):
    """Base error. ``code`` is safe to store on an execution."""

    code = "workflow_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Workflow failed.")


class InvalidDefinition(WorkflowError):
    code = "invalid_definition"

    def __init__(self, message: str = "Workflow definition is not valid.") -> None:
        super().__init__(message)


class UnknownCapability(WorkflowError):
    code = "unknown_capability"

    def __init__(self) -> None:
        super().__init__("Unknown capability.")


class CapabilityRejected(WorkflowError):
    code = "capability_rejected"

    def __init__(self, message: str = "Capability is not allowed.") -> None:
        super().__init__(message)


class SecretRejected(WorkflowError):
    code = "secret_rejected"

    def __init__(self) -> None:
        super().__init__("Workflow data must not carry secrets.")


class ExecutionNotFound(WorkflowError):
    code = "execution_not_found"

    def __init__(self) -> None:
        super().__init__("Execution not found.")


class InvalidExecutionState(WorkflowError):
    code = "invalid_execution_state"

    def __init__(self, message: str = "Execution cannot accept that operation.") -> None:
        super().__init__(message)


class CapabilityFailure(WorkflowError):
    """Raised by a registered handler to fail a step with a stable code."""

    def __init__(self, code: str, message: str = "Step failed.") -> None:
        if not code or not code.replace("_", "").isalnum():
            code = "failed"
        self.code = code
        super().__init__(message)
