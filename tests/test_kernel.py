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
        workdir = tmp_path / "todo_slice"

        def run(*args):
            return subprocess.run([str(cli), *args], capture_output=True,
                                  text=True, timeout=10, cwd=workdir)

        # add: the item must be reported and persisted
        r = run("add", "Buy milk")
        assert r.returncode == 0 and "Buy milk" in r.stdout
        # list: the item must be visible
        r = run("list")
        assert r.returncode == 0 and "Buy milk" in r.stdout
        # complete: marked done
        r = run("complete", "1")
        assert r.returncode == 0
        assert "x 1: Buy milk" in run("list").stdout
        # delete: gone from the store
        r = run("delete", "1")
        assert r.returncode == 0
        assert "no todos" in run("list").stdout
        # operating on a missing id is a real failure, not a silent no-op
        assert run("complete", "99").returncode != 0

    def test_noop_cli_cannot_produce_pass_evidence(self, tmp_path):
        """A CLI that exits 0 without doing anything must FAIL QA.

        Guards the evidence contract: exit codes alone are not evidence, so a
        silent no-op can never be recorded as acceptance evidence.
        """
        from jaxir.qa import QAEngine
        from jaxir.todoslice import TodoCoder

        workdir = tmp_path / "todo_slice"
        TodoCoder.write(workdir)
        noop = workdir / "todo"
        noop.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n",
                        encoding="utf-8")
        noop.chmod(0o755)

        engine = QAEngine(events.EventBus(), None, None)
        ev = engine.test_todo(str(workdir), expected_todos=1,
                              expected_completed=1)
        assert ev.status == models.EvidenceStatus.FAIL
        assert ev.actual["all_passed"] is False
        assert any(not c["ok"] for c in ev.actual["checks"])

    def test_evidence_recorded(self, app, tmp_path):
        # build() already records evidence in the registry
        goal = app.build()
        evs = app.registry.list_artifacts(goal.goal_id)
        assert len(evs) > 0

    def test_preview_is_verified_not_just_built(self, app):
        """AT-PREV-001: preview evidence must be served-response based."""
        goal = app.build()
        by_id = {v["id"]: v["status"] for v in goal.verification_requirements}
        assert by_id.get("qa.preview") == "PASS"
        assert by_id.get("qa.todo.functional") == "PASS"

    def test_preview_server_is_released_after_build(self, app):
        """Resource cleanup: the preview server must not outlive the build."""
        app.build()
        assert app.prv.status("web").running is False


class TestWebPreviewAdapter:
    """The web adapter must really serve the artifact over loopback."""

    @pytest.fixture()
    def served(self, tmp_path):
        from jaxir.preview import PreviewEngine
        from jaxir.sandbox import Sandbox, SandboxConfig

        workdir = tmp_path / "site"
        workdir.mkdir()
        (workdir / "index.html").write_text(
            "<!doctype html><html><head><title>JaXir Todo</title></head>"
            "<body><form id='new-form'><input type='text'><button>Add</button>"
            "</form><ul id='list'></ul></body></html>",
            encoding="utf-8",
        )
        sandbox = Sandbox(SandboxConfig(project_root=str(tmp_path),
                                        workdir=str(workdir)),
                          events.EventBus())
        engine = PreviewEngine(sandbox, events.EventBus())
        status = engine.start("web", 0, "g1", "p1", {"workdir": str(workdir)})
        yield engine, status
        engine.stop("web")

    def test_serves_the_artifact_over_http(self, served):
        import urllib.request
        engine, status = served
        assert status.running, status.error
        with urllib.request.urlopen(status.url + "/index.html", timeout=5) as r:
            assert r.getcode() == 200
            assert "JaXir Todo" in r.read().decode("utf-8")

    def test_capture_records_served_dom(self, served):
        engine, status = served
        cap = engine.capture("web")
        assert cap["http_status"] == 200
        dom = cap["dom_snapshot"]
        assert dom["title"] == "JaXir Todo"
        assert "new-form" in dom["ids"] and "list" in dom["ids"]
        assert dom["interactive"] is True

    def test_inspect_is_stdlib_only(self, served):
        engine, _ = served
        dom = engine.inspect("web")
        assert dom["title"] == "JaXir Todo"
        assert "form" in dom["tags_found"]

    def test_stop_releases_the_port(self, served):
        engine, status = served
        engine.stop("web")
        assert engine.status("web").running is False
        import urllib.error
        import urllib.request
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(status.url + "/index.html", timeout=2)

    def test_failed_preview_emits_preview_failed(self, tmp_path):
        from jaxir.preview import PreviewEngine
        from jaxir.sandbox import Sandbox, SandboxConfig

        bus = events.EventBus()
        sandbox = Sandbox(SandboxConfig(project_root=str(tmp_path),
                                        workdir=str(tmp_path / "missing")),
                          bus)
        engine = PreviewEngine(sandbox, bus)
        # An impossible port makes start() fail; that must be observable.
        status = engine.start("web", -1, "g1", "p1", {})
        assert status.running is False
        assert bus.count(models.EventType.PREVIEW_FAILED) == 1
