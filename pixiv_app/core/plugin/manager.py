from __future__ import annotations

import importlib.util
import json
import logging
import sys
from collections import defaultdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pixiv_app.core.plugin.base import BasePlugin, PluginManifest

logger = logging.getLogger(__name__)


class PluginEvent(str, Enum):
    BEFORE_LOAD = "before_load"
    AFTER_LOAD = "after_load"
    BEFORE_UNLOAD = "before_unload"
    AFTER_UNLOAD = "after_unload"
    ERROR = "error"


class PluginManager:
    """Discover and load plugins from filesystem directories."""

    def __init__(self, plugin_dirs: list[str | Path]) -> None:
        self.plugin_dirs = [Path(d).resolve() for d in plugin_dirs]
        self.plugins: dict[str, BasePlugin] = {}
        self.manifests: dict[str, PluginManifest] = {}
        self.loaded_modules: dict[str, Any] = {}
        self._paths: dict[str, Path] = {}
        self.load_errors: dict[Path, str] = {}
        self.event_handlers: dict[PluginEvent, list[Callable[..., None]]] = defaultdict(list)

    def on_event(self, event: PluginEvent, callback: Callable[..., None]) -> None:
        self.event_handlers[event].append(callback)

    def _emit(self, event: PluginEvent, *args: Any, **kwargs: Any) -> None:
        for cb in self.event_handlers.get(event, []):
            try:
                cb(*args, **kwargs)
            except Exception as exc:
                logger.exception("Plugin event handler failed: %s", exc)

    def discover(self) -> list[Path]:
        """Return paths to plugin.py files."""
        found: list[Path] = []
        for root in self.plugin_dirs:
            if not root.is_dir():
                continue
            for subdir in sorted(root.iterdir()):
                if not subdir.is_dir():
                    continue
                candidate = subdir / "plugin.py"
                if candidate.is_file():
                    found.append(candidate)
        return found

    def _load_manifest(self, plugin_dir: Path) -> PluginManifest | None:
        manifest_path = plugin_dir / "manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return PluginManifest(
                name=str(data.get("name", plugin_dir.name)),
                version=str(data.get("version", "0.0.0")),
                domain=str(data.get("domain", "")),
                author=str(data.get("author", "")),
                description=str(data.get("description", "")),
                requires_auth=bool(data.get("requires_auth", False)),
                min_platform_version=str(data.get("min_platform_version", "1.0.0")),
                tags=list(data.get("tags") or []),
                dependencies=list(data.get("dependencies") or []),
            )
        except Exception as exc:
            logger.warning("Invalid manifest %s: %s", manifest_path, exc)
            return None

    def load_plugin(self, plugin_path: Path) -> BasePlugin | None:
        """Load a single plugin from ``.../plugin.py``."""
        plugin_path = plugin_path.resolve()
        plugin_dir = plugin_path.parent
        module_name = f"sakura_dynamic_plugin_{plugin_dir.name}"
        module_stem = module_name.rsplit(".", 1)[0]
        for loaded_name in list(sys.modules):
            if loaded_name == module_name or loaded_name.startswith(f"{module_name}."):
                del sys.modules[loaded_name]
        self.load_errors.pop(plugin_path, None)

        self._emit(PluginEvent.BEFORE_LOAD, plugin_path)

        spec = importlib.util.spec_from_file_location(module_name, plugin_path)
        if spec is None or spec.loader is None:
            self._emit(PluginEvent.ERROR, plugin_path, "invalid spec")
            self.load_errors[plugin_path] = "invalid spec"
            return None

        module = importlib.util.module_from_spec(spec)
        module.__package__ = module_stem
        module.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
        module.__file__ = str(plugin_path)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.exception("Failed to load %s", plugin_path)
            self._emit(PluginEvent.ERROR, plugin_path, exc)
            self.load_errors[plugin_path] = f"{type(exc).__name__}: {exc}"
            return None

        plugin_class = getattr(module, "plugin_class", None)
        if plugin_class is None:
            logger.error("Module %s has no plugin_class export", plugin_path)
            self._emit(PluginEvent.ERROR, plugin_path, "missing plugin_class")
            self.load_errors[plugin_path] = "missing plugin_class"
            return None

        try:
            plugin = plugin_class()
        except Exception as exc:
            logger.exception("Failed to instantiate plugin from %s", plugin_path)
            self._emit(PluginEvent.ERROR, plugin_path, exc)
            self.load_errors[plugin_path] = f"{type(exc).__name__}: {exc}"
            return None

        if not isinstance(plugin, BasePlugin):
            logger.error("plugin_class is not BasePlugin: %s", plugin_path)
            self.load_errors[plugin_path] = "plugin_class is not BasePlugin"
            return None

        key = plugin.name
        manifest = self._load_manifest(plugin_dir)
        if manifest:
            self.manifests[key] = manifest

        self.plugins[key] = plugin
        self.loaded_modules[key] = module
        self._paths[key] = plugin_path
        self.load_errors.pop(plugin_path, None)
        self._emit(PluginEvent.AFTER_LOAD, plugin)
        return plugin

    def load_all(self) -> dict[str, BasePlugin]:
        for path in self.discover():
            try:
                self.load_plugin(path)
            except Exception as exc:
                logger.exception("Failed during load_all %s: %s", path, exc)
        return self.plugins

    def reload_all_from_disk(self) -> None:
        """Unload every plugin entry then scan directories again."""
        for name in list(self.plugins.keys()):
            self.unload_plugin(name)
        self.load_errors.clear()
        self.load_all()

    def reload_plugin(self, name: str) -> bool:
        path = self._paths.get(name)
        if path is None:
            return False
        self.unload_plugin(name)
        return self.load_plugin(path) is not None

    def get_plugin_path(self, name: str) -> Path | None:
        return self._paths.get(name)

    def unload_plugin(self, name: str) -> None:
        self._emit(PluginEvent.BEFORE_UNLOAD, name)
        self.plugins.pop(name, None)
        mod = self.loaded_modules.pop(name, None)
        self.manifests.pop(name, None)
        self._paths.pop(name, None)
        if mod is not None and getattr(mod, "__name__", None) in sys.modules:
            del sys.modules[mod.__name__]
        self._emit(PluginEvent.AFTER_UNLOAD, name)

    def get_plugin_for_url(self, url: str) -> BasePlugin | None:
        for plugin in self.plugins.values():
            if plugin.can_handle(url):
                return plugin
        return None

    def check_dependencies(self, manifest: PluginManifest) -> bool:
        for dep in manifest.dependencies:
            if dep not in self.plugins:
                return False
        return True
