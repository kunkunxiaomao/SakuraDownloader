from __future__ import annotations

import copy
import os
import random
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class DeviceType(Enum):
    WINDOWS_CHROME = "windows_chrome"
    WINDOWS_EDGE = "windows_edge"
    MAC_CHROME = "mac_chrome"


@dataclass
class BrowserProfile:
    """Stable browser context settings for one logical session.

    This module intentionally avoids Canvas/WebGL spoofing or large fingerprint rotation.
    The goal is consistency inside a session, not impersonating many different users.
    """

    user_agent: str
    platform: str = "Win32"
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1920, "height": 1080})
    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    color_scheme: str = "light"
    reduced_motion: str = "no-preference"
    device_scale_factor: float = 1.0
    is_mobile: bool = False
    has_touch: bool = False

    def to_playwright_context_options(self) -> dict[str, Any]:
        return {
            "user_agent": self.user_agent,
            "viewport": dict(self.viewport),
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "color_scheme": self.color_scheme,
            "reduced_motion": self.reduced_motion,
            "device_scale_factor": self.device_scale_factor,
            "is_mobile": self.is_mobile,
            "has_touch": self.has_touch,
        }

    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Language": f"{self.locale},zh;q=0.9,en;q=0.8",
        }


class BrowserProfileDatabase:
    @staticmethod
    def windows_chrome() -> BrowserProfile:
        return BrowserProfile(
            user_agent=real_browser_user_agent()
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            platform="Win32",
            viewport={"width": 1920, "height": 1080},
        )

    @staticmethod
    def windows_edge() -> BrowserProfile:
        return BrowserProfile(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
            platform="Win32",
            viewport={"width": 1536, "height": 864},
            device_scale_factor=1.25,
        )

    @staticmethod
    def mac_chrome() -> BrowserProfile:
        return BrowserProfile(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            platform="MacIntel",
            viewport={"width": 1512, "height": 982},
            device_scale_factor=2.0,
        )

    @staticmethod
    def all_desktop() -> list[BrowserProfile]:
        return [
            BrowserProfileDatabase.windows_chrome(),
            BrowserProfileDatabase.windows_edge(),
            BrowserProfileDatabase.mac_chrome(),
        ]

    @staticmethod
    def by_device_type(device_type: DeviceType) -> BrowserProfile:
        mapping = {
            DeviceType.WINDOWS_CHROME: BrowserProfileDatabase.windows_chrome,
            DeviceType.WINDOWS_EDGE: BrowserProfileDatabase.windows_edge,
            DeviceType.MAC_CHROME: BrowserProfileDatabase.mac_chrome,
        }
        return mapping.get(device_type, BrowserProfileDatabase.windows_chrome)()


class SessionFingerprintManager:
    """Session-sticky browser profile manager."""

    def __init__(self, profiles: list[BrowserProfile] | None = None, *, strategy: str = "sticky") -> None:
        self.profiles = profiles or BrowserProfileDatabase.all_desktop()
        self.strategy = strategy
        self._session_map: dict[str, BrowserProfile] = {}
        self._index = 0
        self._lock = threading.Lock()

    def get_fingerprint(self, session_id: str = "default") -> BrowserProfile:
        with self._lock:
            if self.strategy == "sticky" and session_id in self._session_map:
                return copy.deepcopy(self._session_map[session_id])
            if self.strategy == "round_robin":
                profile = self.profiles[self._index % len(self.profiles)]
                self._index += 1
            elif self.strategy == "random":
                profile = random.choice(self.profiles)
            else:
                stable_index = abs(hash(session_id)) % len(self.profiles)
                profile = self.profiles[stable_index]
            self._session_map[session_id] = copy.deepcopy(profile)
            return copy.deepcopy(profile)

    def get_stats(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "total_profiles": len(self.profiles),
            "sticky_sessions": len(self._session_map),
        }


Fingerprint = BrowserProfile
FingerprintManager = SessionFingerprintManager
FingerprintDatabase = BrowserProfileDatabase


def real_browser_user_agent() -> str:
    """Load a user-provided real browser UA, if configured."""
    env_value = os.environ.get("PIXIV_REAL_USER_AGENT", "").strip()
    if env_value:
        return env_value
    path_value = os.environ.get("PIXIV_REAL_USER_AGENT_FILE", "").strip()
    if path_value:
        path = Path(path_value)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return ""


def default_profile(session_id: str = "default") -> BrowserProfile:
    return SessionFingerprintManager().get_fingerprint(session_id)
