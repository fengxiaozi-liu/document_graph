from __future__ import annotations

import time
import uuid
from contextlib import contextmanager

import redis


def redis_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, decode_responses=True)


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

