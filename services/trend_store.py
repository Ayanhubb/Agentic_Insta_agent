"""SQL-backed trend records for the intelligence dashboard.

Rows live in the trend tables. This module does not keep a second copy in
memory, and it never calls Meta, DeepSeek, OpenAI, or Canva. Public payloads
drop credential fields before they leave the process.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.asset_repositories import ProductRepository
from db.models import (
    AccountSnapshot,
    ContentOpportunity,
    FestivalCampaign,
    MediaSnapshot,
    Product,
    TrendEvidence,
    TrendObservation,
    TrendSource,
)
from db.repositories import BusinessProfileRepository, FestivalCampaignRepository
from db.schemas import BusinessProfileWrite
from db.trend_repositories import (
    AccountSnapshotRepository,
    ContentOpportunityRepository,
    InsightSnapshotRepository,
    MediaSnapshotRepository,
    TrendEvidenceRepository,
    TrendObservationRepository,
    TrendReportRepository,
    TrendSourceRepository,
    observation_external_id,
)

_SECRET_FRAGMENTS = (
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "authorization",
    "credential",
)
_KINDS = {"observed", "discovered", "inferred"}
_CONFIDENCE_LABELS = {
    "high": Decimal("0.900"),
    "medium": Decimal("0.600"),
    "low": Decimal("0.300"),
}
_STATUS_TO_PUBLIC = {
    "NEW": "open",
    "REVIEWED": "open",
    "ACCEPTED": "saved",
    "REJECTED": "dismissed",
    "EXPIRED": "open",
    "USED": "saved",
}
_PUBLIC_TO_STATUS = {
    "open": "NEW",
    "saved": "ACCEPTED",
    "dismissed": "REJECTED",
}
_LIST_LIMIT = 1000
_OPPORTUNITY_ROLE = "role:opportunity"
_DISCLAIMER = "This is a content recommendation, not an observed fact."


def strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _SECRET_FRAGMENTS):
                continue
            cleaned[str(key)] = strip_secrets(item)
        return cleaned
    if isinstance(value, list):
        return [strip_secrets(item) for item in value]
    return value


def parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_expired(expires_at: str | datetime | None, now: datetime | None = None) -> bool:
    parsed = parse_timestamp(expires_at)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return parsed < current


class TrendStore:
    """Read and write the trend tables for one request session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_trend(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean = strip_secrets(dict(payload))
        title = str(clean.get("title") or "Untitled")[:160]
        observed_at = parse_timestamp(clean.get("observed_at")) or datetime.now(timezone.utc)
        expires_at = parse_timestamp(clean.get("expires_at"))
        kind = _trend_kind(clean.get("kind"))
        freshness = str(clean.get("freshness") or "").strip()
        industry = str(clean.get("industry") or "")
        region = str(clean.get("region") or "")
        source_name = str(clean.get("source") or "Research")[:160]
        external_id = str(clean.get("id") or clean.get("external_id") or "").strip() or None
        key = observation_external_id(title=title, observed_at=observed_at, external_id=external_id)
        observations = TrendObservationRepository(self.session)
        existing = observations.find_by_external_id(user_id, key)
        if existing is not None:
            return self.public_trend(self._trend_record(existing))
        if clean.get("id"):
            owned = observations.get_owned(user_id, str(clean["id"]))
            if owned is not None:
                return self.public_trend(self._trend_record(owned))
        source = self._source(user_id, source_name, industry, region)
        keywords = _keywords(kind=kind, freshness=freshness)
        evidence_text = _evidence_text(clean.get("evidence"))
        row = observations.record(
            user_id,
            source_id=source.id,
            title=title,
            description=evidence_text or title,
            trend_type=str(clean.get("trend_type") or "industry")[:64],
            industry=industry,
            region=region,
            observed_at=observed_at,
            evidence=evidence_text,
            keywords=keywords,
            valid_until=expires_at,
            confidence=_confidence_value(clean.get("confidence")),
            external_id=key,
            status="EXPIRED" if is_expired(expires_at, observed_at) else "ACTIVE",
            observation_id=str(clean["id"]) if clean.get("id") else None,
        )
        self._write_evidence(user_id, row, clean.get("evidence"), observed_at, expires_at)
        return self.public_trend(self._trend_record(row))

    def add_opportunity(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean = strip_secrets(dict(payload))
        if clean.get("id"):
            owned = ContentOpportunityRepository(self.session).get_owned(user_id, str(clean["id"]))
            if owned is not None:
                return self.public_opportunity(self._opportunity_record(owned))
        title = str(clean.get("title") or "Untitled")[:160]
        observed_at = parse_timestamp(clean.get("observed_at")) or datetime.now(timezone.utc)
        expires_at = parse_timestamp(clean.get("expires_at"))
        industry = str(clean.get("industry") or "")
        region = str(clean.get("region") or "")
        festival = str(clean.get("festival") or "")
        product_name = str(clean.get("product") or "").strip()
        trend_id = str(clean.get("trend_id") or "").strip()
        observation = TrendObservationRepository(self.session).get_owned(user_id, trend_id) if trend_id else None
        if observation is None:
            observation = self._opportunity_anchor(
                user_id,
                title=title,
                industry=industry,
                region=region,
                festival=festival,
                product_name=product_name,
                observed_at=observed_at,
                expires_at=expires_at,
                evidence=clean.get("evidence"),
            )
        elif product_name and not _prefixed(observation.keywords, "display_product:"):
            observation.keywords = list(observation.keywords or []) + [f"display_product:{product_name}"]
        if festival and not observation.festival:
            observation.festival = festival[:120]
        business_id = self._business_id(user_id)
        product_id = self._product_id(user_id, product_name)
        festival_id = self._festival_id(user_id, festival)
        status = _PUBLIC_TO_STATUS.get(str(clean.get("status") or "open"), "NEW")
        row = ContentOpportunityRepository(self.session).create(
            user_id,
            **({"id": str(clean["id"])} if clean.get("id") else {}),
            business_id=business_id,
            trend_id=observation.id,
            title=title,
            why_now=str(clean.get("why_now") or ""),
            creative_direction=str(clean.get("creative_direction") or ""),
            recommended_format=str(clean.get("recommended_format") or "IMAGE")[:32],
            product_id=product_id,
            festival_id=festival_id,
            confidence=_confidence_value(clean.get("confidence")),
            expires_at=expires_at,
            status=status,
        )
        return self.public_opportunity(self._opportunity_record(row))

    def list_trends(
        self,
        user_id: str,
        *,
        industry: str | None = None,
        region: str | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        rows, _truncated = TrendObservationRepository(self.session).list_for_user(user_id, limit=_LIST_LIMIT)
        visible = []
        for row in rows:
            if _is_opportunity_anchor(row):
                continue
            record = self._trend_record(row)
            if _matches(record, industry, region):
                visible.append(self.public_trend(record, now=now))
        return visible

    def list_opportunities(
        self,
        user_id: str,
        *,
        industry: str | None = None,
        region: str | None = None,
        include_dismissed: bool = False,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        visible = []
        for row in ContentOpportunityRepository(self.session).list_for_user(user_id):
            record = self._opportunity_record(row)
            if not include_dismissed and record.get("status") == "dismissed":
                continue
            if _matches(record, industry, region):
                visible.append(self.public_opportunity(record, now=now))
        return visible

    def opportunity_for(self, user_id: str, opportunity_id: str) -> dict[str, Any] | None:
        row = ContentOpportunityRepository(self.session).get_owned(user_id, opportunity_id)
        if row is None:
            return None
        return self._opportunity_record(row)

    def set_status(self, user_id: str, opportunity_id: str, status: str) -> dict[str, Any] | None:
        stored = _PUBLIC_TO_STATUS.get(status)
        if stored is None:
            return None
        row = ContentOpportunityRepository(self.session).set_status(user_id, opportunity_id, stored)
        if row is None:
            return None
        return self._opportunity_record(row)

    def report_note(self, user_id: str, industry: str | None, region: str | None) -> str | None:
        row = TrendReportRepository(self.session).latest_for_user(user_id)
        if row is None or not (row.summary or "").strip():
            return None
        if industry and row.industry and row.industry.lower() != industry.lower():
            return None
        if region and row.region and row.region.lower() != region.lower():
            return None
        return row.summary.strip()

    def account_overlay(self, user_id: str) -> dict[str, Any]:
        accounts = AccountSnapshotRepository(self.session).list_for_user(user_id)
        insights = InsightSnapshotRepository(self.session).list_for_user(user_id)
        media = MediaSnapshotRepository(self.session).list_for_user(user_id)
        latest_account = accounts[-1] if accounts else None
        latest_insight = insights[-1] if insights else None
        return {
            "engagement_metrics": _engagement_metrics(latest_account),
            "top_items": _top_items(media),
            "insight_summary": latest_insight.summary.strip() if latest_insight and latest_insight.summary else None,
        }

    def public_trend(self, item: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        expired = bool(item.get("force_expired")) or is_expired(item.get("expires_at"), now)
        payload = {
            "id": item.get("id"),
            "title": item.get("title") or "",
            "source": item.get("source") or "",
            "observed_at": item.get("observed_at"),
            "freshness": "expired" if expired else (item.get("freshness") or "unknown"),
            "industry": item.get("industry") or "",
            "region": item.get("region") or "",
            "evidence": _public_evidence(item.get("evidence") or []),
            "confidence": item.get("confidence") or "",
            "expires_at": item.get("expires_at"),
            "expired": expired,
            "kind": _trend_kind(item.get("kind")),
        }
        return strip_secrets(payload)

    def public_opportunity(self, item: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        expired = bool(item.get("force_expired")) or is_expired(item.get("expires_at"), now)
        payload = {
            "id": item.get("id"),
            "title": item.get("title") or "",
            "why_now": item.get("why_now") or "",
            "evidence": _public_evidence(item.get("evidence") or []),
            "product": item.get("product") or "",
            "festival": item.get("festival") or "",
            "recommended_format": item.get("recommended_format") or "",
            "creative_direction": item.get("creative_direction") or "",
            "expires_at": item.get("expires_at"),
            "expired": expired,
            "confidence": item.get("confidence") or "",
            "industry": item.get("industry") or "",
            "region": item.get("region") or "",
            "status": item.get("status") or "open",
            "kind": "recommended",
            "disclaimer": _DISCLAIMER,
        }
        return strip_secrets(payload)

    def _trend_record(self, row: TrendObservation) -> dict[str, Any]:
        source_name, evidence = self._source_and_evidence(row)
        keywords = list(row.keywords or [])
        freshness = _prefixed(keywords, "freshness:") or "unknown"
        industry = "" if row.industry in {"", "unspecified"} else row.industry
        return {
            "id": row.id,
            "user_id": row.user_id,
            "title": row.title,
            "source": source_name,
            "observed_at": _iso(row.observed_at),
            "freshness": freshness,
            "industry": industry,
            "region": row.region or "",
            "evidence": evidence,
            "confidence": _confidence_label(row.confidence),
            "expires_at": _iso(row.valid_until),
            "kind": _prefixed(keywords, "kind:") or ("observed" if row.source == "meta" else "discovered"),
            "force_expired": row.status == "EXPIRED",
        }

    def _opportunity_record(self, row: ContentOpportunity) -> dict[str, Any]:
        observation = self.session.get(TrendObservation, row.trend_id)
        industry = ""
        region = ""
        festival = ""
        product = ""
        evidence: list[dict[str, str]] = []
        if observation is not None and observation.user_id == row.user_id:
            industry = "" if observation.industry in {"", "unspecified"} else observation.industry
            region = observation.region or ""
            festival = observation.festival or ""
            product = _prefixed(observation.keywords, "display_product:") or ""
            _source_name, evidence = self._source_and_evidence(observation)
        if row.festival_id:
            campaign = self.session.get(FestivalCampaign, row.festival_id)
            if campaign is not None and campaign.user_id == row.user_id:
                festival = campaign.festival_name
        if row.product_id:
            owned = self.session.get(Product, row.product_id)
            if owned is not None and owned.user_id == row.user_id:
                product = owned.name
        public_status = _STATUS_TO_PUBLIC.get(row.status, "open")
        return {
            "id": row.id,
            "user_id": row.user_id,
            "title": row.title,
            "why_now": row.why_now,
            "evidence": evidence,
            "product": product,
            "festival": festival,
            "recommended_format": row.recommended_format,
            "creative_direction": row.creative_direction,
            "expires_at": _iso(row.expires_at),
            "confidence": _confidence_label(row.confidence),
            "industry": industry,
            "region": region,
            "status": public_status,
            "force_expired": row.status == "EXPIRED",
        }

    def _source_and_evidence(self, row: TrendObservation) -> tuple[str, list[dict[str, str]]]:
        source = self.session.get(TrendSource, row.source_id) if row.source_id else None
        source_name = source.name if source is not None and source.user_id == row.user_id else ""
        rows = TrendEvidenceRepository(self.session).list_for_observation(row.user_id, row.id)
        evidence = [_evidence_item(item, source_name) for item in rows]
        if not evidence and row.evidence:
            evidence = [
                {
                    "kind": _trend_kind(_prefixed(row.keywords, "kind:")),
                    "text": row.evidence,
                    "source": source_name,
                    "observed_at": _iso(row.observed_at) or "",
                }
            ]
        return source_name, evidence

    def _source(self, user_id: str, name: str, industry: str, region: str) -> TrendSource:
        found = self.session.scalar(
            select(TrendSource).where(TrendSource.user_id == user_id, TrendSource.name == name).limit(1)
        )
        if found is not None:
            return found
        return TrendSourceRepository(self.session).create(
            user_id,
            name=name,
            source_type="research",
            industry=industry or None,
            region=region or None,
            enabled=True,
        )

    def _write_evidence(
        self,
        user_id: str,
        row: TrendObservation,
        items: Any,
        observed_at: datetime,
        expires_at: datetime | None,
    ) -> None:
        if not isinstance(items, list):
            return
        repository = TrendEvidenceRepository(self.session)
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            kind = str(item.get("kind") or "discovered")
            if kind not in _KINDS:
                kind = "discovered"
            repository.create(
                user_id,
                row.id,
                source_type=kind,
                source_record_id=str(item.get("source") or "")[:128] or None,
                excerpt=text,
                observed_at=observed_at,
                valid_until=expires_at,
            )

    def _opportunity_anchor(
        self,
        user_id: str,
        *,
        title: str,
        industry: str,
        region: str,
        festival: str,
        product_name: str,
        observed_at: datetime,
        expires_at: datetime | None,
        evidence: Any,
    ) -> TrendObservation:
        source = self._source(user_id, "Content recommendation", industry, region)
        keywords = [_OPPORTUNITY_ROLE]
        if product_name:
            keywords.append(f"display_product:{product_name}")
        row = TrendObservationRepository(self.session).record(
            user_id,
            source_id=source.id,
            title=title[:160],
            description=str(title),
            trend_type="industry",
            industry=industry,
            region=region,
            observed_at=observed_at,
            evidence=_evidence_text(evidence),
            keywords=keywords,
            valid_until=expires_at,
            external_id=observation_external_id(title=f"opportunity:{title}", observed_at=observed_at),
            status="ARCHIVED",
        )
        row.festival = festival[:120] or None
        self._write_evidence(user_id, row, evidence, observed_at, expires_at)
        return row

    def _business_id(self, user_id: str) -> str:
        repository = BusinessProfileRepository(self.session)
        profile = repository.get_for_user(user_id)
        if profile is None:
            profile = repository.upsert(user_id, BusinessProfileWrite(business_name="Account"))
        return profile.id

    def _product_id(self, user_id: str, name: str) -> str | None:
        if not name:
            return None
        wanted = name.casefold()
        for product in ProductRepository(self.session).list_for_user(user_id):
            if product.name.casefold() == wanted:
                return product.id
        return None

    def _festival_id(self, user_id: str, name: str) -> str | None:
        if not name:
            return None
        wanted = name.casefold()
        for campaign in FestivalCampaignRepository(self.session).list_for_user(user_id):
            if campaign.festival_name.casefold() == wanted:
                return campaign.id
        return None


def _matches(item: dict[str, Any], industry: str | None, region: str | None) -> bool:
    if industry and str(item.get("industry") or "").lower() != industry.lower():
        return False
    if region and str(item.get("region") or "").lower() != region.lower():
        return False
    return True


def _trend_kind(value: Any) -> str:
    kind = str(value or "discovered")
    if kind not in _KINDS:
        return "discovered"
    return kind


def _public_evidence(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    public: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "discovered")
        if kind not in _KINDS:
            kind = "discovered"
        public.append(
            {
                "kind": kind,
                "text": str(item.get("text") or ""),
                "source": str(item.get("source") or ""),
                "observed_at": str(item.get("observed_at") or ""),
            }
        )
    return public


def _evidence_item(item: TrendEvidence, source_name: str) -> dict[str, str]:
    kind = item.source_type if item.source_type in _KINDS else "discovered"
    return {
        "kind": kind,
        "text": item.excerpt,
        "source": item.source_record_id or source_name,
        "observed_at": _iso(item.observed_at) or "",
    }


def _evidence_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts = []
    for item in items:
        if isinstance(item, dict) and item.get("text"):
            parts.append(str(item["text"]))
    return " ".join(parts)[:2000]


def _keywords(*, kind: str, freshness: str) -> list[str]:
    keywords = [f"kind:{kind}"]
    if freshness and freshness not in {"unknown", "expired"}:
        keywords.append(f"freshness:{freshness}")
    return keywords


def _prefixed(keywords: list[str] | None, prefix: str) -> str | None:
    for item in keywords or []:
        text = str(item)
        if text.startswith(prefix):
            return text[len(prefix) :]
    return None


def _is_opportunity_anchor(row: TrendObservation) -> bool:
    return _OPPORTUNITY_ROLE in {str(item) for item in (row.keywords or [])}


def _confidence_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().lower() in _CONFIDENCE_LABELS:
        return _CONFIDENCE_LABELS[value.strip().lower()]
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if number < 0 or number > 1:
        return None
    return number


def _confidence_label(value: Decimal | float | None) -> str:
    if value is None:
        return ""
    number = float(value)
    for label, exact in _CONFIDENCE_LABELS.items():
        if abs(number - float(exact)) < 0.001:
            return label
    if number >= 0.75:
        return "high"
    if number >= 0.45:
        return "medium"
    return "low"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _engagement_metrics(snapshot: AccountSnapshot | None) -> dict[str, int | float] | None:
    if snapshot is None:
        return None
    metrics: dict[str, int | float] = {}
    if snapshot.followers_count is not None:
        metrics["followers_count"] = snapshot.followers_count
    if snapshot.media_count is not None:
        metrics["media_count"] = snapshot.media_count
    for key, item in (snapshot.metrics or {}).items():
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        lowered = str(key).lower()
        if any(fragment in lowered for fragment in _SECRET_FRAGMENTS):
            continue
        metrics[str(key)] = item
    return metrics or None


def _top_items(rows: list[MediaSnapshot]) -> list[dict[str, Any]] | None:
    ranked = [
        row
        for row in rows
        if any(getattr(row, name) is not None for name in ("like_count", "comments_count", "reach", "saved", "shares"))
    ]
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row.like_count or 0, row.reach or 0, row.comments_count or 0), reverse=True)
    items = []
    for row in ranked[:8]:
        items.append(
            {
                "id": row.id,
                "instagram_media_id": row.instagram_media_id,
                "like_count": row.like_count,
                "comments_count": row.comments_count,
                "reach": row.reach,
                "saved": row.saved,
                "shares": row.shares,
                "observed_at": _iso(row.observed_at),
            }
        )
    return items
