"""Plugin system: base types, manager, generator, optional market client."""

from pixiv_app.core.plugin.base import (
    BasePlugin,
    PluginAuthError,
    PluginDownloadError,
    PluginError,
    PluginManifest,
    PluginParseError,
    Resource,
)
from pixiv_app.core.plugin.manager import PluginEvent, PluginManager

__all__ = [
    "BasePlugin",
    "PluginAuthError",
    "PluginDownloadError",
    "PluginError",
    "PluginEvent",
    "PluginManager",
    "PluginManifest",
    "PluginParseError",
    "Resource",
]
