from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import aiohttp


class SignatureProvider(ABC):
    @abstractmethod
    async def sign(self, request_payload: dict[str, Any]) -> dict[str, Any]: ...


class NoopSignatureProvider(SignatureProvider):
    async def sign(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        return {}


class RpcSignatureProvider(SignatureProvider):
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    async def sign(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.endpoint:
            return {}
        async with aiohttp.ClientSession() as session:
            async with session.post(self.endpoint, json=request_payload) as response:
                response.raise_for_status()
                data = await response.json()
        return dict(data)
