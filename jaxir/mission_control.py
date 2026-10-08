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
        }


class MissionControl:
    """Operational summary for Goal Mode and execution loops.

    This is intentionally lightweight and does not duplicate the state machine.
    It reads the goal/task/evidence state already managed by the kernel and
displays a concise operational dashboard for operators.
    """

    def __init__(self, goal: Optional[Any] = None, tasks: Optional[Iterable[Any]] = None,
                 evidence: Optional[Iterable[Any]] = None):
        self.goal = goal
        self.tasks = list(tasks or [])
        self.evidence = list(evidence or [])

    def bind_goal(self, goal: Any) -> "MissionControl":
        self.goal = goal
        return self

    def bind_tasks(self, tasks: Iterable[Any]) -> "MissionControl":
        self.tasks = list(tasks)
        return self

    def bind_evidence(self, evidence: Iterable[Any]) -> "MissionControl":
        self.evidence = list(evidence)
        return self

    def snapshot(self) -> MissionSnapshot:
        if self.goal is None:
            return MissionSnapshot()

        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if getattr(t, "status", None) and str(t.status).lower() == "completed")
        failed = sum(1 for t in self.tasks if getattr(t, "status", None) and str(t.status).lower() == "failed")
        passed = sum(1 for e in self.evidence if getattr(e, "status", None) and str(e.status).upper() == "PASS")
        failed_evidence = sum(1 for e in self.evidence if getattr(e, "status", None) and str(e.status).upper() == "FAIL")

        progress = 0.0
        if total > 0:
            progress = (completed / total) * 100.0

        return MissionSnapshot(
            goal_id=getattr(self.goal, "goal_id", None),
            project_id=getattr(self.goal, "project_id", None),
            title=getattr(self.goal, "title", ""),
            status=getattr(self.goal, "status", "UNKNOWN").value if hasattr(getattr(self.goal, "status", None), "value") else str(getattr(self.goal, "status", "UNKNOWN")),
            progress_percent=progress,
            tasks_total=total,
            tasks_completed=completed,
            tasks_failed=failed,
            evidence_passed=passed,
            evidence_failed=failed_evidence,
            active_agent=self._active_agent(),
            preview_status=self._preview_status(),
        )

    def _active_agent(self) -> str:
        if not self.tasks:
            return "n/a"
        for task in self.tasks:
            if getattr(task, "status", None) and str(task.status).lower() in {"running", "queued"}:
                return getattr(task, "owner_agent_id", "n/a") or getattr(task, "agent_type", "n/a") or "n/a"
        return "idle"

    def _preview_status(self) -> str:
        if not self.evidence:
            return "not_started"
        for e in self.evidence:
            if getattr(e, "test_id", "") == "qa.preview":
                return "passed" if str(getattr(e, "status", "NOT_RUN")).upper() == "PASS" else "failed"
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
            "Human actions: " + ", ".join(snap.human_actions),
        ]
        return "\n".join(lines)
