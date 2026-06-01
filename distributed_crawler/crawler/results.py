from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

import asyncpg
from motor.motor_asyncio import AsyncIOMotorClient

from .config import StorageSettings
from .models import CrawlResult, make_url_hash


class ResultRepository(ABC):
    @abstractmethod
    async def setup(self) -> None: ...

    @abstractmethod
    async def store(self, result: CrawlResult) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class JsonlResultRepository(ResultRepository):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.index_path = path.with_suffix(path.suffix + ".url_hashes")
        self._seen_url_hashes: set[str] = set()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def setup(self) -> None:
        if self.index_path.exists():
            self._seen_url_hashes = {
                line.strip()
                for line in self.index_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
            return
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as file_handle:
                for line in file_handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    url_hash = payload.get("url_hash") or (make_url_hash(str(payload["url"])) if payload.get("url") else None)
                    if url_hash:
                        self._seen_url_hashes.add(str(url_hash))
            if self._seen_url_hashes:
                self.index_path.write_text("\n".join(sorted(self._seen_url_hashes)) + "\n", encoding="utf-8")

    async def store(self, result: CrawlResult) -> None:
        if result.url_hash in self._seen_url_hashes:
            return
        with self.path.open("a", encoding="utf-8") as file_handle:
            file_handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        with self.index_path.open("a", encoding="utf-8") as file_handle:
            file_handle.write(result.url_hash + "\n")
        self._seen_url_hashes.add(result.url_hash)

    async def close(self) -> None:
        return None


class MongoResultRepository(ResultRepository):
    def __init__(self, settings: StorageSettings) -> None:
        self.client = AsyncIOMotorClient(settings.mongodb_dsn)
        self.collection = self.client[settings.mongodb_database][settings.mongodb_collection]

    async def setup(self) -> None:
        await self.collection.create_index("task_id", unique=False)
        await self.collection.create_index(
            "url_hash",
            unique=True,
            partialFilterExpression={"url_hash": {"$exists": True}},
        )

    async def store(self, result: CrawlResult) -> None:
        await self.collection.update_one(
            {"url_hash": result.url_hash},
            {"$set": result.to_dict()},
            upsert=True,
        )

    async def close(self) -> None:
        self.client.close()


class PostgresResultRepository(ResultRepository):
    def __init__(self, settings: StorageSettings) -> None:
        self.dsn = settings.postgres_dsn
        self.table = settings.postgres_table
        self.pool: asyncpg.Pool | None = None

    async def setup(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn)
        async with self.pool.acquire() as connection:
            await connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    id BIGSERIAL PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    url TEXT NOT NULL,
                    url_hash TEXT NOT NULL,
                    spider TEXT NOT NULL,
                    http_status INTEGER,
                    payload JSONB NOT NULL,
                    error TEXT,
                    finished_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            await connection.execute(f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS url_hash TEXT")
            await connection.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {self.table}_url_hash_uidx ON {self.table} (url_hash)"
            )

    async def store(self, result: CrawlResult) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres repository is not initialized")
        async with self.pool.acquire() as connection:
            await connection.execute(
                f"""
                INSERT INTO {self.table}
                (task_id, worker_id, status, url, url_hash, spider, http_status, payload, error, finished_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::timestamptz)
                ON CONFLICT (url_hash) DO UPDATE SET
                    task_id = EXCLUDED.task_id,
                    worker_id = EXCLUDED.worker_id,
                    status = EXCLUDED.status,
                    url = EXCLUDED.url,
                    spider = EXCLUDED.spider,
                    http_status = EXCLUDED.http_status,
                    payload = EXCLUDED.payload,
                    error = EXCLUDED.error,
                    finished_at = EXCLUDED.finished_at
                """,
                result.task_id,
                result.worker_id,
                result.status.value,
                result.url,
                result.url_hash,
                result.spider,
                result.http_status,
                json.dumps(result.data, ensure_ascii=False),
                result.error,
                result.finished_at,
            )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()


def build_result_repository(settings: StorageSettings, jsonl_path: Path) -> ResultRepository:
    backend = settings.backend.lower()
    if backend == "mongodb":
        return MongoResultRepository(settings)
    if backend == "postgresql":
        return PostgresResultRepository(settings)
    return JsonlResultRepository(jsonl_path)
