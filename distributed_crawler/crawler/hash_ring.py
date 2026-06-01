from __future__ import annotations

from bisect import bisect
from hashlib import md5


class ConsistentHashRing:
    def __init__(self, replicas: int = 100) -> None:
        self.replicas = replicas
        self._keys: list[int] = []
        self._nodes: dict[int, str] = {}

    def _hash(self, value: str) -> int:
        return int(md5(value.encode("utf-8")).hexdigest(), 16)

    def rebuild(self, nodes: list[str]) -> None:
        self._keys.clear()
        self._nodes.clear()
        for node in sorted(set(nodes)):
            for replica in range(self.replicas):
                key = self._hash(f"{node}:{replica}")
                self._keys.append(key)
                self._nodes[key] = node
        self._keys.sort()

    def get_node(self, key: str) -> str | None:
        if not self._keys:
            return None
        hashed = self._hash(key)
        index = bisect(self._keys, hashed)
        if index == len(self._keys):
            index = 0
        return self._nodes[self._keys[index]]
