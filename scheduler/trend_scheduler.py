"""Daily trend intelligence.

A trend report is not a publish command. Content is generated only when the
user's existing automatic-publishing setting is on, and even then it goes
through the Content Agent, image QA, and the approval policy. The Instagram
Agent is reached only by that existing pipeline.
"""

from __future__ import annotations

import importlib
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from config import Settings
from db.models import AutomationSettings, FestivalCampaign, FestivalPost, Product, User
from db.repositories import (
    AutomationRepository,
    BusinessRepository,
    InstagramAccountRepository,
    PostRepository,
    UserRepository,
)
from festivals.festival_service import FestivalService
from festivals.india_festivals import festivals_for_year
from models.content import ContentMode
from models.errors import AppError
from scheduler.policies import is_at_or_after_local_time
from scheduler.trend_store import (
    TrendReportStore,
    opportunity_is_expired,
    opportunity_public,
)
from services.clock import Clock

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Kolkata"
_FESTIVAL_WINDOW_DAYS = 14
_SECRET_KEYS = frozenset(
    {"access_token", "access_token_encrypted", "api_key", "authorization", "token", "password"}
)
_RESEARCH = (
    ("trends.research", "fetch_observations"),
    ("backend.trends.research", "fetch_observations"),
    ("ai.trend.research", "fetch_observations"),
)
_ANALYZER = (
    ("ai.trend.deepseek", "analyze_trends"),
    ("backend.ai.trend.deepseek", "analyze_trends"),
    ("ai.llm.trend", "analyze_trends"),
)


