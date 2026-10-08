"""Quota Economy / provider health (constitution sections 11 and 9).

Tracks provider availability, rate limits, token usage, capacity, latency,
failures, model capability, task importance and routing state, and emits the
locked ``provider.healthy`` / ``provider.degraded`` / ``provider.exhausted``
events.

**What this is, precisely.** Local accounting and health tracking over provider
declarations. It does not talk to any provider, and it cannot see a provider's
real quota. It exists so routing makes a *recorded, refused-on-exhaustion*
decision instead of guessing.

**What it deliberately will not do.** It never bypasses a provider's limits or
terms (section 11): an exhausted or unhealthy provider is refused, not worked
around. There is no retry-storm, no parallel-quota trick, no credential
rotation. ``acquire()`` raising ``QuotaExhausted`` is the intended outcome.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import models

#: Provider health states.
HEALTHY = "healthy"
DEGRADED = "degraded"
EXHAUSTED = "exhausted"

#: Degrade when the failure ratio over the recent window reaches this.
DEFAULT_DEGRADE_FAILURE_RATIO = 0.34

#: Minimum samples before health is judged on a ratio at all.
DEFAULT_MIN_SAMPLES = 3


@dataclass
class ProviderSpec:
    """A declared provider and its self-imposed limits."""

    name: str
    family: str = ""
    models: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    #: Self-imposed request cap per rolling window (0 = unlimited).
    rate_limit: int = 0
    window_s: float = 60.0
    #: Self-imposed token cap per rolling window (0 = unlimited).
    token_limit: int = 0
    unit_cost: float = 0.0
    latency_budget_ms: float = 0.0
    #: Provider-declared, not discoverable by us.
    context_window: int = 0


@dataclass
class ProviderState:
    name: str
    requests: int = 0
    tokens: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    health: str = HEALTHY
    latencies_ms: List[float] = field(default_factory=list)
    window_start: float = 0.0
    #: Set when the provider itself reports exhaustion (e.g. a 429).
    reported_exhausted: bool = False

    @property
    def samples(self) -> int:
        return self.successes + self.failures

    @property
    def failure_ratio(self) -> float:
        return self.failures / self.samples if self.samples else 0.0

    @property
    def latency_p50_ms(self) -> float:
        return _percentile(self.latencies_ms, 0.50)

    @property
    def latency_p95_ms(self) -> float:
        return _percentile(self.latencies_ms, 0.95)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "health": self.health,
            "requests": self.requests, "tokens": self.tokens,
            "successes": self.successes, "failures": self.failures,
            "failure_ratio": round(self.failure_ratio, 4),
            "consecutive_failures": self.consecutive_failures,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "reported_exhausted": self.reported_exhausted,
        }


class QuotaExhausted(Exception):
    """Raised instead of exceeding a provider's declared limits."""


@dataclass
class Reservation:
    """A granted request slot. ``release`` closes the accounting."""

    provider: str
    cost: float = 0.0
    granted_at: float = 0.0


