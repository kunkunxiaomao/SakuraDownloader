"""
X (Twitter) authentication helper — Playwright **persistent context** only.

- First-time login is always manual (including email verification); no credential stuffing.
- Session persistence is Chromium's user_data_dir (cookies + localStorage live inside it).
- Optional JSON cookie export into ``profiles/x/<profile>/cookies/`` for backup only.

Directory layout (created automatically)::

    profiles/
      x/
        <profile_key>/
          chromium_profile/    # user_data_dir → persistent session (real storage)
          cookies/             # optional Playwright cookie exports (JSON)
          localStorage/        # reserved / notes (Chromium keeps LS inside chromium_profile)

Usage::

    auth = XAuthManager(profile_key="default")
    await auth.start_session()
    page = await auth.get_page()
    await page.goto("https://x.com/i/flow/login")
    # ... user completes login manually ...
    if await auth.is_logged_in():
        ...
    await auth.close()
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from pixiv_app.core.paths import profiles_root

logger = logging.getLogger(__name__)


def _default_profiles_root() -> Path:
    return profiles_root()


class XAuthManager:
    """
    Manage a long-lived Playwright Chromium **persistent** profile for x.com.

    Not thread-safe: use one instance per logical browser session, or guard externally.
    """

    HOME_URL = "https://x.com/home"
    LOGIN_FLOW_HINT = "https://x.com/i/flow/login"

    def __init__(
        self,
        *,
        profile_key: str = "default",
        profiles_root: Path | None = None,
        headless: bool = True,
        auto_reconnect: bool = True,
        max_health_retries: int = 2,
    ) -> None:
        """
        :param profile_key: Subdirectory under ``profiles/x/`` for multi-account switching.
        :param profiles_root: Defaults to ``<project>/profiles``.
        :param headless: Headless for automated checks; use ``False`` for first manual login.
        :param auto_reconnect: If ``start_session`` finds a dead context, restart Playwright.
        :param max_health_retries: Retries when validating session after transient failures.
        """
        self._profile_key = profile_key
        root = profiles_root or _default_profiles_root()
        self._profile_home = root / "x" / profile_key
        self._user_data_dir = self._profile_home / "chromium_profile"
        self._cookies_dir = self._profile_home / "cookies"
        self._localstorage_dir = self._profile_home / "localStorage"

        self._headless = headless
        self._auto_reconnect = auto_reconnect
        self._max_health_retries = max(1, max_health_retries)

        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None
        self._primary_page: Page | None = None

        self._ensure_storage_layout()

    def _ensure_storage_layout(self) -> None:
        """Create profile dirs. Real cookie/localStorage persistence is under ``chromium_profile``."""
        self._user_data_dir.mkdir(parents=True, exist_ok=True)
        self._cookies_dir.mkdir(parents=True, exist_ok=True)
        self._localstorage_dir.mkdir(parents=True, exist_ok=True)

    @property
    def user_data_dir(self) -> Path:
        """Playwright ``user_data_dir`` passed to ``launch_persistent_context``."""
        return self._user_data_dir

    def _context_alive(self) -> bool:
        if self._context is None:
            return False
        try:
            _ = self._context.pages
            return True
        except Exception:
            return False

    async def start_session(self, *, force_new: bool = False) -> BrowserContext:
        """
        Launch or reuse persistent Chromium. Session is restored from disk automatically.

        :param force_new: Close existing context and open a fresh one (same profile dir).
        """
        if self._context_alive() and not force_new:
            return self._context  # type: ignore[return-value]

        if self._context is not None:
            await self.close()

        self._pw = await async_playwright().start()
        try:
            self._context = await self._launch_persistent(self._pw)
        except Exception:
            await self._pw.stop()
            self._pw = None
            raise

        self._primary_page = None
        logger.info("XAuthManager: persistent context ready (%s)", self._user_data_dir)
        return self._context

    async def _launch_persistent(self, pw: Playwright) -> BrowserContext:
        """Single place for launch_persistent_context configuration."""
        self._user_data_dir.mkdir(parents=True, exist_ok=True)

        base_kw: dict[str, Any] = {
            "user_data_dir": str(self._user_data_dir),
            "headless": self._headless,
            "viewport": {"width": 1280, "height": 800},
            "locale": "zh-CN",
            "ignore_default_args": ["--enable-automation"],
            "args": ["--disable-blink-features=AutomationControlled"],
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        import os

        env_ch = os.environ.get("X_PLAYWRIGHT_CHANNEL", "").strip()
        attempts: list[str | None] = []
        seen: set[str | None] = set()
        for ch in (env_ch or None, "chrome", None):
            key = ch if ch is not None else "__bundled__"
            if key in seen:
                continue
            seen.add(key)
            attempts.append(ch)

        last_err: Exception | None = None
        for channel in attempts:
            kw = dict(base_kw)
            if channel:
                kw["channel"] = channel
            try:
                ctx = await pw.chromium.launch_persistent_context(**kw)
                await ctx.add_init_script(
                    """
                    try {
                      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    } catch (e) {}
                    """
                )
                return ctx
            except Exception as exc:
                last_err = exc
                logger.warning("launch_persistent_context failed (%s): %s", channel, exc)
                continue

        assert last_err is not None
        raise last_err

    async def get_context(self) -> BrowserContext:
        if not self._context_alive():
            if self._auto_reconnect:
                return await self.start_session()
            raise RuntimeError("No browser session; call start_session() first.")
        return self._context  # type: ignore[return-value]

    async def get_page(self, *, new: bool = False) -> Page:
        """
        Return a page for navigation. Default reuses one tab; set ``new=True`` for a fresh tab.
        """
        ctx = await self.get_context()
        if new:
            return await ctx.new_page()

        if self._primary_page is not None:
            try:
                _ = self._primary_page.url
                return self._primary_page
            except Exception:
                self._primary_page = None

        if ctx.pages:
            self._primary_page = ctx.pages[0]
            return self._primary_page

        self._primary_page = await ctx.new_page()
        return self._primary_page

    async def is_logged_in(self, *, page: Page | None = None, navigate: bool = True) -> bool:
        """
        Heuristic login detection (DOM + URL). Not guaranteed if X changes UI.

        - ``False`` if still on login / signup flow URLs or login UI is visible.
        - ``True`` if home timeline / primary column or account UI is present.
        """
        p = page if page is not None else await self.get_page()

        if navigate:
            await p.goto(self.HOME_URL, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(0.6)

        url = (p.url or "").lower()
        if any(
            x in url
            for x in (
                "/i/flow/login",
                "/i/flow/signup",
                "/login",
            )
        ):
            return False

        # Logged-in indicators (best-effort; X changes selectors periodically)
        logged_in_selectors = (
            '[data-testid="SideNav_AccountSwitcher_Button"]',
            '[data-testid="AppTabBar_Home_Link"]',
            '[data-testid="primaryColumn"]',
            '[data-testid="tweet"]',
        )
        for sel in logged_in_selectors:
            try:
                el = await p.wait_for_selector(sel, timeout=2500, state="visible")
                if el:
                    return True
            except Exception:
                continue

        # Login / gate modal text (guest wall)
        try:
            guest = p.get_by_text(
                "Sign in to X",
                exact=False,
            )
            if await guest.count() and await guest.first.is_visible(timeout=800):
                return False
        except Exception:
            pass

        return False

    async def session_healthy(self) -> bool:
        """True if browser context responds and ``is_logged_in`` succeeds within retries."""
        for attempt in range(self._max_health_retries):
            try:
                if not self._context_alive():
                    return False
                return await self.is_logged_in(navigate=True)
            except Exception as exc:
                logger.debug("session_healthy attempt %s: %s", attempt + 1, exc)
                await asyncio.sleep(1.0)
        return False

    async def export_playwright_cookies(self, filename: str = "playwright_cookies.json") -> Path:
        """Optional backup: write context cookies to ``profiles/x/<key>/cookies/``."""
        ctx = await self.get_context()
        cookies = await ctx.cookies()
        path = self._cookies_dir / filename
        path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        return path

    async def close(self) -> None:
        """Close Playwright; persistent data remains on disk for next run."""
        if self._primary_page is not None:
            try:
                await self._primary_page.close()
            except Exception:
                pass
            self._primary_page = None

        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

        logger.info("XAuthManager: closed (%s)", self._profile_key)

    # --- sync helpers for Tkinter / scripts without asyncio ---

    def start_session_sync(self, *, force_new: bool = False) -> BrowserContext:
        return asyncio.run(self.start_session(force_new=force_new))

    def get_page_sync(self, *, new: bool = False) -> Page:
        return asyncio.run(self.get_page(new=new))

    def is_logged_in_sync(self, *, navigate: bool = True) -> bool:
        return asyncio.run(self.is_logged_in(navigate=navigate))

    def close_sync(self) -> None:
        asyncio.run(self.close())


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    auth = XAuthManager(profile_key="default", headless=False)
    try:
        await auth.start_session()
        page = await auth.get_page()
        await page.goto(XAuthManager.LOGIN_FLOW_HINT, wait_until="domcontentloaded")
        print(
            "浏览器已打开登录流程。请在窗口中手动完成登录（含邮箱验证），完成后回到终端按 Enter…",
        )
        await asyncio.to_thread(input)
        ok = await auth.is_logged_in()
        print("is_logged_in:", ok)
        if ok:
            path = await auth.export_playwright_cookies()
            print("Cookies 备份:", path)
    finally:
        await auth.close()


if __name__ == "__main__":
    asyncio.run(_demo())
