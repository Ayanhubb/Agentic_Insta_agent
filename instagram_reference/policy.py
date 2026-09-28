"""Approval binding. The decision is ``decide_approval``. This module does not publish."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime.models import ApprovalDecision as RuntimeApproval
from agent_runtime.models import ExecutionContext, WorkflowStep
from instagram_reference.boundary import mentions_publish_tool, public_text
from models.content import ContentMode
from scheduler.approval_policy import decide_approval


class InstagramStillImagePolicy:
    """Policy id ``instagram.still_image``. Publish is true only when the existing gates pass."""

    def evaluate(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        state: Mapping[str, Any],
    ) -> RuntimeApproval:
        del ctx, step
        if mentions_publish_tool(state.get("content_planning")) or mentions_publish_tool(state.get("image_generation")):
            return RuntimeApproval(
                allowed=False,
                reason="A publish tool is not a planning step.",
                requires_human=False,
            )
        decision = decide_approval(**_inputs(state))
        if decision.publish:
            return RuntimeApproval(allowed=True, reason="Approved.", requires_human=False)
        reason = public_text(", ".join(decision.reasons)) or "Held for approval."
        return RuntimeApproval(
            allowed=False,
            reason=reason,
            requires_human="human_approval_required" in decision.reasons,
        )


def _inputs(state: Mapping[str, Any]) -> dict[str, Any]:
    incoming = state.get("input") if isinstance(state.get("input"), dict) else {}
    mode = _mode(incoming.get("mode"))
    automation = incoming.get("automation") if isinstance(incoming.get("automation"), dict) else {}
    vision = state.get("vision_qa") if isinstance(state.get("vision_qa"), dict) else {}
    malformed = bool(vision.get("malformed"))
    image = state.get("image_generation") if isinstance(state.get("image_generation"), dict) else {}
    return {
        "mode": mode,
        "automation": automation,
        "plan": _plan(state),
        "profile": _profile(state, incoming),
        "qa_passed": bool(vision.get("passed")) and not malformed,
        "qa_malformed": malformed,
        "ownership_ok": incoming.get("ownership_ok") is True,
        "festival_valid": _festival_valid(mode, state),
        "provider_malformed": malformed or bool(image.get("malformed")),
    }


def _mode(value: object) -> ContentMode:
    try:
        return ContentMode(str(value or "").strip().upper())
    except ValueError:
        return ContentMode.USER_PROMPT


def _plan(state: Mapping[str, Any]) -> dict[str, Any]:
    planning = state.get("content_planning")
    if isinstance(planning, dict) and isinstance(planning.get("plan"), dict):
        return planning["plan"]
    return {}


def _profile(state: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    business = state.get("load_business_context")
    if isinstance(business, dict):
        live = business.get("live")
        if isinstance(live, dict) and isinstance(live.get("profile"), dict):
            return live["profile"]
    profile = incoming.get("profile")
    if isinstance(profile, dict):
        return profile
    return {}


def _festival_valid(mode: ContentMode, state: Mapping[str, Any]) -> bool:
    if mode != ContentMode.FESTIVAL:
        return True
    festival = state.get("load_festival_context")
    return isinstance(festival, dict) and festival.get("found") is True and festival.get("applicable") is True
