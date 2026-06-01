# proxy_pool.py
"""
独立代理池模块 - 支持动态换IP
功能：
- 多代理轮换（轮询/随机/会话粘性）
- 健康检查（自动剔除失效代理）
- 延迟统计（加权选择最优代理）
- 线程安全
"""

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class RotationStrategy(Enum):
    """轮换策略"""
    ROUND_ROBIN = "round_robin"  # 轮询
    RANDOM = "random"  # 随机
    WEIGHTED = "weighted"  # 加权（按成功率+延迟）
    SESSION_STICKY = "session_sticky"  # 会话粘性（同会话同IP）


@dataclass
class ProxyInfo:
    """代理信息"""
    server: str  # http://ip:port 或 socks5://ip:port
    username: str = ""
    password: str = ""

    # 统计数据
    success_count: int = 0
    fail_count: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    last_check_time: float = 0.0
    last_fail_time: float = 0.0

    # 状态
    healthy: bool = True
    cooldown_until: float = 0.0  # 冷却结束时间

    @property
    def proxy_url(self) -> str:
        """获取完整的代理URL（含认证）"""
        if self.username and self.password:
            # http://user:pass@ip:port
            protocol = self.server.split("://")[0]
            rest = self.server.split("://")[1]
            return f"{protocol}://{self.username}:{self.password}@{rest}"
        return self.server

    def to_requests_proxies(self) -> dict[str, str]:
        return {"http": self.proxy_url, "https": self.proxy_url}

    def to_playwright_proxy(self) -> dict[str, str]:
        data = {"server": self.server}
        if self.username:
            data["username"] = self.username
            data["password"] = self.password
        return data

    @property
    def success_rate(self) -> float:
        """成功率"""
        total = self.success_count + self.fail_count
        if total == 0:
            return 1.0
        return self.success_count / total

    @property
    def weight(self) -> float:
        """
        计算代理权重（用于加权随机选择）
        考虑因素：成功率、延迟、冷却状态
        """
        if not self.healthy:
            return 0.0

        # 冷却中
        if self.cooldown_until > time.time():
            return 0.0

        # 成功率权重
        success_weight = self.success_rate

        # 延迟权重（延迟越低权重越高）
        if self.avg_latency_ms > 0:
            latency_weight = max(0.1, 1000.0 / max(self.avg_latency_ms, 1))
        else:
            latency_weight = 1.0

        # 失败惩罚
        fail_penalty = 1.0 / (1.0 + self.fail_count * 0.5)

        return success_weight * latency_weight * fail_penalty


