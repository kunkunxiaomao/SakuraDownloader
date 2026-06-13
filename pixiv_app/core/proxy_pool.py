from __future__ import annotations

import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

import requests


@dataclass
class ProxyInfo:
    ip: str
    port: int
    protocol: str = "http"
    source: str = "manual"
    location: str = ""
    org: str = ""
    last_check: float = 0.0
    success_count: int = 0
    fail_count: int = 0

    @property
    def proxy_url(self) -> str:
        return f"{self.protocol}://{self.ip}:{self.port}"

    @property
    def is_alive(self) -> bool:
        return self.success_count > 0 or self.fail_count < 3


def parse_proxy_text(text: str) -> list[ProxyInfo]:
    proxies: list[ProxyInfo] = []
    seen: set[tuple[str, int, str]] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" not in line:
            line = f"http://{line}"
        match = re.match(r"^(https?)://([\w\.\-]+):(\d{2,5})$", line, re.IGNORECASE)
        if not match:
            continue
        protocol, ip, port_text = match.groups()
        key = (ip, int(port_text), protocol.lower())
        if key in seen:
            continue
        seen.add(key)
        proxies.append(ProxyInfo(ip=ip, port=int(port_text), protocol=protocol.lower(), source="manual"))
    return proxies


class QuakeClient:
    def __init__(
        self,
        api_key: str = "",
        cookie: str = "",
        mode: str = "api_v3",
        request_kwargs: Optional[dict[str, Any]] = None,
    ):
        self.api_key = api_key.strip()
        self.cookie = cookie.strip()
        self.mode = mode
        self.request_kwargs = dict(request_kwargs or {})
        self.base_url = "https://quake.360.net/api/v3"
        self.web_assoc_url = "https://quake.360.net/api/search/field/association/quake_service"

    def _api_headers(self) -> dict[str, str]:
        return {
            "X-QuakeToken": self.api_key,
            "Content-Type": "application/json",
        }

    def _web_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://quake.360.net",
            "Referer": "https://quake.360.net/quake/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def search_ips_api(self, query: str, size: int = 100, start: int = 0) -> list[dict[str, Any]]:
        if not self.api_key:
            return []
        url = f"{self.base_url}/search/quake_service"
        payload = {"query": query, "start": start, "size": min(size, 500)}
        kwargs = dict(self.request_kwargs)
        kwargs.setdefault("timeout", 30)
        try:
            response = requests.post(url, headers=self._api_headers(), json=payload, **kwargs)
            if response.status_code != 200:
                return []
            body = response.json()
            return body.get("data", []) if isinstance(body, dict) else []
        except Exception:
            return []

    def search_ips_web_assoc(self, search_content: str) -> list[dict[str, Any]]:
        if not self.cookie:
            return []
        payload = {
            "search_content": search_content,
            "device": {
                "device_type": "PC",
                "os": "Windows",
                "os_version": "10.0",
                "language": "zh_CN",
                "network": "4g",
                "browser_info": "Chrome",
                "fingerprint": "sakura-proxy-pool",
                "user_agent": self._web_headers()["User-Agent"],
                "date": time.strftime("%Y/%m/%d %H:%M:%S"),
                "UUID": "sakura-proxy-association",
            },
        }
        kwargs = dict(self.request_kwargs)
        kwargs.setdefault("timeout", 30)
        try:
            response = requests.post(
                self.web_assoc_url,
                headers=self._web_headers(),
                json=payload,
                **kwargs,
            )
            if response.status_code != 200:
                return []
            text = response.text
            ips = sorted(set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)))
            return [{"ip": ip, "port": 80, "organization": "", "location": {}} for ip in ips]
        except Exception:
            return []

    def get_foreign_proxies(
        self,
        size: int = 120,
        countries: Optional[list[str]] = None,
    ) -> list[ProxyInfo]:
        proxy_ports = [80, 443, 8080, 3128, 8888, 1080, 8118]
        port_query = " OR ".join([f"port:{port}" for port in proxy_ports])
        country_codes = countries or ["US", "JP", "KR", "SG", "HK", "TW", "DE", "FR", "GB", "CA", "AU"]
        country_query = " OR ".join([f'country:"{code}"' for code in country_codes])
        query = f"({port_query}) AND ({country_query}) AND response:ok"

        if self.mode == "web_assoc":
            data = self.search_ips_web_assoc(query)
        else:
            data = self.search_ips_api(query=query, size=size)

        proxies: list[ProxyInfo] = []
        seen: set[tuple[str, int, str]] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            ip = item.get("ip")
            port = item.get("port")
            if not ip or not isinstance(port, int):
                continue
            protocol = "https" if port in (443, 8443) else "http"
            key = (ip, port, protocol)
            if key in seen:
                continue
            seen.add(key)
            location_obj = item.get("location", {}) if isinstance(item.get("location"), dict) else {}
            location = "/".join(
                [str(location_obj.get("country", "")).strip(), str(location_obj.get("province", "")).strip()]
            ).strip("/")
            proxies.append(
                ProxyInfo(
                    ip=ip,
                    port=port,
                    protocol=protocol,
                    source=f"quake:{self.mode}",
                    location=location,
                    org=str(item.get("organization", "") or item.get("org", "")),
                )
            )
        return proxies


