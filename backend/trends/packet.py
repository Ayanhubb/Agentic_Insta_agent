"""Assemble a DeepSeek evidence packet from MCP reads.

This module calls read tools only. It does not browse, call Meta, publish,
or ask DeepSeek to fill a missing metric, source, date, product, or offer.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from backend.mcp.errors import MCPError
from backend.mcp.tenant_isolation import TenantContext
from backend.trends.research import region_code
from backend.trends.schemas import (
    DERIVED_ACCOUNT_METRICS,
    INSUFFICIENT_HISTORY,
    RELIABLE_COMPARISON_POSTS,
    AccountInsight,
    BrandGuidelineContext,
    EvidenceCoverage,
    HistoricalPerformance,
    OfferContext,
    PerformancePoint,
    ProductContext,
    TrendAnalysisRequest,
    TrendBrief,
    TrendEvidence,
    TrendObservation,
)
from models.content import BusinessProfileSnapshot, FestivalContext

_REGION = re.compile(r"^[A-Z]{2}(?:-[A-Z0-9]{2,12}){0,2}$")
_FNB = {"fnb", "food", "food_and_beverage", "beverage"}
_READ_LIMIT = 25

# Tools this packet is allowed to call. Publishing tools are not in the list.
MCP_EVIDENCE_TOOLS = (
    "get_business_profile",
    "get_brand_guidelines",
    "get_active_offers",
    "get_account_summary",
    "get_recent_media",
    "get_account_insights",
    "get_top_content",
    "get_content_performance",
    "get_current_trends",
    "get_regional_trends",
    "get_festival_opportunities",
    "get_business_opportunities",
    "get_content_opportunities",
)


def _blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _region_of(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if _REGION.fullmatch(text):
        return text
    coded = region_code(text)
    if coded and _REGION.fullmatch(coded):
        return coded
    return None


def _is_indian(region: str | None) -> bool:
    code = _region_of(region)
    return bool(code and (code == "IN" or code.startswith("IN-")))


def _is_regional(region: str | None) -> bool:
    code = _region_of(region)
    return bool(code and "-" in code)


class TrendIntelligence:
    """Reads MCP evidence, then asks DeepSeek for a brief. Does not publish."""

    def __init__(self, settings: Any, client: Any, *, analyst: Any | None = None, now: Any | None = None) -> None:
        self._client = client
        self._analyst = analyst
        self._settings = settings
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def analyze(self, tenant: TenantContext) -> TrendBrief:
        if self._analyst is None:
            from ai.llm_client import get_llm_provider
            from backend.trends.analyst import DeepSeekTrendAnalyst

            self._analyst = DeepSeekTrendAnalyst(self._settings, provider=get_llm_provider(self._settings))
        readings = await collect_mcp_evidence(self._client, tenant)
        request = build_trend_request(
            user_id=tenant.tenant_id,
            analyzed_at=self._now(),
            readings=readings,
        )
        return await self._analyst.analyze(request)


async def collect_mcp_evidence(client: Any, tenant: TenantContext) -> dict[str, Any]:
    """Call allowlisted read tools. The returned dict is stored evidence only."""
    errors: list[str] = []
    calls: list[str] = []

    async def call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append(name)
        try:
            result = await client.invoke(name, arguments or {}, tenant)
        except MCPError:
            errors.append(f"{name} could not be read.")
            return {}
        data = result.data if hasattr(result, "data") else result
        if not isinstance(data, dict):
            errors.append(f"{name} could not be read.")
            return {}
        return data

    business = await call("get_business_profile")
    guidelines = await call("get_brand_guidelines")
    active_offers = await call("get_active_offers")
    account = await call("get_account_summary")
    media = await call("get_recent_media", {"limit": _READ_LIMIT})
    insights = await call("get_account_insights")
    top = await call("get_top_content")
    performance = await call("get_content_performance")
    trends = await call("get_current_trends", {"limit": _READ_LIMIT})
    retail = await call("get_current_trends", {"industry": "retail", "limit": _READ_LIMIT})
    fnb = await call("get_current_trends", {"industry": "fnb", "limit": _READ_LIMIT})
    food = await call("get_current_trends", {"industry": "food", "limit": _READ_LIMIT})
    indian = await call("get_regional_trends", {"region": "IN", "limit": _READ_LIMIT})
    location = _location(business)
    region = _region_of(location)
    regional: dict[str, Any] = {}
    if region and region != "IN":
        regional = await call("get_regional_trends", {"region": region, "limit": _READ_LIMIT})
    festivals = await call("get_festival_opportunities", {"limit": _READ_LIMIT})
    business_opportunities = await call("get_business_opportunities", {"limit": _READ_LIMIT})
    content_gaps = await call("get_content_opportunities")
    return {
        "business": business,
        "guidelines": guidelines,
        "active_offers": active_offers,
        "account": account,
        "media": media,
        "insights": insights,
        "top": top,
        "performance": performance,
        "trends": [trends, retail, fnb, food, indian, regional],
        "festivals": festivals,
        "business_opportunities": business_opportunities,
        "content_gaps": content_gaps,
        "read_errors": errors,
        "tools_called": calls,
    }


def build_trend_request(
    *,
    user_id: str,
    analyzed_at: datetime,
    readings: dict[str, Any],
) -> TrendAnalysisRequest:
    """Turn MCP payloads into the structured packet DeepSeek is allowed to see."""
    profile = _business(readings.get("business") or {})
    products, offers = _products_and_offers(
        readings.get("business") or {},
        readings.get("business_opportunities") or {},
    )
    guidelines = _guidelines(readings.get("guidelines") or {})
    festival = _festival(readings.get("festivals") or {})
    observations, stale_excluded = _observations(readings.get("trends") or [])
    sample = _sample(readings)
    insights = _account_insights(readings, sample)
    points = _history(readings)
    coverage = _coverage(
        insights=insights,
        points=points,
        observations=observations,
        festival=festival,
        products=products,
        readings=readings,
    )
    gaps = _gaps(
        readings=readings,
        sample=sample,
        coverage=coverage,
        products=products,
        offers=offers,
        insights=insights,
        points=points,
        stale_excluded=stale_excluded,
    )
    moment = analyzed_at if analyzed_at.tzinfo else analyzed_at.replace(tzinfo=timezone.utc)
    return TrendAnalysisRequest(
        user_id=user_id,
        analyzed_at=moment,
        observations=observations,
        account_insights=insights,
        business_profile=profile,
        products=products,
        offers=offers,
        brand_guidelines=guidelines,
        festival=festival,
        historical_performance=HistoricalPerformance(points=points),
        instagram_inferences=_instagram_inferences(readings),
        coverage=coverage,
        required_gaps=gaps,
    )


def _location(business: dict[str, Any]) -> str | None:
    profile = business.get("profile") if business.get("found") else None
    if not isinstance(profile, dict):
        return None
    return _blank(profile.get("location"))


def _business(payload: dict[str, Any]) -> BusinessProfileSnapshot | None:
    profile = payload.get("profile") if payload.get("found") else None
    if not isinstance(profile, dict) or not _blank(profile.get("business_name")):
        return None
    return BusinessProfileSnapshot.model_validate(profile)


def _products_and_offers(
    business: dict[str, Any],
    opportunities: dict[str, Any],
) -> tuple[list[ProductContext], list[OfferContext]]:
    products: list[ProductContext] = []
    offers: list[OfferContext] = []
    seen: set[str] = set()

    def add_product(name: str, *, product_id: str | None = None, category: str | None = None) -> None:
        key = name.casefold()
        if key in seen:
            return
        seen.add(key)
        products.append(
            ProductContext(
                id=product_id or f"product-{len(products) + 1}",
                name=name,
                category=category,
            )
        )

    for row in opportunities.get("opportunities") or []:
        if not isinstance(row, dict):
            continue
        name = _blank(row.get("title"))
        if not name or row.get("kind") not in {"product", "offer"}:
            continue
        sku = _blank(row.get("sku"))
        add_product(name, product_id=sku, category=_blank(row.get("category")))
        if row.get("kind") == "offer":
            offers.append(
                OfferContext(
                    id=f"offer-{sku or len(offers) + 1}",
                    name=name,
                    description=_blank(row.get("reason")),
                    product_id=sku,
                )
            )
    profile = business.get("profile") if isinstance(business.get("profile"), dict) else {}
    for name in profile.get("products") or []:
        text = _blank(name)
        if text:
            add_product(text)
    return products, offers


def _guidelines(payload: dict[str, Any]) -> list[BrandGuidelineContext]:
    body = payload.get("guidelines") if payload.get("found") else None
    if not isinstance(body, dict):
        return []
    items: list[BrandGuidelineContext] = []
    for key in ("brand_style", "preferred_language", "target_audience"):
        text = _blank(body.get(key))
        if text:
            items.append(BrandGuidelineContext(id=f"brand-{key}", title=key, body=text))
    return items


def _festival(payload: dict[str, Any]) -> FestivalContext | None:
    for item in payload.get("opportunities") or []:
        if not isinstance(item, dict):
            continue
        name = _blank(item.get("title") or item.get("festival_name"))
        raw_date = _blank(item.get("date"))
        if not name or not raw_date:
            continue
        try:
            day = date.fromisoformat(raw_date[:10])
        except ValueError:
            continue
        return FestivalContext(name=name, date=day, year=day.year)
    return None


def _observations(payloads: list[Any]) -> tuple[list[TrendObservation], bool]:
    found: dict[str, TrendObservation] = {}
    stale = False
    for payload in payloads:
        rows = payload.get("observations") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("stale") is True or row.get("is_current") is False:
                stale = True
                continue
            observation = _observation(row)
            if observation is not None and observation.id not in found:
                found[observation.id] = observation
    return list(found.values()), stale


def _observation(row: dict[str, Any]) -> TrendObservation | None:
    obs_id = _blank(row.get("id"))
    title = _blank(row.get("title"))
    description = _blank(row.get("summary") or row.get("description"))
    observed = _parse_dt(row.get("observed_at"))
    if not obs_id or not title or not description or observed is None:
        return None
    evidence = _evidence(obs_id, row, observed)
    if not evidence:
        return None
    trend_type = _blank(row.get("trend_type") or row.get("content_type")) or "reported"
    keywords = [str(item).strip() for item in row.get("keywords") or [] if str(item).strip()]
    return TrendObservation(
        id=obs_id,
        title=title[:160],
        description=description,
        trend_type=trend_type,
        industry=_blank(row.get("industry")),
        region=_blank(row.get("region")),
        keywords=keywords,
        evidence=evidence,
        observed_at=observed,
        published_at=_parse_dt(row.get("published_at")),
        valid_until=_parse_dt(row.get("valid_until")),
        epistemic_status="DISCOVERED",
    )


def _evidence(obs_id: str, row: dict[str, Any], observed: datetime) -> list[TrendEvidence]:
    pieces: list[TrendEvidence] = []
    primary = row.get("primary_source") if isinstance(row.get("primary_source"), dict) else {}
    source_name = _blank(row.get("source_name")) or _blank(primary.get("source_name"))
    excerpt = _blank(row.get("evidence")) or _blank(primary.get("title"))
    if source_name and excerpt:
        pieces.append(
            TrendEvidence(
                id=f"{obs_id}-primary",
                source_name=source_name,
                excerpt=excerpt,
                observed_at=_parse_dt(primary.get("observed_at")) or observed,
                published_at=_parse_dt(row.get("published_at")),
            )
        )
    for index, item in enumerate(row.get("supporting_sources") or []):
        if not isinstance(item, dict):
            continue
        name = _blank(item.get("source_name"))
        quote = _blank(item.get("title"))
        seen = _parse_dt(item.get("observed_at")) or observed
        if not name or not quote:
            continue
        pieces.append(
            TrendEvidence(
                id=f"{obs_id}-support-{index + 1}",
                source_name=name,
                excerpt=quote,
                observed_at=seen,
            )
        )
    return pieces


_INSTAGRAM_READS = ("account", "media", "insights", "top", "performance")


def _instagram_contexts(readings: dict[str, Any]) -> list[dict[str, Any]]:
    """Live MCP trend_context payloads, plus the older intelligence fixture shape."""
    found: list[dict[str, Any]] = []
    for key in _INSTAGRAM_READS:
        payload = readings.get(key)
        if not isinstance(payload, dict):
            continue
        for container in ("trend_context", "intelligence"):
            body = payload.get(container)
            if isinstance(body, dict) and body not in found:
                found.append(body)
    return found


def _instagram_failures(readings: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    seen: set[str] = set()
    for key in _INSTAGRAM_READS:
        payload = readings.get(key)
        if not isinstance(payload, dict):
            continue
        failed = payload.get("found") is False or payload.get("status") == "unavailable"
        if not failed:
            continue
        reason = _blank(payload.get("reason")) or "unavailable"
        metric = _blank(payload.get("metric")) or key
        text = f"Instagram {metric} is unavailable ({reason})."
        if text not in seen:
            seen.add(text)
            gaps.append(text)
    return gaps


def _metric_value(item: dict[str, Any]) -> str | int | float | None:
    if item.get("status") == "unavailable":
        return None
    if item.get("status") not in {None, "available"}:
        return None
    value = item.get("value")
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _sample(readings: dict[str, Any]) -> int | None:
    for context in _instagram_contexts(readings):
        if isinstance(context.get("sample_size"), int) and not isinstance(context.get("sample_size"), bool):
            return context["sample_size"]
    media = readings.get("media") or {}
    rows = media.get("media") if isinstance(media.get("media"), list) else []
    if rows:
        return len(rows)
    stored = _stored_insights(readings.get("insights") or {})
    if isinstance(stored.get("published"), int):
        return stored["published"]
    performance = readings.get("performance") or {}
    if isinstance(performance.get("published"), int):
        return performance["published"]
    return None


def _stored_insights(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("insights")
    return body if isinstance(body, dict) else {}


def _account_insights(readings: dict[str, Any], sample: int | None) -> list[AccountInsight]:
    items: list[AccountInsight] = []
    seen_values: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    for context in _instagram_contexts(readings):
        captured = _parse_dt(context.get("captured_at"))
        context_sample = context.get("sample_size")
        if not isinstance(context_sample, int) or isinstance(context_sample, bool):
            context_sample = sample
        for metric in context.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            name = _blank(metric.get("metric"))
            value = _metric_value(metric)
            if not name or value is None:
                continue
            identity = (name, str(value))
            if identity in seen_values:
                continue
            seen_values.add(identity)
            insight_id = f"ig:{name}"
            if insight_id in seen_ids:
                insight_id = f"{insight_id}:{len(seen_ids)}"
            seen_ids.add(insight_id)
            kind = "INFERRED" if name in DERIVED_ACCOUNT_METRICS else "OBSERVED"
            statement = (
                f"The sample interpretation for {name} is {value}."
                if kind == "INFERRED"
                else f"The authorized Instagram sample recorded {name} as {value}."
            )
            items.append(
                AccountInsight(
                    id=insight_id,
                    metric=name,
                    value=value,
                    statement=statement,
                    period_end=captured,
                    sample_size=context_sample,
                    epistemic_status=kind,
                )
            )
    published = _stored_insights(readings.get("insights") or {}).get("published")
    if not isinstance(published, int) or isinstance(published, bool):
        performance = readings.get("performance") or {}
        published = performance.get("published") if isinstance(performance.get("published"), int) else None
    if isinstance(published, int) and not isinstance(published, bool) and not any(item.metric == "published" for item in items):
        items.insert(
            0,
            AccountInsight(
                id="metric-published",
                metric="published",
                value=published,
                statement=f"{published} posts are stored for this account.",
                sample_size=sample if sample is not None else published,
                epistemic_status="OBSERVED",
            ),
        )
    elif isinstance(sample, int) and not any(item.metric in {"published", "sample_size"} for item in items):
        captured = None
        for context in _instagram_contexts(readings):
            captured = _parse_dt(context.get("captured_at")) or captured
        items.insert(
            0,
            AccountInsight(
                id="metric-sample-size",
                metric="sample_size",
                value=sample,
                statement=f"{sample} posts were returned in the authorized Instagram sample.",
                period_end=captured,
                sample_size=sample,
                epistemic_status="OBSERVED",
            ),
        )
    return items


def _instagram_inferences(readings: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    seen: set[str] = set()
    for context in _instagram_contexts(readings):
        for item in context.get("observations") or []:
            text = _blank(item)
            if not text or text in seen:
                continue
            seen.add(text)
            notes.append(text)
    return notes


def instagram_evidence(performance: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize one account-performance payload for the scheduler's DeepSeek request.

    Unavailable metrics stay out of the returned points. Comparison notes are
    inferences, not observed facts.
    """
    if not isinstance(performance, dict):
        return {"inferences": [], "points": []}
    readings = {"account": {"trend_context": performance}}
    return {
        "inferences": _instagram_inferences(readings),
        "points": _history(readings),
    }


