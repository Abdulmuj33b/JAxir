"""Kernel unit tests for JaXir OS.

Validates the locked contracts (RFC-001 / RFC-002) and acceptance targets.
"""

import sys
import pytest

sys.path.insert(0, ".")
from jaxir import models, events, state, taskgraph, checkpoint, registry


def make_goal():
    return models.Goal(
        project_id="test_proj",
        user_intent="build todo app",
        title="Todo App",
        acceptance_criteria=[{"id": "C1", "metric": "success", "threshold": True}],
    )


class TestGoalStateMachine:
    def test_valid_transitions(self):
        bus = events.EventBus()
        sm = state.GoalStateMachine(bus)
        goal = make_goal()
        assert goal.status == models.GoalStatus.CREATED
        sm.transition(goal, models.GoalStatus.ANALYZING)
        assert goal.status == models.GoalStatus.ANALYZING
        sm.transition(goal, models.GoalStatus.PLANNED)
        assert goal.status == models.GoalStatus.PLANNED

    def test_invalid_transition_rejected(self):
        bus = events.EventBus()
        sm = state.GoalStateMachine(bus)
        goal = make_goal()
        with pytest.raises(ValueError):
            sm.transition(goal, models.GoalStatus.COMPLETED)

    def test_idempotent_noop(self):
        bus = events.EventBus()
        sm = state.GoalStateMachine(bus)
        goal = make_goal()
        sm.transition(goal, models.GoalStatus.ANALYZING)
        sm.transition(goal, models.GoalStatus.ANALYZING)
        assert goal.status == models.GoalStatus.ANALYZING


class TestTaskGraph:
    def test_topological_order(self):
        bus = events.EventBus()
        tg = taskgraph.TaskGraph(bus)
        # build tasks with explicit task_id so dependencies resolve
        a = models.Task(goal_id="p", owner_agent_id="p", task_id="a",
                        status=models.TaskStatus.PENDING)
        b = models.Task(goal_id="p", owner_agent_id="p", task_id="b",
                        status=models.TaskStatus.PENDING,
                        dependencies=["a"])
        order = tg.topological_order([a, b])
        assert len(order) == 2
        order_ids = [t.task_id for t in order]
        assert order_ids.index("a") < order_ids.index("b")

    def test_dependency_cycle_raises(self):
        bus = events.EventBus()
        tg = taskgraph.TaskGraph(bus)
        a = models.Task(goal_id="p", owner_agent_id="p", task_id="a",
                        status=models.TaskStatus.PENDING, dependencies=["b"])
        b = models.Task(goal_id="p", owner_agent_id="p", task_id="b",
                        status=models.TaskStatus.PENDING, dependencies=["a"])
        with pytest.raises(ValueError):
            tg.topological_order([a, b])


class TestEventBus:
    def test_event_versioning(self):
        bus = events.EventBus()
        e = models.Event(
            event_type=models.EventType.GOAL_CREATED,
            project_id="p", goal_id="g", payload={},
        )
        bus.publish(e)
        assert e.schema_version == events.EventBus.SCHEMA_VERSION

    def test_replay(self):
        bus = events.EventBus()
        bus.publish(
            models.Event(event_type=models.EventType.GOAL_COMPLETED,
                         project_id="p", goal_id="g", payload={})
        )
        replay = bus.replay()
        assert len(replay) == 1


class TestCheckpoint:
    def test_checkpoint_roundtrip(self):
        bus = events.EventBus()
        cp_mgr = checkpoint.CheckpointManager("/tmp", bus)
        goal = make_goal()
        goal.status = models.GoalStatus.COMPLETED
        cp = cp_mgr.create(
            goal=goal, tasks=[], agents=[], context={}, qa_state={},
            feedback=[], artifacts=[], env_meta={},
        )
        restored = cp_mgr.restore(cp.checkpoint_id, None)
        assert restored.goal_id == cp.goal_id

    def test_compare(self):
        bus = events.EventBus()
        cp_mgr = checkpoint.CheckpointManager("/tmp", bus)
        goal = make_goal()
        a = cp_mgr.create(goal, [], [], {}, {}, [], [], {})
        b = cp_mgr.create(
            models.Goal(project_id=goal.project_id, user_intent=goal.user_intent,
                        title=goal.title, status=models.GoalStatus.COMPLETED),
            [], [], {}, {}, [], [], {}
        )
        diff = cp_mgr.compare(a.checkpoint_id, b.checkpoint_id)
        assert diff["a"] == a.checkpoint_id
        assert diff["b"] == b.checkpoint_id

class TestTodoVerticalSlice:
    """The first end-to-end milestone: autonomous Todo execution with evidence.

    Locked acceptance targets exercised here:
    - AT-PREV-001 (preview passes)
    - AT-QA-001/002/003 (functional/regression/security/performance QA)
    - Evidence-backed completion (no evidence, no completion)
    """

    @pytest.fixture()
    def app(self, tmp_path):
        from jaxir.todoslice import TodoApp
        return TodoApp(str(tmp_path))

    def test_todo_goal_completes_with_evidence(self, app, tmp_path):
        goal = app.build()
        assert goal.status == models.GoalStatus.COMPLETED
        # every task executed
        assert all(t.status == models.TaskStatus.COMPLETED for t in goal.task_graph)
        # every mandatory criterion backed by evidence
        vulns = [v for v in goal.verification_requirements
                 if v.get("status") != "PASS"]
        assert not vulns

    def test_todo_cli_performs_add_complete_delete(self, app, tmp_path):
        # Build the app first (produces the todo CLI in the sandbox).
        app.build()
        import subprocess
        cli = tmp_path / "todo_slice" / "todo"
        assert cli.exists() and (cli.stat().st_mode & 0o111)
        r = subprocess.run([str(cli), "add", "Buy milk"], capture_output=True,
                           text=True, timeout=10, cwd=tmp_path / "todo_slice")
        assert r.returncode == 0
        r = subprocess.run([str(cli), "list"], capture_output=True,
                           text=True, timeout=10, cwd=tmp_path / "todo_slice")
        assert r.returncode == 0
        r = subprocess.run([str(cli), "complete", "1"], capture_output=True,
                           text=True, timeout=10, cwd=tmp_path / "todo_slice")
        assert r.returncode == 0
        r = subprocess.run([str(cli), "delete", "1"], capture_output=True,
                           text=True, timeout=10, cwd=tmp_path / "todo_slice")
        assert r.returncode == 0

    def test_evidence_recorded(self, app, tmp_path):
        # build() already records evidence in the registry
        goal = app.build()
        evs = app.registry.list_artifacts(goal.goal_id)
        assert len(evs) > 0
