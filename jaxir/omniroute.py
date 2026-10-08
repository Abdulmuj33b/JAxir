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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class RoutingPolicy:
    coding_runtime: str = "codex"
    coding_runtime_family: str = "codex"
    fallback: List[str] = None

    def __post_init__(self):
        if self.fallback is None:
            self.fallback = []


class OmniRoute:
    def __init__(self, policy: RoutingPolicy, event_bus: Any):
        self.policy = policy
        self.bus = event_bus

    def select(self, task: Any, goal: Any) -> Dict[str, str]:
        """Return {'model_id', 'provider'} for a task."""
        # Real policy: capability + complexity + quota + availability.
        # This is deterministic for the todo slice so results are reproducible.
        complexity = self._complexity(task, goal)
        model_id = self.policy.coding_runtime
        provider = self.policy.coding_runtime_family
        # Escalate for complex architectures (future extension)
        if complexity > 8:
            model_id = "codex-max"
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.MODEL_REQUESTED,
                    project_id=goal.project_id if goal else "",
                    goal_id=goal.goal_id if goal else None,
                    task_id=getattr(task, "task_id", None),
                    payload={"model_id": model_id, "provider": provider},
                )
            )
        return {"model_id": model_id, "provider": provider}

    @staticmethod
    def _complexity(task: Any, goal: Any) -> int:
        # deterministic proxy: count acceptance criteria + requirements
        n = len(getattr(task, "acceptance_criteria", []))
        if goal is not None:
            n += len(getattr(goal, "requirements", []))
        return min(n, 10)
