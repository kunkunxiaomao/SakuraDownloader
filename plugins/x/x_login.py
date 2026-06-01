"""Interactive X login via persistent Chromium profile."""

from __future__ import annotations

import asyncio
import queue
import shutil
import threading
from pathlib import Path

from playwright.async_api import async_playwright

from .browser_opts import launch_x_persistent_context
from pixiv_app.services.x_login_flow import goto_x_login_entry


async def run_interactive_login(
    profile_dir: Path,
    *,
    proceed: threading.Event | None = None,
    ready_signal: queue.Queue[bool] | None = None,
) -> bool:
    """
    Open visible Chromium with persistent ``profile_dir``.

    Optionally notify GUI via ``ready_signal`` after the login page loads, then block until
    ``proceed`` is set (e.g. user clicked OK after logging in). If ``proceed`` is None,
    falls back to terminal ``input()``.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    context = None
    loop = asyncio.get_running_loop()
    try:
        context = await launch_x_persistent_context(playwright, profile_dir, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await goto_x_login_entry(page, timeout=60_000)

        if ready_signal is not None:
            ready_signal.put(True)

        if proceed is not None:
            await loop.run_in_executor(None, proceed.wait)
        else:
            await loop.run_in_executor(
                None,
                lambda: input("\n在浏览器中完成 X 登录后，回到控制台按 Enter...\n"),
            )
        return True
    finally:
        if context is not None:
            await context.close()
        await playwright.stop()


def clear_profile(profile_dir: Path) -> None:
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
