from __future__ import annotations

import json

from redis.asyncio import Redis

from .models import CrawlResult, CrawlTask, DeadLetterEntry, TaskPriority


class RedisPriorityQueue:
    def __init__(self, redis: Redis, namespace: str, result_queue_name: str, dead_letter_queue_name: str | None = None) -> None:
        self.redis = redis
        self.namespace = namespace
        self.result_queue_name = result_queue_name
        self.dead_letter_queue_name = dead_letter_queue_name or f"{namespace}:dead_letter"

    def _task_key(self, worker_id: str, priority: TaskPriority) -> str:
        return f"{self.namespace}:tasks:{worker_id}:{priority.value}"

    async def enqueue_task(self, worker_id: str, task: CrawlTask) -> None:
        await self.redis.rpush(self._task_key(worker_id, task.priority), json.dumps(task.to_dict()))

    async def dequeue_task(self, worker_id: str, timeout: int = 5) -> CrawlTask | None:
        keys = [self._task_key(worker_id, priority) for priority in (TaskPriority.HIGH, TaskPriority.MEDIUM, TaskPriority.LOW)]
        result = await self.redis.blpop(keys, timeout=timeout)
        if not result:
            return None
        _, payload = result
        return CrawlTask.from_dict(json.loads(payload))

    async def publish_result(self, result: CrawlResult) -> None:
        await self.redis.rpush(self.result_queue_name, json.dumps(result.to_dict()))

    async def consume_result(self, timeout: int = 5) -> CrawlResult | None:
        result = await self.redis.blpop(self.result_queue_name, timeout=timeout)
        if not result:
            return None
        _, payload = result
        return CrawlResult.from_dict(json.loads(payload))

    async def publish_dead_letter(self, entry: DeadLetterEntry) -> None:
        await self.redis.rpush(self.dead_letter_queue_name, json.dumps(entry.to_dict(), ensure_ascii=False))
