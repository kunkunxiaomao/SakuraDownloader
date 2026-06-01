from __future__ import annotations

import json
import random
from pathlib import Path
from time import monotonic
from typing import Any

import aiohttp

from .auth import AuthProvider
from .circuit_breaker import DomainCircuitBreaker
from .config import AppSettings
from .models import CrawlTask
from .proxy import ProxyPool
from .rate_limit import DomainRateLimiter
from .sessions import DualSessionStore
from .signatures import SignatureProvider


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edg/126.0.0.0 Safari/537.36",
]


class AsyncHttpClient:
    def __init__(
        self,
        settings: AppSettings,
        rate_limiter: DomainRateLimiter,
        proxy_pool: ProxyPool,
        session_store: DualSessionStore,
        auth_provider: AuthProvider,
        signature_provider: SignatureProvider,
    ) -> None:
        self.settings = settings
        self.rate_limiter = rate_limiter
        self.proxy_pool = proxy_pool
        self.session_store = session_store
        self.auth_provider = auth_provider
        self.signature_provider = signature_provider
        self.session: aiohttp.ClientSession | None = None
        self.circuit_breaker = DomainCircuitBreaker(settings.circuit_breaker)

    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(
            total=self.settings.worker.request_timeout_seconds,
            connect=self.settings.worker.connect_timeout_seconds,
        )
        connector = aiohttp.TCPConnector(limit=self.settings.worker.concurrency, ssl=False)
        self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()

    async def fetch(self, task: CrawlTask) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("HTTP client is not started")

        await self.rate_limiter.acquire(task.domain)
        self.circuit_breaker.before_request(task.domain)
        proxy = await self.proxy_pool.acquire()
        session_payload = await self.session_store.load(task.domain)
        if self.settings.session.enabled and session_payload.get("expired"):
            session_payload = await self.auth_provider.refresh_session(task.domain, session_payload)
            await self.session_store.save(task.domain, session_payload)

        headers = dict(task.headers)
        headers.setdefault("User-Agent", random.choice(USER_AGENTS))
        headers.setdefault("Accept", "*/*")
        if task.metadata.get("referer"):
            headers.setdefault("Referer", str(task.metadata["referer"]))
        if task.metadata.get("origin"):
            headers.setdefault("Origin", str(task.metadata["origin"]))
        headers.setdefault("X-Task-Id", task.task_id)
        if task.metadata.get("request_id"):
            headers.setdefault("X-Request-Id", str(task.metadata["request_id"]))
        if task.metadata.get("worker_id"):
            headers.setdefault("X-Worker-Id", str(task.metadata["worker_id"]))
        cookie_header = session_payload.get("cookie")
        if cookie_header:
            headers.setdefault("Cookie", str(cookie_header))

        signature_headers = await self.signature_provider.sign(
            {
                "url": task.url,
                "method": task.method,
                "body": task.body,
                "metadata": task.metadata,
            }
        )
        headers.update(signature_headers)

        started_at = monotonic()
        try:
            async with self.session.request(
                task.method,
                task.url,
                headers=headers,
                data=task.body.encode("utf-8") if isinstance(task.body, str) else None,
                proxy=proxy.url if proxy else None,
            ) as response:
                content_type = response.headers.get("Content-Type", "")
                body_bytes = await response.read()
                if "application/json" in content_type:
                    body: Any = json.loads(body_bytes.decode("utf-8", errors="ignore"))
                else:
                    body = body_bytes.decode("utf-8", errors="ignore")
                response_data = {
                    "status": response.status,
                    "headers": dict(response.headers),
                    "body": body,
                    "body_bytes": body_bytes,
                    "proxy": proxy.url if proxy else None,
                }
                if self.settings.session.enabled:
                    if self._is_session_expired(task.domain, response.status, body):
                        session_payload["expired"] = True
                        await self.session_store.save(task.domain, session_payload)
                    else:
                        if "Set-Cookie" in response.headers:
                            cookie_header = self._extract_cookie_header(response)
                            if cookie_header:
                                session_payload["cookie"] = cookie_header
                                session_payload["expired"] = False
                                await self.session_store.save(task.domain, session_payload)
                if response.status >= 500:
                    self.circuit_breaker.record_failure(task.domain)
                else:
                    self.circuit_breaker.record_success(task.domain)
                await self.proxy_pool.mark_success(proxy, (monotonic() - started_at) * 1000)
                return response_data
        except Exception:
            self.circuit_breaker.record_failure(task.domain)
            await self.proxy_pool.mark_failed(proxy)
            raise

    async def save_binary(self, task: CrawlTask, file_name: str, body_bytes: bytes) -> str:
        save_dir = self.settings.resolve_path(self.settings.worker.download_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        target = save_dir / file_name
        target.write_bytes(body_bytes)
        return str(target)

    def _is_session_expired(self, domain: str, status_code: int, body: Any) -> bool:
        if not self.settings.auth.enabled:
            return False
        domain_cfg = self.settings.auth.domains.get(domain)
        if domain_cfg is None:
            return False
        if status_code in set(domain_cfg.expire_status_codes):
            return True
        if isinstance(body, str):
            lower_body = body.lower()
            return any(keyword in lower_body for keyword in domain_cfg.expire_body_keywords)
        if isinstance(body, dict):
            packed = json.dumps(body, ensure_ascii=False).lower()
            return any(keyword in packed for keyword in domain_cfg.expire_body_keywords)
        return False

    def _extract_cookie_header(self, response: aiohttp.ClientResponse) -> str:
        values = response.headers.getall("Set-Cookie", [])
        cookie_parts: list[str] = []
        for value in values:
            head = value.split(";", 1)[0].strip()
            if "=" in head:
                cookie_parts.append(head)
        # keep order and remove duplicates
        seen: set[str] = set()
        deduped: list[str] = []
        for item in cookie_parts:
            key = item.split("=", 1)[0].strip()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return "; ".join(deduped)
