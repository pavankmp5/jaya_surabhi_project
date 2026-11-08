"""Database engine and request-scoped SQLAlchemy sessions."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import database_url


def _engine_options(url: str) -> dict[str, object]:
    if url == "sqlite://":
        return {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


engine = create_engine(database_url(), future=True, **_engine_options(database_url()))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for persisted POS records."""


def get_session() -> Generator[Session, None, None]:
    """Yield a transaction-capable session and always close it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