class TrendIntelligenceScheduler:
    def __init__(
        self,
        settings: Settings,
        session: Session,
        clock: Clock,
        *,
        meta: Any | None = None,
        research: Any | None = None,
        analyzer: Any | None = None,
        llm: Any | None = None,
        content_agent: Any | None = None,
        publisher: Any | None = None,
        vision: Any | None = None,
        festival_mcp: Any | None = None,
        canva: Any | None = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._clock = clock
        self._meta = meta
        self._research = research if research is not None else _load_optional(_RESEARCH)
        self._llm = llm
        self._analyzer = _resolve_analyzer(analyzer, settings, llm=llm)
        self._content = content_agent
        self._publisher = publisher
        self._vision = vision
        self._festival_mcp = festival_mcp
        self._canva = canva
        self._default_meta: Any | None = None
        self._users = UserRepository(session)
        self._business = BusinessRepository(session)
        self._accounts = InstagramAccountRepository(session)
        self._automation = AutomationRepository(session)
        self._posts = PostRepository(session)
        self._festivals = FestivalService(session)
        self._store = TrendReportStore(session)

    async def run_all(self, *, force: bool = False) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for automation in self._enabled_automations():
            if automation.user_id in seen:
                continue
            seen.add(automation.user_id)
            user = self._users.get_by_id(automation.user_id)
            if user is None or not user.is_active:
                continue
            results.append(await self.run_user(user, automation, force=force))
        return results

    async def run_user(
        self,
        user: User,
        automation: AutomationSettings,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        if not automation.daily_enabled and not automation.festival_enabled:
            return {"status": "skipped", "reason": "automation_disabled"}
        profile = self._business.get_for_user(user.id)
        if profile is None:
            return {"status": "skipped", "reason": "missing_business_profile"}
        timezone_name = self._timezone_name(automation, user, profile)
        now = self._clock.now(timezone_name)
        local_date = now.date()
        expired_opportunities = self._expire_stored(user.id, now)
        if not force and not is_at_or_after_local_time(now, automation.daily_post_time):
            return {
                "status": "skipped",
                "reason": "before_post_time",
                "local_date": local_date.isoformat(),
                "timezone": timezone_name,
                "expired_opportunities": expired_opportunities,
            }

        existing = self._store.get_report(user.id, local_date)
        if existing is not None:
            return {
                "status": "already_reported",
                "report_id": existing.id,
                "local_date": local_date.isoformat(),
                "timezone": timezone_name,
                "mode": existing.mode,
                "published": False,
                "expired_opportunities": expired_opportunities,
            }

        context, limitations = await self._collect(user.id, profile, automation, now, local_date)
        brief, degraded = await self._analyze(context)
        if degraded:
            limitations.append("deepseek_unavailable")
        report = _build_report(
            generated_at=now.isoformat(),
            timezone_name=timezone_name,
            local_date=local_date,
            context=context,
            brief=brief,
            limitations=limitations,
            degraded=degraded,
        )
        report["dashboard_notified"] = True
        row = self._store.save_report(
            user_id=user.id,
            business_id=profile.id,
            local_date=local_date,
            timezone=timezone_name,
            mode=report["mode"],
            payload=report,
        )
        created = self._store_opportunities(
            user.id,
            profile.id,
            local_date,
            report["content_opportunities"],
            now,
        )
        self._session.flush()

        generated = 0
        published = 0
        if created:
            generated, published = await self._maybe_generate(
                user_id=user.id,
                profile=profile,
                automation=automation,
                account=self._accounts.get_primary(user.id),
                now=now,
                local_date=local_date,
                opportunities=created,
                festivals=context["festivals"],
            )
        return {
            "status": "reported",
            "report_id": row.id,
            "local_date": local_date.isoformat(),
            "timezone": timezone_name,
            "mode": report["mode"],
            "opportunities": len(created),
            "generated": generated,
            "published": published,
            "published_because_trend_only": False,
        }

    def _enabled_automations(self) -> list[AutomationSettings]:
        return list(
            self._session.scalars(
                select(AutomationSettings).where(
                    or_(
                        AutomationSettings.daily_enabled.is_(True),
                        AutomationSettings.festival_enabled.is_(True),
                    )
                )
            )
        )

    def _timezone_name(self, automation: AutomationSettings, user: User | None = None, profile: Any | None = None) -> str:
        candidates = (
            getattr(automation, "timezone", None),
            getattr(user, "timezone", None) if user is not None else None,
            getattr(profile, "timezone", None) if profile is not None else None,
            getattr(self._settings, "default_timezone", None),
            DEFAULT_TIMEZONE,
        )
        for raw in candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            key = _zone_key(text)
            if key:
                return key
        return DEFAULT_TIMEZONE

    def _expire_stored(self, user_id: str, now: datetime) -> int:
        expired = self._store.expire_opportunities(user_id, now)
        try:
            from db.trend_retention import TrendRetention

            moment = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
            TrendRetention(self._session).apply(moment, user_id=user_id)
        except Exception:
            logger.warning("Trend expiration update failed")
        return expired

    async def _collect(
        self,
        user_id: str,
        profile: Any,
        automation: AutomationSettings,
        now: datetime,
        local_date: date,
    ) -> tuple[dict[str, Any], list[str]]:
        limitations: list[str] = []
        account, performance, meta_failed = await self._fetch_meta(user_id)
        if meta_failed:
            limitations.append("meta_unavailable")
            account = None
            performance = None
        observations, research_failed = await self._fetch_observations(user_id, profile)
        if research_failed:
            limitations.append("trend_research_unavailable")
            observations = []
        festivals, festival_failed = self._fetch_festivals(user_id, local_date, automation)
        if festival_failed:
            limitations.append("festival_source_unavailable")
            festivals = []
        products, offers, products_failed = self._fetch_products(user_id)
        if products_failed:
            limitations.append("products_unavailable")
            products, offers = [], []
        active, expired = _split_observations(observations, local_date)
        context = {
            "user_id": user_id,
            "business_id": profile.id,
            "timezone": (automation.timezone or "").strip() or DEFAULT_TIMEZONE,
            "local_date": local_date.isoformat(),
            "generated_at": now.isoformat(),
            "account": _without_secrets(account) if isinstance(account, dict) else None,
            "performance": performance if isinstance(performance, dict) else _local_performance(self._posts, user_id)
            if not meta_failed
            else None,
            "observations": active,
            "expired_observations": expired,
            "festivals": festivals,
            "business": _business_payload(profile, products, offers),
            "products": products,
            "offers": offers,
        }
        if meta_failed:
            context["performance"] = None
            context["account"] = None
        return context, limitations

    async def _fetch_meta(self, user_id: str) -> tuple[Any, Any, bool]:
        source = self._meta if self._meta is not None else self._intelligence_source()
        if source is None:
            return None, None, False
        failed = False
        account = None
        performance = None
        try:
            account = await _maybe_await(source.fetch_account(user_id))
        except Exception:
            failed = True
        try:
            performance = await _maybe_await(source.fetch_performance(user_id))
        except Exception:
            failed = True
        return account, performance, failed

    async def _fetch_observations(self, user_id: str, profile: Any) -> tuple[list[dict[str, Any]], bool]:
        if self._research is None:
            return [], True
        try:
            payload = {"user_id": user_id, "business_id": profile.id, "business_type": profile.business_type}
            raw = await _maybe_await(_call_research(self._research, payload))
        except Exception:
            return [], True
        if not isinstance(raw, list):
            return [], True
        observations = [item for item in raw if isinstance(item, dict) and str(item.get("id") or "").strip()]
        return observations, False

    def _fetch_festivals(
        self,
        user_id: str,
        local_date: date,
        automation: AutomationSettings,
    ) -> tuple[list[dict[str, Any]], bool]:
        try:
            required = int(automation.festival_posts_per_festival or 2)
            relevant: list[dict[str, Any]] = []
            years = {local_date.year}
            if local_date.month == 12:
                years.add(local_date.year + 1)
            catalog = []
            for year in sorted(years):
                catalog.extend(festivals_for_year(year))
            for item in catalog:
                occurs = date.fromisoformat(str(item["date"]))
                if local_date <= occurs <= local_date + timedelta(days=_FESTIVAL_WINDOW_DAYS):
                    campaign = self._festivals._repo.get_or_create_campaign(
                        user_id,
                        festival_name=str(item["festival_name"]),
                        festival_date=occurs,
                        year=occurs.year,
                        required_posts=required,
                        enabled=bool(automation.festival_enabled),
                    )
                    relevant.append(
                        {
                            "festival_name": campaign.festival_name,
                            "name": campaign.festival_name,
                            "date": occurs.isoformat(),
                            "campaign_id": campaign.id,
                            "required_posts": campaign.required_posts,
                            "published_posts": campaign.published_posts,
                            "remaining_posts": campaign.remaining_posts,
                            "source": "festival_calendar",
                        }
                    )
            return relevant, False
        except Exception:
            logger.warning("Festival source failed for trend report")
            return [], True

    def _fetch_products(self, user_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        try:
            rows = list(self._session.scalars(select(Product).where(Product.user_id == user_id, Product.is_active.is_(True))))
        except Exception:
            return [], [], True
        products: list[dict[str, Any]] = []
        offers: list[dict[str, Any]] = []
        for row in rows:
            item = {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "offer": row.offer,
            }
            products.append(item)
            if (row.offer or "").strip():
                offers.append({"product_id": row.id, "name": row.name, "offer": row.offer})
        return products, offers, False

    async def _analyze(self, context: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        if self._analyzer is None:
            return None, True
        try:
            raw = await _maybe_await(_call_analyzer(self._analyzer, context))
        except Exception:
            return None, True
        if not isinstance(raw, dict):
            return None, True
        return raw, False

    def _store_opportunities(
        self,
        user_id: str,
        business_id: str,
        local_date: date,
        opportunities: list[dict[str, Any]],
        now: datetime,
    ) -> list[Any]:
        created = []
        for item in opportunities:
            row, is_new = self._store.save_opportunity(
                user_id=user_id,
                business_id=business_id,
                trend_id=str(item["trend_id"]),
                local_date=local_date,
                campaign_id=str(item.get("campaign_id") or ""),
                title=str(item.get("title") or "Content opportunity"),
                why_now=str(item["why_now"]),
                creative_direction=item.get("creative_direction"),
                recommended_format=item.get("recommended_format"),
                product_id=item.get("product_id"),
                festival_id=item.get("festival_id"),
                confidence=_confidence(item.get("confidence")),
                expires_at=_parse_datetime(item.get("expires_at")),
                status="EXPIRED" if opportunity_is_expired(_parse_datetime(item.get("expires_at")), now) else "NEW",
                payload=item,
            )
            if is_new and row.status != "EXPIRED":
                created.append(row)
        return created

    async def _maybe_generate(
        self,
        *,
        user_id: str,
        profile: Any,
        automation: AutomationSettings,
        account: Any,
        now: datetime,
        local_date: date,
        opportunities: list[Any],
        festivals: list[dict[str, Any]],
    ) -> tuple[int, int]:
        if self._content is None or self._publisher is None:
            return 0, 0
        from scheduler.pipeline import CampaignPipeline

        pipeline = CampaignPipeline(
            self._content,
            self._publisher,
            self._session,
            vision=self._vision,
            festival_mcp=self._festival_mcp,
            canva=self._canva,
        )
        due_ids = self._due_festival_ids(user_id, local_date, automation)
        generated = 0
        published = 0
        festival_by_campaign = {str(item.get("campaign_id")): item for item in festivals if item.get("campaign_id")}
        for opportunity in opportunities:
            if opportunity.status == "EXPIRED" or opportunity_is_expired(opportunity.expires_at, now):
                opportunity.status = "EXPIRED"
                continue
            festival_item = festival_by_campaign.get(opportunity.campaign_id)
            automatic = _automatic_for(opportunity, automation, festival_item)
            if not automatic:
                continue
            if festival_item is not None and not self._festival_can_publish(
                festival_item,
                due_ids,
                local_date,
                automation,
            ):
                continue
            mode = ContentMode.FESTIVAL if festival_item is not None else ContentMode.DAILY
            festival_payload = None
            if festival_item is not None:
                festival_payload = {
                    "name": festival_item["festival_name"],
                    "festival_name": festival_item["festival_name"],
                    "date": date.fromisoformat(festival_item["date"]),
                    "year": date.fromisoformat(festival_item["date"]).year,
                    "campaign_id": festival_item["campaign_id"],
                }
            prompt = _opportunity_prompt(opportunity)
            try:
                outcome = await pipeline.execute(
                    user_id=user_id,
                    mode=mode,
                    profile=profile,
                    automation=automation,
                    account=account,
                    now=now,
                    post_type="FESTIVAL" if festival_item is not None else "TREND",
                    trigger="FESTIVAL_AUTOMATION" if festival_item is not None else "TREND_INTELLIGENCE",
                    scheduled_date=local_date,
                    festival=festival_payload,
                    festival_campaign_id=festival_item["campaign_id"] if festival_item else None,
                    user_prompt=prompt,
                )
            except AppError:
                continue
            generated += 1
            if outcome.publish:
                opportunity.status = "USED"
                published += 1
                if festival_item is not None and outcome.post is not None:
                    self._record_festival_publication(str(festival_item["campaign_id"]), outcome.post, now)
            else:
                opportunity.status = "REVIEWED"
        self._session.flush()
        return generated, published

    def _intelligence_source(self) -> Any | None:
        if self._default_meta is not None:
            return self._default_meta
        try:
            from services.instagram_intelligence import AccountIntelligenceService
        except ImportError:
            return None
        self._default_meta = _InstagramIntelligenceSource(self._settings, self._session, AccountIntelligenceService)
        return self._default_meta

    def _due_festival_ids(self, user_id: str, local_date: date, automation: AutomationSettings) -> set[str]:
        try:
            due = self._festivals.due_campaigns(
                user_id,
                local_date,
                pre_festival_days=int(getattr(automation, "pre_festival_days", 1) or 1),
                allow_same_day=bool(getattr(automation, "allow_same_day_festival_posts", False)),
            )
        except Exception:
            logger.warning("Festival due check failed for trend generation")
            return set()
        return {campaign.id for campaign, _kind in due}

    def _festival_can_publish(
        self,
        festival_item: dict[str, Any],
        due_ids: set[str],
        local_date: date,
        automation: AutomationSettings,
    ) -> bool:
        campaign_id = str(festival_item.get("campaign_id") or "")
        if not campaign_id or campaign_id not in due_ids:
            return False
        campaign = self._session.get(FestivalCampaign, campaign_id)
        if campaign is None or int(campaign.remaining_posts or 0) <= 0:
            return False
        if bool(getattr(automation, "allow_same_day_festival_posts", False)):
            return True
        return not _festival_posted_today(self._session, campaign_id, local_date)

    def _record_festival_publication(self, campaign_id: str, post: Any, when: datetime) -> None:
        try:
            campaign = self._session.get(FestivalCampaign, campaign_id)
            if campaign is None or int(campaign.remaining_posts or 0) <= 0:
                return
            sequence = self._festivals._repo.next_sequence(campaign)
            if sequence > int(campaign.required_posts or 0):
                return
            fest_post = self._festivals._repo.add_festival_post(
                campaign,
                sequence,
                status="PENDING",
                scheduled_for=when,
            )
            image_id = getattr(post, "generated_image_id", None)
            if image_id:
                fest_post.generated_image_id = image_id
            self._festivals.record_publication(campaign, fest_post, post)
        except Exception:
            logger.warning("Festival campaign count was not updated after trend publication")


def _festival_posted_today(session: Session, campaign_id: str, today: date) -> bool:
    rows = session.scalars(select(FestivalPost).where(FestivalPost.campaign_id == campaign_id))
    for item in rows:
        scheduled = item.scheduled_for
        if scheduled is None:
            continue
        if scheduled.date() == today and item.status in {"PUBLISHED", "AMBIGUOUS_PUBLICATION", "PUBLISHING"}:
            return True
    return False


def _zone_key(name: str) -> str | None:
    try:
        return ZoneInfo(name).key
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _automatic_for(opportunity: Any, automation: AutomationSettings, festival_item: dict[str, Any] | None) -> bool:
    if festival_item is not None or (opportunity.campaign_id or ""):
        return bool(automation.auto_festival_publish) and festival_item is not None
    return bool(automation.auto_daily_publish)


def _opportunity_prompt(opportunity: Any) -> str:
    parts = [opportunity.title, opportunity.why_now]
    if opportunity.creative_direction:
        parts.append(opportunity.creative_direction)
    return "\n".join(part for part in parts if part)


def _build_report(
    *,
    generated_at: str,
    timezone_name: str,
    local_date: date,
    context: dict[str, Any],
    brief: dict[str, Any] | None,
    limitations: list[str],
    degraded: bool,
) -> dict[str, Any]:
    observations = list(context["observations"])
    observation_ids = {str(item.get("id")) for item in observations}
    expired = list(context["expired_observations"])
    festivals = list(context["festivals"])
    festival_keys = _festival_keys(festivals)
    product_keys = _product_keys(context["products"])
    removed = 0
    current_trends = []
    content_opportunities = []
    model_festival = []
    model_product = []
    gaps: list[str] = []
    if isinstance(brief, dict):
        current_trends, dropped = _backed_trends(brief.get("current_trends"), observation_ids, observations)
        removed += dropped
        content_opportunities, dropped = _backed_content(
            brief.get("content_opportunities"),
            observation_ids,
            festival_keys,
            product_keys,
        )
        removed += dropped
        model_festival = brief.get("festival_opportunities") if isinstance(brief.get("festival_opportunities"), list) else []
        model_product = brief.get("business_opportunities") if isinstance(brief.get("business_opportunities"), list) else []
        gaps = [str(item)[:120] for item in brief.get("data_gaps") or [] if str(item).strip()][:8]
    if removed:
        limitations.append("unbacked_recommendations_removed")
    limitations.extend(gap for gap in gaps if gap not in limitations)
    return {
        "generated_at": generated_at,
        "timezone": timezone_name,
        "local_date": local_date.isoformat(),
        "mode": "degraded" if degraded else "full",
        "account_performance": context.get("performance"),
        "new_observations": observations,
        "current_trends": current_trends,
        "festival_opportunities": _festival_section(festivals, model_festival, festival_keys),
        "product_opportunities": _product_section(context["products"], context["offers"], model_product, product_keys, degraded),
        "content_opportunities": content_opportunities,
        "expired_trends": expired,
        "data_limitations": _unique(limitations),
        "dashboard_notified": False,
    }


def _backed_trends(
    raw: Any,
    observation_ids: set[str],
    observations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(raw, list):
        return [], 0
    by_id = {str(item.get("id")): item for item in observations}
    kept: list[dict[str, Any]] = []
    dropped = 0
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        trend_id = str(item.get("trend_id") or item.get("id") or "").strip()
        source = by_id.get(trend_id)
        if source is None or trend_id not in observation_ids:
            dropped += 1
            continue
        kept.append(
            {
                "trend_id": trend_id,
                "title": source.get("title"),
                "trend_type": source.get("trend_type"),
                "observed_at": source.get("observed_at"),
                "source": source.get("source"),
                "evidence": source.get("evidence"),
            }
        )
    return kept, dropped


def _backed_content(
    raw: Any,
    observation_ids: set[str],
    festival_keys: set[str],
    product_keys: set[str],
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(raw, list):
        return [], 0
    kept: list[dict[str, Any]] = []
    dropped = 0
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        trend_id = str(item.get("trend_id") or "").strip()
        campaign_id = str(item.get("campaign_id") or "").strip()
        festival_id = str(item.get("festival_id") or "").strip()
        product_id = str(item.get("product_id") or "").strip()
        why_now = str(item.get("why_now") or "").strip()
        title = str(item.get("title") or "").strip()
        if not trend_id or not why_now or not title:
            dropped += 1
            continue
        if trend_id not in observation_ids:
            dropped += 1
            continue
        if campaign_id and campaign_id not in festival_keys:
            dropped += 1
            continue
        if festival_id and festival_id not in festival_keys:
            dropped += 1
            continue
        if product_id and product_id not in product_keys:
            dropped += 1
            continue
        if not campaign_id and not festival_id and not product_id and trend_id not in observation_ids:
            dropped += 1
            continue
        key = (trend_id, campaign_id)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(
            {
                "trend_id": trend_id,
                "campaign_id": campaign_id or None,
                "festival_id": festival_id or None,
                "product_id": product_id or None,
                "title": title,
                "why_now": why_now,
                "creative_direction": item.get("creative_direction"),
                "recommended_format": item.get("recommended_format"),
                "confidence": _confidence(item.get("confidence")),
                "confidence_label": _confidence_label(item.get("confidence_label") or item.get("confidence")),
                "expires_at": item.get("expires_at"),
            }
        )
    return kept, dropped


def _festival_section(
    festivals: list[dict[str, Any]],
    model_items: list[Any],
    festival_keys: set[str],
) -> list[dict[str, Any]]:
    notes: dict[str, str] = {}
    for item in model_items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("campaign_id") or item.get("festival_id") or item.get("festival_name") or "")
        why = str(item.get("why_now") or "").strip()
        if key in festival_keys and why:
            notes[key] = why
    section = []
    for festival in festivals:
        campaign_id = str(festival.get("campaign_id") or "")
        section.append(
            {
                "festival_name": festival.get("festival_name"),
                "date": festival.get("date"),
                "campaign_id": campaign_id or None,
                "source": "festival_calendar",
                "why_now": notes.get(campaign_id) or notes.get(str(festival.get("festival_name") or "")),
            }
        )
    return section


def _product_section(
    products: list[dict[str, Any]],
    offers: list[dict[str, Any]],
    model_items: list[Any],
    product_keys: set[str],
    degraded: bool,
) -> list[dict[str, Any]]:
    notes: dict[str, str] = {}
    if not degraded:
        for item in model_items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("product_id") or item.get("name") or "")
            why = str(item.get("why_now") or "").strip()
            if key in product_keys and why:
                notes[key] = why
    section = []
    offer_ids = {str(item.get("product_id")) for item in offers}
    for product in products:
        product_id = str(product.get("id") or "")
        if product_id not in offer_ids and product_id not in notes and product.get("name") not in notes:
            continue
        section.append(
            {
                "product_id": product_id or None,
                "name": product.get("name"),
                "offer": product.get("offer"),
                "source": "catalog" if product_id in offer_ids else "analysis",
                "why_now": notes.get(product_id) or notes.get(str(product.get("name") or "")),
            }
        )
    return section


def _split_observations(
    observations: list[dict[str, Any]],
    local_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    for item in observations:
        until = _parse_date(item.get("valid_until"))
        freshness = str(item.get("freshness") or "").strip().lower()
        status = str(item.get("status") or "").strip().upper()
        copy = {
            "id": item.get("id"),
            "title": item.get("title"),
            "description": item.get("description"),
            "trend_type": item.get("trend_type"),
            "observed_at": item.get("observed_at"),
            "valid_until": item.get("valid_until"),
            "freshness": item.get("freshness"),
            "source": item.get("source"),
            "evidence": item.get("evidence"),
        }
        if (until is not None and until < local_date) or freshness == "stale" or status == "EXPIRED":
            expired.append(copy)
        else:
            active.append(copy)
    return active, expired


def _local_performance(posts: PostRepository, user_id: str) -> dict[str, Any]:
    rows = posts.list_for_user(user_id, limit=8)
    published = [row for row in rows if row.status == "PUBLISHED"]
    return {
        "source": "local_posts",
        "published_count": len(published),
        "recent_posts": [
            {"id": row.id, "status": row.status, "post_type": row.post_type}
            for row in rows
        ],
    }


def _business_payload(profile: Any, products: list[dict[str, Any]], offers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "business_id": profile.id,
        "business_name": profile.business_name,
        "business_type": profile.business_type,
        "location": profile.location,
        "products": products or _names(getattr(profile, "products", None)),
        "offers": offers,
    }


def _names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _festival_keys(festivals: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for festival in festivals:
        for field in ("campaign_id", "festival_name", "name"):
            value = festival.get(field)
            if value:
                keys.add(str(value))
    return keys


def _product_keys(products: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for product in products:
        if product.get("id"):
            keys.add(str(product["id"]))
        if product.get("name"):
            keys.add(str(product["name"]))
    return keys


def _without_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key.lower() not in _SECRET_KEYS}


def _confidence_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    label = value.strip().lower()
    if label in {"high", "moderate", "low"}:
        return label
    return None


def _resolve_analyzer(analyzer: Any | None, settings: Settings, *, llm: Any | None = None) -> Any | None:
    if analyzer is not None:
        return analyzer
    loaded = _load_optional(_ANALYZER)
    if loaded is not None:
        return loaded
    from ai.llm_client import reasoning_provider_name

    if reasoning_provider_name(settings) != "deepseek":
        return None
    if llm is not None and hasattr(llm, "generate_structured"):
        return _DeepSeekTrendStage(settings, llm)
    if getattr(settings, "deepseek_configured", False):
        return _DeepSeekTrendStage(settings)
    return None


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            parsed = _parse_date(value)
            if parsed is None:
                return None
            return datetime(parsed.year, parsed.month, parsed.day)
    return None


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _call_research(research: Any, payload: dict[str, Any]) -> Any:
    for name in ("fetch", "fetch_observations", "observations"):
        method = getattr(research, name, None)
        if callable(method):
            return method(payload)
    if callable(research):
        return research(payload)
    raise RuntimeError("Trend research source is unavailable")


def _call_analyzer(analyzer: Any, context: dict[str, Any]) -> Any:
    for name in ("analyze", "analyze_trends"):
        method = getattr(analyzer, name, None)
        if callable(method):
            return method(context)
    if callable(analyzer):
        return analyzer(context)
    raise RuntimeError("Trend analyzer is unavailable")


def _load_optional(candidates: tuple[tuple[str, str], ...]) -> Any | None:
    for module_name, factory_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        factory = getattr(module, factory_name, None)
        if callable(factory):
            return factory
    return None


def public_opportunities(rows: list[Any]) -> list[dict[str, Any]]:
    return [opportunity_public(row) for row in rows]


class _InstagramIntelligenceSource:
    """Read-only account intelligence. A failure here does not invent metrics."""

    def __init__(self, settings: Settings, session: Session, service_type: type) -> None:
        self._settings = settings
        self._session = session
        self._service_type = service_type
        self._cache: dict[str, Any] = {}

    async def fetch_account(self, user_id: str) -> dict[str, Any]:
        payload = await self._load(user_id)
        account = payload.get("account")
        if not isinstance(account, dict):
            raise RuntimeError("instagram account intelligence unavailable")
        return account

    async def fetch_performance(self, user_id: str) -> dict[str, Any]:
        payload = await self._load(user_id)
        performance = payload.get("performance")
        if not isinstance(performance, dict):
            raise RuntimeError("instagram performance unavailable")
        cleaned = dict(performance)
        cleaned["source"] = cleaned.get("source") or "instagram_account_intelligence"
        return cleaned

    async def _load(self, user_id: str) -> dict[str, Any]:
        cached = self._cache.get(user_id)
        if isinstance(cached, Exception):
            raise cached
        if isinstance(cached, dict):
            return cached
        nested = self._session.begin_nested()
        try:
            payload = await self._service_type(self._settings, self._session).account(user_id)
            nested.commit()
        except Exception as exc:
            nested.rollback()
            self._cache[user_id] = exc
            raise
        if not isinstance(payload, dict):
            error = RuntimeError("instagram intelligence unavailable")
            self._cache[user_id] = error
            raise error
        self._cache[user_id] = payload
        return payload


class _DeepSeekTrendStage:
    """Turns collected evidence into a brief through the configured reasoning provider.

    This stage does not open its own DeepSeek HTTP client. It uses the provider
    already selected for the scheduler, or the LLM factory when that provider
    was not injected.
    """

    def __init__(self, settings: Settings, provider: Any | None = None) -> None:
        self._settings = settings
        self._provider = provider

    async def analyze(self, context: dict[str, Any]) -> dict[str, Any]:
        from ai.llm_client import get_llm_provider
        from backend.trends.analyst import DeepSeekTrendAnalyst

        provider = self._provider if self._provider is not None else get_llm_provider(self._settings)
        request = _analysis_request(context)
        brief = await DeepSeekTrendAnalyst(self._settings, provider=provider).analyze(request)
        return _brief_to_dict(brief, context)


def _analysis_request(context: dict[str, Any]) -> Any:
    from backend.trends.schemas import (
        AccountInsight,
        HistoricalPerformance,
        OfferContext,
        PerformancePoint,
        ProductContext,
        TrendAnalysisRequest,
        TrendEvidence,
        TrendObservation,
    )
    from models.content import BusinessProfileSnapshot
    from models.content import FestivalContext as FestivalModel

    analyzed_at = _parse_datetime(context.get("generated_at")) or datetime.now(timezone.utc)
    if analyzed_at.tzinfo is None:
        analyzed_at = analyzed_at.replace(tzinfo=timezone.utc)
    observations = [item for item in (_observation_model(raw, TrendObservation, TrendEvidence) for raw in context.get("observations") or []) if item is not None]
    business_raw = context.get("business") if isinstance(context.get("business"), dict) else {}
    business = None
    if str(business_raw.get("business_name") or "").strip():
        business = BusinessProfileSnapshot(
            id=business_raw.get("business_id"),
            user_id=context.get("user_id"),
            business_name=str(business_raw["business_name"]),
            business_type=business_raw.get("business_type"),
            location=business_raw.get("location"),
            products=[str(item["name"]) for item in context.get("products") or [] if isinstance(item, dict) and item.get("name")],
        )
    products = []
    for item in context.get("products") or []:
        if isinstance(item, dict) and item.get("id") and item.get("name"):
            products.append(
                ProductContext(
                    id=str(item["id"]),
                    name=str(item["name"]),
                    description=item.get("description"),
                )
            )
    offers = []
    for item in context.get("offers") or []:
        if isinstance(item, dict) and item.get("product_id") and str(item.get("offer") or "").strip():
            offers.append(
                OfferContext(
                    id=str(item["product_id"]),
                    name=str(item.get("name") or item["offer"]),
                    description=str(item["offer"]),
                    product_id=str(item["product_id"]),
                )
            )
    festival = None
    festivals = [item for item in context.get("festivals") or [] if isinstance(item, dict) and item.get("date")]
    if len(festivals) == 1:
        item = festivals[0]
        occurs = date.fromisoformat(str(item["date"]))
        festival = FestivalModel(
            name=str(item.get("festival_name") or item.get("name") or "Festival"),
            festival_name=str(item.get("festival_name") or item.get("name") or ""),
            date=occurs,
            year=occurs.year,
            campaign_id=item.get("campaign_id"),
            required_posts=int(item.get("required_posts") or 2),
            published_posts=int(item.get("published_posts") or 0),
        )
    from backend.trends.packet import instagram_evidence

    performance = context.get("performance")
    instagram = instagram_evidence(performance if isinstance(performance, dict) else None)
    return TrendAnalysisRequest(
        user_id=str(context.get("user_id") or ""),
        analyzed_at=analyzed_at,
        observations=observations,
        account_insights=_account_insights(performance, AccountInsight),
        business_profile=business,
        products=products,
        offers=offers,
        festival=festival,
        instagram_inferences=instagram["inferences"],
        historical_performance=_historical_performance(
            performance,
            analyzed_at,
            HistoricalPerformance,
            PerformancePoint,
            AccountInsight,
            extra_points=instagram["points"],
        ),
    )


def _observation_model(item: Any, observation_type: type, evidence_type: type) -> Any | None:
    if not isinstance(item, dict):
        return None
    observation_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or item.get("summary") or "").strip()
    observed = _parse_datetime(item.get("observed_at"))
    if not observation_id or not title or not description or observed is None:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    source_name = str(source.get("name") or item.get("source_name") or "research").strip() or "research"
    evidence = []
    raw_evidence = item.get("evidence")
    pieces: list[Any]
    if isinstance(raw_evidence, str):
        pieces = [raw_evidence]
    elif isinstance(raw_evidence, list):
        pieces = list(raw_evidence)
    else:
        pieces = []
    for index, piece in enumerate(pieces):
        excerpt = ""
        evidence_id = f"{observation_id}:evidence:{index}"
        piece_source = source_name
        piece_time = observed
        if isinstance(piece, str):
            excerpt = piece.strip()
        elif isinstance(piece, dict):
            excerpt = str(piece.get("excerpt") or piece.get("evidence") or "").strip()
            evidence_id = str(piece.get("id") or evidence_id).strip() or evidence_id
            piece_source = str(piece.get("source_name") or source_name).strip() or source_name
            piece_time = _parse_datetime(piece.get("observed_at")) or observed
        if not excerpt:
            continue
        if piece_time.tzinfo is None:
            piece_time = piece_time.replace(tzinfo=timezone.utc)
        evidence.append(
            evidence_type(
                id=evidence_id,
                source_name=piece_source,
                excerpt=excerpt[:500],
                observed_at=piece_time,
            )
        )
    if not evidence:
        return None
    valid_until = _parse_datetime(item.get("valid_until"))
    if valid_until is not None and valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    return observation_type(
        id=observation_id,
        title=title[:160],
        description=description[:500],
        trend_type=str(item.get("trend_type") or "general").strip() or "general",
        observed_at=observed,
        valid_until=valid_until,
        evidence=evidence,
    )


def _account_insights(performance: Any, insight_type: type) -> list[Any]:
    if not isinstance(performance, dict):
        return []
    insights = []
    metrics = performance.get("metrics")
    rows = metrics if isinstance(metrics, list) else []
    published = performance.get("published_count")
    if isinstance(published, int) and not isinstance(published, bool):
        rows = [*rows, {"metric": "published_count", "value": published, "available": True}]
    from backend.trends.schemas import DERIVED_ACCOUNT_METRICS

    for item in rows:
        if not isinstance(item, dict) or item.get("available") is False or item.get("status") == "unavailable":
            continue
        value = item.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        if isinstance(value, str) and not value.strip():
            continue
        metric = str(item.get("metric") or item.get("name") or "").strip()
        if not metric:
            continue
        kind = "INFERRED" if metric in DERIVED_ACCOUNT_METRICS else "OBSERVED"
        insights.append(
            insight_type(
                id=f"perf:{metric}",
                metric=metric,
                value=value,
                statement=f"{metric} is {value}",
                epistemic_status=kind,
            )
        )
    return insights


def _historical_performance(
    performance: Any,
    recorded_at: datetime,
    history_type: type,
    point_type: type,
    insight_type: type,
    extra_points: list[Any] | None = None,
) -> Any:
    points = []
    seen: set[str] = set()
    for insight in _account_insights(performance, insight_type):
        seen.add(insight.id)
        points.append(
            point_type(
                id=insight.id,
                label=insight.metric,
                metric=insight.metric,
                value=insight.value,
                recorded_at=recorded_at,
            )
        )
    for item in extra_points or []:
        point_id = str(getattr(item, "id", "") or "")
        if not point_id or point_id in seen:
            continue
        seen.add(point_id)
        points.append(item)
    summary = None
    if isinstance(performance, dict) and isinstance(performance.get("published_count"), int):
        summary = f"Published posts on record: {performance['published_count']}."
    return history_type(points=points, summary=summary)


def _brief_to_dict(brief: Any, context: dict[str, Any]) -> dict[str, Any]:
    observations = [item for item in context.get("observations") or [] if isinstance(item, dict)]
    evidence_owner: dict[str, str] = {}
    for item in observations:
        observation_id = str(item.get("id") or "").strip()
        if not observation_id:
            continue
        evidence_owner[observation_id] = observation_id
        raw = item.get("evidence")
        pieces = [raw] if isinstance(raw, str) else list(raw) if isinstance(raw, list) else []
        for index, piece in enumerate(pieces):
            evidence_owner[f"{observation_id}:evidence:{index}"] = observation_id
            if isinstance(piece, dict) and piece.get("id"):
                evidence_owner[str(piece["id"])] = observation_id

    def owner_for(evidence_ids: list[Any]) -> str | None:
        for evidence_id in evidence_ids:
            mapped = evidence_owner.get(str(evidence_id))
            if mapped:
                return mapped
        return None

    current = []
    for trend in getattr(brief, "current_trends", []) or []:
        freshness = getattr(getattr(trend, "freshness", None), "value", getattr(trend, "freshness", ""))
        if str(freshness).lower() == "stale":
            continue
        trend_id = owner_for(list(getattr(trend, "evidence_ids", []) or []))
        title = str(getattr(trend, "title", "") or "").strip()
        if not trend_id or not title:
            continue
        current.append({"trend_id": trend_id, "title": title})

    festivals = [item for item in context.get("festivals") or [] if isinstance(item, dict)]
    products = [item for item in context.get("products") or [] if isinstance(item, dict)]
    content = []
    for collection_name in ("content_opportunities", "festival_opportunities", "business_opportunities"):
        for item in getattr(brief, collection_name, []) or []:
            why = getattr(item, "why_now", None)
            why_text = str(getattr(why, "text", "") or "").strip()
            title = str(getattr(item, "title", "") or "").strip()
            evidence_ids = list(getattr(why, "evidence_ids", []) or []) + list(getattr(item, "supporting_evidence", []) or [])
            trend_id = owner_for(evidence_ids)
            if not trend_id or not why_text or not title:
                continue
            row = {
                "trend_id": trend_id,
                "title": title,
                "why_now": why_text,
                "creative_direction": getattr(item, "creative_direction", None),
                "recommended_format": getattr(item, "recommended_format", None),
                "expires_at": item.expiration.isoformat() if getattr(item, "expiration", None) else None,
                "confidence_label": _confidence_label(getattr(getattr(item, "confidence", None), "value", getattr(item, "confidence", None))),
            }
            if collection_name == "festival_opportunities" and len(festivals) == 1 and festivals[0].get("campaign_id"):
                row["campaign_id"] = festivals[0].get("campaign_id")
                row["festival_id"] = festivals[0].get("festival_name")
            named = [
                product
                for product in products
                if product.get("id") and product.get("name") and str(product["name"]).lower() in f"{title} {row.get('creative_direction') or ''}".lower()
            ]
            if len(named) == 1:
                row["product_id"] = named[0]["id"]
            content.append(row)
    return {
        "current_trends": current,
        "content_opportunities": content,
        "festival_opportunities": [],
        "business_opportunities": [],
        "data_gaps": [str(item) for item in (getattr(brief, "data_gaps", []) or []) if str(item).strip()],
    }
