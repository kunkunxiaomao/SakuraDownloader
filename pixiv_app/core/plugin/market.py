"""
Optional plugin market client — stub for future HTTP catalog integration.
"""

from __future__ import annotations

from typing import Any

import requests


class PluginMarketClient:
    def __init__(self, api_url: str = "") -> None:
        self.api_url = api_url.rstrip("/")

    def list_plugins(self) -> list[dict[str, Any]]:
        if not self.api_url:
            return []
        response = requests.get(f"{self.api_url}/plugins", timeout=15)
        response.raise_for_status()
        data = response.json()
        return list(data.get("plugins", []))

    def install_plugin(self, _plugin_name: str) -> bool:
        """Download zip and extract into plugins/ — not implemented."""
        return False

    def check_updates(self) -> list[dict[str, Any]]:
        return []
