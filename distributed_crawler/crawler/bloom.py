from __future__ import annotations

import math
from hashlib import md5, sha1

from redis.asyncio import Redis


class RedisBloomFilter:
    def __init__(self, redis: Redis, key: str, bits: int = 8_000_000, hashes: int = 6) -> None:
        self.redis = redis
        self.key = key
        self.bits = max(int(bits), 1)
        self.hashes = max(int(hashes), 1)

    @classmethod
    def from_capacity(
        cls,
        redis: Redis,
        key: str,
        *,
        capacity: int,
        false_positive_rate: float = 0.01,
        hashes: int | None = None,
    ) -> "RedisBloomFilter":
        capacity = max(int(capacity), 1)
        false_positive_rate = min(max(float(false_positive_rate), 1e-9), 0.999999)
        bits = math.ceil(-(capacity * math.log(false_positive_rate)) / (math.log(2) ** 2))
        optimal_hashes = max(1, round((bits / capacity) * math.log(2)))
        return cls(redis, key, bits=bits, hashes=hashes or optimal_hashes)

    def _positions(self, value: str) -> list[int]:
        digest_a = int(md5(value.encode("utf-8")).hexdigest(), 16)
        digest_b = int(sha1(value.encode("utf-8")).hexdigest(), 16)
        return [((digest_a + index * digest_b) % self.bits) for index in range(self.hashes)]

    async def contains_or_add(self, value: str) -> bool:
        positions = self._positions(value)
        pipeline = self.redis.pipeline()
        for position in positions:
            pipeline.getbit(self.key, position)
        existing = await pipeline.execute()
        seen = all(bit == 1 for bit in existing)

        pipeline = self.redis.pipeline()
        for position in positions:
            pipeline.setbit(self.key, position, 1)
        await pipeline.execute()
        return seen
