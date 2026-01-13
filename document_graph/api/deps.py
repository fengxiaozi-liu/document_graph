from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from document_graph.db.engine import create_engine_and_sessionmaker
from document_graph.settings import load_settings


_engine = None
_SessionLocal = None


def _ensure_db():
    global _engine, _SessionLocal
    if _engine is None or _SessionLocal is None:
        settings = load_settings()
        _engine, _SessionLocal = create_engine_and_sessionmaker(settings)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    SessionLocal = _ensure_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DBDep = Depends(get_db)

