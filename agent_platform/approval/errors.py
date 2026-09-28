"""Errors for the generic approval engine.

Messages are fixed phrases. They do not include tenant ids or record contents.
"""

from __future__ import annotations


class ApprovalError(Exception):
    """Base error. ``code`` is safe to store."""

    code = "approval_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Approval failed.")


class ApprovalNotFound(ApprovalError):
    code = "approval_not_found"

    def __init__(self) -> None:
        super().__init__("Approval not found.")


class MissingApproval(ApprovalError):
    code = "missing_approval"

    def __init__(self) -> None:
        super().__init__("Approval is required before the next step.")


class PolicyDenied(ApprovalError):
    code = "policy_denied"

    def __init__(self) -> None:
        super().__init__("Policy denied the next step.")


class InvalidApprovalState(ApprovalError):
    code = "invalid_approval_state"

    def __init__(self) -> None:
        super().__init__("Approval can no longer be changed.")
