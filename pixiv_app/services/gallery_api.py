from __future__ import annotations

import json
import mimetypes
import time
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pixiv_app.core.auth import SessionStore
from pixiv_app.core.cookie_import import cookie_summary, cookies_to_header, cookies_to_playwright, parse_cookie_text
from pixiv_app.core.gallery_service import LocalGalleryService
from pixiv_app.core.library import DEFAULT_LIBRARY_DB
from pixiv_app.core.paths import app_session_file, downloads_root, plugin_roots, webui_root
from pixiv_app.core.plugin.manager import PluginManager
from pixiv_app.core.thumbnails import DEFAULT_THUMBNAIL_DIR
from pixiv_app.core.x_media_service import XMediaService


class GalleryApiServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        db_path: str | Path = DEFAULT_LIBRARY_DB,
        thumbnail_dir: str | Path = DEFAULT_THUMBNAIL_DIR,
        web_root: str | Path | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.service = LocalGalleryService(db_path=db_path, thumbnail_dir=thumbnail_dir)
        self.x_media_service = XMediaService()
        self.plugin_manager = PluginManager(plugin_roots())
        self.plugin_manager.load_all()
        self.plugin_preview_cache: dict[str, tuple[str, list]] = {}
        self.web_root = Path(web_root) if web_root else webui_root()

    def serve_forever(self) -> None:
        service = self.service
        x_media_service = self.x_media_service
        plugin_manager = self.plugin_manager
        plugin_preview_cache = self.plugin_preview_cache
        web_root = self.web_root

        Handler = type(
            "GalleryApiHandlerForServer",
            (GalleryApiHandler,),
            {
                "gallery_service": service,
                "x_media_service": x_media_service,
                "plugin_manager": plugin_manager,
                "plugin_preview_cache": plugin_preview_cache,
                "web_root": web_root,
            },
        )

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        try:
            server.serve_forever()
        finally:
            server.server_close()


