"""
X (Twitter) plugin — Playwright rendering + httpx media fetch.

Implements synchronous BasePlugin API; async work is wrapped with asyncio.run().
"""

from __future__ import annotations

import asyncio
import json
import queue
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .playwright_client import XPlaywrightClient
from .x_login import run_interactive_login as x_login_flow
from .x_urls import (
    extract_tweet_id,
    extract_search_query,
    extract_username_and_kind,
    is_search_url,
    is_status_url,
    normalize_url,
)

from pixiv_app.core.plugin.base import BasePlugin, PluginParseError, Resource
from pixiv_app.core.paths import runtime_path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass
class XPluginConfig:
    cookie_file: str = ""
    profile_dir: str = ""
    headless: bool = True
    wait_timeout: int = 45000
    timeline_tweet_limit: int = 8
    media_scroll_rounds: int = 5
    max_media_items: int = 40
    parse_cooldown_seconds: float = 25.0
    min_action_delay: float = 3.0
    max_action_delay: float = 8.0
    download_delay_min: float = 1.0
    download_delay_max: float = 3.0
    batch_pause_min: float = 5.0
    batch_pause_max: float = 12.0
    batch_pause_after_min: int = 3
    batch_pause_after_max: int = 5


class XPlugin(BasePlugin):
    """
    Session policy:

    - Chromium **persistent profile** lives on disk (``runtime/x_profile``); each ``parse()`` starts a
      fresh Playwright connection bound to **one** ``asyncio.run()`` lifecycle, then closes it.
      Caching ``BrowserContext`` across multiple ``asyncio.run()`` calls breaks Playwright (dead loop /
      ``NoneType.send``).
    - Cookie JSON ``runtime/x_cookies.json`` is merged on each ``start()`` when present.
    - ``reset_playwright_session()`` kept for API compat (GUI); next parse always loads profile from disk.
    """

    name = "X"
    domain = "twitter.com"
    version = "1.0.0"

    _HANDLE_PATTERNS = (
        re.compile(r"(?:twitter|x)\.com/\w+/status/\d+", re.I),
        re.compile(r"(?:twitter|x)\.com/[^/]+/media\b", re.I),
        re.compile(r"(?:twitter|x)\.com/[A-Za-z0-9_]{1,15}/?$", re.I),
        re.compile(r"(?:twitter|x)\.com/[A-Za-z0-9_]{1,15}/media/?$", re.I),
        re.compile(r"(?:twitter|x)\.com/search\?", re.I),
    )

    def __init__(self) -> None:
        self.config = XPluginConfig(
            cookie_file=str(runtime_path("x_cookies.json")),
            profile_dir=str(runtime_path("x_profile")),
            headless=True,
        )
        self._cache_file = runtime_path("x_download_cache.json")
        # Serialize Playwright use across GUI threads.
        self._playwright_lock = threading.Lock()
        self._last_parse_at = 0.0

    def can_handle(self, url: str) -> bool:
        text = url.strip()
        if not text:
            return False
        return any(p.search(text) for p in self._HANDLE_PATTERNS)

    def validate(self) -> bool:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return True

    def get_headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

    def parse(self, url: str) -> list[Resource]:
        try:
            with self._playwright_lock:
                self._respect_parse_cooldown()
                try:
                    return asyncio.run(self._parse_async(normalize_url(url)))
                finally:
                    self._last_parse_at = time.monotonic()
        except PluginParseError:
            raise
        except Exception as exc:
            raise PluginParseError(f"X 解析失败: {exc}") from exc

    def download(self, resource: Resource, save_path: Path) -> list[Path]:
        try:
            with self._playwright_lock:
                return asyncio.run(self._download_async(resource, save_path))
        except Exception as exc:
            raise PluginParseError(f"X 下载失败: {exc}") from exc

    def reset_playwright_session(self) -> None:
        """
        No-op for in-memory browser (none cached). Next ``parse()`` always opens Chromium against disk profile.
        Call after replacing ``runtime/x_cookies.json`` so operators know the logical session changed.
        """
        pass

    def _respect_parse_cooldown(self) -> None:
        elapsed = time.monotonic() - self._last_parse_at
        remain = float(self.config.parse_cooldown_seconds) - elapsed
        if 0 < remain < self.config.parse_cooldown_seconds:
            time.sleep(remain)

    async def _parse_async(self, url: str) -> list[Resource]:
        if not self.validate():
            raise PluginParseError("未安装 Playwright。请执行: pip install playwright && playwright install chromium")

        client = XPlaywrightClient(self.config)
        await client.start()
        try:
            if is_status_url(url):
                return await self._parse_single_tweet(client, url)
            if is_search_url(url):
                return await self._parse_search_media(client, url)

            user, kind = extract_username_and_kind(url)
            if user and kind == "media":
                return await self._parse_user_media(client, url, user)
            if user and kind == "profile":
                return await self._parse_user_timeline(client, url, user)

            raise PluginParseError(f"无法识别的 X 链接: {url}")
        finally:
            await client.close()

    async def _parse_single_tweet(self, client: XPlaywrightClient, url: str) -> list[Resource]:
        page = await client.new_page_goto(url)
        try:
            await client.raise_if_guest_wall(page)
            await client.wait_for_main_content(page)
            await client.wait_for_media(page)
            tweet_id = extract_tweet_id(url) or ""
            author = await client.get_author_info(page)
            media_urls = await client.extract_media_urls(page)
            content = await client.get_tweet_text(page)
            created = await client.get_tweet_date(page)
        finally:
            await page.close()

        if not media_urls:
            raise PluginParseError("未在该推文解析到媒体（可能需要登录或页面结构已变更）。")

        resources: list[Resource] = []
        for i, media_url in enumerate(media_urls):
            resources.append(
                Resource(
                    id=f"{tweet_id}_{i}",
                    url=url,
                    title=(content[:120] if content else f"tweet_{tweet_id}") or f"tweet_{tweet_id}",
                    author=author.get("name", ""),
                    author_id=author.get("username", ""),
                    files=[{"url": media_url, "type": self._media_type(media_url)}],
                    metadata={"tweet_id": tweet_id, "full_text": content, "author": author},
                    thumbnail=media_urls[0] if i == 0 else None,
                    created_at=created or None,
                )
            )
        return resources

    async def _parse_search_media(self, client: XPlaywrightClient, url: str) -> list[Resource]:
        page = await client.new_page_goto(url)
        try:
            await client.raise_if_guest_wall(page)
            media_urls = await client.scroll_and_collect_media(
                page,
                max_scrolls=self.config.media_scroll_rounds,
                max_items=self.config.max_media_items,
            )
        finally:
            await page.close()

        query = extract_search_query(url) or "search"
        resources: list[Resource] = []
        for i, media_url in enumerate(media_urls):
            resources.append(
                Resource(
                    id=f"search_{abs(hash(query))}_{i}",
                    url=url,
                    title=f"X search — {query}",
                    author=query,
                    author_id="search",
                    files=[{"url": media_url, "type": self._media_type(media_url)}],
                    metadata={"source": "search_media", "query": query},
                    thumbnail=media_url,
                    created_at=None,
                )
            )
        return resources

    async def _parse_user_media(self, client: XPlaywrightClient, url: str, username: str) -> list[Resource]:
        page = await client.new_page_goto(url)
        try:
            await client.raise_if_guest_wall(page)
            info = await client.get_user_display_name(page, username)
            media_urls = await client.scroll_and_collect_media(
                page,
                max_scrolls=self.config.media_scroll_rounds,
                max_items=self.config.max_media_items,
            )
        finally:
            await page.close()

        resources: list[Resource] = []
        for i, media_url in enumerate(media_urls):
            resources.append(
                Resource(
                    id=f"{username}_media_{i}",
                    url=url,
                    title=f"{info.get('name', username)} — media",
                    author=info.get("name", username),
                    author_id=username,
                    files=[{"url": media_url, "type": self._media_type(media_url)}],
                    metadata={"source": "media_tab", "username": username},
                    thumbnail=media_url,
                    created_at=None,
                )
            )
        return resources

    async def _parse_user_timeline(self, client: XPlaywrightClient, url: str, username: str) -> list[Resource]:
        page = await client.new_page_goto(url)
        try:
            await client.raise_if_guest_wall(page)
            tweet_urls = await client.scroll_and_collect_tweet_urls(
                page, limit=self.config.timeline_tweet_limit
            )
        finally:
            await page.close()

        all_res: list[Resource] = []
        for tw in tweet_urls:
            try:
                part = await self._parse_single_tweet(client, tw)
                all_res.extend(part)
            except Exception:
                continue
        return all_res

    async def _download_async(self, resource: Resource, save_path: Path) -> list[Path]:
        # HTTP-only path — no browser; avoids extra Chromium start for direct media URLs.
        client = XPlaywrightClient(self.config)
        out: list[Path] = []
        sub = resource.metadata.get("save_subdir")
        target_base = save_path / sub if sub else save_path
        cache = self._load_download_cache()
        batch_pause_after = random.randint(self.config.batch_pause_after_min, self.config.batch_pause_after_max)

        try:
            for index, item in enumerate(resource.files, start=1):
                file_url = item.get("url")
                if not file_url:
                    continue
                cached = cache.get(file_url)
                if cached and Path(cached).is_file():
                    out.append(Path(cached))
                    continue
                kind = item.get("type", "image")
                if kind == "video":
                    p = await client.download_video(file_url, target_base)
                else:
                    p = await client.download_image(file_url, target_base)
                if p is not None:
                    cache[file_url] = str(p)
                    out.append(p)
                    await asyncio.sleep(
                        random.uniform(self.config.download_delay_min, self.config.download_delay_max)
                        * random.uniform(0.8, 1.5)
                    )
                    if index % batch_pause_after == 0:
                        await asyncio.sleep(random.uniform(self.config.batch_pause_min, self.config.batch_pause_max))
                        batch_pause_after = random.randint(self.config.batch_pause_after_min, self.config.batch_pause_after_max)
            self._save_download_cache(cache)
            return out
        finally:
            await client.close()

    def _load_download_cache(self) -> dict[str, str]:
        try:
            if self._cache_file.is_file():
                payload = json.loads(self._cache_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return {str(k): str(v) for k, v in payload.items()}
        except Exception:
            pass
        return {}

    def _save_download_cache(self, payload: dict[str, str]) -> None:
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_interactive_login(
        self,
        *,
        proceed: threading.Event | None = None,
        ready_signal: queue.Queue[bool] | None = None,
    ) -> bool:
        """Browser login; for GUI pass ``ready_signal`` + ``threading.Event`` — see plugin panel."""
        path = Path(self.config.profile_dir)
        try:
            with self._playwright_lock:
                return asyncio.run(x_login_flow(path, proceed=proceed, ready_signal=ready_signal))
        except Exception:
            return False

    def set_headless(self, headless: bool) -> None:
        self.config.headless = headless

    @staticmethod
    def _media_type(url: str) -> str:
        low = url.lower()
        if "video" in low or ".mp4" in low or "video.twimg.com" in low:
            return "video"
        return "image"

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "headless": {"type": "bool", "label": "无头模式", "default": True},
            "timeline_tweet_limit": {"type": "int", "label": "主页推文采样条数", "default": 8, "min": 1, "max": 30},
            "media_scroll_rounds": {"type": "int", "label": "媒体页滚动轮数", "default": 5, "min": 1, "max": 12},
            "max_media_items": {"type": "int", "label": "单次最多媒体数", "default": 40, "min": 1, "max": 120},
            "parse_cooldown_seconds": {"type": "float", "label": "解析冷却秒数", "default": 25.0, "min": 0, "max": 180},
            "min_action_delay": {"type": "float", "label": "最小操作延迟秒数", "default": 3.0, "min": 0.5, "max": 30},
            "max_action_delay": {"type": "float", "label": "最大操作延迟秒数", "default": 8.0, "min": 1, "max": 60},
            "download_delay_min": {"type": "float", "label": "下载后最小延迟秒数", "default": 1.0, "min": 0, "max": 20},
            "download_delay_max": {"type": "float", "label": "下载后最大延迟秒数", "default": 3.0, "min": 0, "max": 30},
        }


plugin_class = XPlugin
