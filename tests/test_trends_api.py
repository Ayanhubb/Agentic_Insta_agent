"""Trend intelligence API reads the same SQL rows research writes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api.app import create_app
from backend.trends.research import FetchOutcome, SourceDocument, TrendResearcher
from config import Settings
from db.models import TrendObservation, User
from db.trend_repositories import (
    AccountSnapshotRepository,
    InsightSnapshotRepository,
    MediaSnapshotRepository,
)
from services.trend_store import TrendStore

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _user(user_id: str):
    async def provider(_request):
        return SimpleNamespace(id=user_id, must_change_password=False, is_active=True)

    return provider


def _ensure_user(session, user_id: str, email: str) -> None:
    if session.get(User, user_id) is None:
        session.add(
            User(
                id=user_id,
                email=email,
                password_hash="x",
                is_active=True,
                is_admin=False,
                must_change_password=False,
            )
        )
        session.flush()


def _seed(session) -> None:
    _ensure_user(session, "user-a", "user-a@example.com")
    _ensure_user(session, "user-b", "user-b@example.com")
    store = TrendStore(session)
    store.add_trend(
        "user-a",
        {
            "title": "Silk House window",
            "source": "Shop log",
            "observed_at": "2026-09-20T10:00:00+00:00",
            "freshness": "fresh",
            "industry": "apparel",
            "region": "Kolkata",
            "evidence": [{"kind": "observed", "text": "Window traffic rose.", "source": "Shop log"}],
            "confidence": "high",
            "expires_at": "2026-12-01T00:00:00+00:00",
            "deepseek_api_key": "sk-deepseek-should-not-leak",
            "openai_api_key": "sk-openai-should-not-leak",
            "meta_access_token": "meta-should-not-leak",
            "canva_token": "canva-should-not-leak",
        },
    )
    store.add_trend(
        "user-a",
        {
            "id": "trend-old",
            "title": "Old monsoon palette",
            "source": "Archive",
            "observed_at": "2020-01-01T00:00:00+00:00",
            "freshness": "fresh",
            "industry": "apparel",
            "region": "Kolkata",
            "evidence": [],
            "confidence": "low",
            "expires_at": "2020-02-01T00:00:00+00:00",
        },
    )
    store.add_opportunity(
        "user-a",
        {
            "id": "opp-a",
            "title": "Diwali window",
            "why_now": "Festival week is starting.",
            "evidence": [{"kind": "discovered", "text": "Search interest rose.", "source": "Notes"}],
            "product": "Silk saree",
            "festival": "Diwali",
            "recommended_format": "Reel",
            "creative_direction": "Warm light on silk.",
            "expires_at": "2026-12-01T00:00:00+00:00",
            "confidence": "medium",
            "industry": "apparel",
            "region": "Kolkata",
        },
    )
    store.add_opportunity(
        "user-a",
        {
            "id": "opp-old",
            "title": "Old window",
            "why_now": "The date passed.",
            "expires_at": "2020-02-01T00:00:00+00:00",
            "industry": "apparel",
            "region": "Kolkata",
            "creative_direction": "Do not revive this.",
        },
    )
    session.commit()


class _MapFetcher:
    def __init__(self, documents: dict[str, SourceDocument]) -> None:
        self.documents = documents

    async def fetch(self, url: str) -> FetchOutcome:
        return FetchOutcome(url=url, document=self.documents[url])


def _doc(url: str, title: str, summary: str, published_at: datetime) -> SourceDocument:
    return SourceDocument(
        url=url,
        source_name="The Hindu",
        title=title,
        summary=summary,
        body=summary,
        published_at=published_at,
    )


def test_trend_routes_require_auth_and_hide_secrets(tmp_settings: Settings) -> None:
    anonymous = create_app(tmp_settings)
    with TestClient(anonymous) as client:
        denied = client.get("/api/v1/trends/research")
        assert denied.status_code == 401

    app = create_app(tmp_settings, current_user_provider=_user("user-a"))
    with app.state.session_factory() as session:
        _seed(session)
    with TestClient(app) as client:
        research = client.get("/api/v1/trends/research")
        assert research.status_code == 200
        body = research.text
        assert "Silk House window" in body
        assert "sk-deepseek-should-not-leak" not in body
        assert "sk-openai-should-not-leak" not in body
        assert "meta-should-not-leak" not in body
        assert "canva-should-not-leak" not in body
        titles = {item["title"]: item for item in research.json()["trends"]}
        assert "Diwali window" not in titles
        assert titles["Old monsoon palette"]["expired"] is True
        assert titles["Silk House window"]["expired"] is False
        assert titles["Silk House window"]["kind"] == "discovered"
        assert titles["Silk House window"]["source"] == "Shop log"
        assert titles["Silk House window"]["confidence"] == "high"
        assert titles["Silk House window"]["evidence"][0]["text"] == "Window traffic rose."
        assert titles["Silk House window"]["evidence"][0]["source"] == "Shop log"
        assert titles["Silk House window"]["evidence"][0]["observed_at"] == "2026-09-20T10:00:00+00:00"
        account = client.get("/api/v1/trends/account")
        assert account.status_code == 200
        engagement = account.json()["account"]["engagement"]
        assert engagement["available"] is False
        assert engagement["metrics"] == {}
        handoff = client.post("/api/v1/trends/opportunities/opp-a/create-content")
        assert handoff.status_code == 200
        assert handoff.json()["path"] == "/generate"
        assert handoff.json()["product"] == "Silk saree"
        assert handoff.json()["festival"] == "Diwali"
        assert "meta" not in handoff.json()
        expired = client.post("/api/v1/trends/opportunities/opp-old/create-content")
        assert expired.status_code == 404
        saved = client.post("/api/v1/trends/opportunities/opp-a/save")
        assert saved.status_code == 200
        assert saved.json()["opportunity"]["status"] == "saved"
        report = client.get("/api/v1/trends/reports")
        queued = [item["id"] for item in report.json()["report"]["content_queue"]]
        assert "opp-a" in queued
        dismissed = client.post("/api/v1/trends/opportunities/opp-a/dismiss")
        assert dismissed.json()["opportunity"]["status"] == "dismissed"
        listed = client.get("/api/v1/trends/opportunities")
        assert all(item["id"] != "opp-a" for item in listed.json()["opportunities"])


def test_trend_routes_are_tenant_isolated(tmp_settings: Settings) -> None:
    owner = create_app(tmp_settings, current_user_provider=_user("user-a"))
    other = create_app(tmp_settings, current_user_provider=_user("user-b"))
    with owner.state.session_factory() as session:
        _seed(session)
    with TestClient(other) as client:
        research = client.get("/api/v1/trends/research")
        assert research.status_code == 200
        assert research.json()["trends"] == []
        denied = client.post("/api/v1/trends/opportunities/opp-a/dismiss")
        assert denied.status_code == 404
        evidence = client.get("/api/v1/trends/opportunities/opp-a/evidence")
        assert evidence.status_code == 404
    with TestClient(owner) as client:
        kolkata = client.get("/api/v1/trends/research", params={"region": "Mumbai"})
        assert kolkata.json()["trends"] == []
        apparel = client.get("/api/v1/trends/research", params={"industry": "Apparel"})
        assert any(item["title"] == "Silk House window" for item in apparel.json()["trends"])
        own_evidence = client.get("/api/v1/trends/opportunities/opp-a/evidence")
        assert own_evidence.status_code == 200
        assert own_evidence.json()["evidence"][0]["text"] == "Search interest rose."


def test_empty_trend_database(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings, current_user_provider=_user("user-a"))
    with TestClient(app) as client:
        research = client.get("/api/v1/trends/research")
        assert research.status_code == 200
        assert research.json()["trends"] == []
        opportunities = client.get("/api/v1/trends/opportunities")
        assert opportunities.json()["opportunities"] == []
        report = client.get("/api/v1/trends/reports")
        assert report.json()["report"]["trend_summary"]["text"] == "No research signals are stored for this account."
        assert report.json()["report"]["content_queue"] == []
        account = client.get("/api/v1/trends/account")
        assert account.json()["account"]["engagement"]["available"] is False


async def test_research_write_is_readable_without_restart(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings, current_user_provider=_user("user-a"))
    other = create_app(tmp_settings, current_user_provider=_user("user-b"))
    fresh = "https://www.thehindu.com/retail-footfall"
    stale = "https://indianexpress.com/old-retail"
    documents = {
        fresh: _doc(
            fresh,
            "Retail footfall rises in India",
            "Indian retail footfall improved this week.",
            NOW - timedelta(hours=2),
        ),
        stale: _doc(
            stale,
            "Old retail footfall note",
            "Indian retail footfall improved last season.",
            NOW - timedelta(days=40),
        ),
    }
    with app.state.session_factory() as session:
        _ensure_user(session, "user-a", "research-a@example.com")
        _ensure_user(session, "user-b", "research-b@example.com")
        session.commit()
        await TrendResearcher(_MapFetcher(documents), clock=lambda: NOW).collect(
            [fresh, stale],
            user_id="user-a",
            session=session,
        )
        session.commit()
        stored = session.scalar(
            select(func.count()).select_from(TrendObservation).where(TrendObservation.user_id == "user-a")
        )
        assert stored == 2
    with TestClient(app) as client:
        research = client.get("/api/v1/trends/research")
        titles = {item["title"]: item for item in research.json()["trends"]}
        assert titles["Retail footfall rises in India"]["expired"] is False
        assert titles["Retail footfall rises in India"]["source"] == "The Hindu"
        assert titles["Retail footfall rises in India"]["evidence"]
        assert titles["Old retail footfall note"]["expired"] is True
    with TestClient(other) as client:
        assert client.get("/api/v1/trends/research").json()["trends"] == []
    with app.state.session_factory() as session:
        await TrendResearcher(_MapFetcher(documents), clock=lambda: NOW).collect(
            [fresh, stale],
            user_id="user-a",
            session=session,
        )
        session.commit()
        stored = session.scalar(
            select(func.count()).select_from(TrendObservation).where(TrendObservation.user_id == "user-a")
        )
        assert stored == 2
    with TestClient(app) as client:
        titles = [item["title"] for item in client.get("/api/v1/trends/research").json()["trends"]]
        assert titles.count("Retail footfall rises in India") == 1
        assert titles.count("Old retail footfall note") == 1


def test_trends_persist_after_database_restart(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings, current_user_provider=_user("user-a"))
    with app.state.session_factory() as session:
        _seed(session)
    with TestClient(app) as client:
        assert any(item["title"] == "Silk House window" for item in client.get("/api/v1/trends/research").json()["trends"])
    app.state.engine.dispose()

    restarted = create_app(tmp_settings, current_user_provider=_user("user-a"))
    with TestClient(restarted) as client:
        titles = {item["title"] for item in client.get("/api/v1/trends/research").json()["trends"]}
        assert "Silk House window" in titles
        assert "Old monsoon palette" in titles
        opportunities = client.get("/api/v1/trends/opportunities").json()["opportunities"]
        assert any(item["id"] == "opp-a" for item in opportunities)
    restarted.state.engine.dispose()


def test_account_endpoint_reads_snapshots(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings, current_user_provider=_user("user-a"))
    observed = datetime(2026, 9, 20, tzinfo=timezone.utc)
    with app.state.session_factory() as session:
        _ensure_user(session, "user-a", "snapshots@example.com")
        AccountSnapshotRepository(session).create(
            "user-a",
            observed_at=observed,
            followers_count=1200,
            media_count=8,
            metrics={"reach": 40, "access_token": "should-not-leak"},
        )
        MediaSnapshotRepository(session).create(
            "user-a",
            instagram_media_id="media-1",
            observed_at=observed,
            like_count=12,
            reach=40,
        )
        InsightSnapshotRepository(session).create(
            "user-a",
            observed_at=observed,
            insight_type="performance",
            summary="Saves rose on product stills.",
        )
        session.commit()
    with TestClient(app) as client:
        account = client.get("/api/v1/trends/account").json()["account"]
        assert account["engagement"]["available"] is True
        assert account["engagement"]["metrics"]["followers_count"] == 1200
        assert account["engagement"]["metrics"]["reach"] == 40
        assert "should-not-leak" not in client.get("/api/v1/trends/account").text
        assert account["top_performing"]["available"] is True
        assert account["top_performing"]["items"][0]["like_count"] == 12
        assert account["performance_changes"]["note"] == "Saves rose on product stills."
