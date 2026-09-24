"""External trend research.

This module collects public news and government pages, normalizes them, and
stores the result. It does not call DeepSeek, and it does not publish.
DeepSeek may interpret the stored observations later. It is not asked to browse.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from html import unescape
from typing import Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

USER_AGENT = "YottoTrendResearch/1.0"
_FRESHNESS_PREFIX = "freshness:"
_LANGUAGE_PREFIX = "language:"

PERMITTED_HOSTS = frozenset(
    {
        "thehindu.com",
        "indianexpress.com",
        "timesofindia.indiatimes.com",
        "economictimes.indiatimes.com",
        "livemint.com",
        "business-standard.com",
        "hindustantimes.com",
        "ndtv.com",
        "pib.gov.in",
        "india.gov.in",
        "wb.gov.in",
    }
)
BLOCKED_SUFFIXES = (
    "instagram.com",
    "cdninstagram.com",
    "facebook.com",
    "fbcdn.net",
    "threads.net",
    "fb.com",
)
SOURCE_NAMES = {
    "thehindu.com": "The Hindu",
    "indianexpress.com": "The Indian Express",
    "timesofindia.indiatimes.com": "The Times of India",
    "economictimes.indiatimes.com": "The Economic Times",
    "livemint.com": "Mint",
    "business-standard.com": "Business Standard",
    "hindustantimes.com": "Hindustan Times",
    "ndtv.com": "NDTV",
    "pib.gov.in": "Press Information Bureau",
    "india.gov.in": "National Portal of India",
    "wb.gov.in": "Government of West Bengal",
}
_SECRET_QUERY = ("access_token", "api_key", "apikey", "password", "token", "sessionid")

_PLACES: tuple[tuple[str, str], ...] = (
    ("kolkata", "IN-WB-KOLKATA"),
    ("calcutta", "IN-WB-KOLKATA"),
    ("mumbai", "IN-MH-MUMBAI"),
    ("bengaluru", "IN-KA-BENGALURU"),
    ("bangalore", "IN-KA-BENGALURU"),
    ("chennai", "IN-TN-CHENNAI"),
    ("hyderabad", "IN-TG-HYDERABAD"),
    ("west bengal", "IN-WB"),
    ("tamil nadu", "IN-TN"),
    ("andhra pradesh", "IN-AP"),
    ("madhya pradesh", "IN-MP"),
    ("uttar pradesh", "IN-UP"),
    ("himachal pradesh", "IN-HP"),
    ("arunachal pradesh", "IN-AR"),
    ("maharashtra", "IN-MH"),
    ("karnataka", "IN-KA"),
    ("kerala", "IN-KL"),
    ("gujarat", "IN-GJ"),
    ("rajasthan", "IN-RJ"),
    ("telangana", "IN-TG"),
    ("jharkhand", "IN-JH"),
    ("chhattisgarh", "IN-CG"),
    ("uttarakhand", "IN-UT"),
    ("haryana", "IN-HR"),
    ("punjab", "IN-PB"),
    ("odisha", "IN-OD"),
    ("orissa", "IN-OD"),
    ("bihar", "IN-BR"),
    ("assam", "IN-AS"),
    ("delhi", "IN-DL"),
    ("goa", "IN-GA"),
    ("sikkim", "IN-SK"),
    ("tripura", "IN-TR"),
    ("meghalaya", "IN-ML"),
    ("manipur", "IN-MN"),
    ("nagaland", "IN-NL"),
    ("mizoram", "IN-MZ"),
    ("india", "IN"),
    ("indian", "IN"),
)
_FESTIVALS: tuple[tuple[str, str], ...] = (
    ("durga puja", "Durga Puja"),
    ("kali puja", "Kali Puja"),
    ("poila boishakh", "Poila Boishakh"),
    ("poila boisakh", "Poila Boishakh"),
    ("ganesh chaturthi", "Ganesh Chaturthi"),
    ("raksha bandhan", "Raksha Bandhan"),
    ("makar sankranti", "Makar Sankranti"),
    ("janmashtami", "Janmashtami"),
    ("vijayadashami", "Vijayadashami"),
    ("navratri", "Navratri"),
    ("dussehra", "Dussehra"),
    ("diwali", "Diwali"),
    ("deepavali", "Diwali"),
    ("holi", "Holi"),
    ("eid", "Eid"),
    ("onam", "Onam"),
    ("pongal", "Pongal"),
    ("bihu", "Bihu"),
    ("lohri", "Lohri"),
    ("ugadi", "Ugadi"),
    ("christmas", "Christmas"),
)
_LANGUAGES = (
    ("bengali", "Bengali"),
    ("bangla", "Bengali"),
    ("hindi", "Hindi"),
    ("tamil", "Tamil"),
    ("marathi", "Marathi"),
    ("telugu", "Telugu"),
    ("kannada", "Kannada"),
    ("malayalam", "Malayalam"),
    ("gujarati", "Gujarati"),
    ("punjabi", "Punjabi"),
    ("odia", "Odia"),
    ("urdu", "Urdu"),
)
_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("seasonal", ("monsoon", "festive season", "wedding season", "summer collection", "winter collection")),
    ("food", ("restaurant", "cafe", "bakery", "cloud kitchen", "food")),
    ("retail", ("retail", "shopping", "store footfall", "footfall")),
    ("product", ("product launch", "new product")),
    ("campaign", ("ad campaign", "marketing campaign", "consumer campaign")),
    ("local_business", ("local shop", "neighbourhood store", "neighborhood store", "high street")),
    ("consumer_interest", ("consumer demand", "shopper", "buying interest")),
    ("content_format", ("reel", "reels", "carousel", "short-form video", "infographic")),
    ("visual", ("colour palette", "color palette", "flat lay", "visual style", "photography style")),
    ("industry", ("industry report", "sector outlook")),
)


class SourceRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TrendType(str, Enum):
    FESTIVAL = "festival"
    SEASONAL = "seasonal"
    PRODUCT = "product"
    FOOD = "food"
    RETAIL = "retail"
    VISUAL = "visual"
    CONTENT_FORMAT = "content_format"
    CONSUMER_INTEREST = "consumer_interest"
    REGIONAL = "regional"
    LOCAL_BUSINESS = "local_business"
    CAMPAIGN = "campaign"
    INDUSTRY = "industry"


class Freshness(str, Enum):
    FAST = "FAST"
    CURRENT = "CURRENT"
    SEASONAL = "SEASONAL"
    EVERGREEN = "EVERGREEN"

    @classmethod
    def parse(cls, value: str) -> Freshness:
        text = value.strip().upper()
        if text == "BREAKING":
            return cls.FAST
        return cls(text)


class FreshnessPolicy:
    """TTL for each freshness class. Expired observations are not current."""

    def __init__(
        self,
        *,
        fast_hours: int = 6,
        current_days: int = 7,
        seasonal_days: int = 60,
        evergreen_days: int = 180,
    ) -> None:
        self.fast_hours = max(1, int(fast_hours))
        self.current_days = max(1, int(current_days))
        self.seasonal_days = max(1, int(seasonal_days))
        self.evergreen_days = max(1, int(evergreen_days))

    @classmethod
    def from_settings(cls, settings: object) -> FreshnessPolicy:
        return cls(
            fast_hours=getattr(settings, "trend_fast_hours", 6),
            current_days=getattr(settings, "trend_current_days", 7),
            seasonal_days=getattr(settings, "trend_seasonal_days", 60),
            evergreen_days=getattr(settings, "trend_evergreen_days", 180),
        )

    def ttl(self, freshness: Freshness) -> timedelta:
        if freshness is Freshness.FAST:
            return timedelta(hours=self.fast_hours)
        if freshness is Freshness.CURRENT:
            return timedelta(days=self.current_days)
        if freshness is Freshness.SEASONAL:
            return timedelta(days=self.seasonal_days)
        return timedelta(days=self.evergreen_days)

    def valid_until(self, freshness: Freshness, anchor: datetime) -> datetime:
        return _as_utc(anchor) + self.ttl(freshness)

    def is_current(self, freshness: Freshness, anchor: datetime, now: datetime) -> bool:
        return _as_utc(now) < self.valid_until(freshness, anchor)


class TrendSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str
    source_name: str
    published_at: datetime | None = None
    observed_at: datetime
    title: str

    @field_validator("source_url", "source_name", "title")
    @classmethod
    def _text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("source fields must not be blank")
        return text


class TrendObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str
    source_name: str
    published_at: datetime | None = None
    observed_at: datetime
    title: str
    summary: str
    industry: str | None = None
    region: str | None = None
    keywords: list[str] = Field(default_factory=list)
    evidence: str
    confidence: float
    trend_type: TrendType
    freshness: Freshness
    valid_until: datetime
    festival: str | None = None
    content_language: str | None = None
    primary_source: TrendSource
    supporting_sources: list[TrendSource] = Field(default_factory=list)

    @field_validator("source_url", "source_name", "title", "summary", "evidence")
    @classmethod
    def _text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("observation fields must not be blank")
        return text

    def is_current(self, now: datetime) -> bool:
        return _as_utc(now) < _as_utc(self.valid_until)


@dataclass
class SourceDocument:
    url: str
    source_name: str
    title: str
    summary: str
    body: str
    published_at: datetime | None


@dataclass
class FetchOutcome:
    url: str
    error: str | None = None
    document: SourceDocument | None = None


@dataclass
class SourceFailure:
    url: str
    code: str


@dataclass
class ResearchResult:
    observations: list[TrendObservation]
    failures: list[SourceFailure]


class SourceFetcher(Protocol):
    def fetch(self, url: str) -> Awaitable[FetchOutcome]: ...


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _mentions(phrase: str, text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _blocked(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in BLOCKED_SUFFIXES)


def _permitted(host: str) -> bool:
    if not host or _blocked(host):
        return False
    return any(host == item or host.endswith("." + item) for item in PERMITTED_HOSTS)


def source_name_for(url: str) -> str:
    host = _host(url)
    for suffix, name in sorted(SOURCE_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
        if host == suffix or host.endswith("." + suffix):
            return name
    return host or "unknown"


def validate_public_url(url: str) -> str:
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SourceRejected("invalid_url")
    if parsed.username or parsed.password:
        raise SourceRejected("not_permitted")
    query = parsed.query.casefold()
    if any(key in query for key in _SECRET_QUERY):
        raise SourceRejected("not_permitted")
    host = parsed.hostname.lower()
    if _blocked(host) or not _permitted(host):
        raise SourceRejected("not_permitted")
    return text


def robots_allows(body: str, path: str, user_agent: str = USER_AGENT) -> bool:
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal agents, rules
        if agents:
            groups.append((agents, rules))
        agents, rules = [], []

    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules:
                flush()
            agents.append(value.lower())
        elif key in {"allow", "disallow"}:
            rules.append((key, value))
    flush()

    selected: list[tuple[str, str]] | None = None
    agent = user_agent.lower()
    for names, group_rules in groups:
        if any(name != "*" and (agent.startswith(name) or name in agent) for name in names):
            selected = group_rules
            break
    if selected is None:
        for names, group_rules in groups:
            if "*" in names:
                selected = group_rules
                break
    if not selected:
        return True
    target = path or "/"
    best_len = -1
    allowed = True
    for kind, prefix in selected:
        if prefix == "":
            continue
        if target.startswith(prefix) and len(prefix) >= best_len:
            if len(prefix) > best_len or kind == "allow":
                best_len = len(prefix)
                allowed = kind == "allow"
    return True if best_len < 0 else allowed


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>|<style.*?>.*?</style>", " ", html)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _meta(html: str, *, prop: str | None = None, name: str | None = None) -> str:
    if prop:
        match = re.search(
            rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\'](.*?)["\']',
            html,
            flags=re.I | re.S,
        ) or re.search(
            rf'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']{re.escape(prop)}["\']',
            html,
            flags=re.I | re.S,
        )
    else:
        match = re.search(
            rf'<meta[^>]+name=["\']{re.escape(name or "")}["\'][^>]+content=["\'](.*?)["\']',
            html,
            flags=re.I | re.S,
        ) or re.search(
            rf'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']{re.escape(name or "")}["\']',
            html,
            flags=re.I | re.S,
        )
    return unescape(match.group(1)).strip() if match else ""


def parse_document(url: str, html: str) -> SourceDocument:
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    title = unescape(title_match.group(1)).strip() if title_match else ""
    title = re.sub(r"\s+", " ", title)
    summary = _meta(html, name="description") or _strip_html(html)[:320]
    published_raw = _meta(html, prop="article:published_time")
    published_at = None
    if published_raw:
        try:
            published_at = _as_utc(datetime.fromisoformat(published_raw.replace("Z", "+00:00")))
        except ValueError:
            published_at = None
    return SourceDocument(
        url=url,
        source_name=source_name_for(url),
        title=title[:160],
        summary=summary[:500],
        body=_strip_html(html)[:4000],
        published_at=published_at,
    )


class HttpSourceFetcher:
    """Fetches allowlisted public pages. robots.txt failures are closed, except a 404."""

    def __init__(self, *, client: httpx.AsyncClient | None = None, timeout: float = 5.0) -> None:
        self._client = client
        self._timeout = timeout

    async def fetch(self, url: str) -> FetchOutcome:
        try:
            clean = validate_public_url(url)
        except SourceRejected as exc:
            return FetchOutcome(url=url, error=exc.code)
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        try:
            return await self._fetch(client, clean)
        finally:
            if owns:
                await client.aclose()

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> FetchOutcome:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            robots = await client.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=self._timeout)
        except httpx.TimeoutException:
            return FetchOutcome(url=url, error="timeout")
        except httpx.HTTPError:
            return FetchOutcome(url=url, error="unavailable")
        if robots.status_code == 404:
            allowed = True
        elif robots.status_code == 200:
            allowed = robots_allows(robots.text, parsed.path or "/")
        else:
            return FetchOutcome(url=url, error="unavailable")
        if not allowed:
            return FetchOutcome(url=url, error="robots_disallow")
        try:
            page = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=self._timeout)
        except httpx.TimeoutException:
            return FetchOutcome(url=url, error="timeout")
        except httpx.HTTPError:
            return FetchOutcome(url=url, error="unavailable")
        if page.status_code != 200:
            return FetchOutcome(url=url, error="unavailable")
        document = parse_document(url, page.text)
        if not document.title or not (document.summary or document.body):
            return FetchOutcome(url=url, error="no_evidence")
        return FetchOutcome(url=url, document=document)


def _region(text: str) -> str | None:
    matches = [code for phrase, code in _PLACES if _mentions(phrase, text)]
    if not matches:
        return None
    return max(matches, key=lambda code: (code.count("-"), len(code)))


def region_code(text: str) -> str | None:
    """Map a place name to the gazetteer code. Unknown places stay uncoded."""
    return _region(text or "")


def _language(text: str) -> str | None:
    for phrase, name in _LANGUAGES:
        if _mentions(phrase, text):
            return name
    return None


def _festival(text: str) -> str | None:
    for phrase, name in _FESTIVALS:
        if _mentions(phrase, text):
            return name
    return None


def _industry(text: str) -> str | None:
    food = _mentions("food", text) or any(
        _mentions(phrase, text) for phrase in ("restaurant", "cafe", "bakery", "cloud kitchen")
    )
    retail = any(_mentions(phrase, text) for phrase in ("retail", "shopping", "footfall"))
    if food and retail:
        return None
    if food:
        return "food"
    if retail:
        return "retail"
    return None


def _trend_type(text: str, festival: str | None, region: str | None) -> TrendType | None:
    if festival:
        return TrendType.FESTIVAL
    for name, phrases in _TYPE_RULES:
        if any(_mentions(phrase, text) for phrase in phrases):
            return TrendType(name)
    if region:
        return TrendType.REGIONAL
    return None


def _freshness_for(trend_type: TrendType, text: str) -> Freshness:
    if "breaking" in text or "just in" in text:
        return Freshness.FAST
    if trend_type in {TrendType.FESTIVAL, TrendType.SEASONAL}:
        return Freshness.SEASONAL
    if trend_type in {TrendType.CONTENT_FORMAT, TrendType.VISUAL} and any(
        phrase in text for phrase in ("guide", "how to", "evergreen")
    ):
        return Freshness.EVERGREEN
    return Freshness.CURRENT


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def _norm_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.casefold()))


def _claim_blob(observation: TrendObservation) -> str:
    return f"{observation.title} {observation.summary}"


def _conflicts(left: TrendObservation, right: TrendObservation) -> bool:
    left_numbers = _numbers(_claim_blob(left))
    right_numbers = _numbers(_claim_blob(right))
    return bool(left_numbers and right_numbers and left_numbers != right_numbers)


def _same_claim(left: TrendObservation, right: TrendObservation) -> bool:
    if _conflicts(left, right):
        return False
    if _norm_title(left.title) == _norm_title(right.title):
        return True
    left_tokens = _tokens(left.title)
    right_tokens = _tokens(right.title)
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.8


def _flatten_sources(observation: TrendObservation) -> list[TrendSource]:
    found: list[TrendSource] = []
    seen: set[str] = set()
    for source in (observation.primary_source, *observation.supporting_sources):
        key = source.source_url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        found.append(source)
    return found


def _merge(cluster: list[TrendObservation]) -> TrendObservation:
    sources = []
    seen: set[str] = set()
    for observation in cluster:
        for source in _flatten_sources(observation):
            key = source.source_url.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            sources.append(source)
    sources.sort(key=lambda source: (_as_utc(source.published_at or source.observed_at), source.source_url))
    primary = sources[0]
    base = next(
        observation
        for observation in cluster
        if observation.source_url.rstrip("/") == primary.source_url.rstrip("/")
    )
    supporting = [source for source in sources if source.source_url.rstrip("/") != primary.source_url.rstrip("/")]
    confidence = min(0.70, round(0.55 + 0.03 * len(supporting), 2))
    return base.model_copy(
        update={
            "source_url": primary.source_url,
            "source_name": primary.source_name,
            "published_at": primary.published_at,
            "observed_at": primary.observed_at,
            "title": primary.title,
            "primary_source": primary,
            "supporting_sources": supporting,
            "confidence": confidence,
        }
    )


def dedupe(observations: list[TrendObservation]) -> list[TrendObservation]:
    clusters: list[list[TrendObservation]] = []
    for observation in observations:
        placed = False
        for cluster in clusters:
            if any(_conflicts(item, observation) for item in cluster):
                continue
            if any(_same_claim(item, observation) for item in cluster):
                cluster.append(observation)
                placed = True
                break
        if not placed:
            clusters.append([observation])
    return [_merge(cluster) for cluster in clusters]


def claim_key(observation: TrendObservation) -> str:
    numbers = ",".join(sorted(_numbers(_claim_blob(observation))))
    digest = hashlib.sha256(f"{_norm_title(observation.title)}|{numbers}".encode()).hexdigest()
    return digest[:32]


def normalize(
    document: SourceDocument,
    *,
    observed_at: datetime,
    policy: FreshnessPolicy,
) -> TrendObservation | None:
    text = f"{document.title} {document.summary} {document.body}".casefold()
    festival = _festival(text)
    region = _region(text)
    trend_type = _trend_type(text, festival, region)
    quote = (document.summary or document.body).strip()
    if trend_type is None or not document.title.strip() or not quote:
        return None
    freshness = _freshness_for(trend_type, text)
    anchor = _as_utc(document.published_at or observed_at)
    language = _language(text)
    industry = _industry(text)
    keywords = [trend_type.value]
    if festival:
        keywords.append(festival)
    if region:
        keywords.append(region)
    if industry:
        keywords.append(industry)
    if language:
        keywords.append(f"{_LANGUAGE_PREFIX}{language}")
    keywords.append(f"{_FRESHNESS_PREFIX}{freshness.value}")
    source = TrendSource(
        source_url=document.url,
        source_name=document.source_name,
        published_at=document.published_at,
        observed_at=observed_at,
        title=document.title.strip()[:160],
    )
    return TrendObservation(
        source_url=source.source_url,
        source_name=source.source_name,
        published_at=source.published_at,
        observed_at=observed_at,
        title=source.title,
        summary=quote[:320],
        industry=industry,
        region=region,
        keywords=list(dict.fromkeys(keywords)),
        evidence=quote[:500],
        confidence=0.55,
        trend_type=trend_type,
        freshness=freshness,
        valid_until=policy.valid_until(freshness, anchor),
        festival=festival,
        content_language=language,
        primary_source=source,
    )


class TrendResearcher:
    """Collects evidence from explicit public URLs. It has no business-location input."""

    def __init__(
        self,
        fetcher: SourceFetcher,
        *,
        policy: FreshnessPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._policy = policy or FreshnessPolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def collect(
        self,
        urls: list[str],
        *,
        user_id: str | None = None,
        session: object | None = None,
        business_id: str | None = None,
    ) -> ResearchResult:
        observed_at = _as_utc(self._clock())
        raw: list[TrendObservation] = []
        failures: list[SourceFailure] = []
        seen: set[str] = set()
        for url in urls:
            try:
                clean = validate_public_url(url)
            except SourceRejected as exc:
                failures.append(SourceFailure(url=url, code=exc.code))
                continue
            if clean in seen:
                continue
            seen.add(clean)
            outcome = await self._fetcher.fetch(clean)
            if outcome.error or outcome.document is None:
                failures.append(SourceFailure(url=clean, code=outcome.error or "unavailable"))
                continue
            observation = normalize(outcome.document, observed_at=observed_at, policy=self._policy)
            if observation is None:
                failures.append(SourceFailure(url=clean, code="no_evidence"))
                continue
            raw.append(observation)
        observations = dedupe(raw)
        if session is not None and user_id:
            save_observations(
                session,
                user_id,
                observations,
                business_id=business_id,
                now=observed_at,
            )
        return ResearchResult(observations=observations, failures=failures)


def save_observations(
    session: object,
    user_id: str,
    observations: list[TrendObservation],
    *,
    business_id: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Insert one row per claim. Repeat articles become supporting evidence, not new signals."""

    from db.trend_repositories import TrendEvidenceRepository, TrendObservationRepository, TrendSourceRepository

    moment = _as_utc(now or datetime.now(timezone.utc))
    sources = TrendSourceRepository(session)
    observations_repo = TrendObservationRepository(session)
    evidence = TrendEvidenceRepository(session)
    saved: list[str] = []
    for observation in observations:
        key = claim_key(observation)
        existing = observations_repo.find_by_external_id(user_id, key)
        if existing is not None:
            _attach_support(sources, evidence, user_id, existing, observation, business_id)
            saved.append(existing.id)
            continue
        source = _upsert_source(sources, user_id, observation.primary_source, observation, business_id)
        row = observations_repo.record(
            user_id,
            source_id=source.id,
            title=observation.title[:160],
            description=observation.summary,
            trend_type=observation.trend_type.value,
            industry=observation.industry or "unspecified",
            region=observation.region or "",
            observed_at=observation.observed_at,
            evidence=observation.evidence,
            keywords=observation.keywords,
            published_at=observation.published_at,
            valid_until=observation.valid_until,
            confidence=observation.confidence,
            external_id=key,
            status="ACTIVE" if observation.is_current(moment) else "EXPIRED",
        )
        row.festival = observation.festival
        row.content_type = observation.trend_type.value.upper()
        evidence.create(
            user_id,
            row.id,
            source_type="primary",
            source_record_id=observation.source_url[:128],
            excerpt=observation.evidence[:500],
            observed_at=observation.observed_at,
            valid_until=observation.valid_until,
        )
        for support in observation.supporting_sources:
            _upsert_source(sources, user_id, support, observation, business_id)
            evidence.create(
                user_id,
                row.id,
                source_type="supporting",
                source_record_id=support.source_url[:128],
                excerpt=support.title[:500],
                observed_at=support.observed_at,
                valid_until=observation.valid_until,
            )
        saved.append(row.id)
    return saved


