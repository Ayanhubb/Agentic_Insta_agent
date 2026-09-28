"""Instagram Marketing Agent, the first reference agent on the platform.

The package registers a definition and binds it to the existing studio.
It does not replace ``InstagramAgent`` or the ``/api/v1`` routes.
"""

from instagram_reference.binding import build_reference_runtime, dispatch
from instagram_reference.definition import (
    AGENT_KEY,
    DISPLAY_NAME,
    POLICY_ID,
    REFERENCE_STEP_KEYS,
    TRIGGER_KEY,
    WORKFLOW_KEY,
    instagram_marketing_definition,
    register_instagram_marketing,
)

__all__ = [
    "AGENT_KEY",
    "DISPLAY_NAME",
    "POLICY_ID",
    "REFERENCE_STEP_KEYS",
    "TRIGGER_KEY",
    "WORKFLOW_KEY",
    "build_reference_runtime",
    "dispatch",
    "instagram_marketing_definition",
    "register_instagram_marketing",
]
