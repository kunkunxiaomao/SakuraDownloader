"""Playwright helpers for X (Twitter) — uses BrowserContext from launch_persistent_context."""

from __future__ import annotations

import asyncio
import json
import random
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from .browser_opts import launch_x_persistent_context

from pixiv_app.core.plugin.base import PluginParseError

try:
    from fingerprint import default_profile
except Exception:  # pragma: no cover
    default_profile = None

_GUEST_WALL_HINT = (
    "X 要求先登录（检测到登录或注册弹窗）。\n"
    "请在「插件管理」点击「X 登录」完成登录（会话在 runtime/x_profile），"
    "或将 Cookie 导出为 JSON 保存到 runtime/x_cookies.json 后重试。\n"
    "若窗口停在「注册 X」界面，请点击「已有账号？登录」或「Sign in」切换到登录再输入账号。"
)

if TYPE_CHECKING:
    pass


class XPlaywrightClient:
    def __init__(self, config: Any) -> None:
        self.config = config
        self._pw: Any = None
        self.context: BrowserContext | None = None

    async def start(self) -> None:
        if self.context is not None:
            return
        self._pw = await async_playwright().start()
        profile = Path(self.config.profile_dir)
        profile.mkdir(parents=True, exist_ok=True)

        self.context = await launch_x_persistent_context(
            self._pw,
            profile,
            headless=self.config.headless,
        )

        cookie_path = Path(self.config.cookie_file)
        if cookie_path.is_file():
            try:
                raw = json.loads(cookie_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    await self.context.add_cookies(raw)
            except (json.JSONDecodeError, OSError):
                pass

    async def close(self) -> None:
        if self.context is not None:
            await self.context.close()
            self.context = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def new_page_goto(self, url: str, *, wait_until: str = "domcontentloaded") -> Page:
        assert self.context is not None
        page = await self.context.new_page()
        timeout = getattr(self.config, "wait_timeout", 30000)
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
        except PlaywrightTimeoutError as exc:
            # X can keep the document in a long-loading state while usable DOM is already present.
            # If navigation reached a real URL, continue and let the parser inspect the page.
            if page.url and page.url != "about:blank":
                await asyncio.sleep(2.0)
                return page
            try:
                await page.goto(url, wait_until="commit", timeout=min(timeout, 15000))
            except PlaywrightTimeoutError as retry_exc:
                raise PluginParseError(
                    "X 页面加载超时：可能是访问过快触发风控、网络慢，或 Cookie 会话被 X 拦截。"
                    "请等待 1-3 分钟后重试；若仍失败，重新导入 X cookies.txt。"
                ) from retry_exc
            except Exception as retry_exc:
                raise PluginParseError(f"X 页面加载失败: {retry_exc}") from retry_exc
            if page.url == "about:blank":
                raise PluginParseError("X 页面没有成功打开，请稍后重试。") from exc
        await self.human_pause(2.0, 4.5)
        await self.move_mouse_softly(page)
        return page

    async def human_pause(self, low: float | None = None, high: float | None = None) -> None:
        low = float(low if low is not None else getattr(self.config, "min_action_delay", 3.0))
        high = float(high if high is not None else getattr(self.config, "max_action_delay", 8.0))
        if high < low:
            high = low
        await asyncio.sleep(random.uniform(low, high))

    async def move_mouse_softly(self, page: Page) -> None:
        try:
            viewport = page.viewport_size or {"width": 1280, "height": 800}
            width = int(viewport.get("width", 1280))
            height = int(viewport.get("height", 800))
            for _ in range(random.randint(1, 3)):
                x = random.randint(max(12, width // 8), max(20, width - 24))
                y = random.randint(max(12, height // 8), max(20, height - 24))
                await page.mouse.move(x, y, steps=random.randint(8, 18))
                await asyncio.sleep(random.uniform(0.2, 0.7))
        except Exception:
            pass

    async def human_scroll_page(self, page: Page) -> float:
        await self.move_mouse_softly(page)
        viewport = page.viewport_size or {"height": 800}
        height = int(viewport.get("height", 800))
        delta = random.randint(max(260, height // 2), max(420, int(height * 1.2)))
        if random.random() < 0.12:
            delta = -random.randint(120, 280)
        await page.mouse.wheel(0, delta)
        if random.random() < 0.07:
            await asyncio.sleep(random.uniform(30.0, 90.0))
        await self.human_pause()
        return float(await page.evaluate("document.body.scrollHeight"))

    async def raise_if_guest_wall(self, page: Page) -> None:
        """
        Fail fast when X shows login/signup overlay (guest cannot see timeline/media).
        Avoids long waits on wait_for_main_content / empty scroll loops.
        """
        await asyncio.sleep(0.45)
        try:
            url = page.url or ""
            if "/i/flow/login" in url or "/i/flow/signup" in url:
                raise PluginParseError(_GUEST_WALL_HINT)
        except PluginParseError:
            raise
        except Exception:
            pass

        try:
            wall = page.get_by_text(
                re.compile(
                    r"注册 X|Sign up for X|使用 Google 账号登录|Sign in with Google|"
                    r"手机号码、邮件地址或用户名|Phone, email, or username",
                    re.I,
                )
            ).first
            if await wall.is_visible(timeout=2200):
                raise PluginParseError(_GUEST_WALL_HINT)
        except PluginParseError:
            raise
        except Exception:
            pass

        try:
            dialog = page.locator('[role="dialog"]').first
            if await dialog.is_visible(timeout=600):
                inner = (await dialog.inner_text())[:800]
                if ("注册" in inner or "Sign up" in inner) and (
                    "Google" in inner or "Apple" in inner or "下一步" in inner or "Next" in inner
                ):
                    raise PluginParseError(_GUEST_WALL_HINT)
        except PluginParseError:
            raise
        except Exception:
            pass

    async def wait_for_main_content(self, page: Page) -> None:
        timeout = min(15000, getattr(self.config, "wait_timeout", 30000))
        for sel in ('article[data-testid="tweet"]', "article", '[data-testid="tweetPhoto"]'):
            try:
                await page.wait_for_selector(sel, timeout=timeout)
                return
            except Exception:
                continue

    async def wait_for_media(self, page: Page) -> None:
        timeout = 10000
        for sel in (
            '[data-testid="tweetPhoto"]',
            'img[src*="pbs.twimg.com/media"]',
            "video",
            "video source",
        ):
            try:
                await page.wait_for_selector(sel, timeout=timeout)
                return
            except Exception:
                continue

    async def extract_media_urls(self, page: Page) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()

        imgs = await page.query_selector_all('img[src*="pbs.twimg.com/media"]')
        for img in imgs:
            src = await img.get_attribute("src")
            if src and "profile_images" not in src:
                orig = self._original_tw_image_url(src)
                if orig and orig not in seen:
                    seen.add(orig)
                    urls.append(orig)

        for sel in ('[data-testid="tweetPhoto"] img', "article img"):
            elements = await page.query_selector_all(sel)
            for el in elements:
                src = await el.get_attribute("src")
                if src and "pbs.twimg.com/media" in src:
                    orig = self._original_tw_image_url(src)
                    if orig and orig not in seen:
                        seen.add(orig)
                        urls.append(orig)

        videos = await page.query_selector_all("video source")
        for v in videos:
            src = await v.get_attribute("src")
            if src and src not in seen:
                seen.add(src)
                urls.append(src)

        return urls

    async def scroll_and_collect_media(self, page: Page, max_scrolls: int = 12, max_items: int = 40) -> list[str]:
        collected: list[str] = []
        seen: set[str] = set()
        last_height = 0.0
        for _ in range(max_scrolls):
            batch = await self.extract_media_urls(page)
            for u in batch:
                if u not in seen:
                    seen.add(u)
                    collected.append(u)
                    if len(collected) >= max_items:
                        return collected
            try:
                height = await self.human_scroll_page(page)
            except Exception:
                break
            if height == last_height:
                break
            last_height = float(height)
        return collected

    async def scroll_and_collect_tweet_urls(self, page: Page, limit: int = 40) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        stale_rounds = 0
        max_rounds = 25

        for _ in range(max_rounds):
            if len(found) >= limit:
                break
            before = len(found)
            links = await page.query_selector_all('a[href*="/status/"]')
            for link in links:
                href = await link.get_attribute("href")
                if not href or "/status/" not in href:
                    continue
                if href.startswith("/"):
                    full = f"https://x.com{href}"
                elif href.startswith("http"):
                    full = href
                else:
                    continue
                full = re.sub(r"\?.*$", "", full.split("#")[0])
                if full not in seen:
                    seen.add(full)
                    found.append(full)
                    if len(found) >= limit:
                        break

            if len(found) == before:
                stale_rounds += 1
                if stale_rounds >= 5:
                    break
            else:
                stale_rounds = 0

            try:
                await self.human_scroll_page(page)
            except Exception:
                break

        return found[:limit]

    async def get_author_info(self, page: Page) -> dict[str, str]:
        info = {"name": "", "username": ""}
        try:
            block = await page.query_selector('[data-testid="User-Name"]')
            if block:
                text = await block.inner_text()
                lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
                if lines:
                    info["name"] = lines[0]
                for ln in lines:
                    if ln.startswith("@"):
                        info["username"] = ln.lstrip("@")
                        break
        except Exception:
            pass
        return info

    async def get_user_display_name(self, page: Page, fallback: str) -> dict[str, str]:
        info = {"name": fallback, "username": fallback}
        try:
            h = await page.query_selector('[data-testid="UserName"]')
            if h:
                t = await h.inner_text()
                lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
                if lines:
                    info["name"] = lines[0]
        except Exception:
            pass
        return info

    async def get_tweet_text(self, page: Page) -> str:
        try:
            el = await page.query_selector('[data-testid="tweetText"]')
            if el:
                return (await el.inner_text()).strip()
        except Exception:
            pass
        return ""

    async def get_tweet_date(self, page: Page) -> str:
        try:
            t = await page.query_selector("article time")
            if t:
                return (await t.get_attribute("datetime")) or ""
        except Exception:
            pass
        return ""

    async def download_image(self, url: str, save_path: Path) -> Path | None:
        save_path.mkdir(parents=True, exist_ok=True)
        name = url.split("/")[-1].split("?")[0]
        if not any(name.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            name += ".jpg"
        target = save_path / name
        if target.exists() and target.stat().st_size > 256:
            return target
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": self._ua()})
            if response.status_code == 200:
                target.write_bytes(response.content)
                return target
        return None

    async def download_video(self, url: str, save_path: Path) -> Path | None:
        save_path.mkdir(parents=True, exist_ok=True)
        name = url.split("/")[-1].split("?")[0]
        if not name.endswith(".mp4"):
            name += ".mp4"
        target = save_path / name
        if target.exists() and target.stat().st_size > 1024:
            return target
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": self._ua()})
            if response.status_code == 200:
                target.write_bytes(response.content)
                return target
        return None

    def _ua(self) -> str:
        if default_profile:
            return default_profile("x").user_agent
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

    @staticmethod
    def _original_tw_image_url(url: str) -> str:
        if "pbs.twimg.com/media" not in url:
            return url
        base = url.split("?")[0]
        return f"{base}?format=jpg&name=orig" if "format=" not in url else re.sub(
            r"name=\w+", "name=orig", url
        )
