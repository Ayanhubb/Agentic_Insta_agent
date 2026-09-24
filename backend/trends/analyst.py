"""DeepSeek reasoning over normalized trend evidence.

The model receives only the structured packet. It does not browse, call Meta,
publish, or receive permission to invent numbers or evidence ids. Malformed
JSON and briefs that break the classification rules are rejected. Retries stop
at ``llm_max_attempts``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from pydantic import ValidationError

from ai.llm.deepseek import DeepSeekLLMProvider
from backend.trends.schemas import (
    INSUFFICIENT_HISTORY,
    RELIABLE_COMPARISON_POSTS,
    TREND_CLASSIFICATION_METHODOLOGY,
    AccountSummary,
    ClaimKind,
    ClassifiedStatement,
    Confidence,
    ContentOpportunity,
    CreativeAsset,
    EvidenceStrength,
    Freshness,
    Relevance,
    TrendAnalysisRequest,
    TrendAssessment,
    TrendBrief,
    TrendEvidence,
    TrendObservation,
    VisualCreativeNote,
)
from config import Settings
from models.errors import AppError, ErrorCode
from services.logging import log_step

logger = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
_INTERPRET_RE = re.compile(r"\b(may indicate|might indicate|suggests that|this may)\b", re.IGNORECASE)
_RECOMMEND_RE = re.compile(r"\b(consider|recommend|should test|try testing)\b", re.IGNORECASE)
_KIND_MARKERS = ("OBSERVED:", "DISCOVERED:", "INFERRED:", "RECOMMENDED:")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_DISCOUNT_RE = re.compile(r"\b(?:discount|percent off|%\s*off|flat off)\b", re.IGNORECASE)
_COMPARE_RE = re.compile(
    r"\b(higher|lower|better|worse|outperform|outperforms|increased|decreased|compared with)\b",
    re.IGNORECASE,
)
_GROUNDED = {ClaimKind.OBSERVED, ClaimKind.DISCOVERED}
_SENSITIVE_RE = re.compile(
    r"\b(years old|age|gender|race|ethnicity|religion|disability|pregnant|skin tone|attractiveness)\b",
    re.IGNORECASE,
)
_BRIEF_KEYS = [
    "account_summary",
    "current_trends",
    "business_opportunities",
    "festival_opportunities",
    "content_opportunities",
    "risks",
    "data_gaps",
]

TREND_SYSTEM = """You are the reasoning model for Trend Intelligence.
Return only a JSON object. The word JSON means the response format, not a tool.
You do not browse, fetch URLs, or retrieve web pages.
You do not call Meta, Instagram, or any other API.
You do not publish, schedule, or approve content.
You do not invent evidence, sources, metrics, or evidence ids.
Use only ids and numbers that appear in the supplied packet.
Keep each statement one kind only: OBSERVED, DISCOVERED, INFERRED, or RECOMMENDED.
OBSERVED cites account, historical, festival, business, product, or offer records.
DISCOVERED cites researched trend observations.
INFERRED interprets cited evidence and does not recommend an action.
RECOMMENDED proposes an action and cites evidence.
Never merge those kinds into one statement.
Each recommendation includes why_now, evidence, business_relevance, product_relevance,
festival_relevance, recommended_format, creative_direction, confidence, and expiration.
Use evidence_strength values "strong evidence", "moderate evidence", or "limited evidence".
Do not return a numeric trend score.
Do not invent metrics, trend sources, festival dates, products, offers, or account statistics.
Label freshness as current, recent, or stale using the supplied dates.
If the historical sample is smaller than the reliable comparison size, add this data gap exactly:
Insufficient historical data for a reliable performance comparison.
Do not compare performance when that gap applies.
If evidence is missing, return empty trend and opportunity lists and explain the gap in data_gaps.
The festival catalog row id is "festival" when a festival was supplied.
"""


class TrendBriefRejected(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CreativeVision(Protocol):
    async def describe_creative(self, asset: CreativeAsset) -> VisualCreativeNote | None: ...


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _number_tokens(text: str) -> set[str]:
    return {match.replace("%", "") for match in _NUMBER_RE.findall(text)}


def is_stale(observation: TrendObservation, analyzed_at: datetime, stale_after_days: int) -> bool:
    moment = _as_utc(analyzed_at)
    if observation.valid_until is not None and _as_utc(observation.valid_until) < moment:
        return True
    return _as_utc(observation.observed_at) < moment - timedelta(days=stale_after_days)


def is_current(observation: TrendObservation, analyzed_at: datetime, stale_after_days: int) -> bool:
    if is_stale(observation, analyzed_at, stale_after_days):
        return False
    moment = _as_utc(analyzed_at)
    return _as_utc(observation.observed_at) >= moment - timedelta(days=7)


def observations_conflict(observations: list[TrendObservation]) -> bool:
    ids = {item.id for item in observations}
    return any(other in ids for item in observations for other in item.conflicts_with)


def classify_freshness(
    observations: list[TrendObservation],
    analyzed_at: datetime,
    stale_after_days: int,
) -> Freshness:
    if not observations or all(is_stale(item, analyzed_at, stale_after_days) for item in observations):
        return Freshness.STALE
    if any(is_current(item, analyzed_at, stale_after_days) for item in observations):
        return Freshness.CURRENT
    return Freshness.RECENT


def classify_strength(
    observations: list[TrendObservation],
    analyzed_at: datetime,
    stale_after_days: int,
) -> EvidenceStrength:
    if not observations or observations_conflict(observations):
        return EvidenceStrength.LIMITED
    fresh = [item for item in observations if not is_stale(item, analyzed_at, stale_after_days)]
    sourced = [item for item in fresh if any(piece.source_name.strip() and piece.excerpt.strip() for piece in item.evidence)]
    if len(sourced) >= 2:
        return EvidenceStrength.STRONG
    if fresh:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.LIMITED


def classify_confidence(
    strength: EvidenceStrength,
    freshness: Freshness,
    business_relevance: Relevance,
) -> Confidence:
    if freshness is Freshness.STALE or strength is EvidenceStrength.LIMITED:
        return "low"
    if strength is EvidenceStrength.STRONG and business_relevance is Relevance.HIGH:
        return "high"
    return "moderate"


def sensitive_visual_text(text: str | None) -> bool:
    return bool(text and _SENSITIVE_RE.search(text))


def sanitize_visual_note(note: VisualCreativeNote) -> VisualCreativeNote | None:
    fields = {
        "composition": note.composition,
        "product_visibility": note.product_visibility,
        "logo_visibility": note.logo_visibility,
        "text_readability": note.text_readability,
        "visual_consistency": note.visual_consistency,
    }
    if any(sensitive_visual_text(value) for value in fields.values()):
        return None
    patterns = [item for item in note.repeated_patterns if not sensitive_visual_text(item)]
    if any(sensitive_visual_text(item) for item in note.repeated_patterns):
        return None
    cleaned = note.model_copy(update={"repeated_patterns": patterns})
    if not any(fields.values()) and not cleaned.repeated_patterns:
        return None
    return cleaned


class _EvidenceIndex:
    def __init__(self, request: TrendAnalysisRequest, notes: list[VisualCreativeNote]) -> None:
        self.observations = {item.id: item for item in request.observations}
        self.records: dict[str, Any] = {}
        for item in request.observations:
            self.records[item.id] = item
            for piece in item.evidence:
                self.records[piece.id] = piece
        for item in request.account_insights:
            self.records[item.id] = item
        for item in request.historical_performance.points:
            self.records[item.id] = item
        for item in request.products:
            self.records[item.id] = item
        for item in request.offers:
            self.records[item.id] = item
        for item in request.brand_guidelines:
            self.records[item.id] = item
        if request.festival is not None:
            self.records["festival"] = request.festival
        for note in notes:
            self.records[note.evidence_id] = note

    def require(self, evidence_id: str) -> Any:
        if evidence_id not in self.records:
            raise TrendBriefRejected(f"Evidence id {evidence_id} was not supplied.")
        return self.records[evidence_id]

    def numbers_for(self, evidence_ids: list[str]) -> set[str]:
        blobs = []
        for evidence_id in evidence_ids:
            record = self.require(evidence_id)
            payload = record.model_dump(mode="json") if hasattr(record, "model_dump") else record
            blobs.append(payload)
        return _number_tokens(json.dumps(blobs))


def historical_sample_size(request: TrendAnalysisRequest) -> int | None:
    sizes = [item.sample_size for item in request.account_insights if isinstance(item.sample_size, int)]
    posts = [item for item in request.historical_performance.points if item.metric == "stored_post"]
    candidates = list(sizes)
    if posts:
        candidates.append(len(posts))
    if not candidates:
        return None
    return min(candidates)


def comparison_blocked(request: TrendAnalysisRequest) -> bool:
    sample = historical_sample_size(request)
    return sample is not None and sample < RELIABLE_COMPARISON_POSTS


def _packet_dates(request: TrendAnalysisRequest) -> set[str]:
    return set(_ISO_DATE.findall(json.dumps(request.model_dump(mode="json"), default=str)))


def _source_names(request: TrendAnalysisRequest) -> set[str]:
    names: set[str] = set()
    for observation in request.observations:
        for piece in observation.evidence:
            text = piece.source_name.strip().casefold()
            if len(text) >= 4:
                names.add(text)
    return names


def _cited_source_names(evidence_ids: list[str], index: _EvidenceIndex) -> set[str]:
    names: set[str] = set()
    for evidence_id in evidence_ids:
        record = index.require(evidence_id)
        pieces: list[TrendEvidence] = []
        if isinstance(record, TrendEvidence):
            pieces = [record]
        elif isinstance(record, TrendObservation):
            pieces = list(record.evidence)
        for piece in pieces:
            text = piece.source_name.strip().casefold()
            if len(text) >= 4:
                names.add(text)
    return names


def _epistemic_status(record: Any, index: _EvidenceIndex) -> str:
    if isinstance(record, TrendObservation):
        return record.epistemic_status
    if isinstance(record, TrendEvidence):
        for observation in index.observations.values():
            if any(piece.id == record.id for piece in observation.evidence):
                return observation.epistemic_status
        return "DISCOVERED"
    return "OBSERVED"


def _merged_claim(statement: ClassifiedStatement) -> bool:
    text = statement.text
    markers = [marker for marker in _KIND_MARKERS if marker in text.upper()]
    interpretive = _INTERPRET_RE.search(text) is not None
    advisory = _RECOMMEND_RE.search(text) is not None
    if len(markers) > 1 or (interpretive and advisory):
        return True
    if statement.kind in _GROUNDED and (interpretive or advisory):
        return True
    if statement.kind is ClaimKind.INFERRED and advisory:
        return True
    return statement.kind is ClaimKind.RECOMMENDED and interpretive


def _check_dates(text: str, packet_dates: set[str]) -> None:
    invented = set(_ISO_DATE.findall(text)) - packet_dates
    if invented:
        raise TrendBriefRejected("A statement used a date that was not supplied.")


def _check_offer_language(text: str, request: TrendAnalysisRequest) -> None:
    if _DISCOUNT_RE.search(text) is None:
        return
    if not request.offers:
        raise TrendBriefRejected("An offer was mentioned without a supplied offer.")
    blob = text.casefold()
    if not any(offer.name.casefold() in blob for offer in request.offers):
        raise TrendBriefRejected("An offer was mentioned without the supplied offer name.")


def _check_comparison(text: str, request: TrendAnalysisRequest) -> None:
    if comparison_blocked(request) and _COMPARE_RE.search(text):
        raise TrendBriefRejected(INSUFFICIENT_HISTORY)


def _check_statement(
    statement: ClassifiedStatement,
    index: _EvidenceIndex,
    request: TrendAnalysisRequest,
    packet_dates: set[str],
    known_sources: set[str],
) -> None:
    if _merged_claim(statement):
        raise TrendBriefRejected("A statement merged observed, discovered, inferred, or recommended claims.")
    if not statement.evidence_ids:
        raise TrendBriefRejected("Every statement must cite supplied evidence.")
    for evidence_id in statement.evidence_ids:
        record = index.require(evidence_id)
        if statement.kind in _GROUNDED and _epistemic_status(record, index) != statement.kind.value:
            raise TrendBriefRejected(f"A {statement.kind.value} statement cited evidence of another kind.")
    invented = _number_tokens(statement.text) - index.numbers_for(statement.evidence_ids)
    if invented:
        raise TrendBriefRejected("A statement used numbers that are not in the cited evidence.")
    cited_sources = _cited_source_names(statement.evidence_ids, index)
    blob = statement.text.casefold()
    if any(name in blob and name not in cited_sources for name in known_sources):
        raise TrendBriefRejected("A statement named a trend source it did not cite.")
    _check_dates(statement.text, packet_dates)
    _check_offer_language(statement.text, request)
    _check_comparison(statement.text, request)


def _cited_observations(evidence_ids: list[str], index: _EvidenceIndex) -> list[TrendObservation]:
    found: list[TrendObservation] = []
    for evidence_id in evidence_ids:
        record = index.require(evidence_id)
        if isinstance(record, TrendObservation) and record.id not in {item.id for item in found}:
            found.append(record)
        elif isinstance(record, TrendEvidence):
            for observation in index.observations.values():
                if any(piece.id == record.id for piece in observation.evidence) and observation.id not in {item.id for item in found}:
                    found.append(observation)
    return found


def _check_relevance(trend: TrendAssessment, request: TrendAnalysisRequest, cited: list[TrendObservation]) -> None:
    if trend.business_relevance is Relevance.HIGH and request.business_profile is None:
        raise TrendBriefRejected("High business relevance requires a business profile.")
    if trend.regional_relevance is Relevance.HIGH and not any(item.region for item in cited):
        raise TrendBriefRejected("High regional relevance requires a cited region.")
    if not request.products and trend.product_relevance is not Relevance.NONE:
        raise TrendBriefRejected("Product relevance requires a supplied product.")
    if trend.product_relevance is Relevance.HIGH:
        blob = f"{trend.title} {trend.summary}".casefold()
        if not any(product.name.casefold() in blob for product in request.products):
            raise TrendBriefRejected("High product relevance must name a supplied product.")
    _check_dates(f"{trend.title} {trend.summary}", _packet_dates(request))
    _check_offer_language(f"{trend.title} {trend.summary}", request)
    _check_comparison(f"{trend.title} {trend.summary}", request)


def _check_trend(
    trend: TrendAssessment,
    request: TrendAnalysisRequest,
    index: _EvidenceIndex,
    packet_dates: set[str],
    known_sources: set[str],
) -> None:
    if not trend.evidence_ids:
        raise TrendBriefRejected("A trend requires cited evidence.")
    cited = _cited_observations(trend.evidence_ids, index)
    if not cited:
        raise TrendBriefRejected("A trend must cite a supplied observation.")
    for statement in trend.statements:
        _check_statement(statement, index, request, packet_dates, known_sources)
    if not any(item.kind in _GROUNDED for item in trend.statements):
        raise TrendBriefRejected("A trend must keep an observed or discovered claim separate from the recommendation.")
    strength = classify_strength(cited, request.analyzed_at, request.stale_after_days)
    freshness = classify_freshness(cited, request.analyzed_at, request.stale_after_days)
    confidence = classify_confidence(strength, freshness, trend.business_relevance)
    if trend.evidence_strength is not strength or trend.freshness is not freshness or trend.confidence != confidence:
        raise TrendBriefRejected("Trend classification did not match the evidence methodology.")
    _check_relevance(trend, request, cited)


def _names_product(text: str, request: TrendAnalysisRequest) -> bool:
    blob = text.casefold()
    return any(product.name.casefold() in blob for product in request.products)


def _check_opportunity(
    opportunity: ContentOpportunity,
    request: TrendAnalysisRequest,
    index: _EvidenceIndex,
    packet_dates: set[str],
    known_sources: set[str],
    *,
    festival: bool,
) -> None:
    if not opportunity.evidence:
        raise TrendBriefRejected("A content opportunity requires evidence.")
    cited = _cited_observations(opportunity.evidence, index)
    cites_festival = request.festival is not None and "festival" in opportunity.evidence
    if not cited and not cites_festival:
        raise TrendBriefRejected("A content opportunity must cite supplied evidence.")
    _check_statement(opportunity.why_now, index, request, packet_dates, known_sources)
    if opportunity.why_now.kind not in {ClaimKind.INFERRED, ClaimKind.RECOMMENDED}:
        raise TrendBriefRejected("why_now must stay inferred or recommended.")
    if opportunity.business_relevance is Relevance.HIGH and request.business_profile is None:
        raise TrendBriefRejected("High business relevance requires a business profile.")
    if not request.products and opportunity.product_relevance is not Relevance.NONE:
        raise TrendBriefRejected("Product relevance requires a supplied product.")
    relevance_text = f"{opportunity.title} {opportunity.why_now.text} {opportunity.creative_direction}"
    if opportunity.product_relevance in {Relevance.HIGH, Relevance.MODERATE} and not _names_product(relevance_text, request):
        raise TrendBriefRejected("Product relevance must name a supplied product.")
    corpus = index.numbers_for(opportunity.evidence)
    for text in (opportunity.creative_direction, opportunity.recommended_format, opportunity.title):
        invented = _number_tokens(text) - corpus
        if invented:
            raise TrendBriefRejected("An opportunity used numbers that are not in the cited evidence.")
        _check_dates(text, packet_dates)
        _check_offer_language(text, request)
        _check_comparison(text, request)
    if opportunity.expiration is not None:
        expires = _as_utc(opportunity.expiration)
        if expires < _as_utc(request.analyzed_at):
            raise TrendBriefRejected("An opportunity expiration is already past.")
        if expires.date().isoformat() not in packet_dates:
            raise TrendBriefRejected("An opportunity expiration was not supplied.")
    if cited:
        strength = classify_strength(cited, request.analyzed_at, request.stale_after_days)
        freshness = classify_freshness(cited, request.analyzed_at, request.stale_after_days)
    else:
        strength = EvidenceStrength.LIMITED
        festival_date = request.festival.date if request.festival else None
        ahead = False
        if festival_date is not None:
            day = festival_date.date() if isinstance(festival_date, datetime) else festival_date
            ahead = day >= _as_utc(request.analyzed_at).date()
        freshness = Freshness.CURRENT if ahead else Freshness.STALE
    expected = "low" if freshness is Freshness.STALE or strength is EvidenceStrength.LIMITED else (
        "high" if strength is EvidenceStrength.STRONG else "moderate"
    )
    if opportunity.confidence != expected:
        raise TrendBriefRejected("Opportunity confidence did not match the evidence methodology.")
    if festival:
        name = request.festival.display_name if request.festival else ""
        fit = f"{opportunity.why_now.text} {opportunity.creative_direction}"
        if opportunity.festival_relevance is Relevance.NONE or not name or name.casefold() not in fit.casefold():
            raise TrendBriefRejected("A festival opportunity must use the supplied festival.")
    elif opportunity.festival_relevance is not Relevance.NONE:
        raise TrendBriefRejected("Festival relevance requires a supplied festival opportunity.")


def _gap_text(gaps: list[str]) -> str:
    return " ".join(gaps).casefold()


def accept_trend_brief(
    payload: dict[str, Any],
    request: TrendAnalysisRequest,
    notes: list[VisualCreativeNote],
    extra_gaps: list[str],
) -> TrendBrief:
    body = dict(payload)
    body.pop("trend_score", None)
    body["generated_at"] = request.analyzed_at
    body["classification_methodology"] = TREND_CLASSIFICATION_METHODOLOGY
    body["visual_notes"] = [note.model_dump(mode="json") for note in notes]
    try:
        brief = TrendBrief.model_validate(body)
    except ValidationError as exc:
        raise TrendBriefRejected("The DeepSeek response was not a valid trend brief.") from exc
    if "trend_score" in payload:
        raise TrendBriefRejected("A numeric trend score is not part of the trend brief.")

    index = _EvidenceIndex(request, notes)
    packet_dates = _packet_dates(request)
    known_sources = _source_names(request)
    sufficient = bool(request.account_insights or request.historical_performance.points)
    if brief.account_summary.sufficient_data is not sufficient:
        raise TrendBriefRejected("Account sufficiency did not match the supplied account data.")
    if not sufficient and any(item.kind in _GROUNDED for item in brief.account_summary.statements):
        raise TrendBriefRejected("Account facts require supplied account data.")
    for statement in brief.account_summary.statements:
        _check_statement(statement, index, request, packet_dates, known_sources)
    for statement in brief.risks:
        _check_statement(statement, index, request, packet_dates, known_sources)

    if not request.observations:
        if brief.current_trends or brief.business_opportunities or brief.festival_opportunities or brief.content_opportunities:
            raise TrendBriefRejected("Trends cannot be created when no observations were supplied.")
        if not brief.data_gaps:
            raise TrendBriefRejected("Missing observations must be recorded as a data gap.")
    elif not brief.current_trends and not brief.data_gaps:
        raise TrendBriefRejected("An empty trend list must record a data gap.")

    for trend in brief.current_trends:
        _check_trend(trend, request, index, packet_dates, known_sources)
    if request.festival is None and brief.festival_opportunities:
        raise TrendBriefRejected("Festival opportunities require a supplied festival.")
    if not sufficient and not brief.data_gaps:
        raise TrendBriefRejected("Missing account data must be recorded as a data gap.")
    if not sufficient and "account" not in _gap_text(brief.data_gaps):
        raise TrendBriefRejected("Missing account data must be named in data_gaps.")

    for opportunity in [*brief.business_opportunities, *brief.content_opportunities]:
        _check_opportunity(opportunity, request, index, packet_dates, known_sources, festival=False)
    for opportunity in brief.festival_opportunities:
        _check_opportunity(opportunity, request, index, packet_dates, known_sources, festival=True)

    gaps = list(brief.data_gaps)
    for gap in [*extra_gaps, *_server_gaps(request)]:
        if gap not in gaps:
            gaps.append(gap)
    return brief.model_copy(update={"data_gaps": gaps, "visual_notes": notes})


def _server_gaps(request: TrendAnalysisRequest) -> list[str]:
    gaps: list[str] = []
    if comparison_blocked(request):
        gaps.append(INSUFFICIENT_HISTORY)
    if not request.products:
        gaps.append("No product was supplied.")
    for gap in request.required_gaps:
        if gap not in gaps:
            gaps.append(gap)
    return gaps


def _prompt_packet(request: TrendAnalysisRequest, notes: list[VisualCreativeNote]) -> dict[str, Any]:
    packet = request.model_dump(mode="json", exclude={"creative_assets"})
    packet["visual_notes"] = [note.model_dump(mode="json") for note in notes]
    packet["methodology"] = TREND_CLASSIFICATION_METHODOLOGY
    packet["response_schema"] = {
        "account_summary": AccountSummary.model_json_schema(),
        "trend": TrendAssessment.model_json_schema(),
        "recommendation": ContentOpportunity.model_json_schema(),
    }
    return packet


class DeepSeekCreativeVision:
    """Maps DeepSeek image understanding onto creative notes.

    The prompt allows composition, product visibility, logo visibility, text
    readability, visual consistency, and repeated patterns. Sensitive personal
    attributes are dropped before they can enter the evidence packet.
    """

    def __init__(self, settings: Settings, *, transport: Any | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def describe_creative(self, asset: CreativeAsset) -> VisualCreativeNote | None:
        from ai.vision.deepseek import DeepSeekVisionProvider, VisionImage

        provider = DeepSeekVisionProvider(self._settings, transport=self._transport)
        image = VisionImage(
            user_id="trend-vision",
            local_path=asset.local_path,
            storage_ref=asset.storage_ref,
            image_base64=asset.image_base64,
        )
        understood = await provider.understand_image(
            image,
            prompt=(
                "Describe only composition, product visibility, logo visibility, text readability, "
                "visual consistency, and repeated creative patterns. "
                "Put composition in summary, product visibility in objects, logo visibility in brand_cues, "
                "readable text in visible_text, and consistency or repeated patterns in quality_notes. "
                "Do not infer age, gender, race, ethnicity, religion, health, disability, or any other "
                "sensitive attribute of people."
            ),
        )
        note = VisualCreativeNote(
            asset_id=asset.id,
            composition=understood.summary,
            product_visibility=", ".join(understood.objects) or None,
            logo_visibility=", ".join(understood.brand_cues) or None,
            text_readability=", ".join(understood.visible_text) or None,
            visual_consistency=", ".join(understood.quality_notes) or None,
            repeated_patterns=list(understood.quality_notes),
        )
        return sanitize_visual_note(note)


class DeepSeekTrendAnalyst:
    """Produces a validated TrendBrief from normalized evidence."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Any | None = None,
        vision: CreativeVision | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._vision = vision
        self._provider = DeepSeekLLMProvider(settings, transport=transport, sleeper=sleeper)

    async def analyze(self, request: TrendAnalysisRequest) -> TrendBrief:
        notes, gaps = await self._visual_notes(request)
        attempts = max(1, int(self._settings.llm_max_attempts))
        feedback: str | None = None
        last_error: TrendBriefRejected | None = None
        for attempt in range(1, attempts + 1):
            user_prompt = json.dumps(
                {
                    "evidence": _prompt_packet(request, notes),
                    "output_keys": _BRIEF_KEYS,
                    "retry_instruction": feedback,
                },
                default=str,
            )
            data = await self._provider.generate_structured(
                system_prompt=TREND_SYSTEM,
                user_prompt=user_prompt,
                schema={"required": _BRIEF_KEYS},
                user_id=request.user_id,
            )
            try:
                brief = accept_trend_brief(data, request, notes, gaps)
            except TrendBriefRejected as exc:
                last_error = exc
                feedback = exc.message
                if attempt >= attempts:
                    raise _rejected(exc.message) from exc
                continue
            log_step(
                logger,
                event="trend_analysis",
                task_id=request.user_id,
                step="deepseek_trend_brief",
                status="ok",
                attempt=attempt,
                trend_count=len(brief.current_trends),
            )
            return brief
        raise _rejected(last_error.message if last_error else "The DeepSeek trend brief was rejected.")

    async def _visual_notes(self, request: TrendAnalysisRequest) -> tuple[list[VisualCreativeNote], list[str]]:
        if not request.creative_assets:
            return [], []
        vision = self._vision
        if vision is None and self._settings.deepseek_configured:
            vision = DeepSeekCreativeVision(self._settings, transport=self._transport)
        if vision is None:
            return [], ["Creative assets were supplied, and visual analysis was not run."]
        notes: list[VisualCreativeNote] = []
        gaps: list[str] = []
        for asset in request.creative_assets:
            described = await vision.describe_creative(asset)
            note = sanitize_visual_note(described) if described is not None else None
            if note is None:
                gaps.append("A creative asset was omitted because the visual description inferred a sensitive attribute.")
                continue
            notes.append(note)
        return notes, gaps


def _rejected(message: str) -> AppError:
    return AppError(ErrorCode.DEEPSEEK_INVALID_RESPONSE, message, http_status=502, retryable=False)
