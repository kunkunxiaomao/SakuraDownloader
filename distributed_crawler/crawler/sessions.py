from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from redis.asyncio import Redis


class DualSessionStore:
    def __init__(self, redis: Redis, redis_prefix: str, backup_path: Path) -> None:
        self.redis = redis
        self.redis_prefix = redis_prefix
        self.backup_path = backup_path
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)

    def _key(self, domain: str) -> str:
        return f"{self.redis_prefix}:{domain}"

    async def load(self, domain: str) -> dict[str, Any]:
        raw = await self.redis.get(self._key(domain))
        if raw:
            return json.loads(raw)
        if self.backup_path.exists():
            data = json.loads(self.backup_path.read_text(encoding="utf-8"))
            return data.get(domain, {})
        return {}

    async def save(self, domain: str, payload: dict[str, Any]) -> None:
        await self.redis.set(self._key(domain), json.dumps(payload))
        snapshot: dict[str, Any] = {}
        if self.backup_path.exists():
            snapshot = json.loads(self.backup_path.read_text(encoding="utf-8"))
        snapshot[domain] = payload
        self.backup_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