def _upsert_source(repository: object, user_id: str, source: TrendSource, observation: TrendObservation, business_id: str | None) -> object:
    found = repository.find_by_url(user_id, source.source_url)
    if found is not None:
        return found
    return repository.create(
        user_id,
        business_id=business_id,
        name=source.source_name[:160],
        source_type="news",
        url=source.source_url[:1024],
        industry=observation.industry,
        region=observation.region,
        enabled=True,
    )


def _attach_support(sources: object, evidence_repo: object, user_id: str, row: object, observation: TrendObservation, business_id: str | None) -> None:
    existing = {item.source_record_id for item in evidence_repo.list_for_observation(user_id, row.id)}
    for support in _flatten_sources(observation):
        record_id = support.source_url[:128]
        if record_id in existing or support.source_url.rstrip("/") == (row_source_url(row) or "").rstrip("/"):
            continue
        _upsert_source(sources, user_id, support, observation, business_id)
        evidence_repo.create(
            user_id,
            row.id,
            source_type="supporting",
            source_record_id=record_id,
            excerpt=support.title[:500],
            observed_at=support.observed_at,
            valid_until=observation.valid_until,
        )
        existing.add(record_id)


def row_source_url(row: object) -> str | None:
    source = getattr(row, "source_row", None)
    if source is not None and getattr(source, "url", None):
        return source.url
    return None


