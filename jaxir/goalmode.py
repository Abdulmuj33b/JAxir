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
        # The state machine is the single authority for goal status, so every
        # transition is validated and audited (locked target: 100%).
        if goal.status in (models.GoalStatus.PLANNED, models.GoalStatus.REPLANNING):
            self.state_m.transition(goal, models.GoalStatus.EXECUTING)
        self.orchestrator.assign(goal)
        # Commit an initial checkpoint before execution.
        self._checkpoint(goal, project_dir)
        for t in goal.task_graph:
            # Idempotent resume: completed work is never re-executed, so a
            # replanned goal runs only its new corrective work (section 7).
            if t.status == models.TaskStatus.COMPLETED:
                continue
            self.orchestrator.run_task(t, goal)
            self._checkpoint(goal, project_dir)
        self.state_m.transition(goal, models.GoalStatus.VERIFYING)
        return goal

    def complete(self, goal: models.Goal) -> models.Goal:
        """Record verification success, then completion."""
        if goal.status != models.GoalStatus.PASSED:
            self.state_m.transition(goal, models.GoalStatus.PASSED)
        self.state_m.transition(goal, models.GoalStatus.COMPLETED)
        return goal

    def fail(self, goal: models.Goal, reason: str) -> models.Goal:
        self.state_m.transition(goal, models.GoalStatus.FAILED)
        if self.bus is not None:
            self.bus.publish(
                models.Event(event_type=models.EventType.GOAL_FAILED,
                             project_id=goal.project_id, goal_id=goal.goal_id,
                             payload={"reason": reason})
            )
        return goal

    def block(self, goal: models.Goal, reason: str,
              detail: Optional[Dict[str, Any]] = None) -> models.Goal:
        """Escalate: stop, preserve evidence, request intervention (section 41)."""
        self.state_m.transition(goal, models.GoalStatus.BLOCKED)
        goal.state["escalation"] = {"reason": reason, **(detail or {})}
        if self.bus is not None:
            self.bus.publish(
                models.Event(event_type=models.EventType.GOAL_FAILED,
                             project_id=goal.project_id, goal_id=goal.goal_id,
                             payload={"escalated": True, "reason": reason,
                                      **(detail or {})})
            )
        return goal

    # ------------------------------------------------------------------
    # Replanning (sections 18 / 51)
    # ------------------------------------------------------------------

    def replan(self, goal: models.Goal, feedback_engine: Any,
               failures: List[Any], project_dir: Path,
               strategy: str = "retry", attempt: int = 1) -> models.Goal:
        """Revise the plan from failures: REPLANNING -> corrective tasks -> PLANNED.

        One corrective task per root cause (deduplication and clustering happen
        in the Feedback engine). The caller then executes the revised graph.
        """
        self.state_m.transition(goal, models.GoalStatus.REPLANNING)
        specs = feedback_engine.corrective_tasks(
            goal.goal_id, failures, project_dir, strategy=strategy,
            attempt=attempt,
        )

        created: List[models.Task] = []
        for spec in specs:
            task = self._materialize_corrective(goal, spec)
            goal.task_graph.append(task)
            created.append(task)
            # Close the loop: link each contributing failure to its correction.
            for f in feedback_engine.feedback:
                if f.feedback_id in spec["feedback_ids"]:
                    f.corrective_task_id = task.task_id

        goal.state.setdefault("replans", []).append({
            "attempt": attempt,
            "strategy": strategy,
            "failures": len(failures),
            "clusters": len(specs),
            "corrective_tasks": [
                {"task_id": t.task_id,
                 "root_cause": (t.inputs or {}).get("root_cause"),
                 "severity": (t.inputs or {}).get("severity")}
                for t in created
            ],
        })
        self.state_m.transition(goal, models.GoalStatus.PLANNED)
        return goal

    @staticmethod
    def _materialize_corrective(goal: models.Goal,
                                spec: Dict[str, Any]) -> models.Task:
        """Materialise a corrective spec into an executable task.

        Routed to the coder agent because the correction is an artifact change.
        """
        return models.Task(
            goal_id=goal.goal_id,
            owner_agent_id="planner",
            agent_type="coder",
            capabilities=["coding", "build", "sandbox_exec", "corrective"],
            status=models.TaskStatus.PENDING,
            inputs={
                "project_dir": spec["project_dir"],
                "corrective": True,
                "root_cause": spec["root_cause"],
                "severity": spec["severity"],
                "symptom_signature": spec["symptom_signature"],
                "feedback_ids": spec["feedback_ids"],
                "strategy": spec["strategy"],
                "attempt": spec["attempt"],
            },
            acceptance_criteria=[{
                "id": "C-CORR",
                "metric": "corrective_task_reverified",
                "threshold": True,
                "measurement_method": "reverify_after_correction",
            }],
            verification={"suite": "qa.reverify"},
            retry_policy={"max_attempts": 1, "strategy": spec["strategy"]},
        )

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
