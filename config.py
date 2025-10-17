import os
from datetime import timedelta
from urllib.parse import urlparse, urlunparse

DEFAULT_DATABASE_URL = "sqlite:///samprox.db"


def _normalize_db_url(url: str) -> str:
    if not url:
        return url

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    parsed = urlparse(url)

    if parsed.scheme not in {"postgresql", "postgresql+psycopg2"}:
        return url

    def _preferred_db_name() -> str | None:
        for key in ("PGDATABASE", "POSTGRES_DB", "POSTGRES_DATABASE", "DATABASE_NAME"):
            value = os.getenv(key)
            if value:
                return value
        return None

    path = (parsed.path or "").lstrip("/")
    preferred_db = _preferred_db_name()

    if preferred_db:
        if not path:
            parsed = parsed._replace(path=f"/{preferred_db}")
        elif path == "postgres" and preferred_db != "postgres":
            parsed = parsed._replace(path=f"/{preferred_db}")

    return urlunparse(parsed)


def current_database_url() -> str:
    return _normalize_db_url(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "SQLALCHEMY_DATABASE_URI", current_database_url()
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=10)
    ENV = os.getenv("FLASK_ENV", "production")
