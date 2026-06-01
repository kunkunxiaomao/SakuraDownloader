# plugins/xiaohongshu/plugin.py
"""
小红书爬虫插件 - 核心功能
- Playwright 浏览器自动化
- 动态代理池轮换
- 浏览器指纹伪装
- 行为节奏模拟
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

# 动态加载 playwright
try:
    from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from pixiv_app.core.plugin.base import BasePlugin, PluginParseError, Resource
from pixiv_app.core.paths import runtime_path

try:
    from fingerprint import BrowserProfile, SessionFingerprintManager
except Exception:  # pragma: no cover
    BrowserProfile = None
    SessionFingerprintManager = None


def _extract_note_id_from_text(text: str) -> str:
    patterns = [
        r"xiaohongshu\.com/explore/([a-zA-Z0-9]+)",
        r"/explore/([a-zA-Z0-9]+)",
        r"note_id[=:]([a-zA-Z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _extract_redirect_path(url: str) -> str:
    try:
        query = parse_qs(urlparse(url).query)
        value = (query.get("redirectPath") or [""])[0]
    except Exception:
        value = ""
    value = unquote(value or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return "https://www.xiaohongshu.com" + value
    if "xiaohongshu.com" in value.lower() or "xhslink.com" in value.lower():
        return value if "://" in value else "https://" + value.lstrip("/")
    return ""


def _normalize_xhs_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return "https://www.xiaohongshu.com" + value
    lower = value.lower()
    if ("xiaohongshu.com" in lower or "xhslink.com" in lower) and "://" not in value:
        return "https://" + value.lstrip("/")
    return value


# ==================== 配置定义 ====================

@dataclass
class ProxyConfig:
    """代理配置"""
    server: str  # http://ip:port 或 socks5://ip:port
    username: str = ""
    password: str = ""
    failures: int = 0
    successes: int = 0
    latency_ms: float = 0.0
    healthy: bool = True

    @property
    def url(self) -> str:
        if self.username and self.password:
            # 带认证的代理
            return self.server.replace("://", f"://{self.username}:{self.password}@")
        return self.server

    @property
    def score(self) -> float:
        """计算代理评分（用于加权选择）"""
        if not self.healthy:
            return 0.0
        total = self.successes + self.failures
        if total == 0:
            return 1.0
        success_rate = self.successes / total
        latency_penalty = max(0.1, min(1.0, 1000.0 / max(self.latency_ms, 1.0)))
        return success_rate * latency_penalty


@dataclass
class FingerprintConfig:
    """浏览器指纹配置"""
    user_agent: str
    viewport: dict[str, int]
    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    color_scheme: str = "light"
    reduced_motion: str = "no-preference"
    device_scale_factor: float = 1.0
    is_mobile: bool = False
    has_touch: bool = False

    @classmethod
    def from_browser_profile(cls, profile: Any) -> "FingerprintConfig":
        return cls(
            user_agent=profile.user_agent,
            viewport=dict(profile.viewport),
            locale=profile.locale,
            timezone_id=profile.timezone_id,
            color_scheme=profile.color_scheme,
            reduced_motion=profile.reduced_motion,
            device_scale_factor=profile.device_scale_factor,
            is_mobile=profile.is_mobile,
            has_touch=profile.has_touch,
        )

    @classmethod
    def desktop_chrome(cls) -> "FingerprintConfig":
        return cls(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1.0,
        )

    @classmethod
    def desktop_edge(cls) -> "FingerprintConfig":
        return cls(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
            viewport={"width": 1536, "height": 864},
            device_scale_factor=1.25,
        )

    @classmethod
    def mac_chrome(cls) -> "FingerprintConfig":
        return cls(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1512, "height": 982},
            device_scale_factor=2.0,
        )

    @classmethod
    def mobile_ios(cls) -> "FingerprintConfig":
        return cls(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            viewport={"width": 390, "height": 844},
            device_scale_factor=3.0,
            is_mobile=True,
            has_touch=True,
        )


@dataclass
class XiaohongshuConfig:
    """小红书插件配置"""
    # 代理配置
    proxy_enabled: bool = False
    proxy_list: list[ProxyConfig] = field(default_factory=list)
    proxy_rotation: str = "round_robin"  # round_robin, random, session_sticky

    # 指纹配置
    fingerprint_list: list[FingerprintConfig] = field(default_factory=lambda: [
        FingerprintConfig.desktop_chrome(),
        FingerprintConfig.desktop_edge(),
        FingerprintConfig.mac_chrome(),
    ])

    # 行为控制
    request_delay_min: float = 3.0  # 最小延迟(秒)
    request_delay_max: float = 8.0  # 最大延迟(秒)
    scroll_delay_min: float = 0.5  # 滚动延迟(秒)
    scroll_delay_max: float = 1.5
    batch_pause_min: float = 5.0  # 批次暂停(秒)
    batch_pause_max: float = 12.0
    max_scrolls: int = 15  # 最大滚动次数

    # 采集限制
    max_notes_per_session: int = 50  # 单会话最大笔记数
    max_concurrent: int = 2  # 最大并发数

    # 会话持久化
    profile_dir: str = "runtime/xiaohongshu_profile"
    cookie_file: str = "runtime/xiaohongshu_cookies.json"


# ==================== 代理池管理器 ====================

class ProxyPool:
    """代理池管理器 - 支持轮换和健康检查"""

    def __init__(self, config: XiaohongshuConfig):
        self.config = config
        self._proxies: list[ProxyConfig] = list(config.proxy_list)
        self._current_index = 0
        self._lock = threading.Lock()

    def add_proxy(self, proxy: ProxyConfig) -> None:
        """添加代理"""
        with self._lock:
            # 去重
            existing = {p.server for p in self._proxies}
            if proxy.server not in existing:
                self._proxies.append(proxy)

    def get_proxy(self) -> Optional[ProxyConfig]:
        """获取一个代理（按策略）"""
        if not self.config.proxy_enabled or not self._proxies:
            return None

        with self._lock:
            healthy = [p for p in self._proxies if p.healthy]
            if not healthy:
                return None

            if self.config.proxy_rotation == "random":
                return random.choice(healthy)

            # round_robin
            proxy = healthy[self._current_index % len(healthy)]
            self._current_index += 1
            return proxy

    def mark_success(self, proxy: Optional[ProxyConfig], latency_ms: float = 0) -> None:
        """标记代理成功"""
        if proxy is None:
            return
        with self._lock:
            proxy.successes += 1
            proxy.failures = 0
            proxy.healthy = True
            if latency_ms > 0:
                proxy.latency_ms = latency_ms

    def mark_failed(self, proxy: Optional[ProxyConfig]) -> None:
        """标记代理失败"""
        if proxy is None:
            return
        with self._lock:
            proxy.failures += 1
            if proxy.failures >= 3:
                proxy.healthy = False

    def healthy_count(self) -> int:
        """可用代理数量"""
        with self._lock:
            return len([p for p in self._proxies if p.healthy])

    def total_count(self) -> int:
        return len(self._proxies)


# ==================== 指纹管理器 ====================

class FingerprintManager:
    """浏览器指纹管理器"""

    def __init__(self, config: XiaohongshuConfig):
        self.config = config
        self._fingerprints = list(config.fingerprint_list)
        self._current_index = 0
        self._shared = SessionFingerprintManager(strategy="sticky") if SessionFingerprintManager else None

    def get_fingerprint(self, session_id: str = "default") -> FingerprintConfig:
        """获取一个指纹"""
        if self._shared is not None:
            return FingerprintConfig.from_browser_profile(self._shared.get_fingerprint(f"xiaohongshu:{session_id}"))
        if not self._fingerprints:
            return FingerprintConfig.desktop_chrome()
        index = abs(hash(session_id)) % len(self._fingerprints)
        return self._fingerprints[index]

    def apply_to_context(self, context: BrowserContext, fingerprint: FingerprintConfig) -> None:
        """将指纹应用到浏览器上下文"""
        # 注意：viewport 在创建 context 时设置，这里只做额外配置
        pass


# ==================== 浏览器会话管理器 ====================

class XiaohongshuSession:
    """小红书浏览器会话 - 管理单个 IP+指纹 的会话"""

    def __init__(
            self,
            session_id: str,
            proxy_pool: ProxyPool,
            fingerprint_manager: FingerprintManager,
            config: XiaohongshuConfig,
    ):
        self.session_id = session_id
        self.proxy_pool = proxy_pool
        self.fingerprint_manager = fingerprint_manager
        self.config = config

        self._pw: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._current_proxy: Optional[ProxyConfig] = None
        self._fingerprint: Optional[FingerprintConfig] = None

        self.request_count = 0
        self.created_at = datetime.now()

    async def start(self) -> None:
        """启动会话（创建独立的浏览器上下文）"""
        self._pw = await async_playwright().start()

        # 获取代理和指纹
        self._current_proxy = self.proxy_pool.get_proxy()
        self._fingerprint = self.fingerprint_manager.get_fingerprint(self.session_id)

        # 构建 context 参数
        context_options = {
            "viewport": self._fingerprint.viewport,
            "user_agent": self._fingerprint.user_agent,
            "locale": self._fingerprint.locale,
            "timezone_id": self._fingerprint.timezone_id,
            "color_scheme": self._fingerprint.color_scheme,
            "reduced_motion": self._fingerprint.reduced_motion,
            "device_scale_factor": self._fingerprint.device_scale_factor,
            "is_mobile": self._fingerprint.is_mobile,
            "has_touch": self._fingerprint.has_touch,
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        }

        # 添加代理配置
        if self._current_proxy:
            context_options["proxy"] = {
                "server": self._current_proxy.server,
            }
            if self._current_proxy.username:
                context_options["proxy"]["username"] = self._current_proxy.username
                context_options["proxy"]["password"] = self._current_proxy.password

        # 创建上下文
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(Path(self.config.profile_dir) / self.session_id),
            headless=False,  # 小红书能检测无头模式，必须 False
            **context_options
        )

        # 加载保存的 Cookie
        await self._load_cookies()

        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    async def _load_cookies(self) -> None:
        """加载保存的 Cookie"""
        cookie_path = Path(self.config.cookie_file)
        if cookie_path.exists():
            try:
                cookies = json.loads(cookie_path.read_text(encoding="utf-8"))
                if isinstance(cookies, list):
                    await self._context.add_cookies(cookies)
            except Exception:
                pass

    async def _save_cookies(self) -> None:
        """保存 Cookie 到文件"""
        if self._context:
            cookies = await self._context.cookies()
            Path(self.config.cookie_file).write_text(
                json.dumps(cookies, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )

    async def get_page(self) -> Page:
        """获取页面对象"""
        if self._page is None:
            await self.start()
        return self._page

    async def random_delay(self) -> None:
        """随机延迟（模拟真人节奏）"""
        delay = random.uniform(self.config.request_delay_min, self.config.request_delay_max)
        await asyncio.sleep(delay)
        self.request_count += 1

    async def random_scroll(self) -> None:
        """随机滚动页面"""
        scroll_y = random.randint(220, 620)
        try:
            await self._page.mouse.move(random.randint(80, 900), random.randint(120, 700), steps=random.randint(8, 18))
            await self._page.mouse.wheel(0, scroll_y)
        except Exception:
            await self._page.evaluate(f"window.scrollBy(0, {scroll_y})")
        await asyncio.sleep(random.uniform(self.config.scroll_delay_min, self.config.scroll_delay_max))

    async def simulate_browsing(self, duration: float = 2.0) -> None:
        """模拟浏览行为（停留、思考）"""
        await asyncio.sleep(duration * random.uniform(0.7, 1.3))

    async def close(self) -> None:
        """关闭会话"""
        if self._context:
            await self._save_cookies()
            await self._context.close()
        if self._pw:
            await self._pw.stop()
        self._page = None
        self._context = None
        self._pw = None

    @property
    def proxy_info(self) -> str:
        return self._current_proxy.server if self._current_proxy else "直连"


# ==================== 小红书 API 封装 ====================

class XiaohongshuAPI:
    """小红书 API 封装（基于 Playwright）"""

    BASE_URL = "https://www.xiaohongshu.com"

    def __init__(self, session: XiaohongshuSession):
        self.session = session

    async def search(self, keyword: str, limit: int = 20) -> list[dict]:
        """搜索笔记"""
        page = await self.session.get_page()

        # 访问搜索页面
        search_url = f"{self.BASE_URL}/search_result?keyword={quote(keyword)}"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        await self.session.random_delay()

        # 滚动加载
        notes = []
        last_count = 0
        scrolls_without_new = 0

        for scroll_idx in range(self.session.config.max_scrolls):
            # 等待笔记容器加载
            try:
                await page.wait_for_selector(".note-item, .feeds-container .note", timeout=5000)
            except:
                pass

            # 提取当前页面的笔记
            current_notes = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll('.note-item, .feeds-container .note, section, a[href*="/explore/"]');
                    return Array.from(items).map(el => {
                        const anchor = el.matches?.('a') ? el : (
                            el.querySelector('a[href*="/explore/"], a[href*="xhslink.com"], a')
                        );
                        const href = anchor?.href || anchor?.getAttribute('href') || '';
                        const img = el.querySelector('img');
                        const id = el.getAttribute('data-note-id') ||
                            href.match(/\\/explore\\/([a-zA-Z0-9]+)/)?.[1] ||
                            href.match(/note_id[=:]([a-zA-Z0-9]+)/)?.[1] ||
                            '';
                        return {
                            id,
                            title: el.querySelector('.title, .note-title, [class*="title"]')?.innerText?.trim() || '',
                            author: el.querySelector('.author .name, .nickname, [class*="nickname"]')?.innerText?.trim() || '',
                            likes: el.querySelector('.like-count, .likes, [class*="like"]')?.innerText?.trim() || '0',
                            coverUrl: img?.currentSrc || img?.src || img?.getAttribute('data-src') || '',
                            url: href
                        };
                    });
                }
            """)

            # 去重合并
            existing_ids = {n.get("id") for n in notes}
            for note in current_notes:
                note_url = _normalize_xhs_url(str(note.get("url") or ""))
                note_id = str(note.get("id") or _extract_note_id_from_text(note_url))
                if note_id and note_id not in existing_ids:
                    note["id"] = note_id
                    note["url"] = note_url
                    existing_ids.add(note_id)
                    notes.append(note)
                    if len(notes) >= limit:
                        break

            if len(notes) >= limit:
                break

            # 滚动
            if len(current_notes) == last_count:
                scrolls_without_new += 1
                if scrolls_without_new >= 3:
                    break
            else:
                scrolls_without_new = 0
                last_count = len(current_notes)

            await self.session.random_scroll()
            await self.session.simulate_browsing(1.0)

        return notes[:limit]

    async def get_note_detail(self, note_id: str = "", note_url: str | None = None) -> dict:
        """获取笔记详情"""
        page = await self.session.get_page()

        url = note_url or f"{self.BASE_URL}/explore/{note_id}"
        url = _normalize_xhs_url(url)
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await self.session.random_delay()

        current_url = page.url
        if "/404" in urlparse(current_url).path:
            redirect_url = _extract_redirect_path(current_url)
            if redirect_url and redirect_url != current_url and redirect_url != url:
                await page.goto(redirect_url, wait_until="domcontentloaded", timeout=45000)
                await self.session.random_delay()
                current_url = page.url

        final_note_id = note_id or _extract_note_id_from_text(current_url)

        try:
            await page.wait_for_selector("img", timeout=10000)
        except Exception:
            pass

        # 提取详情
        detail = await page.evaluate("""
            () => {
                // 标题
                const title = document.querySelector('.title, .note-title')?.innerText?.trim() || '';
                // 作者
                const author = document.querySelector('.author .name, .nickname')?.innerText?.trim() || '';
                // 内容描述
                const desc = document.querySelector('.desc, .note-content')?.innerText?.trim() || '';
                // 点赞数
                const likes = document.querySelector('.like-count, .likes')?.innerText?.trim() || '0';
                // 收藏数
                const collects = document.querySelector('.collect-count')?.innerText?.trim() || '0';
                // 评论数
                const comments = document.querySelector('.comment-count')?.innerText?.trim() || '0';
                // 图片列表
                const seen = new Set();
                const images = Array.from(document.querySelectorAll('img'))
                    .map(img => img.currentSrc || img.src || img.getAttribute('data-src') || '')
                    .filter(src => /^https?:\\/\\//.test(src))
                    .filter(src => /xhscdn|sns-webpic|xiaohongshu/.test(src))
                    .filter(src => {
                        if (seen.has(src)) return false;
                        seen.add(src);
                        return true;
                    });

                return { title, author, desc, likes, collects, comments, images };
            }
        """)

        if "/404" in urlparse(current_url).path and not detail.get("images"):
            raise PluginParseError(
                "小红书页面跳转到 404。请粘贴浏览器地址栏里的完整笔记链接，"
                "尤其要保留 ?xsec_token=...&xsec_source=... 参数。"
            )

        if not detail.get("images"):
            raise PluginParseError(
                "页面已打开，但没有解析到图片。请确认链接是图文笔记，"
                "并优先粘贴带 xsec_token 的完整浏览器地址栏链接。"
            )

        detail["id"] = final_note_id
        detail["url"] = current_url

        return detail

    async def get_note_images(self, note_id: str, note_url: str | None = None) -> list[str]:
        """获取笔记中的图片 URL"""
        detail = await self.get_note_detail(note_id, note_url=note_url)
        return detail.get("images", [])

    async def download_image(self, url: str, save_path: Path) -> Optional[Path]:
        """下载图片"""
        import httpx

        page = await self.session.get_page()

        # 构造文件名
        filename = url.split("/")[-1].split("?")[0]
        if not filename.endswith((".jpg", ".jpeg", ".png", ".webp")):
            filename = f"{filename}.jpg"

        target = save_path / filename

        # 检查是否已存在
        if target.exists() and target.stat().st_size > 1024:
            return target

        # 使用浏览器上下文下载（保持会话）
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                # 获取当前页面的 cookies
                cookies = await page.context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}

                response = await client.get(
                    url,
                    headers={
                        "User-Agent": self.session._fingerprint.user_agent,
                        "Referer": page.url if page and page.url != "about:blank" else self.BASE_URL,
                    },
                    cookies=cookie_dict,
                )

                if response.status_code == 200:
                    target.write_bytes(response.content)
                    return target
        except Exception:
            pass

        return None


