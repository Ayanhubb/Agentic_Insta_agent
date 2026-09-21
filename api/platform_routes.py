"""Authenticated platform routes: business, generation, automation, festivals, dashboard."""

from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agent.content_agent import ContentAgent
from auth.deps import get_db, require_admin, require_password_ok
from festivals.festival_service import FestivalService
from festivals.india_festivals import festivals_for_year
from config import Settings
from db.models import GeneratedImage, User
from db.repositories import (
    AutomationRepository,
    BusinessRepository,
    GeneratedImageRepository,
    InstagramAccountRepository,
    PostRepository,
    TaskRepository,
    UserRepository,
)
from models.content import ContentMode, ContentStrategyRequest
from models.errors import AppError, ErrorCode
from services.media_storage import MediaStorage
from services.publication import InstagramConnectRequest, PublicationGateway, PublicationService, strip_secrets
from scheduler.scheduler import AutomationRunner

router = APIRouter(tags=["platform"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _content_agent(request: Request, db: Session) -> ContentAgent:
    return ContentAgent(request.app.state.llm, request.app.state.images, session=db)


def _publisher(request: Request, db: Session) -> PublicationService:
    return PublicationService(
        request.app.state.settings,
        db,
        request.app.state.task_store,
        instagram_client=request.app.state.instagram_client,
        media=request.app.state.media_storage,
    )


def _preview_url(image: GeneratedImage) -> str:
    return f"/api/v1/media/generated/{image.id}"


def _image_payload(image: GeneratedImage) -> dict[str, Any]:
    return {
        "id": image.id,
        "original_prompt": image.original_prompt,
        "enhanced_prompt": image.enhanced_prompt,
        "model": image.model,
        "provider": image.provider,
        "filename": image.filename,
        "mime_type": image.mime_type,
        "width": image.width,
        "height": image.height,
        "generation_status": image.generation_status,
        "approval_status": image.approval_status,
        "publication_status": image.publication_status,
        "source": image.source,
        "preview_url": _preview_url(image),
        "image_url": _preview_url(image),
        "created_at": image.created_at.isoformat() if image.created_at else None,
        "approved_at": image.approved_at.isoformat() if image.approved_at else None,
        "content_type": image.content_type,
        "theme": image.theme,
    }


class BusinessBody(BaseModel):
    business_name: str
    business_type: str | None = None
    business_category: str | None = None
    description: str | None = None
    target_audience: str | None = None
    location: str | None = None
    brand_style: str | None = None
    preferred_language: str | None = None
    products: str | None = None
    services: str | None = None


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=3)


class AutomationBody(BaseModel):
    daily_enabled: bool | None = None
    daily_posts_per_day: int | None = None
    daily_post_time: str | None = None
    festival_enabled: bool | None = None
    festival_posts_per_festival: int | None = None
    auto_daily_publish: bool | None = None
    auto_festival_publish: bool | None = None
    timezone: str | None = None
    pre_festival_days: int | None = None
    allow_same_day_festival_posts: bool | None = None


class InstagramConnectBody(BaseModel):
    instagram_account_id: str
    access_token: str


def _profile_payload(profile) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "business_name": profile.business_name,
        "business_type": profile.business_type,
        "business_category": profile.business_category,
        "description": profile.description,
        "target_audience": profile.target_audience,
        "location": profile.location,
        "brand_style": profile.brand_style,
        "preferred_language": profile.preferred_language,
        "products": profile.products,
        "services": profile.services,
    }


