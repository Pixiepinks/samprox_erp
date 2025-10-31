import os
from datetime import timedelta
from urllib.parse import urlparse, urlunparse

DEFAULT_DATABASE_URL = "sqlite:///samprox.db"


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    text = value.strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _normalize_db_url(url: str) -> str:
    if not url:
        return DEFAULT_DATABASE_URL

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


def _env_database_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    return url if url and url.strip() else None


def current_database_url() -> str:
    return _normalize_db_url(_env_database_url() or DEFAULT_DATABASE_URL)


def _env_sqlalchemy_database_uri() -> str | None:
    uri = os.getenv("SQLALCHEMY_DATABASE_URI")
    return uri if uri and uri.strip() else None


class Config:
    SQLALCHEMY_DATABASE_URI = _env_sqlalchemy_database_uri() or current_database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=10)
    ENV = os.getenv("FLASK_ENV", "production")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "mail.samprox.lk")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "465"))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "false").lower() == "true"
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "true").lower() == "true"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "donotreply@samprox.lk")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "1890071481Vip*(DTY)")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "donotreply@samprox.lk")
    MAIL_SUPPRESS_SEND = os.getenv("MAIL_SUPPRESS_SEND", "false").lower() == "true"
    MAIL_TIMEOUT = _env_float("MAIL_TIMEOUT", 10.0)
