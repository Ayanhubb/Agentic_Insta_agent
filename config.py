"""Environment-based application configuration. Secrets are never hard-coded."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent

ALLOWED_GRAPH_HOSTS = frozenset({"graph.facebook.com", "graph.instagram.com"})
ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png"})
ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG"})
DEFAULT_MOCK_PUBLIC_BASE_URL = "https://cdn.example.test/instagram-agent"


def _optional_env(name: str) -> str:
    return os.getenv(name, "").strip()


def _float_env(name: str, default: float) -> float:
    raw = _optional_env(name)
    return float(raw) if raw else default


def _int_env(name: str, default: int) -> int:
    raw = _optional_env(name)
    return int(raw) if raw else default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = _optional_env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meta_access_token: str = ""
    instagram_access_token: str | None = None
    instagram_account_id: str = ""
    instagram_ig_user_id: str | None = None
    # Production publishing never reads the process Meta token. The legacy
    # single-account path requires both a development APP_ENV and this flag.
    app_env: str = "production"
    instagram_legacy_env_fallback: bool = False
    meta_graph_api_base_url: str = "https://graph.facebook.com"
    instagram_graph_base_url: str | None = None
    meta_api_version: str = "v26.0"
    instagram_api_version: str | None = None

    max_image_size_mb: int = 8
    max_image_bytes: int | None = None
    request_timeout_seconds: float = 30.0
    agent_timeout_seconds: float = 120.0

    public_image_base_url: str = DEFAULT_MOCK_PUBLIC_BASE_URL
    image_public_base_url: str | None = None
    mock_image_public_base_url: str = DEFAULT_MOCK_PUBLIC_BASE_URL

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    storage_dir: Path = PROJECT_ROOT / "storage"
    media_root: Path = PROJECT_ROOT / "storage"
    temp_dir: Path = PROJECT_ROOT / "tmp"
    static_dir: Path = PROJECT_ROOT / "api" / "static"
    input_dir: Path = PROJECT_ROOT / "input"
    output_dir: Path = PROJECT_ROOT / "output"

    min_image_width: int = 320
    min_image_height: int = 320
    min_image_side: int = 320
    max_image_side: int = 1440
    target_image_width: int = 1080
    jpeg_quality: int = 90

    storage_retry_attempts: int = 3
    verification_retry_attempts: int = 5
    verification_retry_delay_seconds: float = 2.0
    container_ready_attempts: int = 8
    container_ready_delay_seconds: float = 1.5
    media_ready_timeout_seconds: float = 60.0
    media_poll_interval_seconds: float = 2.0
    max_retry_after_seconds: float = 15.0
    graph_retry_attempts: int = 2
    allow_insecure_graph_http: bool = False
    strict_graph_hosts: bool = True

    openai_api_key: str = ""
    llm_provider: str = "deepseek"
    llm_model: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_timeout_seconds: float = 30.0
    vision_provider: str = "deepseek"
    image_provider: str = "openai"
    image_model: str = ""
    openai_image_model: str = ""
    openai_image_quality: str = ""
    openai_image_output_format: str = ""
    openai_image_size: str = ""
    llm_max_attempts: int = 3
    image_max_attempts: int = 3
    llm_temperature: float = 0.4
    image_size: str = "1024x1024"
    openai_retry_delay_seconds: float = 0.4
    trend_fast_hours: int = 6
    trend_current_days: int = 7
    trend_seasonal_days: int = 60
    trend_evergreen_days: int = 180

    database_url: str = ""
    token_encryption_key: str = ""
    jwt_secret: str = "dev-change-me"
    jwt_expire_minutes: int = 480
    jwt_cookie_secure: bool = False
    bcrypt_rounds: int = 12
    default_admin_email: str = ""
    default_admin_password: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000"
    scheduler_enabled: bool = False
    scheduler_interval_seconds: int = 60
    default_timezone: str = "Asia/Kolkata"

    # Official Canva remote MCP. Disabled unless CANVA_ENABLED is set.
    # Image generation does not use these settings.
    canva_enabled: bool = False
    canva_mcp_url: str = "https://mcp.canva.com/mcp"
    canva_authorize_url: str = "https://mcp.canva.com/authorize"
    canva_token_url: str = "https://mcp.canva.com/token"
    canva_client_id: str = ""
    canva_client_secret: str = ""
    canva_redirect_uri: str = ""
    canva_oauth_scopes: str = ""
    canva_timeout_seconds: float = 60.0
    canva_oauth_success_url: str = ""
    canva_allow_unofficial_endpoint: bool = False

    @model_validator(mode="after")
    def normalize(self) -> "Settings":
        if not self.meta_access_token and self.instagram_access_token:
            self.meta_access_token = self.instagram_access_token
        if not self.instagram_account_id and self.instagram_ig_user_id:
            self.instagram_account_id = self.instagram_ig_user_id
        if self.instagram_graph_base_url:
            self.meta_graph_api_base_url = self.instagram_graph_base_url.rstrip("/")
        if self.instagram_api_version:
            self.meta_api_version = self.instagram_api_version
        if self.image_public_base_url:
            self.public_image_base_url = self.image_public_base_url.rstrip("/")
        if self.max_image_bytes is None:
            self.max_image_bytes = int(self.max_image_size_mb * 1024 * 1024)
        else:
            self.max_image_size_mb = max(1, int(self.max_image_bytes / (1024 * 1024)))
        self.min_image_width = max(self.min_image_width, self.min_image_side)
        self.min_image_height = max(self.min_image_height, self.min_image_side)
        if self.media_poll_interval_seconds >= 0:
            self.container_ready_delay_seconds = self.media_poll_interval_seconds
        self.meta_graph_api_base_url = self.meta_graph_api_base_url.rstrip("/")
        if not self.image_model.strip() and self.openai_image_model.strip():
            self.image_model = self.openai_image_model.strip()
        if self.openai_image_size.strip():
            self.image_size = self.openai_image_size.strip()
        self.deepseek_base_url = (self.deepseek_base_url or "https://api.deepseek.com").rstrip("/")
        return self

    @property
    def credentials_configured(self) -> bool:
        return bool(self.meta_access_token.strip() and self.instagram_account_id.strip())

    def legacy_environment_credentials_allowed(self) -> bool:
        """Single-account development path. Never true for production or staging.

        `META_ACCESS_TOKEN` and `INSTAGRAM_ACCOUNT_ID` are ignored unless
        `INSTAGRAM_LEGACY_ENV_FALLBACK` is set and `APP_ENV` is development,
        dev, or local. Unset `APP_ENV` is production.
        """
        if not self.instagram_legacy_env_fallback or not self.credentials_configured:
            return False
        env = (self.app_env or "").strip().lower()
        if env in {"production", "prod", "staging"}:
            return False
        return env in {"development", "dev", "local"}

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def deepseek_configured(self) -> bool:
        return bool(self.deepseek_api_key.strip())

    @property
    def canva_configured(self) -> bool:
        """True when Canva is enabled and this app can start per-user OAuth."""
        if not self.canva_enabled:
            return False
        client_id = self.canva_client_id.strip()
        if not client_id or not self.canva_redirect_uri.strip():
            return False
        if client_id.startswith("https://"):
            return True
        return bool(self.canva_client_secret.strip())

    def public_canva_status(self) -> dict[str, str | bool]:
        """Safe Canva configuration. Never includes client secrets or user tokens."""
        return {
            "enabled": self.canva_enabled,
            "configured": self.canva_configured,
            "mcp_host": urlparse(self.canva_mcp_url).hostname or "",
        }

    def public_ai_status(self) -> dict[str, str | bool]:
        """Safe AI configuration for diagnostics. Never includes API keys."""
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "image_provider": self.image_provider,
            "image_model": self.image_model,
            "vision_provider": self.vision_provider,
            "deepseek_model": self.deepseek_model or "deepseek-flash",
            "openai_configured": self.openai_configured,
            "deepseek_configured": self.deepseek_configured,
            "canva_enabled": self.canva_enabled,
            "canva_configured": self.canva_configured,
            "mcp_enabled": True,
        }

    @property
    def instagram_configured(self) -> bool:
        return self.credentials_configured

    @property
    def graph_api_root(self) -> str:
        return f"{self.meta_graph_api_base_url}/{self.meta_api_version.lstrip('/')}"

    @property
    def prepared_dir(self) -> Path:
        return self.output_dir / "prepared"

    @property
    def hosted_dir(self) -> Path:
        return self.output_dir / "hosted"

    @property
    def generated_dir(self) -> Path:
        return Path(self.media_root) / "generated"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url.strip():
            return self.database_url.strip()
        db_path = (PROJECT_ROOT / "data" / "agentic.db").resolve()
        return f"sqlite:///{db_path.as_posix()}"

    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def ensure_directories(self) -> None:
        for path in (
            self.storage_dir,
            self.media_root,
            self.media_root / "generated",
            self.media_root / "prepared",
            self.media_root / "published",
            self.media_root / "assets",
            self.temp_dir,
            self.input_dir,
            self.output_dir,
            self.prepared_dir,
            self.hosted_dir,
            self.generated_dir,
            PROJECT_ROOT / "data",
        ):
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        max_bytes = _int_env("MAX_IMAGE_BYTES", 0)
        max_mb = _int_env("MAX_IMAGE_SIZE_MB", 8)
        return cls(
            meta_access_token=_optional_env("META_ACCESS_TOKEN")
            or _optional_env("INSTAGRAM_ACCESS_TOKEN"),
            instagram_account_id=_optional_env("INSTAGRAM_ACCOUNT_ID")
            or _optional_env("INSTAGRAM_IG_USER_ID"),
            app_env=_optional_env("APP_ENV") or "production",
            instagram_legacy_env_fallback=_bool_env("INSTAGRAM_LEGACY_ENV_FALLBACK", False),
            meta_graph_api_base_url=_optional_env("META_GRAPH_API_BASE_URL")
            or _optional_env("INSTAGRAM_GRAPH_BASE_URL")
            or "https://graph.facebook.com",
            meta_api_version=_optional_env("META_API_VERSION")
            or _optional_env("INSTAGRAM_API_VERSION")
            or "v26.0",
            max_image_size_mb=max_mb,
            max_image_bytes=max_bytes or None,
            request_timeout_seconds=_float_env("REQUEST_TIMEOUT_SECONDS", 30.0),
            agent_timeout_seconds=_float_env("AGENT_TIMEOUT_SECONDS", 120.0),
            public_image_base_url=_optional_env("PUBLIC_IMAGE_BASE_URL")
            or _optional_env("IMAGE_PUBLIC_BASE_URL")
            or DEFAULT_MOCK_PUBLIC_BASE_URL,
            host=_optional_env("HOST") or "0.0.0.0",
            port=_int_env("PORT", 8000),
            log_level=_optional_env("LOG_LEVEL") or "INFO",
            min_image_side=_int_env("MIN_IMAGE_SIDE", 320),
            max_image_side=_int_env("MAX_IMAGE_SIDE", 1440),
            target_image_width=_int_env("TARGET_IMAGE_WIDTH", 1080),
            jpeg_quality=_int_env("JPEG_QUALITY", 90),
            media_ready_timeout_seconds=_float_env("MEDIA_READY_TIMEOUT_SECONDS", 60.0),
            media_poll_interval_seconds=_float_env("MEDIA_POLL_INTERVAL_SECONDS", 2.0),
            openai_api_key=_optional_env("OPENAI_API_KEY"),
            llm_provider=_optional_env("LLM_PROVIDER") or "deepseek",
            llm_model=_optional_env("LLM_MODEL"),
            deepseek_api_key=_optional_env("DEEPSEEK_API_KEY"),
            deepseek_model=_optional_env("DEEPSEEK_MODEL") or "deepseek-flash",
            deepseek_base_url=_optional_env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            deepseek_timeout_seconds=_float_env("DEEPSEEK_TIMEOUT_SECONDS", 30.0),
            vision_provider=_optional_env("VISION_PROVIDER") or "deepseek",
            image_provider=_optional_env("IMAGE_PROVIDER") or "openai",
            image_model=_optional_env("IMAGE_MODEL"),
            openai_image_model=_optional_env("OPENAI_IMAGE_MODEL"),
            openai_image_quality=_optional_env("OPENAI_IMAGE_QUALITY"),
            openai_image_output_format=_optional_env("OPENAI_IMAGE_OUTPUT_FORMAT"),
            openai_image_size=_optional_env("OPENAI_IMAGE_SIZE"),
            llm_max_attempts=_int_env("LLM_MAX_ATTEMPTS", 3),
            image_max_attempts=_int_env("IMAGE_MAX_ATTEMPTS", 3),
            llm_temperature=_float_env("LLM_TEMPERATURE", 0.4),
            image_size=_optional_env("IMAGE_SIZE") or "1024x1024",
            openai_retry_delay_seconds=_float_env("OPENAI_RETRY_DELAY_SECONDS", 0.4),
            trend_fast_hours=_int_env("TREND_FAST_HOURS", 6),
            trend_current_days=_int_env("TREND_CURRENT_DAYS", 7),
            trend_seasonal_days=_int_env("TREND_SEASONAL_DAYS", 60),
            trend_evergreen_days=_int_env("TREND_EVERGREEN_DAYS", 180),
            database_url=_optional_env("DATABASE_URL"),
            token_encryption_key=_optional_env("TOKEN_ENCRYPTION_KEY")
            or _optional_env("ACCOUNT_TOKEN_FERNET_KEY"),
            jwt_secret=_optional_env("JWT_SECRET") or _optional_env("SECRET_KEY") or "dev-change-me",
            jwt_expire_minutes=_int_env("JWT_EXPIRE_MINUTES", 480),
            jwt_cookie_secure=_optional_env("JWT_COOKIE_SECURE").lower() in {"1", "true", "yes"},
            bcrypt_rounds=_int_env("BCRYPT_ROUNDS", 12),
            default_admin_email=_optional_env("DEFAULT_ADMIN_EMAIL"),
            default_admin_password=_optional_env("DEFAULT_ADMIN_PASSWORD"),
            cors_origins=_optional_env("CORS_ORIGINS")
            or "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000",
            scheduler_enabled=_optional_env("SCHEDULER_ENABLED").lower() in {"1", "true", "yes"},
            scheduler_interval_seconds=_int_env("SCHEDULER_INTERVAL_SECONDS", 60),
            default_timezone=_optional_env("DEFAULT_TIMEZONE") or "Asia/Kolkata",
            canva_enabled=_bool_env("CANVA_ENABLED", False),
            canva_mcp_url=_optional_env("CANVA_MCP_URL") or "https://mcp.canva.com/mcp",
            canva_authorize_url=_optional_env("CANVA_AUTHORIZE_URL") or "https://mcp.canva.com/authorize",
            canva_token_url=_optional_env("CANVA_TOKEN_URL") or "https://mcp.canva.com/token",
            canva_client_id=_optional_env("CANVA_CLIENT_ID"),
            canva_client_secret=_optional_env("CANVA_CLIENT_SECRET"),
            canva_redirect_uri=_optional_env("CANVA_REDIRECT_URI"),
            canva_oauth_scopes=_optional_env("CANVA_OAUTH_SCOPES"),
            canva_timeout_seconds=_float_env("CANVA_TIMEOUT_SECONDS", 60.0),
            canva_oauth_success_url=_optional_env("CANVA_OAUTH_SUCCESS_URL"),
            canva_allow_unofficial_endpoint=_bool_env("CANVA_ALLOW_UNOFFICIAL_ENDPOINT", False),
        )


def validate_graph_base_url(url: str, *, strict: bool = True) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Graph API base URL must be HTTP(S)")
    if strict and host not in ALLOWED_GRAPH_HOSTS and host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Graph API host is not allowlisted")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_directories()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