def _automation_payload(settings) -> dict[str, Any]:
    post_time = settings.daily_post_time
    if hasattr(post_time, "strftime"):
        post_time = post_time.strftime("%H:%M")
    return {
        "daily_enabled": settings.daily_enabled,
        "daily_posts_per_day": settings.daily_posts_per_day,
        "daily_post_time": post_time,
        "festival_enabled": settings.festival_enabled,
        "festival_posts_per_festival": settings.festival_posts_per_festival,
        "auto_daily_publish": settings.auto_daily_publish,
        "auto_festival_publish": settings.auto_festival_publish,
        "auto_publish": settings.auto_daily_publish,
        "timezone": settings.timezone,
        "pre_festival_days": getattr(settings, "pre_festival_days", 1),
        "allow_same_day_festival_posts": getattr(settings, "allow_same_day_festival_posts", False),
    }


@router.get("/business")
def get_business(user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    profile = BusinessRepository(db).get_for_user(user.id)
    return {"profile": _profile_payload(profile)}


@router.put("/business")
def put_business(body: BusinessBody, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    profile = BusinessRepository(db).upsert(user.id, **body.model_dump())
    return {"profile": _profile_payload(profile)}


@router.post("/generation")
async def create_generation(
    body: GenerateBody,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    profile = BusinessRepository(db).get_for_user(user.id)
    agent = _content_agent(request, db)
    result = await agent.run(
        ContentStrategyRequest(
            user_id=user.id,
            mode=ContentMode.USER_PROMPT,
            user_prompt=body.prompt,
            business_profile=profile,
        )
    )
    image = None
    if result.generated_image and result.generated_image.id:
        row = db.get(GeneratedImage, result.generated_image.id)
        if row:
            image = _image_payload(row)
    return {
        "task": result.task.model_dump(mode="json"),
        "plan": result.plan.model_dump(mode="json"),
        "image": image,
        "generated_image": image,
        "approval_status": result.approval_status.value,
        "published": False,
    }


@router.get("/generation")
def list_generation(user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    images = GeneratedImageRepository(db).list_for_user(user.id)
    return {"images": [_image_payload(item) for item in images]}


@router.get("/generation/{image_id}")
def get_generation(image_id: str, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    image = GeneratedImageRepository(db).get_owned(user.id, image_id)
    if image is None:
        raise AppError(ErrorCode.NOT_FOUND, "Image was not found.")
    return {"image": _image_payload(image)}


@router.post("/generation/{image_id}/reject")
def reject_generation(image_id: str, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    image = GeneratedImageRepository(db).get_owned(user.id, image_id)
    if image is None:
        raise AppError(ErrorCode.NOT_FOUND, "Image was not found.")
    image.approval_status = "REJECTED"
    return {"image": _image_payload(image)}


@router.post("/generation/{image_id}/regenerate")
async def regenerate(
    image_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    image = GeneratedImageRepository(db).get_owned(user.id, image_id)
    if image is None:
        raise AppError(ErrorCode.NOT_FOUND, "Image was not found.")
    profile = BusinessRepository(db).get_for_user(user.id)
    result = await _content_agent(request, db).run(
        ContentStrategyRequest(
            user_id=user.id,
            mode=ContentMode.USER_PROMPT,
            user_prompt=image.original_prompt,
            business_profile=profile,
        )
    )
    payload = None
    if result.generated_image and result.generated_image.id:
        row = db.get(GeneratedImage, result.generated_image.id)
        if row:
            payload = _image_payload(row)
    return {"image": payload, "generated_image": payload}


@router.post("/generation/{image_id}/approve")
async def approve_and_post(
    image_id: str,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    image = GeneratedImageRepository(db).get_owned(user.id, image_id)
    if image is None:
        raise AppError(ErrorCode.NOT_FOUND, "Image was not found.")
    image.approval_status = "APPROVED"
    image.approved_at = datetime.now(timezone.utc)
    account = InstagramAccountRepository(db).get_primary(user.id)
    post = await _publisher(request, db).publish_generated_image(
        user_id=user.id,
        image=image,
        account=account,
        post_type="USER_PROMPT",
        trigger="USER_APPROVAL",
    )
    return {"image": _image_payload(image), "post": _post_payload(post, image)}


def _post_payload(post, image: GeneratedImage | None = None) -> dict[str, Any]:
    return {
        "id": post.id,
        "generated_image_id": post.generated_image_id,
        "instagram_media_id": post.instagram_media_id,
        "permalink": post.permalink,
        "status": post.status,
        "post_type": post.post_type,
        "source": post.post_type,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "created_at": post.created_at.isoformat() if post.created_at else None,
        "error": post.error,
        "preview_url": _preview_url(image) if image is not None else None,
        "image_url": _preview_url(image) if image is not None else None,
    }


@router.get("/posts")
def list_posts(user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    posts = PostRepository(db).list_for_user(user.id)
    images = {item.id: item for item in GeneratedImageRepository(db).list_for_user(user.id, limit=200)}
    return {"posts": [_post_payload(post, images.get(post.generated_image_id or "")) for post in posts]}


@router.get("/automation")
def get_automation(request: Request, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    tz = request.app.state.settings.default_timezone
    row = AutomationRepository(db).get_or_create(user.id, tz)
    return _automation_payload(row)


@router.put("/automation")
def put_automation(
    body: AutomationBody,
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict:
    tz = request.app.state.settings.default_timezone
    row = AutomationRepository(db).get_or_create(user.id, tz)
    data = body.model_dump(exclude_none=True)
    if "daily_posts_per_day" in data:
        data["daily_posts_per_day"] = 1
    if isinstance(data.get("daily_post_time"), str):
        parts = data["daily_post_time"].split(":")
        data["daily_post_time"] = time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    for key, value in data.items():
        setattr(row, key, value)
    return _automation_payload(row)


@router.post("/automation/run-now")
async def run_now(request: Request, user: User = Depends(require_password_ok)) -> dict:
    runner: AutomationRunner = request.app.state.automation_runner
    return await runner.tick(user_id=user.id)


@router.get("/festivals")
def list_festivals(request: Request, user: User = Depends(require_password_ok)) -> dict:
    year = request.app.state.clock.now("Asia/Kolkata").year
    return {"festivals": festivals_for_year(year)}


@router.get("/festivals/campaigns")
def list_campaigns(request: Request, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    year = request.app.state.clock.now("Asia/Kolkata").year
    automation = AutomationRepository(db).get_or_create(user.id, "Asia/Kolkata")
    service = FestivalService(db)
    if automation.festival_enabled:
        service.ensure_campaigns(user.id, year, required_posts=automation.festival_posts_per_festival or 2)
    campaigns = []
    for item in service._repo.list_campaigns(user.id):
        campaigns.append(
            {
                "id": item.id,
                "festival_name": item.festival_name,
                "name": item.festival_name,
                "festival_date": item.festival_date.isoformat(),
                "date": item.festival_date.isoformat(),
                "year": item.year,
                "required_posts": item.required_posts,
                "generated_posts": item.generated_posts,
                "published_posts": item.published_posts,
                "remaining_posts": item.remaining_posts,
                "enabled": item.enabled,
                "status": "completed" if item.remaining_posts == 0 else "active",
            }
        )
    return {"campaigns": campaigns}


@router.put("/festivals/settings")
def put_festival_settings(body: AutomationBody, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    row = AutomationRepository(db).get_or_create(user.id, "Asia/Kolkata")
    data = body.model_dump(exclude_none=True)
    for key in (
        "festival_enabled",
        "festival_posts_per_festival",
        "auto_festival_publish",
        "pre_festival_days",
        "allow_same_day_festival_posts",
        "timezone",
    ):
        if key in data:
            setattr(row, key, data[key])
    return _automation_payload(row)


@router.get("/instagram/status")
async def instagram_status(request: Request, user: User = Depends(require_password_ok)) -> dict:
    gateway: PublicationGateway = request.app.state.publication_gateway
    return strip_secrets(await gateway.account_status(user.id))


@router.post("/instagram/connect")
async def instagram_connect(
    body: InstagramConnectBody,
    request: Request,
    user: User = Depends(require_password_ok),
) -> dict:
    gateway: PublicationGateway = request.app.state.publication_gateway
    return strip_secrets(
        await gateway.connect_account(
            user.id,
            InstagramConnectRequest(
                instagram_account_id=body.instagram_account_id,
                access_token=body.access_token,
            ),
        )
    )


@router.post("/instagram/disconnect")
async def instagram_disconnect(request: Request, user: User = Depends(require_password_ok)) -> dict:
    gateway: PublicationGateway = request.app.state.publication_gateway
    return strip_secrets(await gateway.disconnect_account(user.id))


@router.get("/dashboard")
def dashboard(request: Request, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    posts = PostRepository(db)
    images = GeneratedImageRepository(db)
    automation = AutomationRepository(db).get_or_create(user.id, "Asia/Kolkata")
    tz = ZoneInfo(automation.timezone or "Asia/Kolkata")
    now = request.app.state.clock.now(automation.timezone)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    image_rows = images.list_for_user(user.id, limit=8)
    post_rows = posts.list_for_user(user.id, limit=8)
    account = InstagramAccountRepository(db).get_primary(user.id)
    festivals = festivals_for_year(now.year)
    upcoming = next((item for item in festivals if item["date"] >= now.date().isoformat()), None)
    image_map = {item.id: item for item in images.list_for_user(user.id, limit=200)}
    return {
        "total_posts": posts.count_published(user.id),
        "todays_posts": posts.count_published(user.id, since=start_today),
        "monthly_posts": posts.count_published(user.id, since=start_month),
        "generated_images": len(images.list_for_user(user.id, limit=500)),
        "published_posts": posts.count_published(user.id),
        "festival_posts": sum(1 for item in post_rows if item.post_type == "FESTIVAL" and item.status == "PUBLISHED"),
        "daily_automation": automation.daily_enabled,
        "festival_automation": automation.festival_enabled,
        "instagram_connected": account is not None,
        "recent_images": [_image_payload(item) for item in image_rows],
        "recent_posts": [_post_payload(item, image_map.get(item.generated_image_id or "")) for item in post_rows],
        "upcoming_festival": upcoming,
        "next_scheduled_post": automation.daily_post_time if automation.daily_enabled else None,
        "recent_activity": [
            {"label": f"Post {item.status}", "at": item.created_at.isoformat() if item.created_at else None}
            for item in post_rows[:5]
        ],
    }


@router.get("/tasks/{task_id}/owned")
def owned_task(task_id: str, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    task = TaskRepository(db).get_owned(user.id, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, "Task was not found.")
    events = TaskRepository(db).events_for_task(task.id)
    return {
        "task_id": task.id,
        "status": task.status,
        "current_step": task.current_step,
        "events": [
            {
                "from": event.from_state,
                "to": event.to_state,
                "tool": event.tool,
                "result": event.result,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            }
            for event in events
        ],
    }


@router.get("/media/generated/{image_id}")
def serve_generated(image_id: str, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> FileResponse:
    image = GeneratedImageRepository(db).get_owned(user.id, image_id)
    if image is None:
        raise AppError(ErrorCode.NOT_FOUND, "Image was not found.")
    path = Path(image.storage_path)
    if not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "Image file was not found.")
    return FileResponse(path, media_type=image.mime_type or "image/jpeg")


@router.get("/admin/users")
def admin_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    users = UserRepository(db).list_active()
    return {
        "users": [
            {
                "id": item.id,
                "email": item.email,
                "is_admin": item.is_admin,
                "is_active": item.is_active,
            }
            for item in users
        ]
    }


@router.get("/settings")
def get_settings_page(request: Request, user: User = Depends(require_password_ok), db: Session = Depends(get_db)) -> dict:
    automation = get_automation(request=request, user=user, db=db)
    return {"user": {"id": user.id, "email": user.email, "is_admin": user.is_admin}, "automation": automation}
