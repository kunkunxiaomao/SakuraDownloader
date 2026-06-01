from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path
from uuid import uuid4

import aiohttp
from redis.asyncio import Redis

from .auth import ConfigurableAuthProvider, ManualAuthProvider
from .backoff import exponential_backoff
from .config import AppSettings
from .http import AsyncHttpClient
from .models import CrawlResult, CrawlTask, TaskStatus, WorkerHeartbeat
from .proxy import ProxyPool
from .queue import RedisPriorityQueue
from .rate_limit import DomainRateLimiter
from .sessions import DualSessionStore
from .signatures import NoopSignatureProvider
from .spider import BaseSpider, build_spider_registry
from .state import CheckpointManager

LOGGER = logging.getLogger(__name__)


class WorkerNode:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.worker_id = settings.worker.worker_id
        self.redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
        self.queue = RedisPriorityQueue(
            self.redis,
            settings.master.queue_name,
            settings.master.result_queue_name,
            settings.master.dead_letter_queue_name,
        )
        self.checkpoints = CheckpointManager(self.redis, settings.resolve_path(settings.checkpoint_dir), settings.master.queue_name)
        self.rate_limiter = DomainRateLimiter(settings.rate_limit)
        self.proxy_pool = ProxyPool(settings.proxy)
        self.session_store = DualSessionStore(
            self.redis,
            settings.session.redis_prefix,
            settings.resolve_path(settings.session.local_backup_path),
        )
        auth_provider = ConfigurableAuthProvider(settings.auth) if settings.auth.enabled else ManualAuthProvider()
        self.client = AsyncHttpClient(
            settings,
            self.rate_limiter,
            self.proxy_pool,
            self.session_store,
            auth_provider,
            NoopSignatureProvider(),
        )
        self.spiders: dict[str, BaseSpider] = build_spider_registry()
        self.active_tasks: set[str] = set()
        self.semaphore = asyncio.Semaphore(settings.worker.concurrency)
        self.master_base_url = f"http://{settings.master.host}:{settings.master.port}"
        self._loops: list[asyncio.Task] = []
        self._http_session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._http_session = aiohttp.ClientSession()
        await self.client.start()
        if self.settings.proxy.enabled:
            await self.proxy_pool.refresh()
            await self.proxy_pool.verify()
        recovered = await self.checkpoints.recover_local()
        for task in recovered:
            if task.assigned_worker in {None, self.worker_id, "default"}:
                await self.queue.enqueue_task(self.worker_id, task)
        self._loops = [
            asyncio.create_task(self.consume_loop(), name=f"{self.worker_id}-consume"),
            asyncio.create_task(self.heartbeat_loop(), name=f"{self.worker_id}-heartbeat"),
            asyncio.create_task(self.proxy_refresh_loop(), name=f"{self.worker_id}-proxy-refresh"),
        ]

    async def close(self) -> None:
        for task in self._loops:
            task.cancel()
        await asyncio.gather(*self._loops, return_exceptions=True)
        await self.client.close()
        if self._http_session is not None:
            await self._http_session.close()
        await self.redis.aclose()

    async def consume_loop(self) -> None:
        while True:
            try:
                task = await self.queue.dequeue_task(self.worker_id, timeout=5)
                if task is None:
                    task = await self.queue.dequeue_task("default", timeout=1)
                if task is None:
                    continue
                await self.semaphore.acquire()
                asyncio.create_task(self.execute_task(task))
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Worker %s consume loop failed", self.worker_id)
                await asyncio.sleep(1)

    async def execute_task(self, task: CrawlTask) -> None:
        self.active_tasks.add(task.task_id)
        try:
            task.assigned_worker = self.worker_id
            task.metadata.setdefault("task_id", task.task_id)
            task.metadata["worker_id"] = self.worker_id
            task.metadata["request_id"] = f"{task.task_id}-{task.retries}-{uuid4().hex[:8]}"
            await self.checkpoints.mark_status(task, TaskStatus.RUNNING)
            spider = self.spiders.get(task.spider)
            if spider is None:
                raise RuntimeError(f"Unknown spider: {task.spider}")
            result = await spider.execute(task, self.client, self.worker_id)
            if result.status == TaskStatus.FAILED and task.retries < task.max_retries:
                await self._retry_task(task, result.error or "worker failure")
            else:
                await self.queue.publish_result(result)
        except Exception as exc:
            LOGGER.exception("Task %s failed", task.task_id)
            if task.retries < task.max_retries:
                await self._retry_task(task, str(exc))
            else:
                result = CrawlResult(
                    task_id=task.task_id,
                    worker_id=self.worker_id,
                    status=TaskStatus.FAILED,
                    url=task.url,
                    spider=task.spider,
                    error=str(exc),
                )
                await self.queue.publish_result(result)
        finally:
            self.active_tasks.discard(task.task_id)
            self.semaphore.release()

    async def heartbeat_loop(self) -> None:
        while True:
            try:
                await self.send_heartbeat()
                await asyncio.sleep(self.settings.worker.heartbeat_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Worker %s heartbeat failed", self.worker_id)
                await asyncio.sleep(2)

    async def send_heartbeat(self) -> None:
        if self._http_session is None:
            return
        heartbeat = WorkerHeartbeat(
            worker_id=self.worker_id,
            concurrency=self.settings.worker.concurrency,
            active_tasks=len(self.active_tasks),
            host=socket.gethostname(),
        )
        async with self._http_session.post(f"{self.master_base_url}/heartbeat", json=heartbeat.to_dict()) as response:
            response.raise_for_status()

    async def proxy_refresh_loop(self) -> None:
        if not self.settings.proxy.enabled:
            return
        while True:
            try:
                await asyncio.sleep(self.settings.proxy.refresh_interval_seconds)
                await self.proxy_pool.refresh()
                await self.proxy_pool.verify()
                LOGGER.info("Worker %s healthy proxies: %s", self.worker_id, await self.proxy_pool.healthy_count())
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Proxy refresh loop failed")

    async def _retry_task(self, task: CrawlTask, reason: str) -> None:
        task.retries += 1
        task.status = TaskStatus.RETRY
        await self.checkpoints.save(task)
        delay = exponential_backoff(
            task.retries - 1,
            self.settings.worker.base_backoff_seconds,
            self.settings.worker.max_backoff_seconds,
        )
        LOGGER.warning("Retry task %s in %.2fs: %s", task.task_id, delay, reason)
        await asyncio.sleep(delay)
        await self.queue.enqueue_task(self.worker_id, task)
