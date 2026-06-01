"""
Helpers for X (Twitter) web login flow in Playwright.

X often opens a combined sheet titled 「注册 X」 even from /i/flow/login; users must switch to
the actual sign-in path or they loop after entering email. These helpers only automate UI clicks
(no credential stuffing, no bypass).
"""

from __future__ import annotations

import asyncio
import re

from playwright.async_api import Page

LOGIN_URL_X = "https://x.com/i/flow/login"
LOGIN_URL_TWITTER = "https://twitter.com/i/flow/login"

# When X shows this, login inside Playwright often cannot proceed (rate limit / bot heuristics).
X_FLOW_ERROR_RECOVERY_HINT_CN = (
    "这是 X 端返回的通用错误，常见于自动化浏览器或风控，并不代表本程序损坏。\n\n"
    "可行做法（任选）：\n"
    "1）点击弹窗「重试」后等待几分钟再试，或更换网络（如手机热点）；\n"
    "2）在插件管理中「清除本 Profile 浏览器数据」后重新「手动登录」；\n"
    "3）用本机普通 Chrome 登录 x.com，导出 Cookie 为 JSON，放到 runtime/x_cookies.json，再点「同步到抓取」。\n\n"
    "我们无法绕过 X/Google 的风控策略。"
)


async def detect_x_generic_flow_error(page: Page) -> bool:
    """True if the grey modal 「出错了 / Something went wrong」 is likely visible."""
    try:
        dlg = page.locator('[role="dialog"]')
        if await dlg.count() > 0:
            text = (await dlg.first.inner_text())[:1200]
            low = text.lower()
            if ("出错了" in text or "something went wrong" in low) and (
                "重试" in text or "reload" in low or "retry" in low or "重新加载" in text
            ):
                return True
    except Exception:
        pass

    return False


async def prefer_login_over_signup(page: Page, *, max_attempts: int = 5) -> None:
    """
    If the modal looks like sign-up (注册 X / Sign up for X), try to switch to login.

    Safe to call multiple times; no-op when login sheet is already shown.
    """
    for _ in range(max_attempts):
        await asyncio.sleep(0.35)

        # Already looks like login-oriented sheet (best-effort)
        try:
            login_heading = page.get_by_role("heading", name=re.compile(r"登录.*X|Sign in to X|登录或注册", re.I))
            if await login_heading.count() > 0 and await login_heading.first.is_visible(timeout=400):
                return
        except Exception:
            pass

        signup_visible = False
        try:
            su = page.get_by_text(re.compile(r"注册 X|Sign up for X"), exact=False)
            if await su.count() > 0:
                signup_visible = await su.first.is_visible(timeout=500)
        except Exception:
            pass

        if not signup_visible:
            try:
                dlg = page.locator('[role="dialog"]')
                if await dlg.count() > 0:
                    t = await dlg.first.inner_text()
                    if "注册 X" in t or "Sign up for X" in t:
                        signup_visible = True
            except Exception:
                pass

        if not signup_visible:
            return

        clicked = await _try_click_login_switch(page)
        if not clicked:
            break


async def _try_click_login_switch(page: Page) -> bool:
    """Return True if a qualifying control was clicked."""

    # Known Twitter/X test ids (may change)
    for tid in ("signupSheetSwitchToLogin", "switch_to_login"):
        try:
            loc = page.locator(f'[data-testid="{tid}"]')
            if await loc.count() > 0 and await loc.first.is_visible(timeout=600):
                await loc.first.click(timeout=3000)
                await asyncio.sleep(0.8)
                return True
        except Exception:
            continue

    # Footer / inverse of 「还没有账号？注册」 → often 「登录」 link nearby
    try:
        inv = page.get_by_text(re.compile(r"已有账号|已有帐户|Already have an account", re.I))
        if await inv.count() > 0:
            await inv.first.click(timeout=3500)
            await asyncio.sleep(0.85)
            return True
    except Exception:
        pass

    try:
        plain = page.get_by_role("link", name=re.compile(r"^(登录|Sign in)$", re.I))
        if await plain.count() > 0:
            await plain.first.click(timeout=3500)
            await asyncio.sleep(0.85)
            return True
    except Exception:
        pass

    try:
        href_login = page.locator('a[href*="login"]').filter(has_not_text=re.compile(r"Google|Apple", re.I))
        if await href_login.count() > 0 and await href_login.first.is_visible(timeout=600):
            await href_login.first.click(timeout=3500)
            await asyncio.sleep(0.85)
            return True
    except Exception:
        pass

    try:
        link = page.locator('a[href="/login"]').first
        if await link.is_visible(timeout=600):
            await link.click(timeout=3500)
            await asyncio.sleep(0.85)
            return True
    except Exception:
        pass

    return False


async def goto_x_login_entry(page: Page, *, timeout: float = 90_000) -> None:
    """Navigate to official login flow and try to leave sign-up shell."""
    await page.goto(LOGIN_URL_X, wait_until="domcontentloaded", timeout=timeout)
    await prefer_login_over_signup(page)

    # Some locales keep showing 「注册 X」 on x.com; twitter.com entry occasionally differs.
    try:
        su = page.get_by_text(re.compile(r"注册 X|Sign up for X"), exact=False)
        if await su.count() > 0 and await su.first.is_visible(timeout=700):
            await page.goto(LOGIN_URL_TWITTER, wait_until="domcontentloaded", timeout=timeout)
            await prefer_login_over_signup(page)
    except Exception:
        pass
