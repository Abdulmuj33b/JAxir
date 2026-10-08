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
            models.GoalStatus.BLOCKED,
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
        models.GoalStatus.PASSED: [
            # Verification passed; the goal engine may now record completion.
            # Without this edge COMPLETED would be an unreachable terminal
            # state - the success endpoint of the whole execution loop.
            models.GoalStatus.COMPLETED,
        ],
        models.GoalStatus.COMPLETED: [],
    }

    #: Closest event for each target state. The payload always carries the exact
    #: ``from``/``to`` states, so auditability does not depend on this mapping;
    #: it exists because the locked event set (section 12) has no event per state.
    _EVENT_FOR_STATUS: Dict[models.GoalStatus, models.EventType] = {
        models.GoalStatus.CREATED: models.EventType.GOAL_CREATED,
        models.GoalStatus.ANALYZING: models.EventType.GOAL_ANALYZED,
        models.GoalStatus.PLANNED: models.EventType.GOAL_PLANNED,
        models.GoalStatus.REPLANNING: models.EventType.GOAL_PLANNED,
        models.GoalStatus.EXECUTING: models.EventType.GOAL_RESUMED,
        models.GoalStatus.VERIFYING: models.EventType.GOAL_RESUMED,
        models.GoalStatus.PASSED: models.EventType.GOAL_COMPLETED,
        models.GoalStatus.COMPLETED: models.EventType.GOAL_COMPLETED,
        models.GoalStatus.FAILED: models.EventType.GOAL_FAILED,
        models.GoalStatus.BLOCKED: models.EventType.GOAL_FAILED,
        models.GoalStatus.CANCELLED: models.EventType.GOAL_FAILED,
        models.GoalStatus.ROLLED_BACK: models.EventType.GOAL_FAILED,
        models.GoalStatus.PAUSED: models.EventType.GOAL_PAUSED,
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
        previous = goal.status
        goal.status = to_status
        goal.updated_at = datetime_now()
        self._audit(goal, previous, to_status)
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=self._EVENT_FOR_STATUS.get(
                        to_status, models.EventType.GOAL_CREATED),
                    project_id=goal.project_id,
                    goal_id=goal.goal_id,
                    payload={"from": previous.value, "to": to_status.value},
                )
            )
        return True

    @staticmethod
    def _audit(goal: models.Goal, previous: models.GoalStatus,
               to_status: models.GoalStatus) -> None:
        """Append to the goal's transition log.

        The locked target is 100% auditable state transitions: every accepted
        transition is recorded on the goal itself, so a goal that reaches a
        terminal state carries the full path it took.
        """
        trail = goal.state.setdefault("transitions", [])
        trail.append({
            "from": previous.value,
            "to": to_status.value,
            "at": datetime_now().isoformat(),
        })


def datetime_now() -> Any:
    """Deterministic clock injection point for tests."""
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc)
