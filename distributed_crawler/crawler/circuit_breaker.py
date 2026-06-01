from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import monotonic

from .config import CircuitBreakerSettings


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class Circuit:
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    half_open_successes: int = 0
    opened_at: float = 0.0


class CircuitOpenError(RuntimeError):
    pass


class DomainCircuitBreaker:
    def __init__(self, settings: CircuitBreakerSettings) -> None:
        self.settings = settings
        self._circuits: dict[str, Circuit] = {}

    def before_request(self, domain: str) -> None:
        if not self.settings.enabled:
            return
        circuit = self._circuits.setdefault(domain, Circuit())
        if circuit.state != CircuitState.OPEN:
            return
        elapsed = monotonic() - circuit.opened_at
        if elapsed >= self.settings.recovery_timeout_seconds:
            circuit.state = CircuitState.HALF_OPEN
            circuit.half_open_successes = 0
            return
        raise CircuitOpenError(f"Circuit open for {domain}")

    def record_success(self, domain: str) -> None:
        if not self.settings.enabled:
            return
        circuit = self._circuits.setdefault(domain, Circuit())
        if circuit.state == CircuitState.HALF_OPEN:
            circuit.half_open_successes += 1
            if circuit.half_open_successes < self.settings.half_open_success_threshold:
                return
        circuit.state = CircuitState.CLOSED
        circuit.failures = 0
        circuit.half_open_successes = 0
        circuit.opened_at = 0.0

    def record_failure(self, domain: str) -> None:
        if not self.settings.enabled:
            return
        circuit = self._circuits.setdefault(domain, Circuit())
        circuit.failures += 1
        circuit.half_open_successes = 0
        if circuit.failures >= self.settings.failure_threshold or circuit.state == CircuitState.HALF_OPEN:
            circuit.state = CircuitState.OPEN
            circuit.opened_at = monotonic()
