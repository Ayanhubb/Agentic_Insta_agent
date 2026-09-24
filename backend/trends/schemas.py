"""Contracts for DeepSeek trend reasoning.

Evidence strength, freshness, and confidence are categorical. This module does
not define a numeric trend score.

Methodology
-----------
strong evidence:
    At least two cited observations are current or recent, none of the cited
    observations conflict, and each of those fresh observations has source
    metadata.
moderate evidence:
    At least one cited observation is current or recent, and the cited set does
    not conflict.
limited evidence:
    The cited observations conflict, every cited observation is stale, or no
    fresh sourced observation is available.

freshness:
    current — a cited observation was seen within 7 days and is not expired.
    recent — a cited observation is inside the stale window and is not expired.
    stale — every cited observation is past ``valid_until`` or older than the
    stale window (14 days unless the request sets another window).

confidence:
    high — strong evidence, current or recent, and high business relevance.
    moderate — moderate evidence that is still current or recent.
    low — limited evidence, or freshness is stale.

Epistemic status
----------------
OBSERVED:
    A value read from the account, historical posts, the festival catalog,
    the business profile, a product, or an offer.
DISCOVERED:
    A value returned by trend research, with that source's name and time.
INFERRED:
    An interpretation that cites only supplied OBSERVED or DISCOVERED evidence.
RECOMMENDED:
    A content action. It cites evidence and is not itself a measured fact.

A performance comparison uses the supplied sample size. Fewer than 8 posts is
insufficient historical data. That gap is stated, and no comparison is made.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.content import BusinessProfileSnapshot, FestivalContext

FRESH_WINDOW_DAYS = 7
DEFAULT_STALE_AFTER_DAYS = 14
RELIABLE_COMPARISON_POSTS = 8
INSUFFICIENT_HISTORY = "Insufficient historical data for a reliable performance comparison."

TREND_CLASSIFICATION_METHODOLOGY = """
Evidence strength is categorical. There is no numeric trend score.
OBSERVED statements cite account, historical, festival, business, product, or offer records.
DISCOVERED statements cite researched trend observations.
INFERRED statements interpret cited evidence and do not recommend an action.
RECOMMENDED statements propose an action and cite evidence.
strong evidence: two or more cited observations are current or recent, they do not conflict, and each has source metadata.
moderate evidence: one or more cited observations are current or recent, and they do not conflict.
limited evidence: cited observations conflict, all cited observations are stale, or no fresh sourced observation is available.
freshness is current within 7 days, recent inside the stale window, and stale when expired or older than the stale window.
confidence is high only for strong, fresh evidence with high business relevance; moderate for moderate fresh evidence; low when evidence is limited or stale.
A performance comparison requires at least 8 posts in the supplied sample. A smaller sample is insufficient historical data and is not compared.
""".strip()


class ClaimKind(str, Enum):
    OBSERVED = "OBSERVED"
    DISCOVERED = "DISCOVERED"
    INFERRED = "INFERRED"
    RECOMMENDED = "RECOMMENDED"


class EvidenceStrength(str, Enum):
    STRONG = "strong evidence"
    MODERATE = "moderate evidence"
    LIMITED = "limited evidence"


class Freshness(str, Enum):
    CURRENT = "current"
    RECENT = "recent"
    STALE = "stale"


class Relevance(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    NONE = "none"


Confidence = Literal["high", "moderate", "low"]


class TrendEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_name: str
    excerpt: str
    observed_at: datetime
    published_at: datetime | None = None

    @field_validator("id", "source_name", "excerpt")
    @classmethod
    def _text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("evidence fields must not be blank")
        return text


class TrendObservation(BaseModel):
    """Normalized external or internal trend evidence. URLs are not included."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str
    trend_type: str
    industry: str | None = None
    region: str | None = None
    keywords: list[str] = Field(default_factory=list)
    evidence: list[TrendEvidence] = Field(default_factory=list)
    observed_at: datetime
    published_at: datetime | None = None
    valid_until: datetime | None = None
    conflicts_with: list[str] = Field(default_factory=list)
    epistemic_status: Literal["OBSERVED", "DISCOVERED"] = "DISCOVERED"

    @field_validator("id", "title", "description", "trend_type")
    @classmethod
    def _text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("observation fields must not be blank")
        return text


class AccountInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    metric: str
    value: str | int | float
    statement: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    sample_size: int | None = None

    @field_validator("id", "metric", "statement")
    @classmethod
    def _text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("insight fields must not be blank")
        return text


class ProductContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str | None = None
    category: str | None = None
    is_active: bool = True


class OfferContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str | None = None
    product_id: str | None = None
    valid_until: datetime | None = None


class BrandGuidelineContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    body: str


class PerformancePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    metric: str
    value: str | int | float
    recorded_at: datetime | None = None


class HistoricalPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[PerformancePoint] = Field(default_factory=list)
    summary: str | None = None


class CreativeAsset(BaseModel):
    """A previous Instagram creative or product still for vision analysis."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["instagram_creative", "product_asset"]
    local_path: str | None = None
    storage_ref: str | None = None
    image_base64: str | None = None


class EvidenceCoverage(BaseModel):
    """Which analysis lenses were actually populated from stored evidence."""

    model_config = ConfigDict(extra="forbid")

    recent_account_performance: bool = False
    content_history: bool = False
    engagement_metrics: bool = False
    retail_trends: bool = False
    food_and_beverage_trends: bool = False
    indian_trends: bool = False
    regional_trends: bool = False
    festival_opportunities: bool = False
    product_opportunities: bool = False


class TrendAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    analyzed_at: datetime
    observations: list[TrendObservation] = Field(default_factory=list)
    account_insights: list[AccountInsight] = Field(default_factory=list)
    business_profile: BusinessProfileSnapshot | None = None
    products: list[ProductContext] = Field(default_factory=list)
    offers: list[OfferContext] = Field(default_factory=list)
    brand_guidelines: list[BrandGuidelineContext] = Field(default_factory=list)
    festival: FestivalContext | None = None
    historical_performance: HistoricalPerformance = Field(default_factory=HistoricalPerformance)
    creative_assets: list[CreativeAsset] = Field(default_factory=list)
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS
    coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)
    required_gaps: list[str] = Field(default_factory=list)

    @field_validator("stale_after_days")
    @classmethod
    def _window(cls, value: int) -> int:
        if value < FRESH_WINDOW_DAYS:
            raise ValueError("stale_after_days must be at least the current window")
        return value


class ClassifiedStatement(BaseModel):
    """One claim of exactly one kind. Kinds are never combined in one statement."""

    model_config = ConfigDict(extra="forbid")

    kind: ClaimKind
    text: str
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("statement text is required")
        return text


class VisualCreativeNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    composition: str | None = None
    product_visibility: str | None = None
    logo_visibility: str | None = None
    text_readability: str | None = None
    visual_consistency: str | None = None
    repeated_patterns: list[str] = Field(default_factory=list)

    @property
    def evidence_id(self) -> str:
        return f"vision:{self.asset_id}"


class AccountSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sufficient_data: bool
    statements: list[ClassifiedStatement] = Field(default_factory=list)


class TrendAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str
    evidence_ids: list[str]
    evidence_strength: EvidenceStrength
    freshness: Freshness
    business_relevance: Relevance
    regional_relevance: Relevance
    product_relevance: Relevance
    confidence: Confidence
    statements: list[ClassifiedStatement]


class ContentOpportunity(BaseModel):
    """A recommendation. Relevance values are categorical, not a trend score."""

    model_config = ConfigDict(extra="forbid")

    title: str
    why_now: ClassifiedStatement
    evidence: list[str]
    business_relevance: Relevance
    product_relevance: Relevance
    festival_relevance: Relevance = Relevance.NONE
    recommended_format: str
    creative_direction: str
    confidence: Confidence
    expiration: datetime | None = None


class TrendBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    account_summary: AccountSummary
    current_trends: list[TrendAssessment] = Field(default_factory=list)
    business_opportunities: list[ContentOpportunity] = Field(default_factory=list)
    festival_opportunities: list[ContentOpportunity] = Field(default_factory=list)
    content_opportunities: list[ContentOpportunity] = Field(default_factory=list)
    risks: list[ClassifiedStatement] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    visual_notes: list[VisualCreativeNote] = Field(default_factory=list)
    classification_methodology: str = TREND_CLASSIFICATION_METHODOLOGY
