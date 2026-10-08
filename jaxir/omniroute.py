"""OmniRoute -- model/provider routing policy.

Architecture::

    Agent -> Model Runtime -> Routing Policy -> OmniRoute -> Provider

Codex is the initial coding runtime. OmniRoute is routing infrastructure,
not a permanent architectural dependency. The system is model/provider-agnostic:
routing policy considers capability, task complexity, quality, reliability,
latency, availability, cost/capacity, quota, and context requirements.

Policy:
- never bypass provider restrictions or quotas
- route coding tasks to the coding runtime (Codex yet)
- prefer adequate-but-cheaper providers for simple tasks
- escalate to better capability when task complexity exceeds threshold

Routing is *capacity-aware*: a provider the QuotaManager reports as degraded or
exhausted is not selected. When no provider can serve the task, ``select``
returns a refusal (``available: False``) rather than inventing capacity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import models


@dataclass
class RoutingPolicy:
    coding_runtime: str = "codex"
    coding_runtime_family: str = "codex"
    fallback: List[str] = field(default_factory=list)
    #: Capability a coding task requires of whichever provider serves it.
    required_capability: str = "code"
    #: Complexity above which the policy escalates to a stronger model.
    escalation_complexity: int = 8
    escalation_model: str = "codex-max"

    def __post_init__(self):
        if self.fallback is None:
            self.fallback = []


class OmniRoute:
    def __init__(self, policy: RoutingPolicy, event_bus: Any,
                 quota: Any = None):
        self.policy = policy
        self.bus = event_bus
        self.quota = quota

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def select(self, task: Any, goal: Any) -> Dict[str, Any]:
        """Choose {'model_id', 'provider', 'available', 'reason'} for a task.

        Considers capability, complexity and quota/capacity. Emits
        ``model.requested`` when a provider is chosen.
        """
        complexity = self._complexity(task, goal)
        required = self._required_capability(task)
        model_id = (self.policy.escalation_model
                    if complexity > self.policy.escalation_complexity
                    else self.policy.coding_runtime)
        provider = self.policy.coding_runtime_family

        # Capacity-aware: ask the quota manager which providers may serve this.
        candidates = self._candidates(required)
        if candidates:
            provider = candidates[0]
        elif self.quota is not None:
            # Declared providers exist but none is usable: refuse honestly.
            return self._refuse(task, goal, model_id, provider, required,
                                reason="no_provider_available")

        decision = {
            "model_id": model_id,
            "provider": provider,
            "family": self.policy.coding_runtime_family,
            "available": True,
            "reason": "selected",
            "complexity": complexity,
            "required_capability": required,
            "candidates": candidates,
        }
        self._emit(models.EventType.MODEL_REQUESTED, task, goal, {
            "model_id": model_id, "provider": provider,
            "complexity": complexity, "required_capability": required,
        })
        return decision

    def _refuse(self, task: Any, goal: Any, model_id: str, provider: str,
                required: str, reason: str) -> Dict[str, Any]:
        """No provider may serve the task: refuse, do not bypass (section 11)."""
        decision = {
            "model_id": model_id,
            "provider": provider,
            "family": self.policy.coding_runtime_family,
            "available": False,
            "reason": reason,
            "required_capability": required,
            "candidates": [],
        }
        self._emit(models.EventType.MODEL_FAILED, task, goal, {
            "model_id": model_id, "provider": provider, "reason": reason,
            "refused": True,
        })
        return decision

    def _candidates(self, required: str) -> List[str]:
        """Available providers, cheapest-first (section 9)."""
        if self.quota is None:
            return []
        candidates = self.quota.healthy_providers(required)
        return sorted(candidates,
                      key=lambda n: (self.quota.specs[n].unit_cost,
                                     self.quota.states[n].latency_p50_ms, n))

    def _required_capability(self, task: Any) -> str:
        """A corrective or build task needs code capability; fall back to the policy."""
        agent_type = str(getattr(task, "agent_type", "") or "")
        if agent_type in ("coder", "planner"):
            return self.policy.required_capability
        return self.policy.required_capability

    def _emit(self, event_type: models.EventType, task: Any, goal: Any,
              payload: Dict[str, Any]) -> None:
        if self.bus is None:
            return
        self.bus.publish(
            models.Event(
                event_type=event_type,
                project_id=getattr(goal, "project_id", "") if goal else "",
                goal_id=getattr(goal, "goal_id", None) if goal else None,
                task_id=getattr(task, "task_id", None),
                payload=payload,
            )
        )

    @staticmethod
    def _complexity(task: Any, goal: Any) -> int:
        # deterministic proxy: count acceptance criteria + requirements
        n = len(getattr(task, "acceptance_criteria", []) or [])
        if goal is not None:
            n += len(getattr(goal, "requirements", []) or [])
        return min(n, 10)