class QuotaManager:
    """Accounts for provider capacity and reports health.

    ``clock`` is injectable so rolling windows are deterministic in tests.
    """

    def __init__(self, event_bus: Any = None,
                 degrade_failure_ratio: float = DEFAULT_DEGRADE_FAILURE_RATIO,
                 min_samples: int = DEFAULT_MIN_SAMPLES,
                 clock: Any = None):
        self.bus = event_bus
        self.degrade_failure_ratio = degrade_failure_ratio
        self.min_samples = min_samples
        self._clock = clock or time.monotonic
        self.specs: Dict[str, ProviderSpec] = {}
        self.states: Dict[str, ProviderState] = {}
        self._events: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, spec: ProviderSpec) -> ProviderSpec:
        self.specs[spec.name] = spec
        self.states.setdefault(spec.name, ProviderState(
            name=spec.name, window_start=self._clock()))
        return spec

    def capabilities(self, provider: str) -> List[str]:
        spec = self.specs.get(provider)
        return list(spec.capabilities) if spec else []

    # ------------------------------------------------------------------
    # Capacity
    # ------------------------------------------------------------------

    def _roll_window(self, state: ProviderState, spec: ProviderSpec) -> None:
        now = self._clock()
        if spec.window_s <= 0:
            return
        if now - state.window_start >= spec.window_s:
            state.requests = 0
            state.tokens = 0
            state.window_start = now
            # A new window is fresh capacity: exhaustion lifts, and health is
            # re-judged so a provider that only hit its window limit becomes
            # usable again (reliability history still counts).
            state.reported_exhausted = False
            if state.health == EXHAUSTED:
                self._set_health(state, self._judge(state))

    def available(self, provider: str) -> bool:
        """True when the provider may be used right now."""
        spec = self.specs.get(provider)
        state = self.states.get(provider)
        if spec is None or state is None:
            return False
        self._roll_window(state, spec)
        if state.health == EXHAUSTED:
            return False
        if state.health == DEGRADED:
            return False
        if state.reported_exhausted:
            return False
        if spec.rate_limit and state.requests >= spec.rate_limit:
            return False
        if spec.token_limit and state.tokens >= spec.token_limit:
            return False
        return True

    def acquire(self, provider: str, tokens: int = 0,
                project_id: str = "", goal_id: Optional[str] = None,
                task_id: Optional[str] = None) -> Reservation:
        """Reserve capacity or refuse. Never exceeds declared limits."""
        spec = self.specs.get(provider)
        state = self.states.get(provider)
        if spec is None or state is None:
            raise KeyError(f"provider {provider!r} is not registered")
        self._roll_window(state, spec)

        if state.reported_exhausted or state.health == EXHAUSTED:
            self._reason(provider, "provider_exhausted")
            raise QuotaExhausted(
                f"provider {provider!r} is exhausted; refusing rather than "
                f"exceeding its limits (section 11)")
        if state.health == DEGRADED:
            self._reason(provider, "provider_degraded")
            raise QuotaExhausted(f"provider {provider!r} is degraded")
        if spec.rate_limit and state.requests + 1 > spec.rate_limit:
            self._set_health(state, EXHAUSTED)
            self._reason(provider, "rate_limit_reached")
            raise QuotaExhausted(
                f"provider {provider!r} hit its {spec.rate_limit}-request "
                f"limit for this {spec.window_s:g}s window")
        if spec.token_limit and state.tokens + tokens > spec.token_limit:
            self._set_health(state, EXHAUSTED)
            self._reason(provider, "token_limit_reached")
            raise QuotaExhausted(
                f"provider {provider!r} hit its {spec.token_limit}-token "
                f"limit for this {spec.window_s:g}s window")

        state.requests += 1
        state.tokens += max(0, tokens)
        return Reservation(provider=provider, cost=spec.unit_cost,
                           granted_at=self._clock())

    # ------------------------------------------------------------------
    # Outcomes
    # ------------------------------------------------------------------

    def record_success(self, provider: str, latency_ms: Optional[float] = None,
                       tokens: int = 0) -> ProviderState:
        state = self._require(provider)
        state.successes += 1
        state.consecutive_failures = 0
        if latency_ms is not None:
            state.latencies_ms.append(float(latency_ms))
        if tokens:
            state.tokens += tokens
        self._set_health(state, self._judge(state))
        return state

    def record_failure(self, provider: str, error: str = "",
                       latency_ms: Optional[float] = None,
                       exhausted: bool = False) -> ProviderState:
        """Record a failure. ``exhausted`` marks a provider-reported limit."""
        state = self._require(provider)
        state.failures += 1
        state.consecutive_failures += 1
        if latency_ms is not None:
            state.latencies_ms.append(float(latency_ms))
        if exhausted:
            state.reported_exhausted = True
        self._set_health(state, self._judge(state))
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.MODEL_FAILED,
                    project_id="",
                    payload={"provider": provider, "error": error,
                             "health": state.health,
                             "reported_exhausted": state.reported_exhausted},
                )
            )
        return state

    def mark_reported_exhausted(self, provider: str) -> ProviderState:
        """The provider itself said 'no more' - honour it immediately."""
        state = self._require(provider)
        state.reported_exhausted = True
        self._set_health(state, EXHAUSTED)
        return state

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def _judge(self, state: ProviderState) -> str:
        if state.reported_exhausted:
            return EXHAUSTED
        if state.samples < self.min_samples:
            return HEALTHY
        return DEGRADED if state.failure_ratio >= self.degrade_failure_ratio else HEALTHY

    def _set_health(self, state: ProviderState, health: str) -> None:
        if state.health == health:
            return
        previous = state.health
        state.health = health
        event_type = {
            HEALTHY: models.EventType.PROVIDER_HEALTHY,
            DEGRADED: models.EventType.PROVIDER_DEGRADED,
            EXHAUSTED: models.EventType.PROVIDER_EXHAUSTED,
        }[health]
        if self.bus is not None:
            self.bus.publish(
                models.Event(event_type=event_type, project_id="",
                             payload={"provider": state.name,
                                      "from": previous, "to": health,
                                      "failure_ratio": round(state.failure_ratio, 4),
                                      "samples": state.samples})
            )

    def _require(self, provider: str) -> ProviderState:
        state = self.states.get(provider)
        if state is None:
            raise KeyError(f"provider {provider!r} is not registered")
        spec = self.specs[provider]
        self._roll_window(state, spec)
        return state

    def _reason(self, provider: str, reason: str) -> None:
        """Record why a request was refused (audit, no bypass)."""
        self._events.append({"provider": provider, "reason": reason,
                             "at": self._clock()})

    # ------------------------------------------------------------------
    # Routing support / reporting
    # ------------------------------------------------------------------

    def healthy_providers(self, capability: Optional[str] = None) -> List[str]:
        out = []
        for name, spec in self.specs.items():
            if capability and capability not in spec.capabilities:
                continue
            if self.available(name):
                out.append(name)
        return out

    def cheapest(self, capability: Optional[str] = None) -> Optional[str]:
        """The adequate-but-cheaper provider (section 9), among available ones."""
        candidates = self.healthy_providers(capability)
        if not candidates:
            return None
        return min(candidates, key=lambda n: (self.specs[n].unit_cost,
                                              self.states[n].latency_p50_ms,
                                              n))

    def refusals(self) -> List[Dict[str, Any]]:
        return list(self._events)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "providers": {name: self.states[name].to_dict()
                          for name in sorted(self.states)},
            "available": sorted(self.healthy_providers()),
            "refusals": len(self._events),
            "degrade_failure_ratio": self.degrade_failure_ratio,
            "min_samples": self.min_samples,
            "note": ("local accounting over provider declarations; it cannot see "
                     "a provider's real quota and never bypasses provider limits"),
        }


def _percentile(samples: Sequence[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))
    return round(ordered[idx], 3)
