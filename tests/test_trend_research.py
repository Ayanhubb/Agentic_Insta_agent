"""External trend research: freshness, dedupe, regions, and source failures."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from backend.mcp.client import MCPClient
from backend.mcp.servers import build_registry
from backend.mcp.sources import RepositoryGateway
from backend.mcp.tenant_isolation import trusted_tenant
from backend.trends.research import (
    Freshness,
    HttpSourceFetcher,
    SourceDocument,
    TrendObservation,
    TrendResearcher,
    TrendSource,
    TrendType,
    FetchOutcome,
)
from db.crypto import TokenEncryptor
from db.schemas import UserCreate
from db.session import create_engine_from_url, init_db
from db.uow import Database
from services.clock import FrozenClock

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class MapFetcher:
    def __init__(self, documents: dict[str, SourceDocument | str]) -> None:
        self.documents = documents
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchOutcome:
        self.calls.append(url)
        item = self.documents[url]
        if isinstance(item, str):
            return FetchOutcome(url=url, error=item)
        return FetchOutcome(url=url, document=item)


def _doc(
    url: str,
    title: str,
    summary: str,
    *,
    published_at: datetime,
    body: str | None = None,
) -> SourceDocument:
    return SourceDocument(
        url=url,
        source_name="The Hindu",
        title=title,
        summary=summary,
        body=body or summary,
        published_at=published_at,
    )


def _researcher(documents: dict[str, SourceDocument | str]) -> tuple[TrendResearcher, MapFetcher]:
    fetcher = MapFetcher(documents)
    return TrendResearcher(fetcher, clock=lambda: NOW), fetcher


@pytest.mark.asyncio
async def test_fresh_source_is_current() -> None:
    url = "https://www.thehindu.com/retail-footfall"
    researcher, _fetcher = _researcher(
        {
            url: _doc(
                url,
                "Retail footfall rises in India",
                "Indian retail footfall improved this week.",
                published_at=NOW - timedelta(hours=2),
            )
        }
    )
    result = await researcher.collect([url])
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.trend_type is TrendType.RETAIL
    assert observation.freshness is Freshness.CURRENT
    assert observation.is_current(NOW) is True
    assert observation.region == "IN"
    assert observation.source_url == url
    assert observation.evidence
    assert observation.primary_source.source_name == "The Hindu"
    assert observation.confidence == 0.55


@pytest.mark.asyncio
async def test_stale_source_is_not_current() -> None:
    url = "https://www.thehindu.com/old-retail"
    researcher, _fetcher = _researcher(
        {
            url: _doc(
                url,
                "Retail footfall rises in India",
                "Indian retail footfall improved this week.",
                published_at=NOW - timedelta(days=30),
            )
        }
    )
    result = await researcher.collect([url])
    observation = result.observations[0]
    assert observation.is_current(NOW) is False
    assert observation.valid_until < NOW


@pytest.mark.asyncio
async def test_duplicate_articles_are_one_signal() -> None:
    hosts = [
        "https://www.thehindu.com/same",
        "https://indianexpress.com/same",
        "https://timesofindia.indiatimes.com/same",
        "https://economictimes.indiatimes.com/same",
        "https://www.livemint.com/same",
        "https://www.business-standard.com/same",
        "https://www.hindustantimes.com/same",
        "https://www.ndtv.com/same",
        "https://pib.gov.in/same",
        "https://www.india.gov.in/same",
    ]
    title = "Retail sales rise 4 percent in India"
    summary = "Indian retail sales rise 4 percent in India this quarter."
    documents = {
        url: _doc(url, title, summary, published_at=NOW - timedelta(hours=index + 1))
        for index, url in enumerate(hosts)
    }
    documents[hosts[-1]] = _doc(hosts[-1], title, summary, published_at=NOW - timedelta(days=2))
    researcher, fetcher = _researcher(documents)
    result = await researcher.collect(hosts)
    assert fetcher.calls == hosts
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.source_url == hosts[-1]
    assert len(observation.supporting_sources) == 9
    assert observation.confidence == 0.70
    assert observation.primary_source.source_url == hosts[-1]


@pytest.mark.asyncio
async def test_conflicting_sources_stay_separate() -> None:
    first = "https://www.thehindu.com/rise-4"
    second = "https://indianexpress.com/rise-9"
    researcher, _fetcher = _researcher(
        {
            first: _doc(
                first,
                "Retail sales rise 4 percent in India this quarter",
                "Indian retail sales rise 4 percent in India this quarter.",
                published_at=NOW - timedelta(hours=3),
            ),
            second: _doc(
                second,
                "Retail sales rise 9 percent in India this quarter",
                "Indian retail sales rise 9 percent in India this quarter.",
                published_at=NOW - timedelta(hours=1),
            ),
        }
    )
    result = await researcher.collect([first, second])
    assert len(result.observations) == 2
    assert {item.confidence for item in result.observations} == {0.55}


@pytest.mark.asyncio
async def test_regional_source_uses_only_stated_place() -> None:
    kolkata = "https://www.thehindu.com/kolkata-footfall"
    national = "https://indianexpress.com/national-footfall"
    researcher, _fetcher = _researcher(
        {
            kolkata: _doc(
                kolkata,
                "Kolkata weekend footfall",
                "Kolkata retailers report stronger weekend footfall.",
                published_at=NOW - timedelta(hours=1),
            ),
            national: _doc(
                national,
                "Retail footfall improved",
                "Retail footfall improved this week.",
                published_at=NOW - timedelta(hours=1),
            ),
        }
    )
    result = await researcher.collect([kolkata, national])
    by_title = {item.title: item for item in result.observations}
    assert by_title["Kolkata weekend footfall"].region == "IN-WB-KOLKATA"
    assert by_title["Retail footfall improved"].region is None
    assert "location" not in inspect.signature(TrendResearcher.collect).parameters


@pytest.mark.asyncio
async def test_festival_source_does_not_infer_west_bengal() -> None:
    url = "https://www.thehindu.com/durga"
    researcher, _fetcher = _researcher(
        {
            url: _doc(
                url,
                "Durga Puja shopping starts",
                "Durga Puja shopping starts in markets.",
                published_at=NOW - timedelta(days=1),
            )
        }
    )
    result = await researcher.collect([url])
    observation = result.observations[0]
    assert observation.trend_type is TrendType.FESTIVAL
    assert observation.festival == "Durga Puja"
    assert observation.freshness is Freshness.SEASONAL
    assert observation.region is None
    assert observation.is_current(NOW) is True


@pytest.mark.asyncio
async def test_language_is_not_a_region() -> None:
    url = "https://www.thehindu.com/bengali-reels"
    researcher, _fetcher = _researcher(
        {
            url: _doc(
                url,
                "Bengali reel guide",
                "A Bengali reel guide for short-form video captions.",
                published_at=NOW - timedelta(days=10),
            )
        }
    )
    result = await researcher.collect([url])
    observation = result.observations[0]
    assert observation.content_language == "Bengali"
    assert observation.region is None
    assert observation.trend_type is TrendType.CONTENT_FORMAT
    assert observation.freshness is Freshness.EVERGREEN


def test_no_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TrendObservation(
            source_url="",
            source_name="The Hindu",
            observed_at=NOW,
            title="Retail footfall",
            summary="Indian retail footfall improved.",
            evidence="",
            confidence=0.55,
            trend_type=TrendType.RETAIL,
            freshness=Freshness.CURRENT,
            valid_until=NOW,
            primary_source=TrendSource(
                source_url="https://www.thehindu.com/x",
                source_name="The Hindu",
                observed_at=NOW,
                title="Retail footfall",
            ),
        )


@pytest.mark.asyncio
async def test_missing_evidence_creates_no_observation() -> None:
    url = "https://www.thehindu.com/weather"
    researcher, _fetcher = _researcher(
        {url: _doc(url, "Clear skies", "The afternoon was sunny.", published_at=NOW)}
    )
    result = await researcher.collect([url])
    assert result.observations == []
    assert result.failures[0].code == "no_evidence"
    empty = await researcher.collect([])
    assert empty.observations == []


@pytest.mark.asyncio
async def test_network_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    del monkeypatch

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.TimeoutException("timed out")

    fetcher = HttpSourceFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    outcome = await fetcher.fetch("https://www.thehindu.com/slow")
    assert outcome.error == "timeout"
    assert outcome.document is None


@pytest.mark.asyncio
async def test_invalid_url_and_instagram_are_not_fetched() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise AssertionError("blocked URL was fetched")

    fetcher = HttpSourceFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    instagram = await fetcher.fetch("https://www.instagram.com/p/abc")
    invalid = await fetcher.fetch("notaurl")
    credential = await fetcher.fetch("https://user:pass@www.thehindu.com/story")
    assert instagram.error == "not_permitted"
    assert invalid.error == "invalid_url"
    assert credential.error == "not_permitted"
    assert calls == []


@pytest.mark.asyncio
async def test_source_unavailable_is_not_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403, text="Private Kolkata retail boom secret")

    fetcher = HttpSourceFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    outcome = await fetcher.fetch("https://www.thehindu.com/private")
    assert outcome.error == "unavailable"
    assert outcome.document is None
    assert "secret" not in str(outcome)


@pytest.mark.asyncio
async def test_robots_disallow_does_not_fetch_the_page() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /news\n")
        raise AssertionError("disallowed page was fetched")

    fetcher = HttpSourceFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    outcome = await fetcher.fetch("https://www.thehindu.com/news/story")
    assert outcome.error == "robots_disallow"
    assert seen == ["/robots.txt"]


def test_research_module_does_not_publish_or_call_deepseek() -> None:
    source = (ROOT / "backend" / "trends" / "research.py").read_text(encoding="utf-8").casefold()
    assert "from ai.llm.deepseek" not in source
    assert "deepseek_api" not in source
    assert "media_publish" not in source
    assert "publish_instagram_media" not in source
    assert "publicationgateway" not in source


@pytest.mark.asyncio
async def test_mcp_returns_current_observations_and_hides_stale(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'trends.db').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    database = Database(session, encryptor=TokenEncryptor("test-token-encryption-key"))
    user = database.users.create(UserCreate(email="trends@example.com", password_hash="x"))
    database.commit()
    fresh = "https://www.thehindu.com/fresh-retail"
    stale = "https://indianexpress.com/stale-retail"
    kolkata = "https://www.livemint.com/kolkata-retail"
    researcher, _fetcher = _researcher(
        {
            fresh: _doc(
                fresh,
                "Retail footfall rises in India",
                "Indian retail footfall improved this week.",
                published_at=NOW - timedelta(hours=2),
            ),
            stale: _doc(
                stale,
                "Old retail footfall note",
                "Indian retail footfall improved last season.",
                published_at=NOW - timedelta(days=40),
            ),
            kolkata: _doc(
                kolkata,
                "Kolkata weekend footfall",
                "Kolkata retailers report stronger weekend footfall.",
                published_at=NOW - timedelta(hours=3),
            ),
        }
    )
    await researcher.collect([fresh, stale, kolkata], user_id=user.id, session=session)
    database.commit()
    client = MCPClient(build_registry(RepositoryGateway(factory, clock=FrozenClock(NOW))))
    tenant = trusted_tenant(user.id)
    current = await client.invoke("get_current_trends", {}, tenant)
    titles = {item["title"] for item in current.data["observations"]}
    assert "Retail footfall rises in India" in titles
    assert "Kolkata weekend footfall" in titles
    assert "Old retail footfall note" not in titles
    assert current.data["stale_excluded"] >= 1
    assert all(item["is_current"] is True for item in current.data["observations"])
    fresh_row = next(item for item in current.data["observations"] if item["title"].startswith("Retail footfall"))
    assert fresh_row["source_url"] == fresh
    assert fresh_row["source_name"] == "The Hindu"
    assert fresh_row["observed_at"]
    assert "evidence" not in fresh_row
    assert fresh_row["primary_source"]["source_url"] == fresh
    assert fresh_row["primary_source"]["observed_at"] == fresh_row["observed_at"]
    regional = await client.invoke("get_regional_trends", {"region": "Kolkata"}, tenant)
    assert [item["title"] for item in regional.data["observations"]] == ["Kolkata weekend footfall"]
    evidence = await client.invoke("get_trend_evidence", {"observation_id": fresh_row["id"]}, tenant)
    assert evidence.data["found"] is True
    assert "footfall" in evidence.data["observation"]["evidence"]
    schema = next(item for item in client.discover() if item["name"] == "get_current_trends")
    assert "url" not in schema["input_schema"]["properties"]
    session.close()
