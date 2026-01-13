from __future__ import annotations

import os
from dataclasses import dataclass


def _env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is None or value == "":
        return default
    return value


@dataclass(frozen=True)
class PostgresSettings:
    url: str


@dataclass(frozen=True)
class RedisSettings:
    url: str


@dataclass(frozen=True)
class AppSettings:
    postgres: PostgresSettings
    redis: RedisSettings


def load_settings() -> AppSettings:
    # Keep settings minimal for MVP; model/provider settings stay in existing config.yaml for now.
    pg_url = _env("POSTGRES_URL", "postgresql+psycopg2://document_graph:document_graph@localhost:5432/document_graph")
    redis_url = _env("REDIS_URL", "redis://localhost:6379/0")
    if not pg_url:
        raise RuntimeError("POSTGRES_URL is required")
    if not redis_url:
        raise RuntimeError("REDIS_URL is required")
    return AppSettings(postgres=PostgresSettings(url=pg_url), redis=RedisSettings(url=redis_url))

