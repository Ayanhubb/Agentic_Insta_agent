"""Approval gates and secret-safe pipeline events."""

from __future__ import annotations

from models.content import ContentMode, ContentPlan, ContentType
from scheduler.approval_policy import decide_approval
from services.logging import redact_text, redact_value
from services.task_events import TaskEventLog


def _plan(**overrides: object) -> ContentPlan:
    payload = {
        "content_type": ContentType.PRODUCT,
        "theme": "silk sarees",
        "image_prompt": "A detailed photo of silk sarees in the shop",
        "business_context": "Demo Store sells sarees",
        "reason": "daily_automation",
        "featured_product_or_service": "sarees",
    }
    payload.update(overrides)
    return ContentPlan(**payload)  # type: ignore[arg-type]


def _profile() -> dict[str, str]:
    return {"business_name": "Demo Store", "products": "sarees", "services": "stitching", "description": ""}


def test_automatic_mode_approves_when_every_gate_passes() -> None:
    decision = decide_approval(
        mode=ContentMode.DAILY,
        automation={"auto_daily_publish": True},
        plan=_plan(),
        profile=_profile(),
        qa_passed=True,
        qa_malformed=False,
        ownership_ok=True,
        festival_valid=True,
        provider_malformed=False,
    )
    assert decision.status == "APPROVED"
    assert decision.publish is True
    assert decision.reasons == []


def test_human_mode_never_auto_approves() -> None:
    decision = decide_approval(
        mode=ContentMode.DAILY,
        automation={"auto_daily_publish": False},
        plan=_plan(),
        profile=_profile(),
        qa_passed=True,
        qa_malformed=False,
        ownership_ok=True,
        festival_valid=True,
        provider_malformed=False,
    )
    assert decision.status == "PENDING_APPROVAL"
    assert decision.publish is False
    assert decision.reasons[0] == "human_approval_required"


def test_each_blocking_gate_prevents_auto_approval() -> None:
    base = {
        "mode": ContentMode.DAILY,
        "automation": {"auto_daily_publish": True},
        "plan": _plan(),
        "profile": _profile(),
        "qa_passed": True,
        "qa_malformed": False,
        "ownership_ok": True,
        "festival_valid": True,
        "provider_malformed": False,
    }
    cases = {
        "image_qa_failed": {"qa_passed": False},
        "provider_result_malformed": {"provider_malformed": True},
        "product_reference_missing": {"plan": _plan(featured_product_or_service=None)},
        "required_logo_missing": {"plan": _plan(logo_required=True)},
        "offer_cannot_be_verified": {"plan": _plan(offer_text="50% off")},
        "festival_context_invalid": {"mode": ContentMode.FESTIVAL, "automation": {"auto_festival_publish": True}, "festival_valid": False},
        "tenant_ownership_failed": {"ownership_ok": False},
    }
    for reason, changes in cases.items():
        decision = decide_approval(**{**base, **changes})
        assert decision.publish is False, reason
        assert decision.status == "PENDING_APPROVAL", reason
        assert reason in decision.reasons, (reason, decision.reasons)


def test_pipeline_events_do_not_keep_secrets() -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123"
    log = TaskEventLog(task_id="task-1", correlation_id="corr-1", request_id="req-1", user_id="user-1")
    log.record(
        "MCP_CONTEXT_FETCHED",
        status="success",
        note=f"DEEPSEEK_API_KEY={secret}",
        deepseek_api_key=secret,
        canva_token=secret,
    )
    detail = log.events[0].detail
    assert secret not in str(detail)
    assert detail["deepseek_api_key"] == "[REDACTED]"
    assert detail["canva_token"] == "[REDACTED]"
    assert redact_text(f"CANVA_API_KEY={secret}") == "CANVA_API_KEY=[REDACTED]"
    assert redact_text(f"CANVA_TOKEN={secret}") == "CANVA_TOKEN=[REDACTED]"
    assert redact_text(f"JWT_SECRET={secret}") == "JWT_SECRET=[REDACTED]"
    assert redact_text(f"TOKEN_ENCRYPTION_KEY={secret}") == "TOKEN_ENCRYPTION_KEY=[REDACTED]"
    assert redact_value({"api_key": secret, "jwt_secret": secret, "token_encryption_key": secret}) == {
        "api_key": "[REDACTED]",
        "jwt_secret": "[REDACTED]",
        "token_encryption_key": "[REDACTED]",
    }
    assert log.events[0].correlation_id == "corr-1"
    assert log.events[0].request_id == "req-1"
