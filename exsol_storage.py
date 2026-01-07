from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid
from typing import Optional

from flask import current_app
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

from config import _normalize_db_url
from models import GUID

ExsolBase = declarative_base()


class ExsolStorageUnavailable(RuntimeError):
    """Raised when Exsol storage cannot be reached."""

    pass


@dataclass
class ExsolStorage:
    engine: Optional[object] = None
    session_factory: Optional[scoped_session] = None
    schema_name: Optional[str] = None
    error: Optional[str] = None
    used_url: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        return self.error is None and self.engine is not None and self.session_factory is not None

    def session(self):
        if not self.is_ready or self.session_factory is None:
            raise ExsolStorageUnavailable(self.error or "Exsol storage is not available.")
        return self.session_factory()

    def remove(self) -> None:
        if self.session_factory:
            try:
                self.session_factory.remove()
            except Exception:
                pass


class ExsolStockItem(ExsolBase):
    __tablename__ = "exsol_stock_items"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    item_code = Column(String(120), nullable=False, unique=True, index=True)
    item_name = Column(String(255), nullable=False)
    category = Column(String(120), nullable=True)
    hp = Column(Numeric(10, 2), nullable=True)
    size = Column(String(120), nullable=True)
    voltage = Column(String(80), nullable=True)
    pressure_bar = Column(Numeric(10, 2), nullable=True)
    variant = Column(String(120), nullable=True)
    unit = Column(String(40), nullable=False, default="NOS", server_default="NOS")
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ExsolProductionEntry(ExsolBase):
    __tablename__ = "exsol_production_entries"
    __table_args__ = (
        UniqueConstraint("company", "serial_number", name="uq_exsol_production_company_serial"),
        Index("ix_exsol_production_company_date", "company", "production_date"),
        Index("ix_exsol_production_company_item_date", "company", "item_code", "production_date"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    company = Column(String(255), nullable=False)
    production_date = Column(Date, nullable=False)
    item_code = Column(String(120), nullable=False)
    item_name = Column(String(255), nullable=False)
    serial_number = Column(String(8), nullable=False)
    production_shift = Column(String(20), nullable=False)
    remarks = Column(Text, nullable=True)
    created_by = Column(Integer, nullable=False)
    created_role = Column(String(120), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    is_confirmed = Column(Boolean, nullable=False, default=False, server_default="0")
    confirmed_by = Column(Integer, nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)


def _ensure_schema(engine, schema_name: str) -> None:
    if not schema_name or engine.dialect.name != "postgresql":
        return

    sanitized = schema_name.replace('"', "")
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{sanitized}"'))


def _apply_schema_to_metadata(schema_name: Optional[str]) -> None:
    for table in ExsolBase.metadata.tables.values():
        table.schema = schema_name


def init_exsol_storage(app) -> ExsolStorage:
    """Initialise the Exsol storage engine and session factory."""

    storage = ExsolStorage()
    configured_url = app.config.get("EXSOL_DATABASE_URL")
    fallback_url = app.config.get("SQLALCHEMY_DATABASE_URI")

    if not configured_url and not fallback_url:
        storage.error = "Exsol storage is not configured."
        return storage

    target_url = _normalize_db_url(configured_url) if configured_url else fallback_url
    schema_name = (app.config.get("EXSOL_SCHEMA") or "exsol").strip() or "exsol"
    use_schema = not configured_url

    try:
        engine = create_engine(target_url, future=True, pool_pre_ping=True)
        storage.used_url = target_url
        schema_to_apply: Optional[str] = None

        if use_schema and engine.dialect.name == "postgresql":
            _ensure_schema(engine, schema_name)
            schema_to_apply = schema_name

        _apply_schema_to_metadata(schema_to_apply)
        ExsolBase.metadata.create_all(engine)

        Session = scoped_session(sessionmaker(bind=engine, autoflush=False))
        storage.engine = engine
        storage.session_factory = Session
        storage.schema_name = schema_to_apply
    except (OperationalError, ProgrammingError) as exc:
        storage.error = f"Exsol storage unavailable: {exc}"
        app.logger.error(storage.error)
    except Exception as exc:  # pragma: no cover - defensive guard
        storage.error = f"Exsol storage unavailable: {exc}"
        app.logger.error(storage.error)

    app.extensions["exsol_storage"] = storage

    @app.teardown_appcontext
    def _cleanup_exsol_session(_exc):
        storage.remove()

    return storage


def get_exsol_storage(app=None) -> ExsolStorage:
    target_app = app or current_app
    storage: ExsolStorage | None = None
    if target_app:
        storage = target_app.extensions.get("exsol_storage")

    if not storage:
        raise ExsolStorageUnavailable("Exsol storage has not been initialized.")
    if not storage.is_ready:
        raise ExsolStorageUnavailable(storage.error or "Exsol storage is not available.")
    return storage
