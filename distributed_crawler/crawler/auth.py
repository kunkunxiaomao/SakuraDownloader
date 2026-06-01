from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import aiohttp

from .config import AuthSettings

LOGGER = logging.getLogger(__name__)


class AuthProvider(ABC):
    @abstractmethod
    async def refresh_session(self, domain: str, existing_session: dict[str, Any]) -> dict[str, Any]:
        """
        Provide an authorized and compliant session refresh implementation for the target system.
        """


class ManualAuthProvider(AuthProvider):
    async def refresh_session(self, domain: str, existing_session: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            f"Session refresh for {domain} must be implemented with authorized credentials and target-specific logic."
        )


class ConfigurableAuthProvider(AuthProvider):
    """
    Generic username/password login provider.
    This intentionally keeps site-specific anti-bot behavior out of scope.
    """

    def __init__(self, settings: AuthSettings, timeout_seconds: int = 20) -> None:
        self.settings = settings
        self.timeout_seconds = timeout_seconds

    async def refresh_session(self, domain: str, existing_session: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.enabled:
            return {**existing_session, "expired": False}
        cfg = self.settings.domains.get(domain)
        if cfg is None or not cfg.login_url or not cfg.username or not cfg.password:
            LOGGER.warning("No auth config for domain=%s, keeping existing session", domain)
            return {**existing_session, "expired": True}

        payload: dict[str, Any] = dict(cfg.extra_payload)
        payload[cfg.username_field] = cfg.username
        payload[cfg.password_field] = cfg.password

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if cfg.submit_type.lower() == "json":
                async with session.post(cfg.login_url, json=payload) as response:
                    body_text = await response.text()
                    if response.status not in set(cfg.success_status_codes):
                        raise RuntimeError(f"Login failed for {domain}: {response.status} {body_text[:180]}")
            else:
                async with session.post(cfg.login_url, data=payload) as response:
                    body_text = await response.text()
                    if response.status not in set(cfg.success_status_codes):
                        raise RuntimeError(f"Login failed for {domain}: {response.status} {body_text[:180]}")

            cookie_pairs = [f"{k}={v.value}" for k, v in session.cookie_jar.filter_cookies(cfg.login_url).items()]
            cookie_header = "; ".join(cookie_pairs)
            if not cookie_header:
                # Fallback: keep old cookies if target does not set cookies in login response.
                cookie_header = str(existing_session.get("cookie", ""))

        return {
            **existing_session,
            "expired": False,
            "cookie": cookie_header,
            "updated_by": "ConfigurableAuthProvider",
        }
