import importlib

import pytest

import config as config_module
from config import _normalize_db_url


@pytest.mark.parametrize(
    "env_key",
    ["PGDATABASE", "POSTGRES_DB", "POSTGRES_DATABASE", "DATABASE_NAME"],
)
def test_normalize_db_url_replaces_postgres_with_preferred_database(monkeypatch, env_key):
    for key in ("PGDATABASE", "POSTGRES_DB", "POSTGRES_DATABASE", "DATABASE_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(env_key, "railway")

    normalized = _normalize_db_url("postgresql://user:pass@host:5432/postgres")
    assert normalized.endswith("/railway")


def test_normalize_db_url_adds_database_when_missing(monkeypatch):
    for key in ("PGDATABASE", "POSTGRES_DB", "POSTGRES_DATABASE", "DATABASE_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PGDATABASE", "app_db")

    normalized = _normalize_db_url("postgres://user:pass@host:5432")
    assert normalized == "postgresql://user:pass@host:5432/app_db"


def test_normalize_db_url_returns_original_when_no_hint(monkeypatch):
    for key in ("PGDATABASE", "POSTGRES_DB", "POSTGRES_DATABASE", "DATABASE_NAME"):
        monkeypatch.delenv(key, raising=False)

    url = "postgresql://user:pass@host:5432/postgres"
    assert _normalize_db_url(url) == url


def test_normalize_db_url_leaves_sqlite_untouched(monkeypatch):
    for key in ("PGDATABASE", "POSTGRES_DB", "POSTGRES_DATABASE", "DATABASE_NAME"):
        monkeypatch.delenv(key, raising=False)

    url = "sqlite:///samprox.db"
    assert _normalize_db_url(url) == url


def test_mail_password_collapses_whitespace(monkeypatch):
    monkeypatch.setenv("MAIL_PASSWORD", "  abcd efgh ijkl mnop  ")
    importlib.reload(config_module)
    try:
        assert config_module.Config.MAIL_PASSWORD == "abcdefghijklmnop"
    finally:
        monkeypatch.delenv("MAIL_PASSWORD", raising=False)
        importlib.reload(config_module)


def test_mail_force_ipv4_flag(monkeypatch):
    monkeypatch.setenv("MAIL_FORCE_IPV4", "true")
    importlib.reload(config_module)
    try:
        assert config_module.Config.MAIL_FORCE_IPV4 is True
    finally:
        monkeypatch.delenv("MAIL_FORCE_IPV4", raising=False)
        importlib.reload(config_module)


def test_mail_max_delivery_seconds_from_env(monkeypatch):
    monkeypatch.setenv("MAIL_MAX_DELIVERY_SECONDS", "15")
    importlib.reload(config_module)
    try:
        assert config_module.Config.MAIL_MAX_DELIVERY_SECONDS == 15.0
    finally:
        monkeypatch.delenv("MAIL_MAX_DELIVERY_SECONDS", raising=False)
        importlib.reload(config_module)
