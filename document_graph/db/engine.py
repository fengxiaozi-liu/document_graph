from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from document_graph.settings import AppSettings


def create_engine_and_sessionmaker(settings: AppSettings):
    engine = create_engine(settings.postgres.url, pool_pre_ping=True, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine, SessionLocal

