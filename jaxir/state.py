"""Goal state machine.

Goal state is LOCKED (RFC-001): CREATED, ANALYZING, PLANNED, EXECUTING,
VERIFYING, FAILED, PASSED, REPLANNING, COMPLETED, PAUSED, BLOCKED,
CANCELLED, ROLLED_BACK.

State transitions are audited: every transition emits an event and is
idempotent (redundant calls to the same state are no-ops).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import models


class GoalStateMachine:
    _TRANSITIONS: Dict[models.GoalStatus, List[models.GoalStatus]] = {
        models.GoalStatus.CREATED: [
            models.GoalStatus.ANALYZING,
            models.GoalStatus.PAUSED,
            models.GoalStatus.CANCELLED,
            models.GoalStatus.BLOCKED,
        ],
        models.GoalStatus.ANALYZING: [
            models.GoalStatus.PLANNED,
            models.GoalStatus.FAILED,
            models.GoalStatus.PAUSED,
            models.GoalStatus.CANCELLED,
            models.GoalStatus.BLOCKED,
        ],
        models.GoalStatus.PLANNED: [
            models.GoalStatus.EXECUTING,
            models.GoalStatus.REPLANNING,
            models.GoalStatus.PAUSED,
            models.GoalStatus.CANCELLED,
            models.GoalStatus.BLOCKED,
        ],
        models.GoalStatus.EXECUTING: [
            models.GoalStatus.VERIFYING,
            models.GoalStatus.FAILED,
            models.GoalStatus.PAUSED,
            models.GoalStatus.CANCELLED,
            models.GoalStatus.BLOCKED,
        ],
        models.GoalStatus.VERIFYING: [
            models.GoalStatus.PASSED,
            models.GoalStatus.FAILED,
            models.GoalStatus.REPLANNING,
            models.GoalStatus.PAUSED,
            models.GoalStatus.CANCELLED,
        ],
        models.GoalStatus.REPLANNING: [
            models.GoalStatus.PLANNED,
            models.GoalStatus.EXECUTING,
            models.GoalStatus.PAUSED,
            models.GoalStatus.CANCELLED,
            models.GoalStatus.BLOCKED,
        ],
        models.GoalStatus.FAILED: [
            models.GoalStatus.REPLANNING,
            models.GoalStatus.PASSED,
            models.GoalStatus.ROLLED_BACK,
            models.GoalStatus.PAUSED,
            models.GoalStatus.CANCELLED,
        ],
        models.GoalStatus.PAUSED: [
            models.GoalStatus.EXECUTING,
            models.GoalStatus.REPLANNING,
            models.GoalStatus.CANCELLED,
            models.GoalStatus.FAILED,
        ],
        models.GoalStatus.BLOCKED: [
            models.GoalStatus.EXECUTING,
            models.GoalStatus.REPLANNING,
            models.GoalStatus.CANCELLED,
            models.GoalStatus.FAILED,
        ],
        models.GoalStatus.CANCELLED: [],
        models.GoalStatus.ROLLED_BACK: [],
        models.GoalStatus.PASSED: [],
        models.GoalStatus.COMPLETED: [],
    }

    def __init__(self, event_bus: Any):
        self.bus = event_bus

    def transition(self, goal: models.Goal, to_status: models.GoalStatus) -> bool:
        if goal.status == to_status:
            return False  # idempotent
        allowed = self._TRANSITIONS.get(goal.status, [])
        if to_status not in allowed:
            raise ValueError(
                f"Invalid goal state transition: {goal.status.value} -> "
                f"{to_status.value} (allowed: {[s.value for s in allowed]})"
            )
        goal.status = to_status
        goal.updated_at = datetime_now()
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.GOAL_COMPLETED
                    if to_status in (
                        models.GoalStatus.COMPLETED,
                        models.GoalStatus.PASSED,
                        models.GoalStatus.FAILED,
                    )
                    else models.EventType.GOAL_CREATED,
                    project_id=goal.project_id,
                    goal_id=goal.goal_id,
                    payload={"from": goal.status.value, "to": to_status.value},
                )
            )
        return True


def datetime_now() -> Any:
    """Deterministic clock injection point for tests."""
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc)
