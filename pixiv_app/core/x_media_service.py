from __future__ import annotations

import time
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pixiv_app.core.cookie_import import (
    cookie_summary,
    cookies_to_playwright,
    parse_cookie_text,
    save_playwright_cookies,
)
from pixiv_app.core.paths import downloads_root, plugin_roots, plugins_root as app_plugins_root, runtime_path
from pixiv_app.core.plugin.base import Resource
from pixiv_app.core.plugin.manager import PluginManager


def project_root() -> Path:
    return app_plugins_root().parent


class XMediaService:
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}

    def __init__(self, *, plugins_root: str | Path | None = None, save_root: str | Path | None = None) -> None:
        self.plugins_root = Path(plugins_root) if plugins_root else app_plugins_root()
        self.save_root = Path(save_root) if save_root else downloads_root() / "x"
        self.cookie_path = runtime_path("x_cookies.json")
        self._manager: PluginManager | None = None
        self._preview_cache: dict[str, list[Resource]] = {}

    def import_cookie_text(self, text: str) -> dict[str, Any]:
        cookies = parse_cookie_text(text)
        x_cookies = cookies_to_playwright(cookies, ("x.com", "twitter.com"))
        path = save_playwright_cookies(x_cookies, self.cookie_path)
        plugin = self._get_plugin()
        if hasattr(plugin, "reset_playwright_session"):
            plugin.reset_playwright_session()
        return {"count": len(x_cookies), "path": str(path), "summary": cookie_summary(x_cookies)}

    def preview_author_media(self, username: str, *, media_type: str = "all", max_items: int = 120) -> dict[str, Any]:
        handle = _normalize_username(username)
        media_type = _normalize_media_type(media_type)
        max_items = max(1, min(int(max_items), 500))
        url = f"https://x.com/{handle}/media"

        plugin = self._get_plugin()
        if not plugin.validate():
            raise ValueError("X 插件不可用：请先安装 playwright/httpx，并执行 playwright install chromium。")

        resources = plugin.parse(url)
        resources = _filter_resources(resources, media_type)[:max_items]
        preview_id = uuid.uuid4().hex
        self._preview_cache[preview_id] = resources
        return {
            "preview_id": preview_id,
            "username": handle,
            "media_type": media_type,
            "count": len(resources),
            "items": [_resource_to_json(item, i) for i, item in enumerate(resources)],
            "created_at": int(time.time()),
        }

    def download_preview(self, preview_id: str, *, selected_ids: list[str] | None = None) -> dict[str, Any]:
        resources = self._preview_cache.get(preview_id)
        if resources is None:
            raise ValueError("预览结果已过期，请重新解析作者媒体。")
        selected = set(selected_ids or [])
        if selected:
            resources = [item for item in resources if item.id in selected]
        if not resources:
            raise ValueError("没有选中任何媒体。")

        plugin = self._get_plugin()
        self.save_root.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, Any]] = []
        downloaded = 0
        failed = 0
        for resource in resources:
            try:
                paths = plugin.download(resource, self.save_root)
                downloaded += len(paths)
                results.append(
                    {
                        "id": resource.id,
                        "ok": bool(paths),
                        "paths": [str(path) for path in paths],
                        "title": resource.title,
                    }
                )
                if not paths:
                    failed += 1
            except Exception as exc:  # Keep batch downloads moving.
                failed += 1
                results.append({"id": resource.id, "ok": False, "error": str(exc), "title": resource.title})

        return {"requested": len(resources), "downloaded_files": downloaded, "failed_items": failed, "items": results}

    def list_local_media(self, *, limit: int = 160, offset: int = 0) -> dict[str, Any]:
        self.save_root.mkdir(parents=True, exist_ok=True)
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        files = [
            path
            for path in self.save_root.rglob("*")
            if path.is_file() and path.suffix.lower() in self.IMAGE_EXTS | self.VIDEO_EXTS
        ]
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        page = files[offset : offset + limit]
        return {
            "total": len(files),
            "items": [self._local_media_to_json(path) for path in page],
        }

    def delete_local_media(self, media_ids: list[str]) -> dict[str, Any]:
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        for media_id in media_ids:
            try:
                path = self.resolve_local_media(media_id)
                path.unlink()
                deleted.append(media_id)
            except Exception as exc:
                failed.append({"id": media_id, "error": str(exc)})
        return {"deleted": deleted, "failed": failed}

    def resolve_local_media(self, media_id: str) -> Path:
        rel_text = _decode_media_id(media_id)
        target = (self.save_root / rel_text).resolve()
        root = self.save_root.resolve()
        if root != target and root not in target.parents:
            raise ValueError("非法媒体路径。")
        if not target.is_file():
            raise ValueError("媒体文件不存在。")
        return target

    def _get_plugin(self):
        if self._manager is None:
            self._manager = PluginManager(plugin_roots() if self.plugins_root == app_plugins_root() else [self.plugins_root])
            self._manager.load_all()
        plugin = self._manager.plugins.get("X")
        if plugin is None:
            raise ValueError("未检测到 X 插件，请确认 plugins/x 存在。")
        return plugin

    def _local_media_to_json(self, path: Path) -> dict[str, Any]:
        rel = path.relative_to(self.save_root)
        stat = path.stat()
        kind = "video" if path.suffix.lower() in self.VIDEO_EXTS else "image"
        media_id = _encode_media_id(str(rel).replace("\\", "/"))
        return {
            "id": media_id,
            "name": path.name,
            "relative_path": str(rel).replace("\\", "/"),
            "kind": kind,
            "size": stat.st_size,
            "modified_at": int(stat.st_mtime),
            "url": f"/media/x-local/{media_id}",
        }


def _normalize_username(username: str) -> str:
    value = username.strip()
    if value.startswith("@"):
        value = value[1:]
    if "/" in value:
        parts = [item for item in value.replace("https://", "").replace("http://", "").split("/") if item]
        value = parts[1] if parts and parts[0].lower() in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"} else parts[0]
    if not value:
        raise ValueError("请输入 X 作者名称。")
    if len(value) > 15 or not value.replace("_", "").isalnum():
        raise ValueError("X 作者名称格式不正确。")
    return value


def _normalize_media_type(media_type: str) -> str:
    value = (media_type or "all").strip().lower()
    if value not in {"all", "image", "video"}:
        raise ValueError("媒体类型必须是 all、image 或 video。")
    return value


def _filter_resources(resources: list[Resource], media_type: str) -> list[Resource]:
    if media_type == "all":
        return resources
    return [item for item in resources if any(file.get("type", "image") == media_type for file in item.files)]


def _resource_to_json(resource: Resource, index: int) -> dict[str, Any]:
    payload = asdict(resource)
    payload["index"] = index
    payload["kind"] = "video" if any(file.get("type") == "video" for file in resource.files) else "image"
    payload["file_count"] = len(resource.files)
    return payload


def _encode_media_id(value: str) -> str:
    return urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_media_id(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")
