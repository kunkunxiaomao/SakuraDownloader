from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from .config import RateLimitSettings


@dataclass
class TokenBucket:
    qps: float
    burst: int
    tokens: float
    updated_at: float


class DomainRateLimiter:
    def __init__(self, settings: RateLimitSettings) -> None:
        self.settings = settings
        self._buckets: dict[str, TokenBucket] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, domain: str) -> None:
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            bucket = self._buckets.setdefault(domain, self._new_bucket(domain))
            while True:
                now = monotonic()
                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(bucket.burst, bucket.tokens + elapsed * bucket.qps)
                bucket.updated_at = now
                if bucket.tokens >= 1:
                    bucket.tokens -= 1
                    return
                await asyncio.sleep(max(0.05, 1 / bucket.qps))

    def _new_bucket(self, domain: str) -> TokenBucket:
        domain_settings = self.settings.per_domain.get(domain)
        qps = domain_settings.qps if domain_settings else self.settings.default_qps
        burst = domain_settings.burst if domain_settings else self.settings.default_burst
        return TokenBucket(qps=qps, burst=burst, tokens=float(burst), updated_at=monotonic())