def freshness_of(keywords: list[str] | None) -> str | None:
    for item in keywords or []:
        if str(item).startswith(_FRESHNESS_PREFIX):
            return str(item).split(":", 1)[1]
    return None


def language_of(keywords: list[str] | None) -> str | None:
    for item in keywords or []:
        if str(item).startswith(_LANGUAGE_PREFIX):
            return str(item).split(":", 1)[1]
    return None


def public_keywords(keywords: list[str] | None) -> list[str]:
    visible = []
    for item in keywords or []:
        text = str(item)
        if text.startswith((_FRESHNESS_PREFIX, "kind:", "display_product:", "role:")):
            continue
        visible.append(text)
    return visible


async def fetch_observations(payload: dict | None = None) -> list[dict]:
    """Scheduler hook. Reads this user's saved news URLs and does not browse otherwise."""

    body = payload or {}
    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        return []
    try:
        from db.session import get_session_factory
        from db.trend_repositories import TrendSourceRepository

        factory = get_session_factory()
    except Exception:
        return []
    session = factory()
    try:
        rows = TrendSourceRepository(session).list_for_user(user_id)
        urls = [row.url for row in rows if row.enabled and row.url and row.source_type == "news"]
        if not urls:
            return []
        from config import get_settings

        result = await TrendResearcher(
            HttpSourceFetcher(),
            policy=FreshnessPolicy.from_settings(get_settings()),
        ).collect(urls, user_id=user_id, session=session, business_id=body.get("business_id") or None)
        session.commit()
        now = datetime.now(timezone.utc)
        exported = []
        for observation in result.observations:
            if not observation.is_current(now):
                continue
            exported.append(
                {
                    "id": claim_key(observation),
                    "title": observation.title,
                    "description": observation.summary,
                    "trend_type": observation.trend_type.value,
                    "observed_at": observation.observed_at.isoformat(),
                    "valid_until": observation.valid_until.isoformat(),
                    "source": {"name": observation.source_name, "url": observation.source_url},
                    "evidence": observation.evidence,
                }
            )
        return exported
    except Exception:
        session.rollback()
        return []
    finally:
        session.close()
