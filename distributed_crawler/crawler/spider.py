from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .http import AsyncHttpClient
from .models import CrawlResult, CrawlTask, TaskStatus


class BaseSpider(ABC):
    name: str

    @abstractmethod
    async def execute(self, task: CrawlTask, client: AsyncHttpClient, worker_id: str) -> CrawlResult: ...


class GenericFetchSpider(BaseSpider):
    name = "generic_fetch"

    async def execute(self, task: CrawlTask, client: AsyncHttpClient, worker_id: str) -> CrawlResult:
        response = await client.fetch(task)
        data: dict[str, Any] = {
            "headers": response["headers"],
            "body": response["body"],
            "proxy": response["proxy"],
            "request_id": task.metadata.get("request_id"),
        }
        status = TaskStatus.SUCCESS if 200 <= response["status"] < 400 else TaskStatus.FAILED
        return CrawlResult(
            task_id=task.task_id,
            worker_id=worker_id,
            status=status,
            url=task.url,
            spider=self.name,
            http_status=response["status"],
            data=data,
            error=None if status == TaskStatus.SUCCESS else f"Unexpected status {response['status']}",
        )


class AssetDownloadSpider(BaseSpider):
    name = "asset_download"

    async def execute(self, task: CrawlTask, client: AsyncHttpClient, worker_id: str) -> CrawlResult:
        response = await client.fetch(task)
        parsed = urlparse(task.url)
        file_name = task.metadata.get("file_name") or Path(parsed.path or "resource.bin").name or "resource.bin"
        saved_to = await client.save_binary(task, file_name, response["body_bytes"])
        return CrawlResult(
            task_id=task.task_id,
            worker_id=worker_id,
            status=TaskStatus.SUCCESS if response["status"] < 400 else TaskStatus.FAILED,
            url=task.url,
            spider=self.name,
            http_status=response["status"],
            data={"saved_to": saved_to, "proxy": response["proxy"], "request_id": task.metadata.get("request_id")},
            error=None if response["status"] < 400 else f"Unexpected status {response['status']}",
        )


class JsonApiSpider(BaseSpider):
    name = "json_api"

    async def execute(self, task: CrawlTask, client: AsyncHttpClient, worker_id: str) -> CrawlResult:
        response = await client.fetch(task)
        body = response["body"]
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                body = {"raw": body}
        return CrawlResult(
            task_id=task.task_id,
            worker_id=worker_id,
            status=TaskStatus.SUCCESS if response["status"] < 400 else TaskStatus.FAILED,
            url=task.url,
            spider=self.name,
            http_status=response["status"],
            data={"payload": body, "proxy": response["proxy"], "request_id": task.metadata.get("request_id")},
            error=None if response["status"] < 400 else f"Unexpected status {response['status']}",
        )


def build_spider_registry() -> dict[str, BaseSpider]:
    spiders: list[BaseSpider] = [GenericFetchSpider(), AssetDownloadSpider(), JsonApiSpider()]
    return {spider.name: spider for spider in spiders}
