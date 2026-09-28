"""Errors for the generic policy engine.

Messages are fixed phrases. They do not include request values or secrets.
"""

from __future__ import annotations


class PolicyError(Exception):
    """Base error. ``code`` is safe to store."""

    code = "policy_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Policy failed.")


class InvalidPolicyRequest(PolicyError):
    code = "invalid_policy_request"

    def __init__(self) -> None:
        super().__init__("Policy request is not valid.")


class PolicyNotFound(PolicyError):
    code = "policy_not_found"

    def __init__(self) -> None:
        super().__init__("Unknown policy.")


class PolicyRejected(PolicyError):
    code = "policy_rejected"

    def __init__(self, message: str = "Policy was rejected.") -> None:
        super().__init__(message)


class PolicyBypass(PolicyError):
    code = "policy_bypass"

    def __init__(self) -> None:
        super().__init__("Policy bypass was rejected.")


class SecretRejected(PolicyError):
    code = "secret_rejected"

    def __init__(self) -> None:
        super().__init__("Policy data must not carry secrets.")


class PolicyMismatch(PolicyError):
    code = "policy_mismatch"

    def __init__(self) -> None:
        super().__init__("Policy request does not match the registered policy.")


class InvalidPolicyResult(PolicyError):
    code = "invalid_policy_result"

    def __init__(self) -> None:
        super().__init__("Policy result is not valid.")
