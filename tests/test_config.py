import pytest

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