def _instagram_metric_gaps(readings: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    seen: set[tuple[str, str]] = set()
    for context in _instagram_contexts(readings):
        for item in context.get("metrics") or []:
            if not isinstance(item, dict) or item.get("status") != "unavailable":
                continue
            name = _blank(item.get("metric"))
            reason = _blank(item.get("reason")) or "unavailable"
            if not name or (name, reason) in seen:
                continue
            seen.add((name, reason))
            gaps.append(f"Instagram metric {name} is unavailable ({reason}).")
    return gaps


def _history(readings: dict[str, Any]) -> list[PerformancePoint]:
    points: list[PerformancePoint] = []
    seen: set[str] = set()
    for key in ("media", "top"):
        payload = readings.get(key) or {}
        for item in payload.get("media") or []:
            if not isinstance(item, dict):
                continue
            media_id = _blank(item.get("id"))
            if not media_id or media_id in seen:
                continue
            seen.add(media_id)
            label = _blank(item.get("post_type")) or "post"
            points.append(
                PerformancePoint(
                    id=media_id,
                    label=label,
                    metric="stored_post",
                    value=label,
                    recorded_at=_parse_dt(item.get("published_at")),
                )
            )
    performance = readings.get("performance") or {}
    for item in performance.get("by_type") or []:
        if not isinstance(item, dict):
            continue
        label = _blank(item.get("post_type"))
        count = item.get("published")
        if not label or not isinstance(count, int):
            continue
        points.append(
                PerformancePoint(
                    id=f"perf-{label}",
                    label=label,
                    metric="published",
                    value=count,
                )
            )
    seen_ids = {item.id for item in points}
    for context in _instagram_contexts(readings):
        captured = _parse_dt(context.get("captured_at"))
        for bucket in ("top_content", "low_content"):
            for item in context.get(bucket) or []:
                if not isinstance(item, dict):
                    continue
                media_id = _blank(item.get("instagram_media_id") or item.get("id"))
                if not media_id or media_id in seen_ids:
                    continue
                seen_ids.add(media_id)
                label = _blank(item.get("media_type")) or "post"
                engagement = item.get("engagement")
                recorded = _parse_dt(item.get("published_at")) or captured
                if isinstance(engagement, int) and not isinstance(engagement, bool):
                    points.append(
                        PerformancePoint(
                            id=media_id,
                            label=label,
                            metric="engagement",
                            value=engagement,
                            recorded_at=recorded,
                        )
                    )
                else:
                    points.append(
                        PerformancePoint(
                            id=media_id,
                            label=label,
                            metric="stored_post",
                            value=label,
                            recorded_at=recorded,
                        )
                    )
        mix = context.get("content_mix")
        if not isinstance(mix, dict):
            continue
        for label, count in mix.items():
            if isinstance(count, bool) or not isinstance(count, int):
                continue
            point_id = f"mix-{label}"
            if point_id in seen_ids:
                continue
            seen_ids.add(point_id)
            points.append(
                PerformancePoint(
                    id=point_id,
                    label=str(label),
                    metric="published",
                    value=count,
                    recorded_at=captured,
                )
            )
    return points


def _coverage(
    *,
    insights: list[AccountInsight],
    points: list[PerformancePoint],
    observations: list[TrendObservation],
    festival: FestivalContext | None,
    products: list[ProductContext],
    readings: dict[str, Any],
) -> EvidenceCoverage:
    industries = {(item.industry or "").casefold() for item in observations}
    engagement = any(item.metric == "engagement" for item in points)
    if not engagement:
        for context in _instagram_contexts(readings):
            for metric in context.get("metrics") or []:
                if not isinstance(metric, dict):
                    continue
                name = str(metric.get("metric") or "")
                if "engagement" in name and _metric_value(metric) is not None:
                    engagement = True
                    break
            if engagement:
                break
    return EvidenceCoverage(
        recent_account_performance=bool(insights),
        content_history=any(item.metric in {"stored_post", "engagement"} for item in points),
        engagement_metrics=engagement,
        retail_trends="retail" in industries,
        food_and_beverage_trends=bool(industries & _FNB),
        indian_trends=any(_is_indian(item.region) for item in observations),
        regional_trends=any(_is_regional(item.region) for item in observations),
        festival_opportunities=festival is not None,
        product_opportunities=bool(products),
    )


def _gaps(
    *,
    readings: dict[str, Any],
    sample: int | None,
    coverage: EvidenceCoverage,
    products: list[ProductContext],
    offers: list[OfferContext],
    insights: list[AccountInsight],
    points: list[PerformancePoint],
    stale_excluded: bool,
) -> list[str]:
    gaps = [str(item) for item in readings.get("read_errors") or []]
    gaps.extend(_instagram_failures(readings))
    gaps.extend(_instagram_metric_gaps(readings))
    if sample is not None and sample < RELIABLE_COMPARISON_POSTS:
        gaps.append(INSUFFICIENT_HISTORY)
    if not insights and not points:
        gaps.append("Account posts were not supplied.")
    if not coverage.engagement_metrics:
        gaps.append("Engagement metrics were not provided.")
    if not coverage.retail_trends:
        gaps.append("No retail trend evidence was supplied.")
    if not coverage.food_and_beverage_trends:
        gaps.append("No F&B trend evidence was supplied.")
    if not coverage.indian_trends:
        gaps.append("No Indian trend evidence was supplied.")
    if not coverage.regional_trends:
        gaps.append("No regional trend evidence was supplied.")
    if not coverage.festival_opportunities:
        gaps.append("No festival was supplied.")
    if not products:
        gaps.append("No product was supplied.")
    if stale_excluded:
        gaps.append("Stale trend evidence was excluded.")
    active = readings.get("active_offers") or {}
    if active.get("offers") and not offers:
        gaps.append("Promotion images are stored without offer text.")
    return gaps
