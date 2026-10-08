"""Simple CLI for JaXir OS.

This is intentionally lightweight: it exposes a human-readable mission summary
and the current requirement traceability state without adding framework weight.
"""

from __future__ import annotations

import argparse
import json

from .mission_control import MissionControl
from .traceability import default_traceability


def _demo_goal():
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
    parser = argparse.ArgumentParser(description="JaXir OS mission control")
    parser.add_argument(
        "--traceability",
        action="store_true",
        help="Print the requirement traceability summary.",
    )
    args = parser.parse_args(argv)

    if args.traceability:
        print(json.dumps(default_traceability().summary(), indent=2))
        return 0

    goal, tasks, evidence = _demo_goal()
    control = MissionControl(goal=goal, tasks=tasks, evidence=evidence)
    print(control.render_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
