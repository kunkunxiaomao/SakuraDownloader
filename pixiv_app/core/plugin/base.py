"""
Multi-site plugin core abstractions.

解析与下载采用同步接口，便于与现有 requests / 线程模型集成；
若站点 SDK 仅提供 asyncio，可在插件内部使用 asyncio.run 包装。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Resource:
    """Unified resource model returned by plugins."""

    id: str
    url: str
    title: str
    author: str
    author_id: str
    files: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)
    thumbnail: str | None = None
    created_at: str | None = None


@dataclass
class PluginManifest:
    """Optional manifest shipped as manifest.json next to plugin.py."""

    name: str
    version: str
    domain: str
    author: str = ""
    description: str = ""
    requires_auth: bool = False
    min_platform_version: str = "1.0.0"
    tags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


class PluginError(Exception):
    """Base class for plugin failures."""


class PluginAuthError(PluginError):
    """Authentication or authorization failed."""


class PluginParseError(PluginError):
    """URL or API response could not be parsed."""


class PluginDownloadError(PluginError):
    """Download or filesystem error."""


class BasePlugin(ABC):
    """Interface implemented by every site plugin."""

    @property
    @abstractmethod
    def domain(self) -> str:
        """Primary domain, e.g. pixiv.net."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable plugin name."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version string."""

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Return True if this plugin should process the URL or raw token."""

    @abstractmethod
    def parse(self, url: str) -> list[Resource]:
        """Resolve URL into one or more downloadable resources."""

    @abstractmethod
    def download(self, resource: Resource, save_path: Path) -> list[Path]:
        """Download files for ``resource`` under ``save_path``."""

    @abstractmethod
    def get_headers(self) -> dict[str, str]:
        """HTTP headers for requests (Cookie, Bearer token, etc.)."""

    def validate(self) -> bool:
        """Optional self-check (credentials present, deps installed)."""
        return True

    def get_config_schema(self) -> dict[str, Any]:
        """Optional JSON-schema-like dict for dynamic GUI forms."""
        return {}

    def load_config(self) -> dict[str, Any]:
        """Override to load persisted plugin settings."""
        return {}

    def save_config(self, data: dict[str, Any]) -> None:
        """Override to persist plugin settings."""

__all__ = [
    "BasePlugin",
    "PluginAuthError",
    "PluginDownloadError",
    "PluginError",
    "PluginManifest",
    "PluginParseError",
    "Resource",
]
