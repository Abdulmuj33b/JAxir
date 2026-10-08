"""Goal Mode.

The autonomous execution loop:

    Goal -> Plan -> Build -> Preview -> QA -> Feedback -> Replan -> Verify -> Done

The Planner decomposes the goal into a dependency-aware task graph. The
Orchestrator assigns tasks to agents routed by OmniRoute. The Sandbox
isolates generated code. The Preview/QA/Quality engines verify evidence.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import models, state, taskgraph, orchestrator


class GoalMode:
    def __init__(self, bus: Any, om: Any, tg: Any, og: Any,
                 sandbox: Any, checkpoint_mgr: Any):
        self.bus = bus
        self.omni = om
        self.tg = tg
        self.orchestrator = og
        self.sandbox = sandbox
        self.cpk = checkpoint_mgr
        self.state_m = state.GoalStateMachine(bus)
        self.project_root: Optional[Path] = None
        self.goal: Optional[models.Goal] = None

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def create_goal(self, project_id: str, user_intent: str,
                    title: str, spec: Any) -> models.Goal:
        goal = spec.to_goal()
        goal.project_id = project_id
        goal.user_intent = user_intent
        goal.title = title
        goal.status = models.GoalStatus.CREATED
        self.goal = goal
        self.bus.publish(
            models.Event(event_type=models.EventType.GOAL_CREATED,
                         project_id=project_id, goal_id=goal.goal_id,
                         payload={"title": title, "intent": user_intent})
        )
        return goal

    def analyze(self, goal: models.Goal) -> models.Goal:
        goal.status = models.GoalStatus.ANALYZING
        self.bus.publish(
            models.Event(event_type=models.EventType.GOAL_ANALYZED,
                         project_id=goal.project_id, goal_id=goal.goal_id,
                         payload={"analysis": "requirement_aligned"})
        )
        return goal

    def plan(self, goal: models.Goal) -> models.Goal:
        goal.status = models.GoalStatus.PLANNED
        tasks = taskgraph.TaskGraph.build(goal)
        res = self.tg.schedule(goal)
        goal.task_graph = res.ordered_tasks
        self._set_definition_of_done(goal)
        self.bus.publish(
            models.Event(event_type=models.EventType.GOAL_PLANNED,
                         project_id=goal.project_id, goal_id=goal.goal_id,
                         payload={"tasks": [t.task_id for t in res.ordered_tasks],
                                   "conflicts": [c.description for c in res.conflicts]})
        )
        return goal

    @staticmethod
    def _set_definition_of_done(goal: models.Goal) -> None:
        goal.definition_of_done = [
            "all mandatory requirements pass",
            "all mandatory acceptance criteria pass",
            "QA passes",
            "regression tests pass",
            "security tests pass",
            "preview verification passes",
            "performance targets pass",
            "critical defects = 0",
            "verification evidence exists",
            "provenance exists",
        ]

    def execute(self, goal: models.Goal, project_dir: Path) -> models.Goal:
        self.project_root = project_dir
        goal.status = models.GoalStatus.EXECUTING
        self.orchestrator.assign(goal)
        # Commit an initial checkpoint before execution.
        self._checkpoint(goal, project_dir)
        for t in goal.task_graph:
            t = self.orchestrator.run_task(t)
            self._checkpoint(goal, project_dir)
        goal.status = models.GoalStatus.VERIFYING
        return goal

    def complete(self, goal: models.Goal) -> models.Goal:
        goal.status = models.GoalStatus.COMPLETED
        self.bus.publish(
            models.Event(event_type=models.EventType.GOAL_COMPLETED,
                         project_id=goal.project_id, goal_id=goal.goal_id)
        )
        return goal

    def fail(self, goal: models.Goal, reason: str) -> models.Goal:
        goal.status = models.GoalStatus.FAILED
        self.bus.publish(
            models.Event(event_type=models.EventType.GOAL_FAILED,
                         project_id=goal.project_id, goal_id=goal.goal_id,
                         payload={"reason": reason})
        )
        return goal

    def replan(self, goal: models.Goal) -> models.Goal:
        goal.status = models.GoalStatus.REPLANNING
        self.bus.publish(
            models.Event(event_type=models.EventType.GOAL_COMPLETED,
                         project_id=goal.project_id, goal_id=goal.goal_id,
                         payload={"action": "replan"})
        )
        return goal

    def _checkpoint(self, goal: models.Goal, project_dir: Path) -> None:
        if self.cpk is None:
            return
        self.cpk.create(
            goal=goal,
            tasks=list(goal.task_graph),
            agents=list(self.orchestrator.agents.values()),
            context={"project_root": str(project_dir)},
            qa_state={},
            feedback=[],
            artifacts=[],
            env_meta={"project_dir": str(project_dir)},
        )