class GalleryApiHandler(BaseHTTPRequestHandler):
    gallery_service: LocalGalleryService
    x_media_service: XMediaService
    plugin_manager: PluginManager
    plugin_preview_cache: dict[str, tuple[str, list]]
    web_root: Path

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path in {"/", "/index.html"}:
                self._send_static_file(self.web_root / "index.html")
            elif parsed.path.startswith("/assets/"):
                self._send_static_file(self.web_root / parsed.path.removeprefix("/assets/"))
            elif parsed.path == "/api/health":
                self._send_json({"status": "ok"})
            elif parsed.path == "/api/plugins/pages":
                self._send_json(self._handle_plugin_pages())
            elif parsed.path == "/api/artworks":
                self._send_json(self._handle_artworks(query))
            elif parsed.path == "/api/favorites":
                self._send_json(self.gallery_service.list_favorites(limit=_int(query, "limit", 60), offset=_int(query, "offset", 0)))
            elif parsed.path.startswith("/api/artworks/"):
                self._send_json(self._handle_artwork_detail(parsed.path))
            elif parsed.path == "/api/tags":
                self._send_json(self.gallery_service.list_tags(query=_first(query, "q"), limit=_int(query, "limit", 100)))
            elif parsed.path == "/api/artists":
                self._send_json(self.gallery_service.list_artists(query=_first(query, "q"), limit=_int(query, "limit", 100)))
            elif parsed.path.startswith("/api/artists/"):
                self._send_json(self._handle_artist_detail(parsed.path, query))
            elif parsed.path == "/api/followed-artists":
                self._send_json(self.gallery_service.list_followed_artists())
            elif parsed.path == "/api/thumbnails/build":
                self._send_json({"items": self.gallery_service.build_missing_thumbnails(limit=_int(query, "limit", 200))})
            elif parsed.path == "/api/import/legacy":
                self._send_json(self.gallery_service.scan_legacy_downloads(root=_first(query, "root", "Sakura_Downloads")))
            elif parsed.path == "/api/sync/followed":
                self._send_json(
                    self.gallery_service.sync_followed_artists(
                        max_new_per_artist=_int(query, "max_new", 20),
                        download=_bool(query, "download", True),
                    )
                )
            elif parsed.path == "/api/x/local-media":
                self._send_json(
                    self.x_media_service.list_local_media(
                        limit=_int(query, "limit", 160),
                        offset=_int(query, "offset", 0),
                    )
                )
            elif parsed.path.startswith("/media/thumbnail/"):
                self._send_file(self._resolve_media_path(parsed.path, thumbnail=True))
            elif parsed.path.startswith("/media/source/"):
                self._send_file(self._resolve_media_path(parsed.path, thumbnail=False))
            elif parsed.path.startswith("/media/x-local/"):
                self._send_file(self.x_media_service.resolve_local_media(parsed.path.rsplit("/", 1)[-1]))
            else:
                self._send_json({"error": "not found"}, status=404)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
            if parsed.path == "/api/favorites":
                self._send_json(
                    self.gallery_service.set_favorite(
                        int(payload["artwork_id"]),
                        bool(payload.get("favorite", True)),
                        str(payload.get("note", "")),
                    )
                )
            elif parsed.path == "/api/followed-artists":
                self._send_json(
                    self.gallery_service.set_followed_artist(
                        int(payload["artist_id"]),
                        bool(payload.get("followed", True)),
                        str(payload.get("name", "")),
                    )
                )
            elif parsed.path == "/api/import/legacy":
                self._send_json(self.gallery_service.scan_legacy_downloads(root=str(payload.get("root", "Sakura_Downloads"))))
            elif parsed.path == "/api/sync/followed":
                self._send_json(
                    self.gallery_service.sync_followed_artists(
                        cookie=str(payload.get("cookie", "")),
                        max_new_per_artist=int(payload.get("max_new", 20)),
                        download=bool(payload.get("download", True)),
                    )
                )
            elif parsed.path == "/api/cookies/import":
                self._send_json(self._handle_cookie_import(payload))
            elif parsed.path == "/api/x/media/preview":
                self._send_json(
                    self.x_media_service.preview_author_media(
                        str(payload.get("username", "")),
                        media_type=str(payload.get("media_type", "all")),
                        max_items=int(payload.get("max_items", 120)),
                    )
                )
            elif parsed.path == "/api/x/media/download":
                selected_ids = payload.get("selected_ids")
                if selected_ids is not None and not isinstance(selected_ids, list):
                    raise ValueError("selected_ids 必须是数组。")
                self._send_json(
                    self.x_media_service.download_preview(
                        str(payload.get("preview_id", "")),
                        selected_ids=[str(item) for item in selected_ids] if selected_ids is not None else None,
                    )
                )
            elif parsed.path == "/api/x/local-media/delete":
                media_ids = payload.get("ids", [])
                if not isinstance(media_ids, list):
                    raise ValueError("ids 必须是数组。")
                self._send_json(self.x_media_service.delete_local_media([str(item) for item in media_ids]))
            elif parsed.path == "/api/xiaohongshu/preview":
                self._send_json(self._handle_plugin_preview("小红书", payload))
            elif parsed.path == "/api/xiaohongshu/download":
                self._send_json(self._handle_plugin_download(payload))
            else:
                self._send_json({"error": "not found"}, status=404)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return None

    def _handle_artworks(self, query: dict[str, list[str]]) -> dict:
        artist_id = _first(query, "artist_id")
        return self.gallery_service.list_artworks(
            query=_first(query, "q"),
            tag=_first(query, "tag"),
            artist_id=int(artist_id) if artist_id else None,
            artwork_type=_first(query, "type"),
            include_restricted=_bool(query, "include_restricted", True),
            limit=_int(query, "limit", 60),
            offset=_int(query, "offset", 0),
            ensure_thumbnails=_bool(query, "ensure_thumbnails", True),
        )

    def _handle_plugin_pages(self) -> dict:
        self.plugin_manager.reload_all_from_disk()
        items = []
        if "X" in self.plugin_manager.plugins:
            items.append(
                {
                    "id": "x-media",
                    "view": "xMedia",
                    "title": "X 作者媒体",
                    "description": "按作者名称解析图片或视频并选择下载",
                }
            )
        if "小红书" in self.plugin_manager.plugins:
            plugin = self.plugin_manager.plugins["小红书"]
            items.append(
                {
                    "id": "xiaohongshu",
                    "view": "xiaohongshu",
                    "title": "小红书",
                    "description": "搜索关键词或解析笔记链接，预览后下载",
                    "available": bool(plugin.validate()),
                }
            )
        return {"items": items}

    def _handle_plugin_preview(self, plugin_name: str, payload: dict) -> dict:
        plugin = self.plugin_manager.plugins.get(plugin_name)
        if plugin is None:
            raise ValueError(f"未安装插件: {plugin_name}")
        if not plugin.validate():
            raise ValueError(f"{plugin_name} 插件依赖不可用，请先安装 Playwright。")
        target = str(payload.get("target", "")).strip()
        if not target:
            raise ValueError("请输入解析目标。")
        limit = max(1, min(int(payload.get("limit", 20)), 50))
        resources = plugin.parse(target)[:limit]
        preview_id = uuid.uuid4().hex
        self.plugin_preview_cache[preview_id] = (plugin_name, resources)
        return {
            "preview_id": preview_id,
            "count": len(resources),
            "items": [_resource_to_json(item, i) for i, item in enumerate(resources)],
        }

    def _handle_plugin_download(self, payload: dict) -> dict:
        preview_id = str(payload.get("preview_id", ""))
        cached = self.plugin_preview_cache.get(preview_id)
        if cached is None:
            raise ValueError("预览结果已过期，请重新解析。")
        plugin_name, resources = cached
        plugin = self.plugin_manager.plugins.get(plugin_name)
        if plugin is None:
            raise ValueError(f"插件已卸载: {plugin_name}")
        selected_ids = payload.get("selected_ids")
        selected = set(str(item) for item in selected_ids) if isinstance(selected_ids, list) else set()
        if selected:
            resources = [item for item in resources if item.id in selected]
        save_root = downloads_root()
        results = []
        downloaded = 0
        failed = 0
        for resource in resources:
            try:
                paths = plugin.download(resource, save_root)
                downloaded += len(paths)
                results.append({"id": resource.id, "ok": bool(paths), "paths": [str(path) for path in paths]})
            except Exception as exc:
                failed += 1
                results.append({"id": resource.id, "ok": False, "error": str(exc)})
        return {"requested": len(resources), "downloaded_files": downloaded, "failed_items": failed, "items": results}

    def _handle_artwork_detail(self, path: str) -> dict:
        artwork_id = int(path.rsplit("/", 1)[-1])
        item = self.gallery_service.get_artwork(artwork_id)
        if item is None:
            raise ValueError(f"Artwork not found: {artwork_id}")
        return item

    def _handle_artist_detail(self, path: str, query: dict[str, list[str]]) -> dict:
        artist_id = int(path.rsplit("/", 1)[-1])
        item = self.gallery_service.get_artist(
            artist_id,
            limit=_int(query, "limit", 60),
            offset=_int(query, "offset", 0),
        )
        if item is None:
            raise ValueError(f"Artist not found: {artist_id}")
        return item

    def _resolve_media_path(self, path: str, *, thumbnail: bool) -> Path:
        parts = path.strip("/").split("/")
        if len(parts) != 4:
            raise ValueError("Expected /media/{thumbnail|source}/{artwork_id}/{page_index}")
        artwork_id = int(parts[2])
        page_index = int(parts[3])
        media_path = (
            self.gallery_service.get_thumbnail_path(artwork_id, page_index=page_index)
            if thumbnail
            else self.gallery_service.get_source_file_path(artwork_id, page_index=page_index)
        )
        if media_path is None:
            raise ValueError("Media file not found")
        return media_path

    def _handle_cookie_import(self, payload: dict) -> dict:
        platform = str(payload.get("platform", "")).strip().lower()
        text = str(payload.get("text", ""))
        if platform in {"pixiv", "px"}:
            cookies = parse_cookie_text(text)
            pixiv_cookies = cookies_to_playwright(cookies, ("pixiv.net",))
            header = cookies_to_header(pixiv_cookies, ("pixiv.net",))
            SessionStore(app_session_file()).save(
                {
                    "login_mode": "cookie",
                    "cookie": header,
                    "cookie_json": pixiv_cookies,
                    "login_id": "",
                    "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            return {"platform": "pixiv", "count": len(pixiv_cookies), "summary": cookie_summary(pixiv_cookies)}
        if platform in {"x", "twitter"}:
            result = self.x_media_service.import_cookie_text(text)
            result["platform"] = "x"
            return result
        raise ValueError("platform 必须是 pixiv 或 x。")

    def _send_json(self, payload: dict | list, *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._send_cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_file(path)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload


def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    if not values:
        return default
    return values[0]


def _int(query: dict[str, list[str]], key: str, default: int) -> int:
    value = _first(query, key)
    if not value:
        return default
    return int(value)


def _bool(query: dict[str, list[str]], key: str, default: bool) -> bool:
    value = _first(query, key)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _resource_to_json(resource, index: int) -> dict:
    payload = asdict(resource)
    payload["index"] = index
    payload["kind"] = "video" if any(file.get("type") == "video" for file in resource.files) else "image"
    payload["file_count"] = len(resource.files)
    return payload
