from __future__ import annotations

import json
from pathlib import Path

from redis.asyncio import Redis

from .models import CrawlTask, TaskStatus, utc_now


class CheckpointManager:
    def __init__(self, redis: Redis, checkpoint_dir: Path, namespace: str) -> None:
        self.redis = redis
        self.checkpoint_dir = checkpoint_dir
        self.namespace = namespace
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _task_key(self, task_id: str) -> str:
        return f"{self.namespace}:task:{task_id}"

    def _running_key(self, worker_id: str) -> str:
        return f"{self.namespace}:running:{worker_id}"

    def _file_path(self, task_id: str) -> Path:
        return self.checkpoint_dir / f"{task_id}.json"

    async def save(self, task: CrawlTask) -> None:
        task.updated_at = utc_now()
        await self.redis.set(self._task_key(task.task_id), json.dumps(task.to_dict()))
        await self._write_file(task)

    async def mark_status(self, task: CrawlTask, status: TaskStatus) -> None:
        task.status = status
        await self.save(task)
        if task.assigned_worker:
            running_key = self._running_key(task.assigned_worker)
            if status == TaskStatus.RUNNING:
                await self.redis.zadd(running_key, {task.task_id: self._score_now()})
            elif status in {TaskStatus.SUCCESS, TaskStatus.FAILED}:
                await self.redis.zrem(running_key, task.task_id)
                self._cleanup_file(task.task_id)

    async def load(self, task_id: str) -> CrawlTask | None:
        raw = await self.redis.get(self._task_key(task_id))
        if raw:
            return CrawlTask.from_dict(json.loads(raw))
        file_path = self._file_path(task_id)
        if file_path.exists():
            return CrawlTask.from_dict(json.loads(file_path.read_text(encoding="utf-8")))
        return None

    async def recover_stale(self, worker_id: str, older_than_seconds: int) -> list[CrawlTask]:
        threshold = self._score_now() - older_than_seconds
        task_ids = await self.redis.zrangebyscore(self._running_key(worker_id), min=0, max=threshold)
        tasks: list[CrawlTask] = []
        for task_id in task_ids:
            task = await self.load(task_id)
            if task is None:
                continue
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.RETRY
                tasks.append(task)
        return tasks

    async def recover_local(self) -> list[CrawlTask]:
        tasks: list[CrawlTask] = []
        for file_path in self.checkpoint_dir.glob("*.json"):
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            task = CrawlTask.from_dict(payload)
            if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRY}:
                tasks.append(task)
        return tasks

    async def _write_file(self, task: CrawlTask) -> None:
        self._file_path(task.task_id).write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _cleanup_file(self, task_id: str) -> None:
        file_path = self._file_path(task_id)
        if file_path.exists():
            file_path.unlink()

    def _score_now(self) -> float:
        from time import time

        return time()
