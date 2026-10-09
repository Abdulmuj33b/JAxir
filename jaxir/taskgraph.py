"""Dependency-aware task graph + scheduler.

Tasks are decomposed from a goal and scheduled respecting:
- dependencies (no dependency violations)
- unsafe concurrency (resource/env conflicts)
- file conflicts (isolated sandboxes)
- environment conflicts (mutual exclusion)
- duplicate execution of non-idempotent actions

The scheduler computes a valid topological order and is observable via the
event bus.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from . import models


class ConflictKind(str, Enum):
    FILE = "file"
    ENVIRONMENT = "environment"
    RESOURCE = "resource"
    NETWORK = "network"
    SECRET = "secret"


@dataclass
class Conflict:
    task_id: str
    kind: ConflictKind
    description: str
    severity: str = "high"


@dataclass
class SchedulingResult:
    ordered_tasks: List[models.Task]
    blocked: Dict[str, List[str]]
    conflicts: List[Conflict] = field(default_factory=list)


class TaskGraph:
    def __init__(self, event_bus: Any):
        self.bus = event_bus

    # ------------------------------------------------------------------
    # Topology helpers
    # ------------------------------------------------------------------

    @staticmethod
    def build(goal: models.Goal) -> List[models.Task]:
        """Materialize the task graph from the goal's task_graph spec."""
        tasks: Dict[str, models.Task] = {}
        for spec in goal.task_graph:
            t = models.Task(
                goal_id=goal.goal_id,
                owner_agent_id=spec.get("owner_agent_id", "planner"),
                agent_id=spec.get("agent_id", ""),
                agent_type=spec.get("agent_type", "planner"),
                model_id=spec.get("model_id", ""),
                provider=spec.get("provider", ""),
                capabilities=spec.get("capabilities", []),
                status=models.TaskStatus.PENDING,
                dependencies=spec.get("dependencies", []),
                inputs=spec.get("inputs", {}),
                outputs=spec.get("outputs", {}),
                acceptance_criteria=spec.get("acceptance_criteria", []),
                verification=spec.get("verification"),
                retry_policy=spec.get("retry_policy", {}),
            )
            tasks[t.task_id] = t
        goal.task_graph = list(tasks.values())
        return list(tasks.values())

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def topological_order(self, tasks: List[models.Task]) -> List[models.Task]:
        """Kahn's algorithm; returns a valid execution order."""
        adj: Dict[str, Set[str]] = {t.task_id: set() for t in tasks}
        indeg: Dict[str, int] = {t.task_id: 0 for t in tasks}
        for t in tasks:
            for d in t.dependencies:
                if d in adj:
                    adj[d].add(t.task_id)
                    indeg[t.task_id] += 1
        # priority queue for deterministic tie-breaking
        ready = [t for t, n in indeg.items() if n == 0]
        heapq.heapify(ready)
        order: List[models.Task] = []
        while ready:
            tid = heapq.heappop(ready)
            order.append(next(t for t in tasks if t.task_id == tid))
            for child in adj[tid]:
                indeg[child] -= 1
                if indeg[child] == 0:
                    heapq.heappush(ready, child)
        if len(order) != len(tasks):
            raise ValueError(
                "Dependency cycle detected in task graph "
                f"({len(tasks) - len(order)} tasks unreachable)"
            )
        return order

    def detect_conflicts(self, tasks: List[models.Task],
                         project_id: str) -> List[Conflict]:
        conflicts: List[Conflict] = []
        by_env: Dict[str, List[str]] = {}
        by_resource: Dict[str, List[str]] = {}
        by_path: Dict[str, List[str]] = {}
        by_operation: Dict[str, List[str]] = {}

        for t in tasks:
            env = t.inputs.get("environment", "")
            if env:
                by_env.setdefault(env, []).append(t.task_id)

            resource_keys = (
                "resource",
                "resource_id",
                "resource_name",
                "device",
                "lock",
                "workspace",
                "project_dir",
            )
            for key in resource_keys:
                value = t.inputs.get(key) or t.outputs.get(key)
                if value:
                    by_resource.setdefault(str(value), []).append(t.task_id)

            entries: List[str] = []
            for key in ("path", "file", "target_path", "output_path"):
                value = t.inputs.get(key)
                if value:
                    entries.append(str(value))
            for key in ("path", "file", "target_path", "output_path"):
                value = t.outputs.get(key)
                if value:
                    entries.append(str(value))
            files = t.inputs.get("files") or t.outputs.get("files") or []
            if isinstance(files, (list, tuple, set)):
                entries.extend(str(v) for v in files)
            if isinstance(files, str):
                entries.append(files)
            for entry in set(entries):
                by_path.setdefault(entry, []).append(t.task_id)

            operation = (
                t.inputs.get("operation")
                or t.inputs.get("op")
                or t.inputs.get("action")
                or ""
            )
            if operation:
                op_name = str(operation).lower()
                idempotent = bool(t.inputs.get("idempotent", True))
                if not idempotent:
                    by_operation.setdefault(op_name, []).append(t.task_id)

        for env, ids in by_env.items():
            if len(ids) > 1:
                conflicts.append(
                    Conflict(task_id=ids[0], kind=ConflictKind.ENVIRONMENT,
                             description=f"Shared environment {env} for tasks {ids}")
                )
        for resource, ids in by_resource.items():
            if len(ids) > 1:
                conflicts.append(
                    Conflict(task_id=ids[0], kind=ConflictKind.RESOURCE,
                             description=f"Shared resource {resource} is targeted by tasks {ids}")
                )
        for path, ids in by_path.items():
            if len(ids) > 1:
                conflicts.append(
                    Conflict(task_id=ids[0], kind=ConflictKind.FILE,
                             description=f"File path {path} is targeted by multiple tasks {ids}")
                )
        for op_name, ids in by_operation.items():
            if len(ids) > 1:
                conflicts.append(
                    Conflict(task_id=ids[0], kind=ConflictKind.RESOURCE,
                             description=f"Non-idempotent operation {op_name} is duplicated across tasks {ids}")
                )
        return conflicts

    def schedule(self, goal: models.Goal) -> SchedulingResult:
        tasks = list(goal.task_graph)
        order = self.topological_order(tasks)
        conflicts = self.detect_conflicts(tasks, goal.project_id)
        blocked: Dict[str, List[str]] = {t.task_id: list(t.dependencies) for t in tasks}
        for t in order:
            for d in t.dependencies:
                blocked.pop(d, None)
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.GOAL_PLANNED,
                    project_id=goal.project_id,
                    goal_id=goal.goal_id,
                    payload={"ordered": [t.task_id for t in order]},
                )
            )
        return SchedulingResult(ordered_tasks=order, blocked=blocked,
                                conflicts=conflicts)
