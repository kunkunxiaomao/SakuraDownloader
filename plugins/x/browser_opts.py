"""Shared Playwright launch options for X — reduce automation fingerprint; prefer system Chrome."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright

try:
    from fingerprint import default_profile
except Exception:  # pragma: no cover - plugin can run standalone
    default_profile = None

try:
    from proxy_pool import parse_proxy_url
except Exception:  # pragma: no cover
    parse_proxy_url = None


async def launch_x_persistent_context(
    playwright: Playwright,
    profile_dir: Path,
    *,
    headless: bool,
) -> BrowserContext:
    """
    Launch persistent Chromium with flags that reduce obvious automation signals.

    Order: ``X_PLAYWRIGHT_CHANNEL`` env (if set), then system Chrome, then bundled Chromium.
    Google OAuth often still blocks automation browsers; users should use X email/phone login
    or import cookies from a normal browser session.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    browser_profile = default_profile("x") if default_profile else None

    base_kw: dict = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "viewport": browser_profile.viewport if browser_profile else {"width": 1280, "height": 800},
        "locale": browser_profile.locale if browser_profile else "zh-CN",
        "timezone_id": browser_profile.timezone_id if browser_profile else "Asia/Shanghai",
        "ignore_default_args": ["--enable-automation"],
        "args": [
            "--disable-blink-features=AutomationControlled",
        ],
        "user_agent": browser_profile.user_agent
        if browser_profile
        else (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    if browser_profile:
        base_kw["device_scale_factor"] = browser_profile.device_scale_factor
        base_kw["is_mobile"] = browser_profile.is_mobile
        base_kw["has_touch"] = browser_profile.has_touch

    proxy_value = os.environ.get("X_PROXY_URL", os.environ.get("PIXIV_BROWSER_PROXY", "")).strip()
    if proxy_value and parse_proxy_url:
        parsed_proxy = parse_proxy_url(proxy_value)
        if parsed_proxy is not None:
            base_kw["proxy"] = parsed_proxy.to_playwright_proxy()

    env_ch = os.environ.get("X_PLAYWRIGHT_CHANNEL", "").strip()
    seen: set[str | None] = set()
    attempts: list[str | None] = []
    for ch in (env_ch or None, "chrome", None):
        key = ch if ch is not None else "__default__"
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
            ctx = await playwright.chromium.launch_persistent_context(**kw)
            await _apply_stealth_scripts(ctx)
            return ctx
        except Exception as exc:
            last_err = exc
            continue

    assert last_err is not None
    raise last_err


async def _apply_stealth_scripts(ctx: BrowserContext) -> None:
    """Best-effort scripts; cannot guarantee OAuth providers will accept the browser."""
    await ctx.add_init_script(
        """
        try {
          Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        } catch (e) {}
        """
    )
