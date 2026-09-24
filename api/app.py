"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ai import get_image_generation_provider, get_llm_provider
from ai.mocks import MockImageGenerationProvider, MockLLMProvider
from agent.agent import InstagramAgent
from agent.instagram_tasks import bind_publication_gateway
from api.asset_routes import router as asset_router
from api.canva_routes import router as canva_router
from backend.integrations.canva import CanvaAdapter
from api.auth_routes import admin_router, router as auth_router
from db.repositories import SessionRepository, UserRepository
from api.middleware import RequestContextMiddleware
from api.platform_routes import router as platform_router
from api.routes import create_router
from api.task_store import TaskStore
from auth.bootstrap import bootstrap_admin
from auth.tokens import COOKIE_NAME, decode_access_token
from config import Settings, get_settings
from db.session import bind_runtime, create_engine_from_settings, init_db, session_factory
from models.errors import AppError, ErrorCode
from scheduler.scheduler import AutomationRunner, scheduler_loop
from services.clock import Clock
from services.instagram_client import InstagramClient, InstagramGraphClient
from services.logging import configure_logging, redact_text
from services.media_storage import MediaStorageService
from services.publication import PublicationGateway
from services.publication_store import InMemoryPublicationStore


def _select_llm(settings: Settings):
    provider = (settings.llm_provider or "openai").strip().lower()
    if provider == "deepseek":
        if settings.deepseek_configured and settings.deepseek_model.strip():
            return get_llm_provider(settings)
        return MockLLMProvider()
    if settings.openai_configured and settings.llm_model.strip():
        return get_llm_provider(settings)
    return MockLLMProvider()


def create_app(
    settings: Settings | None = None,
    *,
    instagram_client: InstagramClient | None = None,
    llm_provider=None,
    image_provider=None,
    creative_model=None,
    clock: Clock | None = None,
    current_user_provider=None,
    publication_store=None,
    task_store=None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_directories()
    configure_logging(settings.log_level)

    engine = create_engine_from_settings(settings)
    factory = session_factory(engine)
    init_db(engine)
    bind_runtime(engine, factory, settings)
    with factory() as session:
        bootstrap_admin(session, settings)
        session.commit()

    store = task_store or TaskStore(session_factory=factory)
    client = instagram_client
    media_service = MediaStorageService(settings)
    clock = clock or Clock()

    if llm_provider is None:
        llm_provider = _select_llm(settings)
    if image_provider is None:
        if settings.openai_configured and (settings.image_model.strip() or settings.openai_image_model.strip()):
            image_provider = get_image_generation_provider(settings, storage=media_service)
        else:
            image_provider = MockImageGenerationProvider(media_service)
    from scheduler.integrations import resolve_festival_mcp, resolve_vision

    vision_provider = resolve_vision(settings)
    festival_mcp = resolve_festival_mcp(settings)
    canva = CanvaAdapter(settings, session_factory=factory)
    canva_client = canva if settings.canva_enabled else None

    stop_event = asyncio.Event()
    runner = AutomationRunner(
        settings,
        factory,
        store,
        llm_provider,
        image_provider,
        clock,
        instagram_client=client,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if settings.scheduler_enabled:
            task = asyncio.create_task(scheduler_loop(runner, settings.scheduler_interval_seconds, stop_event))
        yield
        stop_event.set()
        if task is not None:
            task.cancel()

    app = FastAPI(
        title="Instagram Agentic AI",
        version="2.0.0",
        description="AI content studio with Instagram publishing Agent.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list() or ["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware, settings=settings)

    def agent_factory(*, on_change) -> InstagramAgent:
        return InstagramAgent(settings, instagram_client=client or InstagramGraphClient(settings), on_change=on_change)

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(platform_router, prefix="/api/v1")
    app.include_router(asset_router, prefix="/api/v1")
    app.include_router(canva_router, prefix="/api/v1")
    app.include_router(create_router(settings, store, agent_factory, media_service=media_service), prefix="/api/v1")

    static_dir = settings.static_dir
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def demo_ui() -> FileResponse:
        index = static_dir / "index.html"
        if not index.is_file():
            raise AppError(ErrorCode.INTERNAL_ERROR, "Demo UI is missing.", http_status=500)
        return FileResponse(index)

    @app.middleware("http")
    async def attach_user(request: Request, call_next):
        request.state.user = None
        provider = getattr(request.app.state, "current_user_provider", None)
        if callable(provider):
            resolved = provider(request)
            if hasattr(resolved, "__await__"):
                resolved = await resolved
            request.state.user = resolved
            return await call_next(request)
        header = request.headers.get("authorization") or ""
        token = ""
        if header.lower().startswith("bearer "):
            token = header.split(" ", 1)[1].strip()
        if not token:
            token = request.cookies.get(COOKIE_NAME, "")
        if token:
            try:
                payload = decode_access_token(settings, token)
                with factory() as session:
                    jti = payload.get("jti") or ""
                    if jti and SessionRepository(session).get_valid(jti) is not None:
                        request.state.user = UserRepository(session).get_by_id(payload["user_id"])
                        request.state.session_id = jti
            except AppError:
                request.state.user = None
        return await call_next(request)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            {
                "success": False,
                "task_id": None,
                "request_id": request_id,
                "platform": "instagram",
                "status": "failed",
                "error": {
                    "code": exc.code.value,
                    "message": redact_text(exc.message),
                },
            },
            status_code=exc.http_status,
        )

    app.state.settings = settings
    app.state.task_store = store
    app.state.media_service = media_service
    app.state.media_storage = media_service
    app.state.llm = llm_provider
    app.state.llm_provider = llm_provider
    app.state.images = image_provider
    app.state.image_provider = image_provider
    if creative_model is None:
        from ai.creative_model import get_creative_model

        creative_model = get_creative_model(settings)
    app.state.creative_model = creative_model
    app.state.vision_provider = vision_provider
    app.state.festival_mcp = festival_mcp
    app.state.canva = canva
    app.state.canva_client = canva_client
    app.state.instagram_client = client
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.clock = clock
    app.state.automation_runner = runner
    app.state.current_user_provider = current_user_provider
    publication_gateway = PublicationGateway(
        settings,
        publication_store or InMemoryPublicationStore(),
        task_store=store,
        instagram_client=client,
        session_factory=factory,
    )
    bind_publication_gateway(publication_gateway)
    app.state.publication_gateway = publication_gateway
    runner._publication_gateway = publication_gateway
    return app