class ProxyPool:
    def __init__(self, test_url: str = "https://httpbin.org/ip", test_timeout: int = 6):
        self.proxies: list[ProxyInfo] = []
        self.working_proxies: list[ProxyInfo] = []
        self.current_index = 0
        self.test_url = test_url
        self.test_timeout = test_timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "SakuraDownloaderProxyPool/1.0"})

    def add_proxies(self, proxies: list[ProxyInfo]) -> None:
        existing = {(p.ip, p.port, p.protocol) for p in self.proxies}
        for proxy in proxies:
            key = (proxy.ip, proxy.port, proxy.protocol)
            if key not in existing:
                existing.add(key)
                self.proxies.append(proxy)

    def verify_proxy(self, proxy: ProxyInfo) -> bool:
        target_proxies = {"http": proxy.proxy_url, "https": proxy.proxy_url}
        try:
            response = self.session.get(
                self.test_url,
                proxies=target_proxies,
                timeout=self.test_timeout,
                verify=False,
            )
            if response.status_code == 200:
                proxy.last_check = time.time()
                proxy.success_count += 1
                proxy.fail_count = 0
                return True
        except Exception:
            pass
        proxy.fail_count += 1
        return False

    def verify_all(self, max_workers: int = 16, max_proxies: int = 80) -> list[ProxyInfo]:
        to_verify = self.proxies[:max_proxies]
        self.working_proxies = []
        if not to_verify:
            return self.working_proxies
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(self.verify_proxy, proxy): proxy for proxy in to_verify}
            for future in as_completed(future_map):
                proxy = future_map[future]
                try:
                    if future.result():
                        self.working_proxies.append(proxy)
                except Exception:
                    continue
        return self.working_proxies

    def collect_until_target(
        self,
        quake_client: QuakeClient,
        target_working: int = 5,
        stable_rounds: int = 2,
        fetch_batch_size: int = 60,
        verify_batch_size: int = 30,
        max_rounds: int = 6,
        max_workers: int = 16,
        countries: Optional[list[str]] = None,
    ) -> list[ProxyInfo]:
        stable_hits = 0
        for _ in range(max_rounds):
            if stable_hits >= stable_rounds:
                break
            new_items = quake_client.get_foreign_proxies(size=fetch_batch_size, countries=countries)
            if new_items:
                self.add_proxies(new_items)
                self.verify_all(max_workers=max_workers, max_proxies=verify_batch_size)
            if len(self.working_proxies) >= target_working:
                stable_hits += 1
            else:
                stable_hits = 0
        return self.working_proxies

    def get_proxy(self, rotate: bool = True) -> Optional[ProxyInfo]:
        if not self.working_proxies:
            return None
        if not rotate:
            return random.choice(self.working_proxies)
        proxy = self.working_proxies[self.current_index % len(self.working_proxies)]
        self.current_index += 1
        return proxy

    def mark_failed(self, proxy: ProxyInfo) -> None:
        proxy.fail_count += 1
        if proxy.fail_count >= 3 and proxy in self.working_proxies:
            self.working_proxies.remove(proxy)
