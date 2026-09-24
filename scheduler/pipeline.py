"""Scheduled campaign pipeline.

scheduled event → campaign decision → content → image → QA → approval → Instagram Agent.

Content Agent, Festival MCP, DeepSeek, OpenAI Image, DeepSeek Vision, and optional
Canva never publish. Instagram Agent is reached only through the publication service.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from models.content import ContentMode, ContentStrategyRequest, FestivalContext
from models.errors import AppError, ErrorCode
from services.publication import publication_block
from scheduler.approval_policy import ApprovalDecision, decide_approval
from scheduler.image_qa import ImageQAService
from services.metrics import PipelineMetrics, pipeline_metrics
from services.task_events import PipelineEventName, TaskEventLog

logger = logging.getLogger(__name__)


@dataclass
class PipelineOutcome:
    status: str
    correlation_id: str
    request_id: str
    task_id: str
    event_names: list[str] = field(default_factory=list)
    approval_status: str = "PENDING_APPROVAL"
    publish: bool = False
    retryable: bool = False
    reason: str | None = None
    reasons: list[str] = field(default_factory=list)
    post: Any | None = None
    image_id: str | None = None


class CampaignPipeline:
    def __init__(
        self,
        content_agent: Any,
        publisher: Any,
        session: Any,
        *,
        vision: Any | None = None,
        festival_mcp: Any | None = None,
        canva: Any | None = None,
        metrics: PipelineMetrics | None = None,
    ) -> None:
        self._content = content_agent
        self._publisher = publisher
        self._session = session
        self._vision = vision
        self._festival_mcp = festival_mcp
        self._canva = canva
        self._metrics = metrics or pipeline_metrics
        self._qa = ImageQAService(vision)

    async def execute(
        self,
        *,
        user_id: str,
        mode: ContentMode,
        profile: Any,
        automation: Any,
        account: Any | None,
        now: datetime,
        post_type: str,
        trigger: str,
        scheduled_date: date,
        festival: dict[str, Any] | None = None,
        festival_campaign_id: str | None = None,
        festival_sequence: int | None = None,
        user_prompt: str | None = None,
    ) -> PipelineOutcome:
        log = TaskEventLog(user_id=user_id, session=self._session, metrics=self._metrics)
        self._bind_sink(log)
        try:
            return await self._execute(
                log,
                user_id=user_id,
                mode=mode,
                profile=profile,
                automation=automation,
                account=account,
                now=now,
                post_type=post_type,
                trigger=trigger,
                scheduled_date=scheduled_date,
                festival=festival,
                festival_campaign_id=festival_campaign_id,
                festival_sequence=festival_sequence,
                user_prompt=user_prompt,
            )
        finally:
            self._bind_sink(None)

    async def _execute(self, log: TaskEventLog, **kwargs: Any) -> PipelineOutcome:
        user_id = kwargs["user_id"]
        mode: ContentMode = kwargs["mode"]
        profile = kwargs["profile"]
        automation = kwargs["automation"]
        festival = kwargs.get("festival")
        mcp_context = await self._fetch_mcp(log, user_id=user_id, mode=mode, festival=festival, now=kwargs["now"])
        strategy = ContentStrategyRequest(
            user_id=user_id,
            mode=mode,
            user_prompt=kwargs.get("user_prompt"),
            business_profile=profile,
            festival=festival,
            automation=automation if _is_automation_snapshot(automation) else _automation_payload(automation),
            instagram_account_id=getattr(kwargs["account"], "id", None),
            now=kwargs["now"],
            task_id=log.task_id,
            correlation_id=log.correlation_id,
            request_id=log.request_id,
        )
        result = await self._content.run(strategy)
        self._record_content_events(log, result)
        await self._optional_canva(log, result, user_id)

        image = getattr(result, "generated_image", None)
        plan = getattr(result, "plan", None)
        row = self._load_image(getattr(image, "id", None))
        ownership_ok = self._ownership_ok(user_id, image, row)
        provider_malformed = image is None or row is None or not getattr(image, "id", None) or not (
            getattr(image, "storage_path", None) or getattr(row, "storage_path", None)
        )
        log.record(PipelineEventName.IMAGE_QA_STARTED.value, status="success", correlation_id=log.correlation_id)
        qa = await self._qa.review(
            image=image,
            prompt=getattr(plan, "image_prompt", None),
            user_id=user_id,
            correlation_id=log.correlation_id,
        )
        if qa.passed and not qa.malformed and not provider_malformed:
            log.record(
                PipelineEventName.IMAGE_QA_PASSED.value,
                status="success",
                provider=qa.provider,
            )
        else:
            log.record(
                PipelineEventName.IMAGE_QA_FAILED.value,
                status="failed",
                provider=qa.provider,
                reasons=qa.reasons[:5],
            )

        festival_valid = _festival_valid(mode, festival, mcp_context)
        decision = decide_approval(
            mode=mode,
            automation=automation,
            plan=plan,
            profile=profile,
            qa_passed=qa.passed and not qa.malformed,
            qa_malformed=qa.malformed,
            ownership_ok=ownership_ok,
            festival_valid=festival_valid,
            provider_malformed=bool(provider_malformed),
        )
        self._apply_approval(row, decision, qa)
        outcome = PipelineOutcome(
            status="generated_pending_approval",
            correlation_id=log.correlation_id,
            request_id=log.request_id,
            task_id=log.task_id,
            approval_status=decision.status,
            publish=False,
            retryable=decision.retryable,
            reason=decision.reason,
            reasons=list(decision.reasons),
            image_id=getattr(image, "id", None) or getattr(row, "id", None),
        )
        if not decision.publish or row is None:
            if decision.retryable:
                outcome.status = "failed"
            outcome.event_names = log.names()
            self._metrics.increment("approval_held")
            return outcome

        log.record(PipelineEventName.APPROVAL_GRANTED.value, status="success", approval_status="APPROVED")
        self._metrics.increment("approval_granted")
        account = kwargs.get("account")
        owner = getattr(account, "user_id", None) if account is not None else None
        if owner is not None and owner != user_id:
            raise AppError(
                ErrorCode.PERMISSION_ERROR,
                "You can only publish to your connected Instagram account.",
                http_status=403,
            )
        block = publication_block(account)
        if block is not None:
            code, message = block
            raise AppError(
                code,
                message,
                http_status=401 if code == ErrorCode.AUTHENTICATION_ERROR else 409,
            )
        self._session.commit()
        log.record(PipelineEventName.INSTAGRAM_PUBLISH_STARTED.value, status="success")
        caption = getattr(plan, "caption_hint", None) or getattr(plan, "caption", None)
        post = await self._publisher.publish_generated_image(
            user_id=user_id,
            image=row,
            account=kwargs["account"],
            post_type=kwargs["post_type"],
            trigger=kwargs["trigger"],
            scheduled_date=kwargs["scheduled_date"],
            festival_campaign_id=kwargs.get("festival_campaign_id"),
            festival_sequence=kwargs.get("festival_sequence"),
            caption=caption,
            request_id=log.request_id,
        )
        media_id = getattr(post, "instagram_media_id", None)
        published = getattr(post, "status", None) == "PUBLISHED" and bool(media_id)
        if published:
            log.record(PipelineEventName.INSTAGRAM_PUBLISHED.value, status="success")
            log.record(PipelineEventName.INSTAGRAM_VERIFIED.value, status="success")
            self._metrics.increment("instagram_verified")
        outcome.status = str(getattr(post, "status", None) or "FAILED")
        outcome.publish = published
        outcome.post = post
        outcome.approval_status = "APPROVED"
        outcome.event_names = log.names()
        return outcome

    def _bind_sink(self, sink: TaskEventLog | None) -> None:
        bind = getattr(self._content, "bind_event_sink", None)
        if callable(bind):
            bind(sink)

    def _record_content_events(self, log: TaskEventLog, result: Any) -> None:
        if log.has(PipelineEventName.CONTENT_PLAN_CREATED.value):
            return
        plan = getattr(result, "plan", None)
        log.record(
            PipelineEventName.CONTENT_PLAN_CREATED.value,
            status="success",
            content_type=str(getattr(getattr(plan, "content_type", None), "value", getattr(plan, "content_type", ""))),
        )
        image = getattr(result, "generated_image", None)
        if image is None:
            return
        log.record(PipelineEventName.IMAGE_GENERATION_STARTED.value, status="success")
        log.record(
            PipelineEventName.IMAGE_GENERATED.value,
            status="success",
            provider=getattr(image, "provider", None) or "image_provider",
            image_id=getattr(image, "id", None),
        )

    async def _fetch_mcp(
        self,
        log: TaskEventLog,
        *,
        user_id: str,
        mode: ContentMode,
        festival: dict[str, Any] | None,
        now: datetime,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "source": "campaign",
            "valid": True,
            "festival_mcp": self._festival_mcp is not None,
            "mode": mode.value,
        }
        if self._festival_mcp is not None and mode == ContentMode.FESTIVAL:
            fetched = await _call_mcp(
                self._festival_mcp,
                user_id=user_id,
                on_date=now.date(),
                festival_name=_festival_name(festival),
            )
            context = {
                "source": "festival_mcp",
                "valid": bool(fetched.get("valid", True)) if isinstance(fetched, dict) else False,
                "festival_mcp": True,
                "mode": mode.value,
            }
            if not isinstance(fetched, dict):
                context["valid"] = False
        status = "success" if context.get("valid") else "failed"
        log.record(
            PipelineEventName.MCP_CONTEXT_FETCHED.value,
            status=status,
            source=context["source"],
            mode=mode.value,
        )
        return context

    async def _optional_canva(self, log: TaskEventLog, result: Any, user_id: str) -> None:
        action = getattr(getattr(result, "plan", None), "canva_action", None)
        if self._canva is None or not str(action or "").strip() or str(action).strip().lower() == "none":
            return
        try:
            await _call_optional(
                self._canva,
                ("apply", "render", "execute"),
                user_id=user_id,
                action=str(action),
                image_id=getattr(getattr(result, "generated_image", None), "id", None),
                correlation_id=log.correlation_id,
            )
        except Exception:
            logger.warning("Optional Canva step failed", extra={"task_id": log.task_id, "request_id": log.request_id})

    def _load_image(self, image_id: str | None) -> Any | None:
        if not image_id:
            return None
        from db.models import GeneratedImage

        return self._session.get(GeneratedImage, image_id)

    def _ownership_ok(self, user_id: str, image: Any, row: Any) -> bool:
        if row is None or getattr(row, "user_id", None) != user_id:
            return False
        snapshot_owner = getattr(image, "user_id", None)
        if snapshot_owner and snapshot_owner != user_id:
            return False
        return True

    def _apply_approval(self, row: Any, decision: ApprovalDecision, qa: Any | None = None) -> None:
        if row is None:
            return
        row.approval_status = "APPROVED" if decision.publish else "PENDING_APPROVAL"
        if decision.publish:
            row.approved_at = datetime.now(timezone.utc)
        if qa is not None and hasattr(row, "qa_status"):
            row.qa_status = "PASSED" if getattr(qa, "passed", False) and not getattr(qa, "malformed", False) else "FAILED"
            if hasattr(row, "qa_json"):
                row.qa_json = {
                    "passed": bool(getattr(qa, "passed", False)),
                    "reasons": list(getattr(qa, "reasons", []) or [])[:8],
                    "provider": getattr(qa, "provider", None),
                }


def _is_automation_snapshot(automation: Any) -> bool:
    from models.content import AutomationSettingsSnapshot

    return isinstance(automation, (AutomationSettingsSnapshot, dict))


def _automation_payload(automation: Any) -> dict[str, Any]:
    from scheduler.policies import automation_context

    return automation_context(automation)


def _festival_name(festival: dict[str, Any] | None) -> str | None:
    if not festival:
        return None
    return str(festival.get("festival_name") or festival.get("name") or "").strip() or None


def _festival_valid(mode: ContentMode, festival: dict[str, Any] | FestivalContext | None, mcp_context: dict[str, Any]) -> bool:
    if mode != ContentMode.FESTIVAL:
        return True
    if mcp_context.get("valid") is False:
        return False
    if festival is None:
        return False
    try:
        ctx = festival if isinstance(festival, FestivalContext) else FestivalContext.model_validate(festival)
    except Exception:
        return False
    if not ctx.display_name or ctx.date is None:
        return False
    return True


async def _call_mcp(client: Any, **kwargs: Any) -> Any:
    for name in ("get_festival_context", "fetch_context", "get_context"):
        method = getattr(client, name, None)
        if not callable(method):
            continue
        try:
            result = method(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result if isinstance(result, dict) else {"valid": False}
        except TypeError:
            continue
        except Exception:
            logger.warning("Festival MCP context fetch failed")
            return {"valid": False}
    return {"valid": False}


async def _call_optional(client: Any, names: tuple[str, ...], **kwargs: Any) -> Any:
    for name in names:
        method = getattr(client, name, None)
        if not callable(method):
            continue
        result = method(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    return None
