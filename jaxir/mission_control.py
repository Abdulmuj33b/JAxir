"""Mission Control and requirement traceability for JaXir OS.

The UI is intentionally minimal and engineered for clarity: it surfaces the
actual kernel state rather than decorative output. This keeps the system aligned
with Occam's Razor while still satisfying the human-control and auditability
requirements in RFC-001.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class MissionSnapshot:
    """Compact operating view for a running goal."""

    goal_id: Optional[str] = None
    project_id: Optional[str] = None
    title: str = ""
    status: str = "UNKNOWN"
    progress_percent: float = 0.0
    tasks_total: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    evidence_passed: int = 0
    evidence_failed: int = 0
    active_agent: str = "n/a"
    preview_status: str = "not_started"
    human_actions: List[str] = field(
        default_factory=lambda: [
            "pause",
            "resume",
            "cancel",
            "approve",
            "reject",
            "redirect",
            "rollback",
            "inspect",
            "take_control",
        ]
    )
    event_counts: Dict[str, int] = field(default_factory=dict)
    recent_events: List[str] = field(default_factory=list)
    last_event: str = "n/a"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "project_id": self.project_id,
            "title": self.title,
            "status": self.status,
            "progress_percent": round(self.progress_percent, 2),
            "tasks_total": self.tasks_total,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "evidence_passed": self.evidence_passed,
            "evidence_failed": self.evidence_failed,
            "active_agent": self.active_agent,
            "preview_status": self.preview_status,
            "human_actions": list(self.human_actions),
            "event_counts": dict(self.event_counts),
            "recent_events": list(self.recent_events),
            "last_event": self.last_event,
        }

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), indent=2)


class MissionControl:
    """Operational summary for Goal Mode and execution loops."""

    def __init__(self, goal: Optional[Any] = None, tasks: Optional[Iterable[Any]] = None,
                 evidence: Optional[Iterable[Any]] = None, event_bus: Optional[Any] = None):
        self.goal = goal
        self.tasks = list(tasks or [])
        self.evidence = list(evidence or [])
        self.event_bus = event_bus

    @classmethod
    def from_event_bus(cls, bus: Any, goal_id: Optional[str] = None,
                       goal: Optional[Any] = None, tasks: Optional[Iterable[Any]] = None,
                       evidence: Optional[Iterable[Any]] = None) -> "MissionControl":
        control = cls(goal=goal, tasks=tasks, evidence=evidence, event_bus=bus)
        control.goal_id = goal_id
        return control

    def bind_goal(self, goal: Any) -> "MissionControl":
        self.goal = goal
        return self

    def bind_tasks(self, tasks: Iterable[Any]) -> "MissionControl":
        self.tasks = list(tasks)
        return self

    def bind_evidence(self, evidence: Iterable[Any]) -> "MissionControl":
        self.evidence = list(evidence)
        return self

    def bind_event_bus(self, event_bus: Any) -> "MissionControl":
        self.event_bus = event_bus
        return self

    @staticmethod
    def _status_name(value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "value"):
            return str(value.value)
        return str(value)

    def _event_summary(self) -> Dict[str, Any]:
        if self.event_bus is None:
            return {"counts": {}, "recent": [], "last": "n/a"}
        goal_id = getattr(self.goal, "goal_id", None)
        if goal_id is None and hasattr(self, "goal_id"):
            goal_id = self.goal_id
        events = self.event_bus.get(goal_id=goal_id) if goal_id else self.event_bus.get()
        counts: Dict[str, int] = {}
        for event in events:
            key = event.event_type.value
            counts[key] = counts.get(key, 0) + 1
        recent = [event.event_type.value for event in events[-5:]]
        last = events[-1].event_type.value if events else "n/a"
        return {"counts": counts, "recent": recent, "last": last}

    def snapshot(self) -> MissionSnapshot:
        goal_id = getattr(self.goal, "goal_id", None) if self.goal is not None else getattr(self, "goal_id", None)
        if self.goal is None and not goal_id:
            return MissionSnapshot()

        total = len(self.tasks)
        completed = sum(
            1
            for t in self.tasks
            if self._status_name(getattr(t, "status", None)).upper() == "COMPLETED"
        )
        failed = sum(
            1
            for t in self.tasks
            if self._status_name(getattr(t, "status", None)).upper() == "FAILED"
        )
        passed = sum(
            1
            for e in self.evidence
            if self._status_name(getattr(e, "status", None)).upper() == "PASS"
        )
        failed_evidence = sum(
            1
            for e in self.evidence
            if self._status_name(getattr(e, "status", None)).upper() == "FAIL"
        )

        progress = 0.0
        if total > 0:
            progress = (completed / total) * 100.0

        event_summary = self._event_summary()
        project_id = getattr(self.goal, "project_id", None) if self.goal is not None else None
        if self.event_bus is not None and project_id is None and goal_id:
            for event in self.event_bus.get(goal_id=goal_id):
                if event.project_id:
                    project_id = event.project_id
                    break

        goal_status = getattr(self.goal, "status", "UNKNOWN") if self.goal is not None else "UNKNOWN"
        status = (
            goal_status.value if hasattr(goal_status, "value") else str(goal_status)
        )
        title = getattr(self.goal, "title", "") if self.goal is not None else ""

        return MissionSnapshot(
            goal_id=goal_id,
            project_id=project_id,
            title=title,
            status=status,
            progress_percent=progress,
            tasks_total=total,
            tasks_completed=completed,
            tasks_failed=failed,
            evidence_passed=passed,
            evidence_failed=failed_evidence,
            active_agent=self._active_agent(),
            preview_status=self._preview_status(),
            event_counts=event_summary["counts"],
            recent_events=event_summary["recent"],
            last_event=event_summary["last"],
        )

    def _active_agent(self) -> str:
        if not self.tasks:
            return "n/a"
        for task in self.tasks:
            status_name = self._status_name(getattr(task, "status", None)).upper()
            if status_name in {"RUNNING", "QUEUED"}:
                return (
                    getattr(task, "owner_agent_id", "n/a")
                    or getattr(task, "agent_type", "n/a")
                    or "n/a"
                )
        return "idle"

    def _preview_status(self) -> str:
        if not self.evidence:
            return "not_started"
        for evidence in self.evidence:
            if getattr(evidence, "test_id", "") == "qa.preview":
                status_name = self._status_name(getattr(evidence, "status", "NOT_RUN")).upper()
                return "passed" if status_name == "PASS" else "failed"
        return "pending"

    def summary(self) -> Dict[str, Any]:
        return self.snapshot().to_dict()

    def human_questions(self) -> List[str]:
        return [
            "What am I trying to accomplish?",
            "What is JaXir doing?",
            "Is it working?",
            "What has been verified?",
            "What failed?",
            "What is JaXir doing about it?",
            "What needs my attention?",
        ]

    def render_text(self) -> str:
        snap = self.snapshot()
        event_summary = ""
        if snap.event_counts:
            event_summary = " | ".join(f"{k}={v}" for k, v in snap.event_counts.items())
        lines = [
            "JaXir Mission Control",
            "====================",
            f"Goal: {snap.goal_id or 'unspecified'}",
            f"Project: {snap.project_id or 'unspecified'}",
            f"Title: {snap.title or 'unnamed'}",
            f"Status: {snap.status}",
            f"Progress: {snap.progress_percent:.1f}% ({snap.tasks_completed}/{snap.tasks_total})",
            f"Evidence: {snap.evidence_passed} passed / {snap.evidence_failed} failed",
            f"Active agent: {snap.active_agent}",
            f"Preview: {snap.preview_status}",
            f"Last event: {snap.last_event}",
            f"Event summary: {event_summary if event_summary else 'n/a'}",
            "Human actions: " + ", ".join(snap.human_actions),
        ]
        return "\n".join(lines)
