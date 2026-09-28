"""Generic approval engine.

Human decisions are stored here. Publishing permission is granted only after
a person approves an eligible Instagram publication request.
"""

from agent_platform.approval.engine import ApprovalEngine
from agent_platform.approval.errors import (
    ApprovalError,
    ApprovalNotFound,
    InvalidApprovalState,
    MissingApproval,
    PolicyDenied,
)
from agent_platform.approval.interfaces import ApprovalGate
from agent_platform.approval.types import ApprovalRecord

__all__ = [
    "ApprovalEngine",
    "ApprovalError",
    "ApprovalGate",
    "ApprovalNotFound",
    "ApprovalRecord",
    "InvalidApprovalState",
    "MissingApproval",
    "PolicyDenied",
]
