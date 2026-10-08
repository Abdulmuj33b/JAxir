"""JaXir's first vertical slice: the Todo application.

End-to-end closed loop:

    Natural language goal -> Goal spec -> Plan -> Task graph -> Code ->
    Sandbox -> Build -> Preview -> QA -> Evidence -> Feedback -> Verify -> Done

Todos: add, complete, delete. Also builds a responsive web app for web preview.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from . import models, goalmode, orchestrator, sandbox, preview, qa, checkpoint, registry


class TodoPlanner:
    """Turns the Todo goal into a dependency-aware task graph."""

    @staticmethod
    def plan(goal: models.Goal, project_dir: Path) -> List[models.Task]:
        tasks = [
            models.Task(
                goal_id=goal.goal_id,
                owner_agent_id="planner",
                agent_type="planner",
                capabilities=["planning", "decomposition"],
                status=models.TaskStatus.PENDING,
                inputs={"project_dir": str(project_dir), "goal": goal.to_dict()},
                outputs={"todo_cli": "todo"},
                acceptance_criteria=[
                    {"id": "C1", "metric": "end_to_end_success", "threshold": True,
                     "measurement_method": "run_todo_add_complete_delete"},
                ],
                verification=None,
                retry_policy={},
            ),
            models.Task(
                goal_id=goal.goal_id,
                owner_agent_id="planner",
                agent_type="planner",
                capabilities=["task_graph", "dependency_schedule"],
                status=models.TaskStatus.PENDING,
                inputs={"project_dir": str(project_dir), "depends": ["planner"]},
                outputs={"task_graph": "planned"},
                acceptance_criteria=[],
                verification=None,
                retry_policy={},
            ),
            models.Task(
                goal_id=goal.goal_id,
                owner_agent_id="planner",
                agent_type="coder",
                capabilities=["coding", "build", "sandbox_exec"],
                status=models.TaskStatus.PENDING,
                inputs={"project_dir": str(project_dir), "todo_cli": True},
                outputs={"todo_cli_source": "todo", "todo_bin": "todo"},
                acceptance_criteria=[
                    {"id": "C0", "metric": "todo_cli_written", "threshold": True,
                     "measurement_method": "file_exists_and_executable"},
                ],
                verification={"suite": "build.verify"},
                retry_policy={"max_attempts": 2, "strategy": "replan"},
            ),
        ]
        return tasks


class TodoCoder:
    """Coder agent that writes the todo CLI application into the sandbox."""

    TODO_CLI = r'''#!/usr/bin/env python3
# JaXir-built Todo application (single-file CLI).

import json, os, sys, argparse

TODO_STORE = os.environ.get("TODO_STORE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "todos.json"))
STORE = TODO_STORE

def load():
    if os.path.exists(STORE):
        with open(STORE) as f:
            return json.load(f)
    return []

def save(todos):
    with open(STORE, "w") as f:
        json.dump(todos, f, indent=2)

def cmd_add(args):
    todos = load()
    todos.append({"id": len(todos) + 1, "text": args.text, "done": False})
    save(todos)
    print(f"added: {args.text}")

def cmd_list(args):
    todos = load()
    if not todos:
        print("(no todos)")
        return
    for t in todos:
        mark = "x" if t["done"] else " "
        print(f"{mark} {t['id']}: {t['text']}")

def cmd_complete(args):
    todos = load()
    for t in todos:
        if t["id"] == args.id:
            t["done"] = True
            save(todos)
            print(f"completed: {args.id}")
            return
    print(f"no todo {args.id}", file=sys.stderr)
    sys.exit(1)

def cmd_delete(args):
    todos = load()
    kept = [t for t in todos if t["id"] != args.id]
    if len(kept) == len(todos):
        print(f"no todo {args.id}", file=sys.stderr)
        sys.exit(1)
    save(kept)
    print(f"deleted: {args.id}")
'''

    @staticmethod
    def write(project_dir: Path) -> Path:
        """Write the todo CLI into the sandbox workdir."""
        project_dir.mkdir(parents=True, exist_ok=True)
        cli_path = project_dir / "todo"
        cli_path.write_text(TodoCoder.TODO_CLI, encoding="utf-8")
        os.chmod(cli_path, 0o755)
        return cli_path


class TodoApp:
    """Runs the full end-to-end Todo vertical slice through JaXir's kernel.

    First vertical slice: a Todo application (CLI + web app) executed through
    the complete JaXir kernel lifecycle with evidence-backed completion.
    """

    def __init__(self, root: str = "/home/technologists-tec/.cline/data/workspaces/chat/jaxir-os"):
        self.root = Path(root)
        self.project_dir = self.root / "todo_slice"
        self._goal: Optional[models.Goal] = None
        self.bus = None

    def build(self) -> Dict[str, Any]:
        """Assemble the kernel and run the Todo slice end-to-end."""
        from .events import EventBus
        from .state import GoalStateMachine
        from .taskgraph import TaskGraph
        from .orchestrator import Orchestrator
        from .checkpoint import CheckpointManager
        from .registry import Registry
        from .observability import Observability
        from .omniroute import OmniRoute, RoutingPolicy
        from .sandbox import Sandbox, SandboxConfig, NetworkMode
        from .preview import PreviewEngine
        from .qa import QAEngine
        from .feedback import FeedbackEngine
        from . import spec

        self.bus = EventBus()
        self.checkpoint_mgr = CheckpointManager(str(self.project_dir), self.bus)
        self.registry = Registry(str(self.project_dir), self.bus)
        self.sb = Sandbox(
            SandboxConfig(project_root=str(self.project_dir), workdir=str(self.project_dir)),
            self.bus,
        )
        self.og = Orchestrator(self.bus, self.sb)
        self.pm = GoalStateMachine(self.bus)
        self.tg = TaskGraph(self.bus)
        self.ob = Observability(self.bus, None)
        self.omni = OmniRoute(RoutingPolicy(), self.bus)
        self.prv = PreviewEngine(self.sb, self.bus)
        self.qae = QAEngine(self.bus, self.registry, None)
        self.fb = FeedbackEngine(self.bus)

        # Register replaceable agents (planner + coder).
        planner = orchestrator.AgentModel(
            agent_id="planner", agent_type="planner", identity="JaXir Planner",
            capabilities=["planning", "decomposition", "task_graph"],
            tools=["spec", "taskgraph", "planning"],
            environment={"project_type": "todo"},
        )
        coder = orchestrator.AgentModel(
            agent_id="coder", agent_type="coder", identity="JaXir Coder",
            capabilities=["coding", "build", "sandbox_exec"], tools=["write", "run"],
            environment={"project_type": "todo"},
        )
        self.og.register(planner)
        self.og.register(coder)

        # Step 1: create -> analyze -> plan
        self._goal = models.Goal(
            project_id="todo",
            user_intent="Build a production-ready Todo application",
            title="Todo application",
            project_type="terminal",
        )
        self.goalmode = goalmode.GoalMode(self.bus, self.omni, self.tg, self.og,
                                          self.sb, self.checkpoint_mgr)
        spec1 = spec.GoalSpec.from_text(
            "todo", "Build a production-ready Todo application with add, complete, and delete.",
            self._goal,
        )
        self.goalmode.create_goal("todo", "Build a production-ready Todo application",
                                  "Todo application", spec1)
        self.pm.transition(self._goal, models.GoalStatus.ANALYZING)
        self.pm.transition(self._goal, models.GoalStatus.PLANNED)

        # Decompose into tasks.
        self._goal.task_graph = TodoPlanner.plan(self._goal, self.project_dir)
        self._goal.status = models.GoalStatus.EXECUTING

        # Step 2: execute task graph via orchestrator (planner + coder)
        self.goalmode.execute(self._goal, self.project_dir)

        # Step 2b: generate the web app for web preview
        self._generate_web_app()

        # Step 3: preview (web adapter)
        self._preview()

        # Step 4: QA - functional tests -> evidence
        qa_evidence = self._qa()

        # Step 5: feedback - evidence-backed, feeds replanning
        self._feedback(qa_evidence)

        # Step 6: verify (Definition of Done)
        self._verify(qa_evidence)

        return self._goal

    def _generate_web_app(self) -> None:
        """Generate the web app into the sandbox workdir for preview."""
        from jaxir.todoslice import WebTodoApp
        cli = WebTodoApp.write(self.project_dir)
        self._goal.task_graph[-1].result = {
            "ok": True,
            "file": str(cli),
            "summary": "web app generated",
            "preview_type": "web",
        }
        self.registry.register(
            registry.Artifact(name="todo_web_app", kind="web_page",
                              path=str(cli),
                              linked_goal_id=self._goal.goal_id)
        )

    def _preview(self) -> None:
        """Preview the todo app (web adapter)."""
        self._generate_web_app()
        status = self.prv.start(
            "web", 8137, self._goal.goal_id, self._goal.project_id,
            {"workdir": str(self.project_dir)},
        )
        for t in self._goal.task_graph:
            if t.status == models.TaskStatus.COMPLETED and t.result:
                t.result = {**t.result, "preview": status.to_dict()}
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.PREVIEW_STARTED,
                    project_id=self._goal.project_id,
                    goal_id=self._goal.goal_id,
                    payload={"preview_type": "web", "url": status.url},
                )
            )
        self.prv.capture("web")
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.PREVIEW_UPDATED,
                    project_id=self._goal.project_id,
                    goal_id=self._goal.goal_id,
                    payload={"preview_type": "web"},
                )
            )


    def _qa(self) -> List[models.Evidence]:
        """Functional + regression + security + performance evidence."""
        from .qa import QAEngine
        ev_ids = []
        ev = self.qae.test_todo(str(self.project_dir), expected_todos=1,
                                 expected_completed=1)
        ev_ids.append(ev.evidence_id)
        reg = self.qae.run_regression(str(self.project_dir))
        ev_ids.append(reg.evidence_id)
        sec = self.qae.run_security()
        ev_ids.append(sec.evidence_id)
        perf = self.qae.run_performance(str(self.project_dir))
        ev_ids.append(perf.evidence_id)
        verdict = self.qae.verdict([ev, reg, sec, perf])
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=(models.EventType.TEST_PASSED
                                if verdict.status == models.EvidenceStatus.PASS
                                else models.EventType.TEST_FAILED),
                    project_id=self._goal.project_id,
                    goal_id=self._goal.goal_id,
                    payload={"test_id": ev.test_id, "ok": verdict.status == models.EvidenceStatus.PASS},
                )
            )
        return [ev, reg, sec, perf, verdict]

        def _feedback(self, evidence: List[models.Evidence]) -> None:
        """Feedback from failures -> clustering -> corrective tasks."""
        failed = [e for e in evidence if e.status == models.EvidenceStatus.FAIL]
        if failed:
            for e in failed:
                self.fb.create(self._goal.project_id, self._goal.goal_id, None,
                               e.test_id, ["todo_app_failure"], root_cause="todo app bug")
            clusters = self.fb.get_clusters(self.fb.dedupe(self.fb.feedback))
            self._goal.state["feedback_clusters"] = clusters

    def _verify(self, evidence: List[models.Evidence]) -> None:
        """Verify Definition of Done; completing evidence-backed."""
        passed = all(e.status == models.EvidenceStatus.PASS for e in evidence)
        self._goal.verification_requirements = [
            {"id": v.test_id, "status": v.status.value}
            for v in evidence
        ]
        if passed:
            self._goal.status = models.GoalStatus.COMPLETED
            if self.bus is not None:
                self.bus.publish(
                    models.Event(event_type=models.EventType.GOAL_COMPLETED,
                                 project_id=self._goal.project_id,
                                 goal_id=self._goal.goal_id,
                                 payload={"dod": "all_evidence_passed"}))
        else:
            self._goal.status = models.GoalStatus.FAILED

    def _generate_web_app(self) -> None:
        """Generate the web app into the sandbox workdir for preview."""
        from jaxir.todoslice import WebTodoApp
        cli = WebTodoApp.write(self.project_dir)
        self._goal.task_graph[-1].result = {
            "ok": True,
            "file": str(cli),
            "summary": "web app generated",
            "preview_type": "web",
        }
        self.registry.register(
            registry.Artifact(name="todo_web_app", kind="web_page",
                              path=str(cli),
                              linked_goal_id=self._goal.goal_id)
        )

    def _preview(self) -> None:
        """Preview the todo app (web adapter)."""
        self._generate_web_app()
        status = self.prv.start(
            "web", 8137, self._goal.goal_id, self._goal.project_id,
            {"workdir": str(self.project_dir)},
        )
        for t in self._goal.task_graph:
            if t.status == models.TaskStatus.COMPLETED and t.result:
                t.result = {**t.result, "preview": status.to_dict()}
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.PREVIEW_STARTED,
                    project_id=self._goal.project_id,
                    goal_id=self._goal.goal_id,
                    payload={"preview_type": "web", "url": status.url},
                )
            )
        self.prv.capture("web")
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.PREVIEW_UPDATED,
                    project_id=self._goal.project_id,
                    goal_id=self._goal.goal_id,
                    payload={"preview_type": "web"},
                )
            )
