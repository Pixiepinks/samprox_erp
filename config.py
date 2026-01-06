import os
from datetime import timedelta
from urllib.parse import urlparse, urlunparse

DEFAULT_DATABASE_URL = "sqlite:///samprox.db"
DEFAULT_COMPANY_PROFILES = [
    {
        "key": "samprox-international",
        "name": "Samprox International (Pvt) Ltd",
        "address_lines": [
            "16/2, Sasanawardenarama Mawatha,",
            "Galawilawatta, Homagama",
        ],
        "contact": None,
        "tagline": "The Shape of Tomorrow",
        "logo_path": "static/profile_images/GRNLOGO.png",
    },
    {
        "key": "rainbows-end-trading",
        "name": "Rainbows End Trading (Pvt) Ltd",
        "address_lines": [],
        "contact": None,
        "tagline": "Import and distribute spareparts",
        "logo_path": "static/profile_images/GRNLOGO.png",
    },
    {
        "key": "rainbows-industrial",
        "name": "Rainbows Industrial (Pvt) Ltd",
        "address_lines": [],
        "contact": None,
        "tagline": "Welding Plant Import and selling",
        "logo_path": "static/profile_images/GRNLOGO.png",
    },
    {
        "key": "hello-homes",
        "name": "Hello Homes (Pvt) Ltd",
        "address_lines": [],
        "contact": None,
        "tagline": None,
        "logo_path": "static/profile_images/GRNLOGO.png",
    },
    {
        "key": "exsol-engineering",
        "name": "Exsol Engineering (Pvt) Ltd",
        "address_lines": [],
        "contact": None,
        "tagline": None,
        "logo_path": "static/profile_images/GRNLOGO.png",
    },
]


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


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    text = value.strip().lower()
    if not text:
        return default
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    text = value.strip()
    if not text:
        return default
    return text


def _env_list(name: str) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return []
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item]


def _env_int_list(name: str) -> list[int]:
    result: list[int] = []
    for item in _env_list(name):
        try:
            number = int(item)
        except ValueError:
            continue
        if number > 0:
            result.append(number)
    return result


def _env_password(name: str, default: str | None = None) -> str | None:
    """Return a password-like value with whitespace removed."""

    value = os.getenv(name)
    if value is None:
        return default
    text = value.strip()
    if not text:
        return default
    # Gmail app passwords are presented with spaces for readability; remove them.
    cleaned = "".join(text.split())
    return cleaned or default


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


def _env_exsol_database_url() -> str | None:
    url = os.getenv("EXSOL_DATABASE_URL")
    if url is None:
        return None
    text = url.strip()
    if not text:
        return None
    return _normalize_db_url(text)


def current_database_url() -> str:
    return _normalize_db_url(_env_database_url() or DEFAULT_DATABASE_URL)


def _env_sqlalchemy_database_uri() -> str | None:
    uri = os.getenv("SQLALCHEMY_DATABASE_URI")
    return uri if uri and uri.strip() else None


def _default_company_profiles() -> list[dict]:
    return [profile.copy() for profile in DEFAULT_COMPANY_PROFILES]


class Config:
    SQLALCHEMY_DATABASE_URI = _env_sqlalchemy_database_uri() or current_database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=10)
    JWT_TOKEN_LOCATION = ["headers", "cookies"]
    JWT_COOKIE_SECURE = _env_bool(
        "JWT_COOKIE_SECURE",
        False if os.getenv("FLASK_ENV", "production") != "production" else True,
    )
    JWT_COOKIE_SAMESITE = os.getenv("JWT_COOKIE_SAMESITE", "Lax")
    JWT_COOKIE_CSRF_PROTECT = False
    ENV = os.getenv("FLASK_ENV", "production")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", True)
    MAIL_USE_SSL = _env_bool("MAIL_USE_SSL", False)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "donotreplysamprox@gmail.com")
    MAIL_PASSWORD = _env_password("MAIL_PASSWORD", "zzohpxmeoiahipp")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "donotreplysamprox@gmail.com")
    MAIL_SUPPRESS_SEND = _env_bool("MAIL_SUPPRESS_SEND", False)
    MAIL_TIMEOUT = _env_float("MAIL_TIMEOUT", 10.0)
    MAIL_FALLBACK_TO_TLS = _env_bool("MAIL_FALLBACK_TO_TLS", True)
    MAIL_FALLBACK_PORT = int(os.getenv("MAIL_FALLBACK_PORT", "587"))
    MAIL_FALLBACK_USE_SSL = _env_bool("MAIL_FALLBACK_USE_SSL", False)
    MAIL_FALLBACK_SERVER = os.getenv("MAIL_FALLBACK_SERVER")
    MAIL_ADDITIONAL_SERVERS = _env_list("MAIL_ADDITIONAL_SERVERS")
    MAIL_ADDITIONAL_PORTS = _env_int_list("MAIL_ADDITIONAL_PORTS")
    MAIL_FORCE_IPV4 = _env_bool("MAIL_FORCE_IPV4", False)
    MAIL_MAX_DELIVERY_SECONDS = _env_float("MAIL_MAX_DELIVERY_SECONDS", 20.0)
    MAIL_DEFAULT_BCC = _env_list("MAIL_DEFAULT_BCC") or ["prakash@rainbowsholdings.com"]
    EXSOL_DATABASE_URL = _env_exsol_database_url()
    EXSOL_SCHEMA = _env_str("EXSOL_SCHEMA", "exsol") or "exsol"
    COMPANY_PROFILES = _default_company_profiles()
    COMPANY_KEY = _env_str("COMPANY_KEY")
    COMPANY_NAME = _env_str("COMPANY_NAME", "Samprox International (Pvt) Ltd")
    COMPANY_ADDRESS = _env_str(
        "COMPANY_ADDRESS", "16/2, Sasanawardenarama Mawatha, Galawilawatta, Homagama"
    )
    COMPANY_CONTACT = _env_str("COMPANY_CONTACT")
    COMPANY_TAGLINE = _env_str("COMPANY_TAGLINE", "The Shape of Tomorrow")
    COMPANY_LOGO_PATH = _env_str("COMPANY_LOGO_PATH", "static/profile_images/GRNLOGO.png")
    RESEND_API_KEY = _env_password("RESEND_API_KEY")
    RESEND_DEFAULT_SENDER = _env_str(
        "RESEND_DEFAULT_SENDER",
        "Samprox ERP <no-reply@samprox.lk>",
    )
