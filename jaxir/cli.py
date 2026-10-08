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


def _load_live_goal(project_dir: Optional[str] = None) -> tuple:
    """Load live goal state from the latest checkpoint.
    
    Returns: (goal, tasks, evidence) tuple from the most recent checkpoint,
    or (None, [], []) if no checkpoint exists.
    """
    from .checkpoint import CheckpointManager
    from .events import EventBus
    from .registry import Registry

    if not project_dir:
        project_dir = str(Path.cwd() / "todo_slice")
    
    try:
        project_path = Path(project_dir)
        if not project_path.exists():
            return None, [], []
        
        bus = EventBus()
        cp_mgr = CheckpointManager(str(project_path), bus)
        record = cp_mgr.latest()
        
        if not record:
            return None, [], []
        
        # Reconstruct goal and tasks from checkpoint
        from . import models
        
        goal = models.Goal.from_dict(record["goal_state"])
        tasks = [
            models.Task.from_dict(t)
            for t in (record.get("task_state") or {}).values()
        ]
        
        # Collect evidence from registry
        registry = Registry(str(project_path), bus)
        artifacts = registry.list_artifacts(goal.goal_id)
        evidence = [
            models.Evidence.from_dict(a) 
            for a in artifacts 
            if isinstance(a, dict)
        ]
        
        return goal, tasks, evidence
    except Exception as e:
        return None, [], []


def _demo_goal() -> tuple:
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
  jaxir-cli                      # Show live mission summary
  jaxir-cli --traceability       # Show requirement traceability
  jaxir-cli --project /path      # Show mission for a specific project
  jaxir-cli --demo               # Show demo mission (no live project required)
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
        help="Use demo goal instead of loading live state.",
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

    # Load goal state: live or demo
    if args.demo:
        goal, tasks, evidence = _demo_goal()
        source = "demo"
    else:
        goal, tasks, evidence = _load_live_goal(args.project)
        source = "live" if goal else "none"
    
    if goal is None and not args.demo:
        print("error: no live goal found and --demo not specified", file=sys.stderr)
        print(f"hint: pass --project /path or use --demo", file=sys.stderr)
        return 1

    control = MissionControl(goal=goal, tasks=tasks, evidence=evidence)
    
    if args.json:
        snapshot = control.snapshot()
        snapshot_dict = snapshot.to_dict()
        snapshot_dict["_source"] = source
        print(json.dumps(snapshot_dict, indent=2))
    else:
        print(control.render_text())
        print(f"\n[source: {source}]")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
