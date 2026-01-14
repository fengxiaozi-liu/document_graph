from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Any

import redis


def redis_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, decode_responses=True)


def _messages_key(conversation_id: str) -> str:
    return f"convo:{conversation_id}:messages"


def _summary_key(conversation_id: str) -> str:
    return f"convo:{conversation_id}:summary"


def cache_append_message(
    r: redis.Redis,
    *,
    conversation_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
    max_messages: int = 50,
    ttl_s: int | None = None,
) -> None:
    payload = {"role": role, "content": content, "metadata": metadata or {}}
    key = _messages_key(conversation_id)
    pipe = r.pipeline()
    pipe.rpush(key, json.dumps(payload, ensure_ascii=False))
    pipe.ltrim(key, -int(max_messages), -1)
    if ttl_s:
        pipe.expire(key, int(ttl_s))
    pipe.execute()


def cache_get_recent_messages(
    r: redis.Redis, *, conversation_id: str, limit: int = 50
) -> list[dict[str, Any]] | None:
    key = _messages_key(conversation_id)
    try:
        items = r.lrange(key, -int(limit), -1)
    except Exception:
        return None
    if not items:
        return None
    out: list[dict[str, Any]] = []
    for raw in items:
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


def cache_set_summary(r: redis.Redis, *, conversation_id: str, summary: str, ttl_s: int | None = None) -> None:
    key = _summary_key(conversation_id)
    if ttl_s:
        r.set(key, summary, ex=int(ttl_s))
    else:
        r.set(key, summary)


def cache_get_summary(r: redis.Redis, *, conversation_id: str) -> str | None:
    try:
        return r.get(_summary_key(conversation_id))
    except Exception:
        return None


@contextmanager
def conversation_lock(r: redis.Redis, *, conversation_id: str, ttl_s: int = 60):
    key = f"lock:conversation:{conversation_id}"
    token = str(uuid.uuid4())
    ok = r.set(key, token, nx=True, ex=ttl_s)
    if not ok:
        raise RuntimeError("conversation_locked")
    try:
        yield
    finally:
        try:
            current = r.get(key)
            if current == token:
                r.delete(key)
        except Exception:
            # Best-effort; TTL is the safety net.
            pass

