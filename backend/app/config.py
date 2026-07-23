import os
from dataclasses import dataclass, field


def _origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://agencydesk_app:app_pw@localhost:5433/agencydesk",
    )
    owner_database_url: str = os.environ.get(
        "OWNER_DATABASE_URL",
        "postgresql+psycopg://agencydesk_owner:owner_pw@localhost:5433/agencydesk",
    )
    jwt_secret: str = os.environ.get("JWT_SECRET", "dev-secret-change-me")
    jwt_algorithm: str = "HS256"
    # A request holds its connection for the length of its transaction, so this
    # is effectively the concurrency ceiling.
    db_pool_size: int = int(os.environ.get("DB_POOL_SIZE", "10"))
    db_max_overflow: int = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
    session_ttl_minutes: int = int(os.environ.get("SESSION_TTL_MINUTES", "720"))
    invite_ttl_days: int = int(os.environ.get("INVITE_TTL_DAYS", "7"))
    upload_dir: str = os.environ.get("UPLOAD_DIR", "/data/uploads")
    max_upload_bytes: int = int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    cors_origins: list[str] = field(default_factory=_origins)


settings = Settings()
