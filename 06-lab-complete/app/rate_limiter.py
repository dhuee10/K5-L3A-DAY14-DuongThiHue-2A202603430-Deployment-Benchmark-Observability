"""
Rate limiting (tiêu chí 7) — sliding window log bằng Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis nên mọi worker / instance dùng chung và vẫn còn sau
    khi restart agent. Key Redis dùng hash của API key, không lưu key gốc.
"""
import hashlib
import math
import time
import uuid

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(key: str) -> str:
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:24]


def check_rate_limit(key: str) -> int:
    """Ghi nhận 1 request của `key`. Vượt giới hạn → 429. Trả về số request còn lại."""
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"
    try:
        # ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE trong 1 transaction (MULTI/EXEC)
        pipe = r.pipeline(transaction=True)
        pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)
        pipe.zcard(bucket)
        pipe.zadd(bucket, {member: now})
        pipe.expire(bucket, WINDOW_SECONDS + 1)
        _, count, _, _ = pipe.execute()
    except (redis.RedisError, OSError):
        # Không có Redis thì không đếm được → từ chối an toàn (fail closed)
        raise HTTPException(status_code=503, detail="Rate limiter unavailable, try again later.",
                            headers={"Retry-After": "5"})

    if count >= limit:
        # Request này không được tính vào cửa sổ
        r.zrem(bucket, member)
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        retry_after = WINDOW_SECONDS
        if oldest:
            retry_after = max(1, math.ceil(oldest[0][1] + WINDOW_SECONDS - now))
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {limit} requests per {WINDOW_SECONDS}s.",
            headers={"Retry-After": str(retry_after),
                     "X-RateLimit-Limit": str(limit),
                     "X-RateLimit-Remaining": "0"},
        )
    return limit - count - 1
