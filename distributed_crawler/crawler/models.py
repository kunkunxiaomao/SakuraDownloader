from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha1
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def make_url_hash(url: str) -> str:
    return sha1(url.encode("utf-8")).hexdigest()


class TaskPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"


@dataclass
class CrawlTask:
    task_id: str
    url: str
    spider: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    body: str | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    retries: int = 0
    max_retries: int = 4
    assigned_worker: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    schedule_name: str | None = None

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc

    @property
    def fingerprint(self) -> str:
        payload = f"{self.method}:{self.url}:{self.spider}:{self.body or ''}"
        return sha1(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["priority"] = self.priority.value
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CrawlTask":
        return cls(
            task_id=str(payload["task_id"]),
            url=str(payload["url"]),
            spider=str(payload["spider"]),
            method=str(payload.get("method", "GET")),
            headers=dict(payload.get("headers", {})),
            metadata=dict(payload.get("metadata", {})),
            body=payload.get("body"),
            priority=TaskPriority(payload.get("priority", TaskPriority.MEDIUM.value)),
            status=TaskStatus(payload.get("status", TaskStatus.PENDING.value)),
            retries=int(payload.get("retries", 0)),
            max_retries=int(payload.get("max_retries", 4)),
            assigned_worker=payload.get("assigned_worker"),
            created_at=str(payload.get("created_at", utc_now())),
            updated_at=str(payload.get("updated_at", utc_now())),
            schedule_name=payload.get("schedule_name"),
        )

    @classmethod
    def create(
        cls,
        *,
        url: str,
        spider: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        body: str | None = None,
        priority: str = "medium",
        max_retries: int = 4,
        schedule_name: str | None = None,
    ) -> "CrawlTask":
        return cls(
            task_id=uuid4().hex,
            url=url,
            spider=spider,
            method=method.upper(),
            headers=headers or {},
            metadata=metadata or {},
            body=body,
            priority=TaskPriority(priority),
            max_retries=max_retries,
            schedule_name=schedule_name,
        )


@dataclass
class CrawlResult:
    task_id: str
    worker_id: str
    status: TaskStatus
    url: str
    spider: str
    http_status: int | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    finished_at: str = field(default_factory=utc_now)

    @property
    def url_hash(self) -> str:
        return make_url_hash(self.url)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["url_hash"] = self.url_hash
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CrawlResult":
        return cls(
            task_id=str(payload["task_id"]),
            worker_id=str(payload["worker_id"]),
            status=TaskStatus(payload["status"]),
            url=str(payload["url"]),
            spider=str(payload["spider"]),
            http_status=payload.get("http_status"),
            data=dict(payload.get("data", {})),
            error=payload.get("error"),
            finished_at=str(payload.get("finished_at", utc_now())),
        )


@dataclass
class DeadLetterEntry:
    task: dict[str, Any]
    result: dict[str, Any] | None = None
    reason: str = ""
    failed_at: str = field(default_factory=utc_now)
    last_error: str | None = None
    response_status: int | None = None
    retry_count: int = 0
    worker_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_task_result(cls, task: CrawlTask, result: CrawlResult, reason: str | None = None) -> "DeadLetterEntry":
        return cls(
            task=task.to_dict(),
            result=result.to_dict(),
            reason=reason or result.error or "Task failed",
            last_error=result.error,
            response_status=result.http_status,
            retry_count=task.retries,
            worker_id=result.worker_id,
        )


@dataclass
class WorkerHeartbeat:
    worker_id: str
    concurrency: int
    active_tasks: int
    last_seen: str = field(default_factory=utc_now)
    host: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
