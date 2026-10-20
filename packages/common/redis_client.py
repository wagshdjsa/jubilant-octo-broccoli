"""Redis 客户端封装（配额 / 缓存 / 会话）"""
from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis
import redis as sync_redis

from .config import get_settings


_sync_client: Optional[sync_redis.Redis] = None


def get_sync_redis() -> sync_redis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = sync_redis.from_url(get_settings().redis_url, decode_responses=True)
    return _sync_client


_async_client: Optional[aioredis.Redis] = None


def get_async_redis() -> aioredis.Redis:
    global _async_client
    if _async_client is None:
        _async_client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _async_client


class TokenBucket:
    """简化版 token bucket（per key）"""

    @staticmethod
    def check_and_consume(key: str, capacity: int, refill_per_sec: float, cost: float = 1.0) -> bool:
        """返回是否允许本次扣减"""
        r = get_sync_redis()
        with r.pipeline() as p:
            p.hgetall(key)
            data = p.execute()[0] or {}

        tokens = float(data.get("tokens", capacity))
        ts = float(data.get("ts", 0))

        import time
        now = time.time()
        tokens = min(capacity, tokens + (now - ts) * refill_per_sec)

        if tokens >= cost:
            tokens -= cost
            r.hset(key, mapping={"tokens": tokens, "ts": now})
            r.expire(key, 60)
            return True
        else:
            r.hset(key, mapping={"tokens": tokens, "ts": now})
            r.expire(key, 60)
            return False