"""Simple CLI for JaXir OS.

This is intentionally lightweight: it exposes a human-readable mission summary
and the current requirement traceability state without adding framework weight.

The CLI can:
- Display live goal/task state from a running TodoApp
- Surface requirement traceability
- Provide human-readable mission control output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .mission_control import MissionControl
from .traceability import default_traceability


def _load_live_goal(project_dir: Optional[str] = None):
    """Load live goal/task state from the latest checkpoint on disk.

    This is the real source of truth used by Mission Control. Demo payloads are
    only a fallback when no checkpoint is available.
    """
    from . import models
    from .checkpoint import CheckpointManager
    from .events import EventBus

    project_path = Path(project_dir) if project_dir else Path.cwd() / "todo_slice"
    if not project_path.exists():
        return None, [], []

    try:
        bus = EventBus()
        cp_mgr = CheckpointManager(str(project_path), bus)
        record = cp_mgr.latest()
        if record is None:
            return None, [], []

        goal = models.Goal.from_dict(record["goal_state"])
        tasks = [
            models.Task.from_dict(task_state)
            for task_state in (record.get("task_state") or {}).values()
        ]

        evidence = []
        for item in goal.verification_requirements or []:
            test_id = str(item.get("id", "unknown"))
            raw_status = item.get("status", "NOT_RUN")
            try:
                status = models.EvidenceStatus(raw_status)
            except ValueError:
                status = models.EvidenceStatus.NOT_RUN
            evidence.append(
                models.Evidence(
                    test_id=test_id,
                    status=status,
                    goal_id=goal.goal_id,
                    project_id=goal.project_id,
                    provenance={"source": "checkpoint.goal.verification_requirements"},
                )
            )

        return goal, tasks, evidence
    except Exception:
        return None, [], []


def _demo_goal():
    """Demo goal for testing without a live project."""
    from .models import Evidence, EvidenceStatus, Goal, GoalStatus, Task, TaskStatus

    goal = Goal(
        project_id="local-demo",
        user_intent="Build a Todo app",
        title="Todo app",
        goal_id="demo-goal",
        status=GoalStatus.EXECUTING,
    )
    task = Task(
        goal_id="demo-goal",
        owner_agent_id="coder",
        task_id="demo-task",
        agent_type="coder",
        status=TaskStatus.RUNNING,
    )
    evidence = [
        Evidence(
            test_id="qa.preview",
            status=EvidenceStatus.PASS,
            goal_id="demo-goal",
            project_id="local-demo",
        )
    ]
    return goal, [task], evidence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="JaXir OS mission control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  jaxir-cli                      # Show live mission summary from checkpoint state
  jaxir-cli --traceability       # Show requirement traceability
  jaxir-cli --project /path      # Show mission for a specific project
  jaxir-cli --demo               # Show demo mission (fallback only)
        """,
    )
    parser.add_argument(
        "--traceability",
        action="store_true",
        help="Print the requirement traceability summary.",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Path to the project directory (default: ./todo_slice).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use demo goal instead of loading live state (fallback mode).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    if args.traceability:
        print(json.dumps(default_traceability().summary(), indent=2))
        return 0

    if args.demo:
        goal, tasks, evidence = _demo_goal()
        source = "demo"
    else:
        goal, tasks, evidence = _load_live_goal(args.project)
        source = "live" if goal is not None else "none"

    if goal is None:
        print("error: no live goal found and no --demo fallback requested", file=sys.stderr)
        print("hint: run with --demo or build a goal into ./todo_slice", file=sys.stderr)
        return 1

    control = MissionControl(goal=goal, tasks=tasks, evidence=evidence)

    if args.json:
        snapshot = control.snapshot().to_dict()
        snapshot["source"] = source
        print(json.dumps(snapshot, indent=2))
    else:
        print(control.render_text())
        print(f"\n[source: {source}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