# ==================== 插件主类 ====================

class XiaohongshuPlugin(BasePlugin):
    """小红书爬虫插件"""

    name = "小红书"
    domain = "xiaohongshu.com"
    version = "1.0.0"

    def __init__(self):
        self.config = XiaohongshuConfig()
        self.config.profile_dir = str(runtime_path("xiaohongshu_profile"))
        self.config.cookie_file = str(runtime_path("xiaohongshu_cookies.json"))
        self.progress_callback: Optional[Callable[[str, float], None]] = None
        self._proxy_pool: Optional[ProxyPool] = None
        self._fingerprint_manager: Optional[FingerprintManager] = None
        self._sessions: dict[str, XiaohongshuSession] = {}
        self._session_lock = threading.Lock()

    def _progress(self, message: str, value: float) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(message, value)
        except Exception:
            pass

    def can_handle(self, url: str) -> bool:
        """判断是否能处理该 URL"""
        text = url.strip().lower()
        return "xiaohongshu.com" in text or "xhslink.com" in text or text.isdigit()

    def validate(self) -> bool:
        """检查依赖是否安装"""
        return PLAYWRIGHT_AVAILABLE

    def get_headers(self) -> dict[str, str]:
        """获取请求头"""
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def set_proxies(self, proxy_list: list[dict]) -> None:
        """
        设置代理列表
        proxy_list: [{"server": "http://ip:port", "username": "", "password": ""}, ...]
        """
        self.config.proxy_enabled = True
        self.config.proxy_list = [
            ProxyConfig(
                server=p.get("server"),
                username=p.get("username", ""),
                password=p.get("password", ""),
            )
            for p in proxy_list
        ]
        self._proxy_pool = ProxyPool(self.config)

    def set_fingerprints(self, fingerprints: list[dict]) -> None:
        """设置指纹列表"""
        self.config.fingerprint_list = [
            FingerprintConfig(
                user_agent=f.get("user_agent"),
                viewport=f.get("viewport", {"width": 1920, "height": 1080}),
                locale=f.get("locale", "zh-CN"),
                timezone_id=f.get("timezone_id", "Asia/Shanghai"),
            )
            for f in fingerprints
        ]

    def _get_session(self, session_id: str = "default") -> XiaohongshuSession:
        """获取或创建会话（每个会话独立 IP+指纹）"""
        with self._session_lock:
            if session_id not in self._sessions:
                if self._proxy_pool is None:
                    self._proxy_pool = ProxyPool(self.config)
                if self._fingerprint_manager is None:
                    self._fingerprint_manager = FingerprintManager(self.config)

                session = XiaohongshuSession(
                    session_id=session_id,
                    proxy_pool=self._proxy_pool,
                    fingerprint_manager=self._fingerprint_manager,
                    config=self.config,
                )
                self._sessions[session_id] = session

            return self._sessions[session_id]

    def _close_session(self, session_id: str) -> None:
        """关闭会话"""
        with self._session_lock:
            if session_id in self._sessions:
                # 异步关闭需要在 async 环境中处理，这里触发但不等待
                asyncio.create_task(self._sessions[session_id].close())
                del self._sessions[session_id]

    def parse(self, url: str) -> list[Resource]:
        """解析 URL 为资源列表"""
        if not self.validate():
            raise PluginParseError("请先安装 Playwright: pip install playwright && playwright install chromium")

        try:
            return asyncio.run(self._parse_async(url))
        except Exception as exc:
            raise PluginParseError(f"小红书解析失败: {exc}") from exc

    async def _parse_async(self, url: str) -> list[Resource]:
        """异步解析 URL"""
        self._progress("小红书：准备解析目标", 0.05)
        # 提取关键词或笔记 ID
        note_id = self._extract_note_id(url)
        note_url = self._extract_note_url(url, note_id=note_id)
        keyword = self._extract_keyword(url)

        session = self._get_session(f"parse_{int(time.time())}")

        try:
            self._progress("小红书：启动浏览器会话", 0.10)
            await session.start()
            api = XiaohongshuAPI(session)

            resources = []

            if note_id or note_url:
                # 单篇笔记
                self._progress("小红书：打开笔记页面", 0.22)
                detail = await api.get_note_detail(note_id or "", note_url=note_url)
                self._progress("小红书：提取笔记图片", 0.55)
                note_id = detail.get("id") or note_id or _extract_note_id_from_text(detail.get("url", ""))
                if not note_id:
                    note_id = f"note_{int(time.time())}"
                images = detail.get("images", [])

                resource = Resource(
                    id=note_id,
                    url=detail.get("url") or note_url or f"https://www.xiaohongshu.com/explore/{note_id}",
                    title=detail.get("title", "")[:200],
                    author=detail.get("author", ""),
                    author_id="",
                    files=[{"url": img, "type": "image"} for img in images],
                    metadata=detail,
                    thumbnail=images[0] if images else None,
                    created_at="",
                )
                resources.append(resource)

            elif keyword:
                # 关键词搜索
                self._progress("小红书：打开搜索结果页", 0.18)
                notes = await api.search(keyword, limit=min(self.config.max_notes_per_session, 30))
                self._progress(f"小红书：整理搜索结果（{len(notes)} 条）", 0.62)

                for note in notes:
                    note_url = _normalize_xhs_url(note.get("url", ""))
                    note_id = note.get("id") or _extract_note_id_from_text(note_url)
                    if not note_id:
                        continue
                    # 只返回元数据，实际图片在 download 时下载
                    resource = Resource(
                        id=note_id,
                        url=note_url,
                        title=note.get("title", "")[:200],
                        author=note.get("author", ""),
                        author_id="",
                        files=[],  # 延迟下载
                        metadata={
                            "kind": "search_result",
                            "keyword": keyword,
                            "note_id": note_id,
                            "note_url": note_url,
                            "likes": note.get("likes"),
                            "cover_url": note.get("coverUrl"),
                        },
                        thumbnail=note.get("coverUrl"),
                        created_at="",
                    )
                    resources.append(resource)

            self._progress(f"小红书：解析完成（{len(resources)} 个资源）", 0.72)
            return resources

        finally:
            await session.close()

    def download(self, resource: Resource, save_path: Path) -> list[Path]:
        """下载资源"""
        try:
            return asyncio.run(self._download_async(resource, save_path))
        except Exception as exc:
            raise PluginParseError(f"小红书下载失败: {exc}") from exc

    async def _download_async(self, resource: Resource, save_path: Path) -> list[Path]:
        """异步下载"""
        self._progress(f"小红书：准备下载 {resource.id}", 0.0)
        session = self._get_session(f"download_{resource.id}_{int(time.time())}")

        try:
            self._progress(f"小红书：启动下载会话 {resource.id}", 0.10)
            await session.start()
            api = XiaohongshuAPI(session)

            # 创建保存目录
            subdir = resource.metadata.get("keyword", resource.id)
            target_dir = save_path / "xiaohongshu" / subdir
            target_dir.mkdir(parents=True, exist_ok=True)

            downloaded = []

            # 如果有直接的文件列表
            if resource.files:
                if resource.url:
                    try:
                        self._progress(f"小红书：打开笔记页 {resource.id}", 0.20)
                        page = await session.get_page()
                        await page.goto(_normalize_xhs_url(resource.url), wait_until="domcontentloaded", timeout=45000)
                        await session.random_delay()
                    except Exception:
                        pass
                total_files = max(len(resource.files), 1)
                for item in resource.files:
                    file_url = item.get("url")
                    if file_url:
                        self._progress(f"小红书：下载图片 {len(downloaded) + 1}/{total_files}", 0.35 + (len(downloaded) / total_files) * 0.55)
                        path = await api.download_image(file_url, target_dir)
                        if path:
                            downloaded.append(path)
                        await session.random_delay()

            # 如果是搜索结果，需要先获取详情再下载
            elif resource.metadata.get("kind") == "search_result":
                note_id = resource.metadata.get("note_id")
                note_url = _normalize_xhs_url(str(resource.metadata.get("note_url") or resource.url or ""))
                if note_id or note_url:
                    self._progress(f"小红书：进入笔记详情 {resource.id}", 0.20)
                    images = await api.get_note_images(note_id, note_url=note_url)
                    total_files = max(len(images), 1)
                    for img_url in images:
                        self._progress(f"小红书：下载图片 {len(downloaded) + 1}/{total_files}", 0.35 + (len(downloaded) / total_files) * 0.55)
                        path = await api.download_image(img_url, target_dir)
                        if path:
                            downloaded.append(path)
                        await session.random_delay()

            self._progress(f"小红书：资源下载完成 {resource.id}", 0.96)
            return downloaded

        finally:
            await session.close()

    def _extract_note_id(self, url: str) -> Optional[str]:
        """提取笔记 ID"""
        # 标准链接: xiaohongshu.com/explore/xxxxx
        patterns = [
            r"xiaohongshu\.com/explore/([a-zA-Z0-9]+)",
            r"note_id[=:]([a-zA-Z0-9]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return match.group(1)

        # 纯数字 ID
        if url.strip().isdigit():
            return url.strip()

        return None

    def _extract_note_url(self, url: str, *, note_id: str | None = None) -> Optional[str]:
        """提取或还原单篇笔记访问 URL，尽量保留 xsec_token 等安全参数。"""
        text = url.strip()
        if not text:
            return None
        lower = text.lower()
        if "xiaohongshu.com/404" in lower:
            redirect_url = _extract_redirect_path(text)
            if redirect_url:
                return redirect_url
        if "xiaohongshu.com" in lower or "xhslink.com" in lower:
            if text.startswith("//"):
                return "https:" + text
            if "://" not in text:
                return "https://" + text.lstrip("/")
            return text
        if note_id:
            return f"https://www.xiaohongshu.com/explore/{note_id}"
        return None

    def _extract_keyword(self, url: str) -> Optional[str]:
        """提取搜索关键词"""
        # 搜索链接: xiaohongshu.com/search_result?keyword=xxx
        match = re.search(r"keyword=([^&]+)", url)
        if match:
            return unquote(match.group(1))
        if url.strip() and "xiaohongshu.com" not in url.lower() and "xhslink.com" not in url.lower():
            return url.strip()
        return None

    def get_proxy_stats(self) -> dict:
        """获取代理池统计"""
        if self._proxy_pool:
            return {
                "total": self._proxy_pool.total_count(),
                "healthy": self._proxy_pool.healthy_count(),
            }
        return {"total": 0, "healthy": 0}

    def rotate_session(self, session_id: str = "default") -> None:
        """强制轮换会话（换 IP + 换指纹）"""
        self._close_session(session_id)

    def run_interactive_login(self, proceed: Optional[threading.Event] = None) -> bool:
        """交互式登录"""
        if not self.validate():
            return False

        async def _login():
            session = XiaohongshuSession(
                session_id="login",
                proxy_pool=self._proxy_pool or ProxyPool(self.config),
                fingerprint_manager=self._fingerprint_manager or FingerprintManager(self.config),
                config=self.config,
            )
            try:
                await session.start()
                page = await session.get_page()
                await page.goto("https://www.xiaohongshu.com", wait_until="networkidle")

                if proceed:
                    proceed.wait()
                else:
                    input("\n在浏览器中完成小红书登录后，按 Enter 继续...\n")

                await session._save_cookies()
                return True
            finally:
                await session.close()

        return asyncio.run(_login())


# 导出插件类
plugin_class = XiaohongshuPlugin
