from __future__ import annotations

import random


def exponential_backoff(attempt: int, base_seconds: float, max_seconds: float) -> float:
    delay = min(max_seconds, base_seconds * (2 ** max(attempt, 0)))
    jitter = random.uniform(0, delay * 0.2 if delay > 0 else 0)
    return delay + jitter
