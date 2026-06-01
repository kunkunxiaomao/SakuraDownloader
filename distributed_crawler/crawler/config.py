from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class MasterSettings:
    host: str = "127.0.0.1"
    port: int = 8088
    worker_ttl_seconds: int = 30
    stale_task_seconds: int = 120
    bloom_capacity: int = 1_000_000
    bloom_false_positive_rate: float = 0.01
    bloom_hashes: int | None = None
    queue_name: str = "crawler"
    result_queue_name: str = "crawler:results"
    dead_letter_queue_name: str = "crawler:dead_letter"
    default_spider: str = "generic_fetch"


@dataclass
class WorkerSettings:
    worker_id: str = "worker-1"
    concurrency: int = 100
    heartbeat_interval_seconds: int = 5
    request_timeout_seconds: int = 20
    connect_timeout_seconds: int = 10
    retries: int = 4
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 16.0
    download_dir: str = "./runtime/downloads"


@dataclass
class StorageSettings:
    backend: str = "jsonl"
    jsonl_path: str = "./runtime/results/results.jsonl"
    mongodb_dsn: str = "mongodb://127.0.0.1:27017"
    mongodb_database: str = "crawler"
    mongodb_collection: str = "results"
    postgres_dsn: str = "postgresql://postgres:postgres@127.0.0.1:5432/crawler"
    postgres_table: str = "crawl_results"


@dataclass
class ProxySettings:
    enabled: bool = False
    verify_url: str = "https://httpbin.org/ip"
    verify_timeout_seconds: int = 8
    refresh_interval_seconds: int = 300
    failure_threshold: int = 3
    cooldown_seconds: int = 60
    static_proxies: list[str] = field(default_factory=list)
    provider_api_url: str = ""
    provider_auth_header: str = ""


@dataclass
class CircuitBreakerSettings:
    enabled: bool = True
    failure_threshold: int = 8
    recovery_timeout_seconds: int = 30
    half_open_success_threshold: int = 2


@dataclass
class SessionSettings:
    enabled: bool = True
    redis_prefix: str = "crawler:sessions"
    local_backup_path: str = "./runtime/sessions/sessions.json"


@dataclass
class AuthDomainSettings:
    login_url: str = ""
    username: str = ""
    password: str = ""
    username_field: str = "username"
    password_field: str = "password"
    submit_type: str = "form"
    extra_payload: dict[str, Any] = field(default_factory=dict)
    success_status_codes: list[int] = field(default_factory=lambda: [200])
    expire_status_codes: list[int] = field(default_factory=lambda: [401, 403])
    expire_body_keywords: list[str] = field(default_factory=lambda: ["login required", "please login", "unauthorized"])


@dataclass
class AuthSettings:
    enabled: bool = False
    domains: dict[str, AuthDomainSettings] = field(default_factory=dict)


@dataclass
class DomainRateLimitSettings:
    qps: float = 3.0
    burst: int = 6


@dataclass
class RateLimitSettings:
    default_qps: float = 3.0
    default_burst: int = 6
    per_domain: dict[str, DomainRateLimitSettings] = field(default_factory=dict)


@dataclass
class ScheduleTaskSettings:
    name: str
    trigger: str
    task: dict[str, Any]
    priority: str = "medium"
    seconds: int | None = None
    minutes: int | None = None
    cron: str | None = None


@dataclass
class AppSettings:
    app_name: str = "distributed-crawler"
    redis_dsn: str = "redis://127.0.0.1:6379/0"
    checkpoint_dir: str = "./runtime/checkpoints"
    master: MasterSettings = field(default_factory=MasterSettings)
    worker: WorkerSettings = field(default_factory=WorkerSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    proxy: ProxySettings = field(default_factory=ProxySettings)
    circuit_breaker: CircuitBreakerSettings = field(default_factory=CircuitBreakerSettings)
    session: SessionSettings = field(default_factory=SessionSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    rate_limit: RateLimitSettings = field(default_factory=RateLimitSettings)
    schedules: list[ScheduleTaskSettings] = field(default_factory=list)
    source_path: Path | None = None

    @property
    def base_dir(self) -> Path:
        if self.source_path is None:
            return Path.cwd()
        return self.source_path.parent

    def resolve_path(self, value: str) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
        return (self.base_dir / candidate).resolve()


def _load_domain_limits(raw_limits: dict[str, Any]) -> dict[str, DomainRateLimitSettings]:
    result: dict[str, DomainRateLimitSettings] = {}
    for domain, item in raw_limits.items():
        result[domain] = DomainRateLimitSettings(
            qps=float(item.get("qps", 3.0)),
            burst=int(item.get("burst", 6)),
        )
    return result


def _load_auth_domains(raw_domains: dict[str, Any]) -> dict[str, AuthDomainSettings]:
    result: dict[str, AuthDomainSettings] = {}
    for domain, item in raw_domains.items():
        if not isinstance(item, dict):
            continue
        result[domain] = AuthDomainSettings(
            login_url=str(item.get("login_url", "")),
            username=str(item.get("username", "")),
            password=str(item.get("password", "")),
            username_field=str(item.get("username_field", "username")),
            password_field=str(item.get("password_field", "password")),
            submit_type=str(item.get("submit_type", "form")),
            extra_payload=dict(item.get("extra_payload", {})),
            success_status_codes=[int(v) for v in item.get("success_status_codes", [200])],
            expire_status_codes=[int(v) for v in item.get("expire_status_codes", [401, 403])],
            expire_body_keywords=[str(v).lower() for v in item.get("expire_body_keywords", ["login required", "please login", "unauthorized"])],
        )
    return result


def load_settings(path: str | Path) -> AppSettings:
    config_path = Path(path).resolve()
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    settings = AppSettings(
        app_name=str(data.get("app_name", "distributed-crawler")),
        redis_dsn=str(data.get("redis_dsn", "redis://127.0.0.1:6379/0")),
        checkpoint_dir=str(data.get("checkpoint_dir", "./runtime/checkpoints")),
        master=MasterSettings(**(data.get("master") or {})),
        worker=WorkerSettings(**(data.get("worker") or {})),
        storage=StorageSettings(**(data.get("storage") or {})),
        proxy=ProxySettings(**(data.get("proxy") or {})),
        circuit_breaker=CircuitBreakerSettings(**(data.get("circuit_breaker") or {})),
        session=SessionSettings(**(data.get("session") or {})),
        auth=AuthSettings(
            enabled=bool((data.get("auth") or {}).get("enabled", False)),
            domains=_load_auth_domains((data.get("auth") or {}).get("domains", {})),
        ),
        rate_limit=RateLimitSettings(
            default_qps=float((data.get("rate_limit") or {}).get("default_qps", 3.0)),
            default_burst=int((data.get("rate_limit") or {}).get("default_burst", 6)),
            per_domain=_load_domain_limits((data.get("rate_limit") or {}).get("per_domain", {})),
        ),
        schedules=[ScheduleTaskSettings(**item) for item in data.get("schedules", [])],
        source_path=config_path,
    )
    return settings
