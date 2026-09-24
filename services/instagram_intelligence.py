"""Account intelligence from an authorized Instagram professional account.

Analytics describe the returned sample. They do not claim that a content type
caused engagement to change. Raw Graph payloads are not stored or sent to Trend MCP.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from statistics import pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import Settings
from db.crypto import decrypt_token
from db.enums import AccountStatus
from db.exceptions import TokenEncryptionError
from db.models import InstagramIntelligenceRecord, InstagramPost
from db.repositories import BusinessRepository, InstagramAccountRepository
from models.errors import AppError, ErrorCode
from models.intelligence import (
    ContentPerformanceSnapshot,
    InstagramAccountSnapshot,
    InstagramInsightSnapshot,
    InstagramMediaSnapshot,
    MetricReading,
)
from services.instagram_reader import GraphInstagramReader, InstagramReadClient
from services.logging import redact_value

_FESTIVAL_WORDS = (
    "diwali",
    "holi",
    "eid",
    "navratri",
    "pongal",
    "onam",
    "durga",
    "christmas",
    "festival",
)
_OFFER_RE = re.compile(
    r"(₹\s*\d|\brs\.?\s*\d|\b\d+\s*%\s*off\b|\bflat\s+\d+\b|\bdiscount\b|\boffer\b)",
    re.IGNORECASE,
)
_PROFILE_FIELDS = (
    "username",
    "name",
    "biography",
    "website",
    "followers_count",
    "follows_count",
    "media_count",
)
_ACCOUNT_INSIGHTS = (
    ("reach", "day"),
    ("impressions", "day"),
    ("profile_views", "day"),
    ("follower_count", "day"),
    ("accounts_engaged", "day"),
)
_MEDIA_INSIGHTS = (
    ("reach", "lifetime"),
    ("saved", "lifetime"),
    ("total_interactions", "lifetime"),
)
_TOKEN_KEYS = frozenset(
    {
        "access_token",
        "access_token_encrypted",
        "authorization",
        "password",
        "token",
        "caption",
        "permalink",
        "biography",
        "website",
    }
)
_OMITTED_FIELDS = frozenset({"biography", "website"})
_STOP_INSIGHT_REASONS = frozenset({"rate_limited", "timeout"})


def available(metric: str, value: float | int | str, *, unit: str | None = None) -> MetricReading:
    return MetricReading(metric=metric, status="available", value=value, unit=unit)


def unavailable(metric: str, reason: str) -> MetricReading:
    return MetricReading(metric=metric, status="unavailable", value=None, reason=reason)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    if text.endswith("+0000"):
        text = text[:-5] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _aware(parsed)


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    try:
        return int(payload[key])
    except (TypeError, ValueError):
        return None


def _labels(caption: str | None, products: list[str], post_type: str | None) -> list[str]:
    text = (caption or "").lower()
    found: list[str] = []
    if post_type == "FESTIVAL" or any(word in text for word in _FESTIVAL_WORDS):
        found.append("festival")
    if _OFFER_RE.search(text or ""):
        found.append("offer")
    for name in products:
        needle = name.strip().lower()
        if len(needle) >= 3 and needle in text:
            found.append("product")
            break
    return found


def _engagement(likes: int | None, comments: int | None) -> int | None:
    if likes is None or comments is None:
        return None
    return likes + comments


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _compare(label: str, group: list[int], sample: list[int]) -> str | None:
    if len(group) < 1 or len(sample) < 2:
        return None
    group_avg = _mean([float(item) for item in group])
    sample_avg = _mean([float(item) for item in sample])
    if group_avg > sample_avg:
        relation = "higher average engagement"
    elif group_avg < sample_avg:
        relation = "lower average engagement"
    else:
        relation = "average engagement similar to the rest of the sample"
    return (
        f"Posts in this sample labeled as {label} had {relation} "
        f"({group_avg:.1f}) compared with the sample average ({sample_avg:.1f})."
    )


def build_performance(
    *,
    user_id: str,
    media: list[InstagramMediaSnapshot],
    followers: int | None,
    captured_at: datetime,
) -> ContentPerformanceSnapshot:
    sample = [item for item in media if item.engagement is not None]
    engagements = [int(item.engagement) for item in sample if item.engagement is not None]
    timestamps = [item.published_at for item in media if item.published_at is not None]
    metrics: list[MetricReading] = []
    observations: list[str] = []

    if not media:
        metrics.append(unavailable("posting_frequency", "no_posts"))
        metrics.append(unavailable("posting_consistency", "no_posts"))
        metrics.append(unavailable("content_mix", "no_posts"))
        metrics.append(unavailable("engagement_rate", "no_posts"))
        metrics.append(unavailable("average_engagement", "no_posts"))
        metrics.append(unavailable("recent_performance", "no_posts"))
    else:
        metrics.append(_frequency(timestamps))
        metrics.append(_consistency(timestamps))
        mix = _mix(media)
        metrics.append(available("content_mix", len(mix), unit="types"))
        if followers is None:
            metrics.append(unavailable("engagement_rate", "not_provided_by_meta"))
        elif followers <= 0:
            metrics.append(unavailable("engagement_rate", "follower_count_is_zero"))
        elif not engagements:
            metrics.append(unavailable("engagement_rate", "not_provided_by_meta"))
        else:
            rate = _mean([float(item) for item in engagements]) / followers
            metrics.append(available("engagement_rate", round(rate, 4), unit="engagement_per_follower"))
        if engagements:
            metrics.append(available("average_engagement", round(_mean([float(item) for item in engagements]), 2)))
        else:
            metrics.append(unavailable("average_engagement", "not_provided_by_meta"))
        metrics.append(_recent(sample))

    for label in ("product", "festival", "offer"):
        grouped = [int(item.engagement) for item in sample if label in item.labels and item.engagement is not None]
        metric_name = f"{label}_content_performance"
        if not media:
            metrics.append(unavailable(metric_name, "no_posts"))
            continue
        if not grouped:
            metrics.append(unavailable(metric_name, "no_labeled_posts"))
            continue
        if len(engagements) < 2:
            metrics.append(unavailable(metric_name, "insufficient_historical_data"))
            continue
        metrics.append(available(metric_name, round(_mean([float(item) for item in grouped]), 2), unit="average_engagement"))
        note = _compare(f"{label} content", grouped, engagements)
        if note:
            observations.append(note)

    mix_counts = _mix(media) if media else {}
    for media_type, count in mix_counts.items():
        grouped = [
            int(item.engagement)
            for item in sample
            if (item.media_type or "UNKNOWN") == media_type and item.engagement is not None
        ]
        if len(grouped) >= 1 and len(engagements) >= 2:
            note = _compare(f"{media_type} posts", grouped, engagements)
            if note:
                observations.append(note)
        del count

    ranked = sorted(sample, key=lambda item: int(item.engagement or 0), reverse=True)
    top = [_rank_row(item) for item in ranked[:3]] if ranked else []
    low = [_rank_row(item) for item in ranked[-3:]] if len(ranked) >= 2 else []
    if media and not ranked:
        metrics.append(unavailable("top_content", "not_provided_by_meta"))
        metrics.append(unavailable("low_content", "not_provided_by_meta"))
    elif len(ranked) < 2:
        metrics.append(unavailable("low_content", "insufficient_historical_data" if ranked else "no_posts"))

    return ContentPerformanceSnapshot(
        user_id=user_id,
        captured_at=captured_at,
        sample_size=len(media),
        metrics=metrics,
        content_mix=mix_counts,
        observations=observations,
        top_content=top,
        low_content=low,
    )


def trend_context(performance: ContentPerformanceSnapshot, *, account_id: str, username: str | None) -> dict[str, Any]:
    """Normalized evidence for Trend MCP. Captions, biography, and tokens are omitted."""
    payload = {
        "source": "instagram_account_intelligence",
        "account_ref": account_id,
        "username": username,
        "captured_at": performance.captured_at.isoformat(),
        "sample_size": performance.sample_size,
        "metrics": [item.model_dump(mode="json") for item in performance.metrics],
        "content_mix": performance.content_mix,
        "observations": list(performance.observations),
        "top_content": list(performance.top_content),
        "low_content": list(performance.low_content),
    }
    return _scrub(payload)


def _frequency(timestamps: list[datetime]) -> MetricReading:
    if len(timestamps) < 2:
        return unavailable("posting_frequency", "insufficient_historical_data")
    ordered = sorted(timestamps)
    span_days = (ordered[-1] - ordered[0]).total_seconds() / 86400
    if span_days < 1:
        return unavailable("posting_frequency", "insufficient_historical_data")
    per_week = len(ordered) / (span_days / 7)
    return available("posting_frequency", round(per_week, 2), unit="posts_per_week")


def _consistency(timestamps: list[datetime]) -> MetricReading:
    if len(timestamps) < 3:
        return unavailable("posting_consistency", "insufficient_historical_data")
    ordered = sorted(timestamps)
    gaps = [(ordered[index + 1] - ordered[index]).total_seconds() for index in range(len(ordered) - 1)]
    if len(gaps) < 2:
        return unavailable("posting_consistency", "insufficient_historical_data")
    mean_gap = _mean([float(item) for item in gaps])
    if mean_gap <= 0:
        return unavailable("posting_consistency", "insufficient_historical_data")
    variation = pstdev(gaps) / mean_gap
    return available("posting_consistency", round(variation, 3), unit="gap_variation")


def _recent(sample: list[InstagramMediaSnapshot]) -> MetricReading:
    scored = [item for item in sample if item.published_at is not None and item.engagement is not None]
    if len(scored) < 4:
        return unavailable("recent_performance", "insufficient_historical_data")
    ordered = sorted(scored, key=lambda item: item.published_at or _utcnow())
    midpoint = len(ordered) // 2
    older = [int(item.engagement or 0) for item in ordered[:midpoint]]
    newer = [int(item.engagement or 0) for item in ordered[midpoint:]]
    if not older or not newer:
        return unavailable("recent_performance", "insufficient_historical_data")
    return available(
        "recent_performance",
        round(_mean([float(item) for item in newer]) - _mean([float(item) for item in older]), 2),
        unit="engagement_delta_vs_earlier_sample",
    )


def _mix(media: list[InstagramMediaSnapshot]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in media:
        key = item.media_type or "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _rank_row(item: InstagramMediaSnapshot) -> dict[str, Any]:
    return {
        "instagram_media_id": item.instagram_media_id,
        "media_type": item.media_type,
        "labels": list(item.labels),
        "engagement": item.engagement,
        "published_at": item.published_at.isoformat() if item.published_at else None,
    }


def _scrub(value: Any) -> Any:
    cleaned = redact_value(value)
    if isinstance(cleaned, dict):
        kept = {
            key: _scrub(item)
            for key, item in cleaned.items()
            if str(key).lower() not in _TOKEN_KEYS
        }
        fields = kept.get("fields")
        if isinstance(fields, list):
            kept["fields"] = [
                item
                for item in fields
                if not (isinstance(item, dict) and item.get("metric") in _OMITTED_FIELDS)
            ]
        return kept
    if isinstance(cleaned, list):
        return [_scrub(item) for item in cleaned]
    return cleaned


def _insight_from_error(exc: AppError) -> str:
    if exc.code == ErrorCode.AUTHENTICATION_ERROR:
        raise exc
    if exc.code == ErrorCode.RATE_LIMITED:
        return "rate_limited"
    if exc.code == ErrorCode.TIMEOUT:
        return "timeout"
    if exc.code == ErrorCode.PERMISSION_ERROR:
        return "missing_permission"
    if exc.details.get("graph_code") == 100:
        return "unsupported_metric"
    return "not_provided_by_meta"


def _blank_insight(
    user_id: str,
    object_id: str,
    object_type: str,
    metric: str,
    period: str,
    reason: str,
    captured_at: datetime,
) -> InstagramInsightSnapshot:
    return InstagramInsightSnapshot(
        user_id=user_id,
        object_id=object_id,
        object_type=object_type,  # type: ignore[arg-type]
        metric=metric,
        period=period,
        status="unavailable",
        reason=reason,
        captured_at=captured_at,
    )


def _insight_value(payload: dict[str, Any], metric: str) -> float | None:
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict) or item.get("name") != metric:
            continue
        values = item.get("values")
        if isinstance(values, list) and values:
            last = values[-1]
            if isinstance(last, dict) and last.get("value") is not None:
                try:
                    return float(last["value"])
                except (TypeError, ValueError):
                    return None
        if item.get("value") is not None:
            try:
                return float(item["value"])
            except (TypeError, ValueError):
                return None
    return None


class AccountIntelligenceService:
    def __init__(self, settings: Settings, session: Session, reader: Any | None = None) -> None:
        self._settings = settings
        self._session = session
        self._reader = reader
        self._accounts = InstagramAccountRepository(session)
        self._business = BusinessRepository(session)

    async def account(self, user_id: str) -> dict[str, Any]:
        bundle = await self._bundle(user_id)
        context = trend_context(
            bundle["performance"],
            account_id=bundle["account"].instagram_account_id,
            username=bundle["profile"].username,
        )
        self._store("account", bundle["profile"])
        self._store("performance", bundle["performance"])
        self._store("trend", context, external_id=bundle["account"].instagram_account_id)
        return {
            "account": bundle["profile"].model_dump(mode="json"),
            "performance": bundle["performance"].model_dump(mode="json"),
            "trend_context": context,
        }

    async def posts(self, user_id: str, *, limit: int = 25) -> dict[str, Any]:
        bundle = await self._bundle(user_id, media_limit=limit)
        for item in bundle["media"]:
            self._store("media", item, external_id=item.instagram_media_id)
        context = trend_context(
            bundle["performance"],
            account_id=bundle["account"].instagram_account_id,
            username=bundle["profile"].username,
        )
        return {
            "captured_at": bundle["captured_at"].isoformat(),
            "posts": [item.model_dump(mode="json") for item in bundle["media"]],
            "trend_context": context,
        }

    async def insights(self, user_id: str) -> dict[str, Any]:
        bundle = await self._bundle(user_id, include_insights=True)
        for item in bundle["insights"]:
            self._store("insight", item, external_id=f"{item.object_id}:{item.metric}")
        return {
            "captured_at": bundle["captured_at"].isoformat(),
            "insights": [item.model_dump(mode="json") for item in bundle["insights"]],
        }

    async def top_content(self, user_id: str) -> dict[str, Any]:
        bundle = await self._bundle(user_id)
        performance = bundle["performance"]
        context = trend_context(
            performance,
            account_id=bundle["account"].instagram_account_id,
            username=bundle["profile"].username,
        )
        return {
            "captured_at": bundle["captured_at"].isoformat(),
            "sample_size": performance.sample_size,
            "top_content": performance.top_content,
            "low_content": performance.low_content,
            "observations": performance.observations,
            "metrics": [item.model_dump(mode="json") for item in performance.metrics],
            "trend_context": context,
        }

    async def _bundle(
        self,
        user_id: str,
        *,
        include_insights: bool = False,
        media_limit: int = 25,
    ) -> dict[str, Any]:
        self._trend_owner = user_id
        account = self._require_account(user_id)
        captured_at = _utcnow()
        reader, owned = await self._open_reader(account)
        try:
            raw_profile = await reader.read_profile()
            self._assert_graph_account(account, raw_profile)
            raw_media = await reader.read_media(max(1, min(int(media_limit), 50)))
            profile = self._profile(user_id, account, raw_profile, captured_at)
            media = self._media(user_id, raw_media, self._product_names(user_id), self._local_posts(user_id), captured_at)
            followers = _field_int(profile, "followers_count")
            performance = build_performance(
                user_id=user_id,
                media=media,
                followers=followers,
                captured_at=captured_at,
            )
            insights: list[InstagramInsightSnapshot] = []
            if include_insights:
                insights = await self._insights(reader, user_id, account.instagram_account_id, media, captured_at)
        finally:
            if owned:
                closer = getattr(reader, "aclose", None)
                if closer is not None:
                    await closer()
        return {
            "account": account,
            "profile": profile,
            "media": media,
            "performance": performance,
            "insights": insights,
            "captured_at": captured_at,
        }

    def _require_account(self, user_id: str) -> Any:
        account = self._accounts.get_primary(user_id)
        if account is None or account.status == AccountStatus.DISCONNECTED.value:
            raise AppError(
                ErrorCode.INSTAGRAM_NOT_CONNECTED,
                "Connect an Instagram professional account before requesting intelligence.",
                http_status=409,
            )
        if account.status == AccountStatus.EXPIRED.value or _token_expired(account.token_expires_at):
            account.status = AccountStatus.EXPIRED.value
            raise AppError(
                ErrorCode.AUTHENTICATION_ERROR,
                "Instagram access token is expired.",
                http_status=401,
            )
        if account.status != AccountStatus.CONNECTED.value:
            raise AppError(
                ErrorCode.INSTAGRAM_NOT_CONNECTED,
                "The Instagram account is not available.",
                http_status=409,
            )
        if account.user_id != user_id:
            raise AppError(
                ErrorCode.INVALID_ACCOUNT,
                "The Instagram account id did not match the connected professional account.",
                http_status=400,
            )
        return account

    def _assert_graph_account(self, account: Any, raw: dict[str, Any]) -> None:
        confirmed = str(raw.get("id") or "").strip()
        if confirmed != account.instagram_account_id:
            raise AppError(
                ErrorCode.INVALID_ACCOUNT,
                "The Instagram account id did not match the connected professional account.",
                http_status=400,
            )

    async def _open_reader(self, account: Any) -> tuple[Any, bool]:
        if account.user_id != getattr(self, "_trend_owner", account.user_id):
            raise AppError(
                ErrorCode.INVALID_ACCOUNT,
                "The Instagram account id did not match the connected professional account.",
                http_status=400,
            )
        if self._reader is not None:
            bind = getattr(self._reader, "bind_account", None)
            if callable(bind):
                bind(account.instagram_account_id)
            return self._reader, False
        try:
            token = decrypt_token(self._settings, account.access_token_encrypted)
        except TokenEncryptionError as exc:
            raise AppError(
                ErrorCode.AUTHENTICATION_ERROR,
                "Instagram access token could not be used.",
                http_status=401,
            ) from exc
        user_settings = self._settings.model_copy(
            update={
                "meta_access_token": token,
                "instagram_access_token": token,
                "instagram_account_id": account.instagram_account_id,
            }
        )
        return GraphInstagramReader(InstagramReadClient(user_settings), account.instagram_account_id), True

    def _product_names(self, user_id: str) -> list[str]:
        profile = self._business.get_for_user(user_id)
        if profile is None:
            return []
        names: list[str] = []
        for raw in (profile.products, profile.services):
            if isinstance(raw, str):
                names.extend(part.strip() for part in re.split(r"[,;\n]", raw) if part.strip())
            elif isinstance(raw, list):
                names.extend(str(item).strip() for item in raw if str(item).strip())
        return names

    def _local_posts(self, user_id: str) -> dict[str, InstagramPost]:
        rows = self._session.scalars(select(InstagramPost).where(InstagramPost.user_id == user_id))
        found: dict[str, InstagramPost] = {}
        for row in rows:
            if row.instagram_media_id:
                found[row.instagram_media_id] = row
        return found

    def _profile(self, user_id: str, account: Any, raw: dict[str, Any], captured_at: datetime) -> InstagramAccountSnapshot:
        self._assert_graph_account(account, raw)
        fields: list[MetricReading] = []
        for key in _PROFILE_FIELDS:
            if key not in raw or raw.get(key) is None or raw.get(key) == "":
                fields.append(unavailable(key, "not_provided_by_meta"))
                continue
            value = raw[key]
            if key.endswith("_count"):
                parsed = _optional_int(raw, key)
                if parsed is None:
                    fields.append(unavailable(key, "not_provided_by_meta"))
                else:
                    fields.append(available(key, parsed))
            else:
                fields.append(available(key, str(value)))
        username = raw.get("username") if isinstance(raw.get("username"), str) else None
        name = raw.get("name") if isinstance(raw.get("name"), str) else None
        return InstagramAccountSnapshot(
            user_id=user_id,
            instagram_account_pk=account.id,
            instagram_account_id=account.instagram_account_id,
            username=username,
            name=name,
            captured_at=captured_at,
            fields=fields,
        )

    def _media(
        self,
        user_id: str,
        raw_items: list[dict[str, Any]],
        products: list[str],
        local_posts: dict[str, InstagramPost],
        captured_at: datetime,
    ) -> list[InstagramMediaSnapshot]:
        snapshots: list[InstagramMediaSnapshot] = []
        for raw in raw_items:
            media_id = str(raw.get("id") or "").strip()
            if not media_id:
                continue
            likes = _optional_int(raw, "like_count")
            comments = _optional_int(raw, "comments_count")
            local = local_posts.get(media_id)
            post_type = getattr(local, "post_type", None)
            caption = raw.get("caption") if isinstance(raw.get("caption"), str) else None
            metrics = [
                available("like_count", likes) if likes is not None else unavailable("like_count", "not_provided_by_meta"),
                available("comments_count", comments)
                if comments is not None
                else unavailable("comments_count", "not_provided_by_meta"),
            ]
            engagement = _engagement(likes, comments)
            if engagement is None:
                metrics.append(unavailable("engagement", "not_provided_by_meta"))
            else:
                metrics.append(available("engagement", engagement))
            publication_status = getattr(local, "status", None) or "PUBLISHED"
            snapshots.append(
                InstagramMediaSnapshot(
                    user_id=user_id,
                    instagram_media_id=media_id,
                    media_type=str(raw["media_type"]) if raw.get("media_type") else None,
                    media_product_type=str(raw["media_product_type"]) if raw.get("media_product_type") else None,
                    caption=caption,
                    published_at=_parse_time(raw.get("timestamp")),
                    permalink=raw.get("permalink") if isinstance(raw.get("permalink"), str) else None,
                    like_count=likes,
                    comments_count=comments,
                    engagement=engagement,
                    publication_status=str(publication_status),
                    labels=_labels(caption, products, str(post_type) if post_type else None),
                    captured_at=captured_at,
                    metrics=metrics,
                )
            )
        return snapshots

    async def _insights(
        self,
        reader: Any,
        user_id: str,
        account_id: str,
        media: list[InstagramMediaSnapshot],
        captured_at: datetime,
    ) -> list[InstagramInsightSnapshot]:
        snapshots: list[InstagramInsightSnapshot] = []
        stop_reason: str | None = None
        targets: list[tuple[str, str, str, str]] = [
            (account_id, "account", metric, period) for metric, period in _ACCOUNT_INSIGHTS
        ]
        for item in media[:3]:
            for metric, period in _MEDIA_INSIGHTS:
                targets.append((item.instagram_media_id, "media", metric, period))
        for object_id, object_type, metric, period in targets:
            if stop_reason is not None:
                snapshots.append(
                    _blank_insight(user_id, object_id, object_type, metric, period, stop_reason, captured_at)
                )
                continue
            snapshot = await self._one_insight(
                reader, user_id, object_id, object_type, metric, period, captured_at
            )
            snapshots.append(snapshot)
            if snapshot.reason in _STOP_INSIGHT_REASONS:
                stop_reason = snapshot.reason
        return snapshots

    async def _one_insight(
        self,
        reader: Any,
        user_id: str,
        object_id: str,
        object_type: str,
        metric: str,
        period: str,
        captured_at: datetime,
    ) -> InstagramInsightSnapshot:
        try:
            payload = await reader.read_insight(object_id, metric, period)
        except AppError as exc:
            return _blank_insight(
                user_id,
                object_id,
                object_type,
                metric,
                period,
                _insight_from_error(exc),
                captured_at,
            )
        value = _insight_value(payload if isinstance(payload, dict) else {}, metric)
        if value is None:
            return _blank_insight(
                user_id,
                object_id,
                object_type,
                metric,
                period,
                "not_provided_by_meta",
                captured_at,
            )
        return InstagramInsightSnapshot(
            user_id=user_id,
            object_id=object_id,
            object_type=object_type,  # type: ignore[arg-type]
            metric=metric,
            period=period,
            status="available",
            value=value,
            captured_at=captured_at,
        )

    def _store(self, kind: str, payload: Any, external_id: str | None = None) -> None:
        if hasattr(payload, "model_dump"):
            body = payload.model_dump(mode="json")
            captured = getattr(payload, "captured_at", None)
        elif isinstance(payload, dict):
            body = payload
            captured = payload.get("captured_at")
        else:
            return
        if isinstance(captured, str):
            captured_at = _parse_time(captured) or _utcnow()
        elif isinstance(captured, datetime):
            captured_at = _aware(captured)
        else:
            captured_at = _utcnow()
        record = InstagramIntelligenceRecord(
            user_id=str(body.get("user_id") or self._user_from_body(body)),
            kind=kind,
            external_id=external_id,
            payload=_scrub(body),
            captured_at=captured_at,
        )
        if kind == "trend":
            record.user_id = self._trend_owner
        self._session.add(record)
        self._session.flush()

    def _user_from_body(self, body: dict[str, Any]) -> str:
        return str(getattr(self, "_trend_owner", "") or body.get("user_id") or "")


def _field_int(profile: InstagramAccountSnapshot, metric: str) -> int | None:
    for field in profile.fields:
        if field.metric == metric and field.status == "available" and isinstance(field.value, int):
            return field.value
    return None


def _token_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    return _aware(expires_at) <= _utcnow()
