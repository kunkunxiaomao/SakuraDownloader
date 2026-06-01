from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from time import monotonic

import aiohttp

from .config import ProxySettings


@dataclass
class ProxyEndpoint:
    url: str
    failures: int = 0
    successes: int = 0
    latency_ms: float | None = None
    last_verified_at: float = 0.0
    last_failed_at: float = 0.0
    healthy: bool = True
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        if total == 0:
            return 1.0
        return self.successes / total

    @property
    def score(self) -> float:
        latency_penalty = 1.0
        if self.latency_ms is not None:
            latency_penalty = max(0.1, min(1.0, 1000.0 / max(self.latency_ms, 1.0)))
        failure_penalty = 1.0 / (1.0 + self.failures)
        return max(0.01, self.success_rate * latency_penalty * failure_penalty)


class ProxyProvider(ABC):
    @abstractmethod
    async def fetch(self) -> list[ProxyEndpoint]: ...


class StaticProxyProvider(ProxyProvider):
    def __init__(self, proxies: list[str]) -> None:
        self.proxies = proxies

    async def fetch(self) -> list[ProxyEndpoint]:
        return [ProxyEndpoint(url=value) for value in self.proxies]


class ApiProxyProvider(ProxyProvider):
    def __init__(self, api_url: str, auth_header: str = "") -> None:
        self.api_url = api_url
        self.auth_header = auth_header

    async def fetch(self) -> list[ProxyEndpoint]:
        if not self.api_url:
            return []
        headers = {"Authorization": self.auth_header} if self.auth_header else {}
        async with aiohttp.ClientSession() as session:
            async with session.get(self.api_url, headers=headers) as response:
                response.raise_for_status()
                text = await response.text()
        proxies: list[ProxyEndpoint] = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                proxies.append(ProxyEndpoint(url=line))
        return proxies


class ProxyPool:
    def __init__(self, settings: ProxySettings) -> None:
        self.settings = settings
        self._proxies: list[ProxyEndpoint] = []
        self._lock = asyncio.Lock()
        self._next_index = 0

    async def refresh(self) -> None:
        providers: list[ProxyProvider] = [StaticProxyProvider(self.settings.static_proxies)]
        if self.settings.provider_api_url:
            providers.append(ApiProxyProvider(self.settings.provider_api_url, self.settings.provider_auth_header))
        async with self._lock:
            existing = {endpoint.url: endpoint for endpoint in self._proxies}
        merged: dict[str, ProxyEndpoint] = {}
        for provider in providers:
            for endpoint in await provider.fetch():
                merged.setdefault(endpoint.url, existing.get(endpoint.url, endpoint))
        async with self._lock:
            self._proxies = list(merged.values())

    async def verify(self) -> None:
        if not self.settings.enabled:
            return
        async with self._lock:
            proxies = list(self._proxies)
        if not proxies:
            return
        async with aiohttp.ClientSession() as session:
            tasks = [self._verify_proxy(session, proxy) for proxy in proxies]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def acquire(self) -> ProxyEndpoint | None:
        if not self.settings.enabled:
            return None
        async with self._lock:
            now = monotonic()
            candidates = [
                item
                for item in self._proxies
                if item.healthy or now - item.last_failed_at >= self.settings.cooldown_seconds
            ]
            if not candidates:
                return None
            proxy = random.choices(candidates, weights=[item.score for item in candidates], k=1)[0]
            self._next_index += 1
            return proxy

    async def mark_success(self, proxy: ProxyEndpoint | None, latency_ms: float | None = None) -> None:
        if proxy is None:
            return
        async with self._lock:
            proxy.successes += 1
            proxy.failures = 0
            proxy.healthy = True
            if latency_ms is not None:
                proxy.latency_ms = latency_ms
            proxy.last_verified_at = monotonic()

    async def mark_failed(self, proxy: ProxyEndpoint | None) -> None:
        if proxy is None:
            return
        async with self._lock:
            proxy.failures += 1
            proxy.last_failed_at = monotonic()
            if proxy.failures >= self.settings.failure_threshold:
                proxy.healthy = False

    async def healthy_count(self) -> int:
        async with self._lock:
            return len([item for item in self._proxies if item.healthy])

    async def _verify_proxy(self, session: aiohttp.ClientSession, proxy: ProxyEndpoint) -> None:
        try:
            timeout = aiohttp.ClientTimeout(total=self.settings.verify_timeout_seconds)
            started_at = monotonic()
            async with session.get(self.settings.verify_url, proxy=proxy.url, timeout=timeout) as response:
                response.raise_for_status()
                await response.read()
                proxy.healthy = True
                proxy.failures = 0
                proxy.successes += 1
                proxy.latency_ms = (monotonic() - started_at) * 1000
                proxy.last_verified_at = monotonic()
        except Exception:
            proxy.failures += 1
            proxy.last_failed_at = monotonic()
            if proxy.failures >= self.settings.failure_threshold:
                proxy.healthy = False
