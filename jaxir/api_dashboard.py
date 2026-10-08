"""API Slice Dashboard: richer observability for API project-type lifecycle.

The dashboard aggregates state from the lifecycle, event bus, and checkpoint
manager to provide a comprehensive view of API service execution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class APISliceDashboard:
    """Operational dashboard for API slice execution."""

    goal_id: Optional[str] = None
    project_id: Optional[str] = None
    title: str = ""
    status: str = "UNKNOWN"
    current_phase: str = "CREATED"
    progress_percent: float = 0.0
    
    # Planning metrics
    tasks_total: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    task_dependencies: List[Dict[str, Any]] = field(default_factory=list)
    
    # Build metrics
    files_generated: List[str] = field(default_factory=list)
    build_errors: List[str] = field(default_factory=list)
    
    # Preview metrics
    service_endpoint: str = ""
    service_running: bool = False
    service_pid: Optional[int] = None
    service_uptime_seconds: float = 0.0
    
    # QA metrics
    tests_total: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    test_details: List[Dict[str, Any]] = field(default_factory=list)
    
    # Feedback metrics
    feedback_status: str = "pending"
    feedback_action: str = "none"
    failures_detected: List[str] = field(default_factory=list)
    
    # Event metrics
    events_total: int = 0
    event_types: Dict[str, int] = field(default_factory=dict)
    recent_events: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "project_id": self.project_id,
            "title": self.title,
            "status": self.status,
            "current_phase": self.current_phase,
            "progress_percent": round(self.progress_percent, 2),
            "tasks": {
                "total": self.tasks_total,
                "completed": self.tasks_completed,
                "failed": self.tasks_failed,
                "dependencies": self.task_dependencies,
            },
            "build": {
                "files_generated": self.files_generated,
                "errors": self.build_errors,
            },
            "preview": {
                "endpoint": self.service_endpoint,
                "running": self.service_running,
                "pid": self.service_pid,
                "uptime_seconds": round(self.service_uptime_seconds, 2),
            },
            "qa": {
                "tests_total": self.tests_total,
                "tests_passed": self.tests_passed,
                "tests_failed": self.tests_failed,
                "details": self.test_details,
            },
            "feedback": {
                "status": self.feedback_status,
                "action": self.feedback_action,
                "failures_detected": self.failures_detected,
            },
            "events": {
                "total": self.events_total,
                "by_type": self.event_types,
                "recent": self.recent_events,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def render_text(self) -> str:
        lines = [
            "╔═══════════════════════════════════════════╗",
            "║  JaXir API Slice Dashboard                ║",
            "╚═══════════════════════════════════════════╝",
            "",
            f"Goal ID: {self.goal_id}",
            f"Project: {self.project_id}",
            f"Title: {self.title}",
            f"Status: {self.status}",
            f"Phase: {self.current_phase}",
            f"Progress: {self.progress_percent:.1f}%",
            "",
            "┌─ Planning ──────────────────────────────┐",
            f"│ Tasks: {self.tasks_completed}/{self.tasks_total} completed ({self.tasks_failed} failed)",
            f"│ Dependencies: {len(self.task_dependencies)} defined",
            "└─────────────────────────────────────────┘",
            "",
            "┌─ Build ─────────────────────────────────┐",
            f"│ Files generated: {len(self.files_generated)}",
            f"│ Errors: {len(self.build_errors)}",
            f"│ Generated: {', '.join(self.files_generated) if self.files_generated else 'none'}",
            "└─────────────────────────────────────────┘",
            "",
            "┌─ Preview ───────────────────────────────┐",
            f"│ Service: {'RUNNING' if self.service_running else 'STOPPED'}",
            f"│ Endpoint: {self.service_endpoint}",
            f"│ Uptime: {self.service_uptime_seconds:.1f}s",
            "└─────────────────────────────────────────┘",
            "",
            "┌─ QA Testing ────────────────────────────┐",
            f"│ Tests: {self.tests_passed}/{self.tests_total} passed",
            f"│ Failed: {self.tests_failed}",
            "│ Details:",
        ]

        for detail in self.test_details[-5:]:
            status_icon = "✓" if detail.get("passed") else "✗"
            lines.append(f"│   {status_icon} {detail.get('test_id', 'unknown')}")

        lines.extend([
            "└─────────────────────────────────────────┘",
            "",
            "┌─ Feedback ──────────────────────────────┐",
            f"│ Status: {self.feedback_status}",
            f"│ Action: {self.feedback_action}",
        ])

        if self.failures_detected:
            lines.append("│ Failures:")
            for failure in self.failures_detected[:3]:
                lines.append(f"│   - {failure}")

        lines.extend([
            "└─────────────────────────────────────────┘",
            "",
            "┌─ Events ────────────────────────────────┐",
            f"│ Total events: {self.events_total}",
            "│ By type:",
        ])

        for event_type, count in sorted(self.event_types.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"│   {event_type}: {count}")

        lines.extend([
            "└─────────────────────────────────────────┘",
            "",
        ])

        return "\n".join(lines)


class APISliceDashboardFactory:
    """Build dashboards from lifecycle and event bus state."""

    @staticmethod
    def from_lifecycle(
        lifecycle: Any, event_bus: Optional[Any] = None
    ) -> APISliceDashboard:
        """Construct a dashboard from a lifecycle instance."""
        if lifecycle.goal is None:
            return APISliceDashboard()

        goal = lifecycle.goal
        tasks = lifecycle.tasks
        evidence = lifecycle.evidence

        # Calculate phase
        phase = goal.status.value if hasattr(goal.status, "value") else str(goal.status)

        # Count tasks
        total_tasks = len(tasks)
        completed_tasks = sum(
            1 for t in tasks if str(getattr(t, "status", "")).lower() == "completed"
        )
        failed_tasks = sum(
            1 for t in tasks if str(getattr(t, "status", "")).lower() == "failed"
        )

        # Extract build results
        files_generated = []
        build_errors = []
        for task in tasks:
            if task.task_id == "api-build":
                result = getattr(task, "result", {})
                files_generated = result.get("files_generated", [])

        # Extract preview state
        service_endpoint = ""
        service_running = False
        service_pid = None
        service_uptime_seconds = 0.0
        if lifecycle.preview is not None:
            service_running = lifecycle.preview.process is not None and lifecycle.preview.process.poll() is None
            service_pid = lifecycle.preview.process.pid if lifecycle.preview.process else None
            service_endpoint = "http://127.0.0.1:8766"
            if lifecycle.preview.started_at:
                import time
                service_uptime_seconds = time.time() - lifecycle.preview.started_at

        # Extract QA state
        tests_total = len(evidence)
        tests_passed = sum(
            1 for e in evidence if str(getattr(e, "status", "")).upper() == "PASS"
        )
        tests_failed = sum(
            1 for e in evidence if str(getattr(e, "status", "")).upper() == "FAIL"
        )
        test_details = [
            {
                "test_id": getattr(e, "test_id", "unknown"),
                "passed": str(getattr(e, "status", "")).upper() == "PASS",
                "expected": getattr(e, "expected", {}),
                "actual": getattr(e, "actual", {}),
            }
            for e in evidence
        ]

        # Extract feedback state
        feedback = getattr(lifecycle, "feedback", {})
        feedback_status = feedback.get("status", "pending")
        feedback_action = feedback.get("action", "none")
        failures_detected = feedback.get("failures", [])

        # Extract event state
        events_total = 0
        event_types: Dict[str, int] = {}
        recent_events: List[str] = []
        if event_bus is not None:
            events = event_bus.get(goal_id=goal.goal_id)
            events_total = len(events)
            for e in events:
                event_key = getattr(e, "event_type", "unknown")
                if hasattr(event_key, "value"):
                    event_key = event_key.value
                event_types[str(event_key)] = event_types.get(str(event_key), 0) + 1
            recent_events = [
                str(getattr(e, "event_type", "unknown")).replace("EventType.", "")
                for e in events[-5:]
            ]

        # Calculate progress
        progress = 0.0
        if total_tasks > 0:
            progress = (completed_tasks / total_tasks) * 100.0

        return APISliceDashboard(
            goal_id=getattr(goal, "goal_id", None),
            project_id=getattr(goal, "project_id", None),
            title=getattr(goal, "title", ""),
            status=phase,
            current_phase=phase,
            progress_percent=progress,
            tasks_total=total_tasks,
            tasks_completed=completed_tasks,
            tasks_failed=failed_tasks,
            task_dependencies=[
                {"task_id": t.task_id, "dependencies": t.dependencies}
                for t in tasks
            ],
            files_generated=files_generated,
            build_errors=build_errors,
            service_endpoint=service_endpoint,
            service_running=service_running,
            service_pid=service_pid,
            service_uptime_seconds=service_uptime_seconds,
            tests_total=tests_total,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            test_details=test_details,
            feedback_status=feedback_status,
            feedback_action=feedback_action,
            failures_detected=failures_detected,
            events_total=events_total,
            event_types=event_types,
            recent_events=recent_events,
        )
