from __future__ import annotations

import asyncio
import logging
from time import time
from typing import Any
from html import escape

from aiohttp import web
from redis.asyncio import Redis

from .bloom import RedisBloomFilter
from .config import AppSettings
from .hash_ring import ConsistentHashRing
from .models import CrawlResult, CrawlTask, DeadLetterEntry, TaskPriority, TaskStatus, WorkerHeartbeat
from .queue import RedisPriorityQueue
from .results import ResultRepository, build_result_repository
from .scheduler import ScheduleService
from .state import CheckpointManager

LOGGER = logging.getLogger(__name__)


class MasterNode:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
        self.queue = RedisPriorityQueue(
            self.redis,
            settings.master.queue_name,
            settings.master.result_queue_name,
            settings.master.dead_letter_queue_name,
        )
        self.checkpoints = CheckpointManager(self.redis, settings.resolve_path(settings.checkpoint_dir), settings.master.queue_name)
        self.bloom = RedisBloomFilter.from_capacity(
            self.redis,
            key=f"{settings.master.queue_name}:bloom",
            capacity=settings.master.bloom_capacity,
            false_positive_rate=settings.master.bloom_false_positive_rate,
            hashes=settings.master.bloom_hashes,
        )
        self.hash_ring = ConsistentHashRing()
        self.result_repo: ResultRepository = build_result_repository(
            settings.storage,
            settings.resolve_path(settings.storage.jsonl_path),
        )
        self.scheduler = ScheduleService(settings, self.submit_task)
        self.workers: dict[str, WorkerHeartbeat] = {}
        self.app = web.Application()
        self.app.add_routes(
            [
                web.post("/tasks", self.handle_submit_task),
                web.post("/heartbeat", self.handle_heartbeat),
                web.get("/workers", self.handle_workers),
                web.get("/stats", self.handle_stats),
                web.get("/health", self.handle_health),
                web.get("/dashboard", self.handle_dashboard),
            ]
        )
        self._background_tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        await self.result_repo.setup()
        await self._recover_local_checkpoints()
        self.scheduler.start()
        self._background_tasks = [
            asyncio.create_task(self.consume_results_loop(), name="consume-results"),
            asyncio.create_task(self.reap_stale_workers_loop(), name="reap-workers"),
        ]

    async def close(self) -> None:
        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        await self.scheduler.shutdown()
        await self.result_repo.close()
        await self.redis.aclose()

    async def submit_task(self, task: CrawlTask) -> bool:
        duplicate = await self.bloom.contains_or_add(task.fingerprint)
        if duplicate:
            LOGGER.info("Skip duplicate task %s", task.url)
            return False
        worker_id = self._assign_worker(task)
        task.assigned_worker = worker_id
        task.status = TaskStatus.PENDING
        await self.checkpoints.save(task)
        await self.queue.enqueue_task(worker_id, task)
        LOGGER.info("Submitted task %s to %s", task.task_id, worker_id)
        return True

    async def handle_submit_task(self, request: web.Request) -> web.Response:
        payload = await request.json()
        task = CrawlTask.create(
            url=payload["url"],
            spider=payload.get("spider", self.settings.master.default_spider),
            method=payload.get("method", "GET"),
            headers=payload.get("headers"),
            metadata=payload.get("metadata"),
            body=payload.get("body"),
            priority=payload.get("priority", TaskPriority.MEDIUM.value),
            max_retries=int(payload.get("max_retries", self.settings.worker.retries)),
        )
        accepted = await self.submit_task(task)
        return web.json_response({"accepted": accepted, "task_id": task.task_id, "worker_id": task.assigned_worker})

    async def handle_heartbeat(self, request: web.Request) -> web.Response:
        payload = await request.json()
        heartbeat = WorkerHeartbeat(
            worker_id=str(payload["worker_id"]),
            concurrency=int(payload["concurrency"]),
            active_tasks=int(payload["active_tasks"]),
            host=payload.get("host"),
            tags=list(payload.get("tags", [])),
        )
        self.workers[heartbeat.worker_id] = heartbeat
        self.hash_ring.rebuild(list(self.workers.keys()))
        return web.json_response({"ok": True, "known_workers": len(self.workers)})

    async def handle_workers(self, _: web.Request) -> web.Response:
        return web.json_response({"workers": [item.to_dict() for item in self.workers.values()]})

    async def handle_stats(self, _: web.Request) -> web.Response:
        return web.json_response(
            {
                "workers": len(self.workers),
                "queues": {
                    worker_id: {
                        priority.value: await self.redis.llen(self.queue._task_key(worker_id, priority))
                        for priority in TaskPriority
                    }
                    for worker_id in self.workers or {"default": None}
                },
                "dead_letters": await self.redis.llen(self.queue.dead_letter_queue_name),
            }
        )

    async def handle_health(self, _: web.Request) -> web.Response:
        pong = await self.redis.ping()
        return web.json_response({"status": "ok", "redis": bool(pong), "workers": len(self.workers)})

    async def handle_dashboard(self, _: web.Request) -> web.Response:
        queue_stats = await self._collect_queue_stats()
        session_count = await self._count_sessions()
        dead_letter_count = await self.redis.llen(self.queue.dead_letter_queue_name)
        html = self._render_dashboard_html(
            queue_stats=queue_stats,
            session_count=session_count,
            dead_letter_count=dead_letter_count,
        )
        return web.Response(text=html, content_type="text/html")

    async def consume_results_loop(self) -> None:
        while True:
            try:
                result = await self.queue.consume_result(timeout=5)
                if result is None:
                    continue
                await self.process_result(result)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Result consumer failed")
                await asyncio.sleep(1)

    async def process_result(self, result: CrawlResult) -> None:
        task = await self.checkpoints.load(result.task_id)
        if task is None:
            LOGGER.warning("Result for unknown task %s", result.task_id)
            return
        task.status = result.status
        await self.checkpoints.mark_status(task, result.status)
        await self.result_repo.store(result)
        if result.status == TaskStatus.FAILED:
            await self.queue.publish_dead_letter(DeadLetterEntry.from_task_result(task, result))
            LOGGER.warning(
                "Dead-lettered task task_id=%s worker_id=%s request_id=%s reason=%s",
                task.task_id,
                result.worker_id,
                result.data.get("request_id", task.metadata.get("request_id", task.task_id)),
                result.error,
            )

    async def reap_stale_workers_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.settings.master.worker_ttl_seconds)
                now = time()
                stale_workers = [
                    worker_id
                    for worker_id, heartbeat in self.workers.items()
                    if now - self._heartbeat_epoch(heartbeat.last_seen) > self.settings.master.worker_ttl_seconds
                ]
                for worker_id in stale_workers:
                    LOGGER.warning("Worker %s is stale, recovering tasks", worker_id)
                    recovered = await self.checkpoints.recover_stale(worker_id, self.settings.master.stale_task_seconds)
                    for task in recovered:
                        task.retries += 1
                        if task.retries > task.max_retries:
                            task.status = TaskStatus.FAILED
                            await self.checkpoints.mark_status(task, TaskStatus.FAILED)
                            result = CrawlResult(
                                task_id=task.task_id,
                                worker_id=worker_id,
                                status=TaskStatus.FAILED,
                                url=task.url,
                                spider=task.spider,
                                error="Task exceeded retry budget during worker recovery",
                            )
                            await self.result_repo.store(result)
                            await self.queue.publish_dead_letter(DeadLetterEntry.from_task_result(task, result))
                            continue
                        task.assigned_worker = self._assign_worker(task, exclude=worker_id)
                        await self.checkpoints.save(task)
                        await self.queue.enqueue_task(task.assigned_worker, task)
                    self.workers.pop(worker_id, None)
                self.hash_ring.rebuild(list(self.workers.keys()))
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Stale worker reaper failed")

    async def _recover_local_checkpoints(self) -> None:
        tasks = await self.checkpoints.recover_local()
        for task in tasks:
            task.assigned_worker = self._assign_worker(task)
            task.status = TaskStatus.RETRY
            await self.checkpoints.save(task)
            await self.queue.enqueue_task(task.assigned_worker, task)

    async def _collect_queue_stats(self) -> dict[str, dict[str, int]]:
        worker_keys = list(self.workers.keys()) or ["default"]
        result: dict[str, dict[str, int]] = {}
        for worker_id in worker_keys:
            result[worker_id] = {
                priority.value: await self.redis.llen(self.queue._task_key(worker_id, priority))
                for priority in TaskPriority
            }
        return result

    async def _count_sessions(self) -> int:
        pattern = f"{self.settings.session.redis_prefix}:*"
        count = 0
        async for _ in self.redis.scan_iter(match=pattern):
            count += 1
        return count

    def _render_dashboard_html(self, queue_stats: dict[str, dict[str, int]], session_count: int, dead_letter_count: int) -> str:
        worker_rows = []
        for worker_id, hb in self.workers.items():
            load = hb.active_tasks / max(hb.concurrency, 1)
            worker_rows.append(
                f"<tr><td>{escape(worker_id)}</td><td>{hb.active_tasks}</td><td>{hb.concurrency}</td><td>{load:.2f}</td><td>{escape(hb.last_seen)}</td></tr>"
            )
        if not worker_rows:
            worker_rows.append("<tr><td colspan='5'>暂无在线 Worker</td></tr>")

        queue_blocks = []
        for worker_id, levels in queue_stats.items():
            queue_blocks.append(
                "<div class='card'><h3>Queue - "
                + escape(worker_id)
                + "</h3><p>high: "
                + str(levels.get("high", 0))
                + " | medium: "
                + str(levels.get("medium", 0))
                + " | low: "
                + str(levels.get("low", 0))
                + "</p></div>"
            )
        queue_html = "\n".join(queue_blocks)

        enabled_auth_domains = ", ".join(sorted(self.settings.auth.domains.keys())) if self.settings.auth.domains else "-"
        enabled_proxy = "enabled" if self.settings.proxy.enabled else "disabled"
        enabled_session = "enabled" if self.settings.session.enabled else "disabled"
        enabled_auth = "enabled" if self.settings.auth.enabled else "disabled"

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>Distributed Crawler Dashboard</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; background:#f6f8fb; margin:0; padding:24px; color:#1f2d3d; }}
    .grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(280px,1fr)); gap:12px; margin-bottom:16px; }}
    .card {{ background:#fff; border:1px solid #dce6f4; border-radius:10px; padding:12px 14px; }}
    h1 {{ margin:0 0 14px 0; font-size:24px; }}
    h3 {{ margin:0 0 8px 0; font-size:16px; }}
    table {{ width:100%; border-collapse: collapse; background:#fff; border:1px solid #dce6f4; }}
    th, td {{ text-align:left; padding:10px; border-bottom:1px solid #eef2f8; font-size:13px; }}
    th {{ background:#f1f5fc; }}
    .muted {{ color:#607086; font-size:12px; }}
    code {{ background:#eef3fb; padding:2px 6px; border-radius:4px; }}
  </style>
</head>
<body>
  <h1>Distributed Crawler Dashboard</h1>
    <div class="grid">
    <div class="card"><h3>Workers</h3><p>{len(self.workers)}</p></div>
    <div class="card"><h3>Session Keys</h3><p>{session_count}</p></div>
    <div class="card"><h3>Dead Letters</h3><p>{dead_letter_count}</p></div>
    <div class="card"><h3>Proxy</h3><p>{enabled_proxy}</p></div>
    <div class="card"><h3>Auto Login</h3><p>{enabled_auth}</p></div>
    <div class="card"><h3>Session Sharing</h3><p>{enabled_session}</p></div>
    <div class="card"><h3>Auth Domains</h3><p>{escape(enabled_auth_domains)}</p></div>
  </div>
  <div class="grid">
    {queue_html}
  </div>
  <div class="card">
    <h3>Worker Runtime</h3>
    <table>
      <thead><tr><th>worker_id</th><th>active_tasks</th><th>concurrency</th><th>load</th><th>last_seen</th></tr></thead>
      <tbody>
        {''.join(worker_rows)}
      </tbody>
    </table>
  </div>
  <p class="muted">API endpoints: <code>/tasks</code> <code>/workers</code> <code>/stats</code> <code>/health</code></p>
</body>
</html>"""

    def _assign_worker(self, task: CrawlTask, exclude: str | None = None) -> str:
        candidates = [worker_id for worker_id in self.workers if worker_id != exclude]
        if not candidates:
            return "default"
        self.hash_ring.rebuild(candidates)
        preferred = self.hash_ring.get_node(task.url) or candidates[0]
        preferred_heartbeat = self.workers.get(preferred)
        if preferred_heartbeat is None:
            return preferred

        preferred_load = preferred_heartbeat.active_tasks / max(preferred_heartbeat.concurrency, 1)
        if preferred_load < 0.9:
            return preferred

        least_loaded = min(
            candidates,
            key=lambda worker_id: self.workers[worker_id].active_tasks / max(self.workers[worker_id].concurrency, 1),
        )
        return least_loaded

    def _heartbeat_epoch(self, timestamp: str) -> float:
        from datetime import datetime

        return datetime.fromisoformat(timestamp).timestamp()