class ProxyPool:
    """代理池 - 管理多个代理的获取和轮换"""

    def __init__(
            self,
            strategy: RotationStrategy = RotationStrategy.WEIGHTED,
            health_check_url: str = "https://httpbin.org/ip",
            health_check_timeout: int = 8,
            max_failures: int = 3,
            cooldown_seconds: int = 60,
            auto_health_check: bool = True,
    ):
        """
        Args:
            strategy: 轮换策略
            health_check_url: 健康检查URL
            health_check_timeout: 健康检查超时(秒)
            max_failures: 最大失败次数，超过后标记为不健康
            cooldown_seconds: 不健康代理的冷却时间(秒)
            auto_health_check: 是否自动健康检查
        """
        self.strategy = strategy
        self.health_check_url = health_check_url
        self.health_check_timeout = health_check_timeout
        self.max_failures = max_failures
        self.cooldown_seconds = cooldown_seconds
        self.auto_health_check = auto_health_check

        self._proxies: List[ProxyInfo] = []
        self._session_proxy_map: Dict[str, ProxyInfo] = {}  # 会话粘性用
        self._round_robin_index = 0
        self._lock = threading.Lock()

    def add_proxy(self, server: str, username: str = "", password: str = "") -> None:
        """添加单个代理"""
        with self._lock:
            # 去重
            existing = {p.server for p in self._proxies}
            if server not in existing:
                self._proxies.append(ProxyInfo(
                    server=server,
                    username=username,
                    password=password,
                ))

    def add_proxy_url(self, value: str) -> None:
        parsed = parse_proxy_url(value)
        if parsed is not None:
            self.add_proxy(parsed.server, parsed.username, parsed.password)

    def add_proxies(self, proxies: List[dict]) -> None:
        """批量添加代理

        Args:
            proxies: [{"server": "http://1.2.3.4:8080", "username": "", "password": ""}, ...]
        """
        for p in proxies:
            self.add_proxy(
                server=p["server"],
                username=p.get("username", ""),
                password=p.get("password", ""),
            )

    def add_proxy_text(self, text: str) -> int:
        count = 0
        for line in text.splitlines():
            parsed = parse_proxy_url(line)
            if parsed is None:
                continue
            self.add_proxy(parsed.server, parsed.username, parsed.password)
            count += 1
        return count

    def remove_proxy(self, server: str) -> None:
        """移除代理"""
        with self._lock:
            self._proxies = [p for p in self._proxies if p.server != server]

    def get_proxy(self, session_id: str = None) -> Optional[ProxyInfo]:
        """
        获取一个代理

        Args:
            session_id: 会话ID，用于SESSION_STICKY策略

        Returns:
            代理信息，如果没有可用代理返回None
        """
        with self._lock:
            # 过滤出健康的代理
            now = time.time()
            healthy = [p for p in self._proxies if p.healthy and p.cooldown_until <= now]

            if not healthy:
                # 尝试从冷却中恢复
                now = time.time()
                recovered = []
                for p in self._proxies:
                    if not p.healthy and p.cooldown_until <= now:
                        p.healthy = True
                        p.fail_count = 0
                        recovered.append(p)
                if recovered:
                    healthy = recovered

            if not healthy:
                return None

            # 根据策略选择代理
            if self.strategy == RotationStrategy.RANDOM:
                return random.choice(healthy)

            elif self.strategy == RotationStrategy.WEIGHTED:
                # 加权随机选择
                weights = [p.weight for p in healthy]
                total_weight = sum(weights)
                if total_weight <= 0:
                    return random.choice(healthy)
                r = random.uniform(0, total_weight)
                cumsum = 0
                for i, w in enumerate(weights):
                    cumsum += w
                    if r <= cumsum:
                        return healthy[i]
                return healthy[0]

            elif self.strategy == RotationStrategy.SESSION_STICKY:
                if session_id and session_id in self._session_proxy_map:
                    proxy = self._session_proxy_map[session_id]
                    if proxy in healthy:
                        return proxy
                # 新会话，分配一个代理
                proxy = self._get_round_robin(healthy)
                if session_id:
                    self._session_proxy_map[session_id] = proxy
                return proxy

            else:  # ROUND_ROBIN
                return self._get_round_robin(healthy)

    def _get_round_robin(self, proxies: List[ProxyInfo]) -> ProxyInfo:
        """轮询获取代理"""
        proxy = proxies[self._round_robin_index % len(proxies)]
        self._round_robin_index += 1
        return proxy

    def mark_success(self, proxy: ProxyInfo, latency_ms: float = 0) -> None:
        """标记代理请求成功"""
        if proxy is None:
            return
        with self._lock:
            proxy.success_count += 1
            proxy.fail_count = 0
            proxy.healthy = True
            if latency_ms > 0:
                # 更新平均延迟（指数移动平均）
                if proxy.avg_latency_ms == 0:
                    proxy.avg_latency_ms = latency_ms
                else:
                    proxy.avg_latency_ms = proxy.avg_latency_ms * 0.7 + latency_ms * 0.3
            proxy.last_check_time = time.time()

    def mark_failed(self, proxy: ProxyInfo) -> None:
        """标记代理请求失败"""
        if proxy is None:
            return
        with self._lock:
            proxy.fail_count += 1
            if proxy.fail_count >= self.max_failures:
                proxy.healthy = False
                proxy.cooldown_until = time.time() + self.cooldown_seconds

    def health_check(self, proxy: ProxyInfo = None) -> bool:
        """
        健康检查单个或所有代理

        Returns:
            如果检查通过返回True
        """
        import requests

        proxies_to_check = [proxy] if proxy else self._proxies

        for p in proxies_to_check:
            if p is None:
                continue
            try:
                start = time.time()
                resp = requests.get(
                    self.health_check_url,
                    proxies={"http": p.proxy_url, "https": p.proxy_url},
                    timeout=self.health_check_timeout,
                )
                if resp.status_code == 200:
                    latency_ms = (time.time() - start) * 1000
                    self.mark_success(p, latency_ms)
                else:
                    self.mark_failed(p)
            except Exception:
                self.mark_failed(p)

        return proxy.healthy if proxy else len(self.get_healthy_proxies()) > 0

    def get_healthy_proxies(self) -> List[ProxyInfo]:
        """获取所有健康代理"""
        with self._lock:
            return [p for p in self._proxies if p.healthy]

    def get_stats(self) -> dict:
        """获取代理池统计信息"""
        with self._lock:
            healthy = [p for p in self._proxies if p.healthy]
            return {
                "total": len(self._proxies),
                "healthy": len(healthy),
                "unhealthy": len(self._proxies) - len(healthy),
                "proxies": [
                    {
                        "server": p.server,
                        "healthy": p.healthy,
                        "success_rate": p.success_rate,
                        "avg_latency_ms": round(p.avg_latency_ms, 2),
                        "fail_count": p.fail_count,
                    }
                    for p in self._proxies
                ]
            }

    def release_session(self, session_id: str) -> None:
        """释放会话绑定的代理"""
        with self._lock:
            self._session_proxy_map.pop(session_id, None)


def parse_proxy_url(value: str) -> Optional[ProxyInfo]:
    text = value.strip()
    if not text or text.startswith("#"):
        return None
    if "://" not in text:
        text = "http://" + text
    import re

    match = re.match(r"^(https?|socks5)://(?:(?P<user>[^:@/]+):(?P<pwd>[^@/]+)@)?(?P<host>[\w.\-]+):(?P<port>\d{2,5})$", text, re.I)
    if not match:
        return None
    scheme = text.split("://", 1)[0].lower()
    host = match.group("host")
    port = match.group("port")
    user = match.group("user") or ""
    pwd = match.group("pwd") or ""
    return ProxyInfo(server=f"{scheme}://{host}:{port}", username=user, password=pwd)


