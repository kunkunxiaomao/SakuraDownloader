"""Bridge URL-level plugin parsing with the rest of the app (tasks/GUI logging)."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from pixiv_app.core.plugin.base import BasePlugin, PluginParseError, Resource
from pixiv_app.core.plugin.manager import PluginManager

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    plugin_name: str
    resources: list[Resource]


class PluginTaskOrchestrator:
    """Resolve plugins and run parse/download without replacing the legacy Pixiv GUI flow."""

    def __init__(self, plugin_manager: PluginManager) -> None:
        self.plugin_manager = plugin_manager

    def get_plugin_for_url(self, url: str) -> BasePlugin | None:
        return self.plugin_manager.get_plugin_for_url(url)

    def parse_url(self, url: str) -> OrchestrationResult:
        plugin = self.get_plugin_for_url(url)
        if plugin is None:
            raise PluginParseError(f"No plugin registered for: {url}")
        resources = plugin.parse(url)
        return OrchestrationResult(plugin_name=plugin.name, resources=resources)

    def download_resource(
        self,
        plugin_name: str,
        resource: Resource,
        save_path: Path,
    ) -> list[Path]:
        plugin = self.plugin_manager.plugins.get(plugin_name)
        if plugin is None:
            raise PluginParseError(f"Plugin not loaded: {plugin_name}")
        return plugin.download(resource, save_path)
