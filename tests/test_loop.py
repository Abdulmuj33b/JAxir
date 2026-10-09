"""Execution-loop tests: feedback -> corrective tasks -> replan, and the
autonomous-loop protection required by constitution sections 41 and 42.

The locked acceptance target is autonomous-loop detection within <=5 equivalent
failures; these tests assert the loop is bounded and that it never spins.
"""

import sys

import pytest

sys.path.insert(0, ".")

from jaxir import models
from jaxir.events import EventBus
from jaxir.feedback import FeedbackEngine
from jaxir.loopguard import (
    CONTINUE,
    CHANGE_STRATEGY,
    EQUIVALENT_FAILURE_LIMIT,
    ESCALATE,
    LoopGuard,
    failure_signature,
)
from jaxir.qa import QAEngine
from jaxir.spec import GoalSpec
from jaxir.todoslice import TodoApp


# ----------------------------------------------------------------------
# Failure equivalence
# ----------------------------------------------------------------------

class TestFailureSignature:
    def test_signature_is_order_insensitive(self):
        a = failure_signature("qa.failure", ["b", "a"])
        b = failure_signature("qa.failure", ["a", "b"])
        assert a == b

    def test_signature_ignores_duplicates_and_case(self):
        assert failure_signature("qa.failure", ["A", "a"]) == \
            failure_signature("qa.failure", ["a"])

    def test_different_events_are_not_equivalent(self):
        assert failure_signature("qa.regression", ["x"]) != \
            failure_signature("qa.security", ["x"])


# ----------------------------------------------------------------------
# Loop guard
# ----------------------------------------------------------------------

class TestLoopGuard:
    def test_first_failure_continues_with_retry(self):
        g = LoopGuard()
        g.begin_attempt()
        v = g.record_failure("sig")
        assert v.action == CONTINUE and v.strategy == "retry"

    def test_recurrence_changes_strategy(self):
        g = LoopGuard(max_attempts=10)
        g.begin_attempt()
        g.record_failure("sig")
        g.begin_attempt()
        v = g.record_failure("sig")
        assert v.action == CHANGE_STRATEGY
        assert v.strategy != "retry"

    def test_equivalent_failure_limit_triggers_escalation(self):
        """Locked target: detection within <=5 equivalent failures."""
        limit = EQUIVALENT_FAILURE_LIMIT
        g = LoopGuard(max_attempts=limit + 10, equivalent_failure_limit=limit)
        verdict = None
        for _ in range(limit):
            g.begin_attempt()
            verdict = g.record_failure("same")
        assert verdict.action == ESCALATE
        assert verdict.reason == "equivalent_failure_limit"
        assert verdict.equivalent_failures == limit
        assert verdict.equivalent_failures <= 5

    def test_attempt_budget_is_also_a_bound(self):
        g = LoopGuard(max_attempts=2, equivalent_failure_limit=99)
        g.begin_attempt()
        assert g.record_failure("a").should_continue
        g.begin_attempt()
        v = g.record_failure("b")  # different failure, but budget is spent
        assert v.action == ESCALATE
        assert v.reason == "attempt_budget_exhausted"

    def test_distinct_failures_do_not_share_a_counter(self):
        g = LoopGuard(max_attempts=10)
        g.begin_attempt()
        g.record_failure("a")
        g.begin_attempt()
        v = g.record_failure("b")
        assert v.equivalent_failures == 1
        assert v.action == CONTINUE

    def test_oscillating_plan_is_detected(self):
        g = LoopGuard()
        assert g.record_plan("plan-A") is False
        assert g.record_plan("plan-A") is True
        assert g.detect_oscillation() == "identical_replan"

    def test_changing_plan_is_not_oscillation(self):
        g = LoopGuard()
        g.record_plan("plan-A")
        assert g.record_plan("plan-B") is False

    def test_duplicate_tasks_detected(self):
        dupes = LoopGuard.detect_duplicate_tasks([
            {"agent_type": "coder", "inputs": {"project_dir": "/x"}},
            {"agent_type": "coder", "inputs": {"project_dir": "/x"}},
            {"agent_type": "coder", "inputs": {"project_dir": "/y"}},
        ])
        assert len(dupes) == 1

    def test_plan_signature_ignores_task_identity(self):
        a = [{"agent_type": "coder", "capabilities": ["b", "a"], "root_cause": "r"}]
        b = [{"agent_type": "coder", "capabilities": ["a", "b"], "root_cause": "r"}]
        assert LoopGuard.plan_signature(a) == LoopGuard.plan_signature(b)

    def test_summary_is_auditable(self):
        g = LoopGuard()
        g.begin_attempt()
        g.record_failure("sig")
        s = g.summary()
        assert s["attempts"] == 1
        assert s["failure_signatures"] == ["sig"]
        assert s["equivalent_failure_limit"] == EQUIVALENT_FAILURE_LIMIT

    @pytest.mark.parametrize("kwargs", [
        {"max_attempts": 0}, {"equivalent_failure_limit": 0},
    ])
    def test_invalid_bounds_rejected(self, kwargs):
        with pytest.raises(ValueError):
            LoopGuard(**kwargs)


# ----------------------------------------------------------------------
# Feedback -> corrective tasks (section 18)
# ----------------------------------------------------------------------

def _fb():
    return FeedbackEngine(EventBus())


class TestGoalSpecExtraction:
    def test_from_text_extracts_project_type_requirements_and_chains(self):
        goal = models.Goal(project_id="todo", user_intent="Build a secure web app with preview and verification", title="")

        spec = GoalSpec.from_text("proj-1", goal.user_intent, goal)

        assert spec.project_type == "web"
        assert spec.requirements
        assert "preview" in spec.required_capabilities
        assert "security" in spec.required_capabilities
        assert "verification" in spec.required_capabilities
        assert spec.permissions_required
        assert spec.chains
        assert spec.chains[0].requirement_id.startswith("REQ-")
        assert spec.chains[0].acceptance_target_id.startswith("AT-")


class TestTaskGraphScheduling:
    def test_same_agent_type_without_shared_resource_is_not_a_conflict(self):
        from jaxir.taskgraph import TaskGraph

        tasks = [
            models.Task(goal_id="g", owner_agent_id="coder-a", agent_type="coder",
                        inputs={"resource": "cache-a"}),
            models.Task(goal_id="g", owner_agent_id="coder-b", agent_type="coder",
                        inputs={"resource": "cache-b"}),
        ]

        conflicts = TaskGraph(EventBus()).detect_conflicts(tasks, "proj")
        assert conflicts == []


class TestCorrectiveTasks:
    def test_equivalent_failures_collapse_to_one_cluster(self):
        fb = _fb()
        for _ in range(10):
            fb.create("p", "g", None, "qa.regression",
                      ["qa.regression:FAIL"], root_cause="regression")
        fb.create("p", "g", None, "qa.security", ["qa.security:FAIL"],
                  root_cause="security")
        clusters = fb.prioritized_clusters(fb.feedback)
        assert len(clusters) == 2  # 11 symptoms, 2 root causes
        by_root = {c["root_cause"]: c for c in clusters}
        assert by_root["regression"]["member_count"] == 10

    def test_clusters_ranked_by_severity_then_size(self):
        fb = _fb()
        fb.create("p", "g", None, "qa.a", ["qa.a:FAIL"], severity="low",
                  root_cause="low")
        fb.create("p", "g", None, "qa.b", ["qa.b:FAIL"], severity="critical",
                  root_cause="critical")
        clusters = fb.prioritized_clusters(fb.feedback)
        assert clusters[0]["root_cause"] == "critical"
        assert clusters[0]["severity"] == "critical"

    def test_severity_is_the_worst_member(self):
        fb = _fb()
        fb.create("p", "g", None, "qa.a", ["qa.a:FAIL"], severity="medium",
                  root_cause="r")
        fb.create("p", "g", None, "qa.a", ["qa.a:FAIL"], severity="critical",
                  root_cause="r")
        assert fb.prioritized_clusters(fb.feedback)[0]["severity"] == "critical"

    def test_corrective_task_specs_carry_root_cause_and_evidence(self):
        fb = _fb()
        f = fb.create("p", "g", None, "qa.regression", ["qa.regression:FAIL"],
                      severity="high", root_cause="id_reuse")
        specs = fb.corrective_tasks("g", fb.feedback, "/proj",
                                    strategy="change_strategy", attempt=2)
        assert len(specs) == 1
        spec = specs[0]
        assert spec["root_cause"] == "id_reuse"
        assert spec["severity"] == "high"
        assert spec["strategy"] == "change_strategy"
        assert spec["attempt"] == 2
        assert f.feedback_id in spec["feedback_ids"]
        assert "id_reuse" in spec["description"]

    def test_feedback_created_event_carries_signature(self):
        bus = EventBus()
        fb = FeedbackEngine(bus)
        fb.create("p", "g", None, "qa.x", ["qa.x:FAIL"])
        payload = bus.get(event_type=models.EventType.FEEDBACK_CREATED)[0].payload
        assert payload["signature"] == failure_signature("qa.x", ["qa.x:FAIL"])

    def test_feedback_accept_event_preserves_project_goal_and_task_ids(self):
        bus = EventBus()
        fb = FeedbackEngine(bus)
        f = fb.create("proj-1", "goal-1", "task-1", "qa.x", ["qa.x:FAIL"])

        fb.accept(f.feedback_id)

        event = bus.get(event_type=models.EventType.FEEDBACK_ACCEPTED)[0]
        assert event.project_id == "proj-1"
        assert event.goal_id == "goal-1"
        assert event.task_id == "task-1"


# ----------------------------------------------------------------------
# Replanning
# ----------------------------------------------------------------------

class TestReplan:
    def _setup(self, tmp_path):
        from jaxir.goalmode import GoalMode
        from jaxir.omniroute import OmniRoute, RoutingPolicy
        from jaxir.orchestrator import AgentModel, Orchestrator
        from jaxir.sandbox import Sandbox, SandboxConfig
        from jaxir.taskgraph import TaskGraph

        bus = EventBus()
        workdir = tmp_path / "work"
        workdir.mkdir(exist_ok=True)
        sb = Sandbox(SandboxConfig(project_root=str(tmp_path),
                                   workdir=str(workdir)), bus)
        og = Orchestrator(bus, sb)
        og.register(AgentModel(agent_id="coder", agent_type="coder",
                               identity="c", capabilities=["coding"],
                               tools=[], environment={}))
        gm = GoalMode(bus, OmniRoute(RoutingPolicy(), bus), TaskGraph(bus), og,
                      sb, None)
        goal = models.Goal(project_id="todo", user_intent="x", title="t")
        gm.state_m.transition(goal, models.GoalStatus.ANALYZING)
        gm.state_m.transition(goal, models.GoalStatus.PLANNED)
        gm.state_m.transition(goal, models.GoalStatus.EXECUTING)
        gm.state_m.transition(goal, models.GoalStatus.VERIFYING)
        gm.state_m.transition(goal, models.GoalStatus.FAILED)
        return gm, goal, og, bus

    def test_replan_from_failed_appends_corrective_task(self, tmp_path):
        gm, goal, _, _ = self._setup(tmp_path)
        fb = _fb()
        fb.create("todo", goal.goal_id, None, "qa.regression",
                  ["qa.regression:FAIL"], severity="high",
                  root_cause="id_reuse")
        before = len(goal.task_graph)
        gm.replan(goal, fb, fb.feedback, tmp_path, strategy="retry", attempt=1)
        assert len(goal.task_graph) == before + 1
        task = goal.task_graph[-1]
        assert task.inputs["corrective"] is True
        assert task.inputs["root_cause"] == "id_reuse"
        assert task.agent_type == "coder"

    def test_replan_transitions_and_records(self, tmp_path):
        gm, goal, _, bus = self._setup(tmp_path)
        fb = _fb()
        fb.create("todo", goal.goal_id, None, "qa.x", ["qa.x:FAIL"],
                  root_cause="r")
        gm.replan(goal, fb, fb.feedback, tmp_path)
        # FAILED -> REPLANNING -> PLANNED, and PLANNED re-emits goal.planned.
        assert goal.status == models.GoalStatus.PLANNED
        assert [t["to"] for t in goal.state["transitions"]][-2:] == \
            ["REPLANNING", "PLANNED"]
        assert bus.count(models.EventType.GOAL_PLANNED) >= 1

    def test_replan_records_attempts_in_state(self, tmp_path):
        gm, goal, _, _ = self._setup(tmp_path)
        fb = _fb()
        fb.create("todo", goal.goal_id, None, "qa.x", ["qa.x:FAIL"],
                  root_cause="r")
        gm.replan(goal, fb, fb.feedback, tmp_path, strategy="change_agent",
                  attempt=2)
        record = goal.state["replans"][-1]
        assert record["attempt"] == 2 and record["strategy"] == "change_agent"
        assert record["clusters"] == 1

    def test_replan_links_feedback_to_corrective_task(self, tmp_path):
        gm, goal, _, _ = self._setup(tmp_path)
        fb = _fb()
        f = fb.create("todo", goal.goal_id, None, "qa.x", ["qa.x:FAIL"],
                      root_cause="r")
        gm.replan(goal, fb, fb.feedback, tmp_path)
        assert f.corrective_task_id == goal.task_graph[-1].task_id

    def test_replan_rejects_illegal_state(self, tmp_path):
        gm, goal, _, _ = self._setup(tmp_path)
        fresh = models.Goal(project_id="todo", user_intent="x", title="t")
        fb = _fb()
        fb.create("todo", fresh.goal_id, None, "qa.x", ["qa.x:FAIL"],
                  root_cause="r")
        # CREATED -> REPLANNING is not a legal transition and must be rejected.
        with pytest.raises(ValueError):
            gm.replan(fresh, fb, fb.feedback, tmp_path)


# ----------------------------------------------------------------------
# The slice's execution loop
# ----------------------------------------------------------------------

class TestExecutionLoop:
    def test_healthy_goal_completes_on_first_attempt(self, tmp_path):
        goal = TodoApp(str(tmp_path)).build()
        assert goal.status == models.GoalStatus.COMPLETED
        assert goal.state["loop_outcome"]["attempts"] == 1
        assert "replans" not in goal.state  # nothing to correct

    def test_goal_recovers_after_a_failed_attempt(self, tmp_path):
        """Build -> QA fails -> Feedback -> Replan -> QA passes -> COMPLETED."""
        original = QAEngine.run_regression
        calls = {"n": 0}

        def flaky(self, project_dir):
            calls["n"] += 1
            ev = original(self, project_dir)
            if calls["n"] == 1:
                ev.status = models.EvidenceStatus.FAIL
            return ev

        QAEngine.run_regression = flaky
        try:
            goal = TodoApp(str(tmp_path)).build()
        finally:
            QAEngine.run_regression = original

        assert goal.status == models.GoalStatus.COMPLETED
        assert goal.state["loop_outcome"]["attempts"] == 2
        # The failure produced a corrective task, and the correction is linked.
        assert len(goal.state["replans"]) == 1
        assert goal.state["replans"][0]["clusters"] == 1
        assert goal.state["dod_history"][0]["all_evidence_passed"] is False
        corrective = [t for t in goal.task_graph if t.inputs.get("corrective")]
        assert len(corrective) == 1
        assert all(t.status == models.TaskStatus.COMPLETED
                   for t in goal.task_graph)

    def test_aggregate_verdict_does_not_spawn_a_duplicate_correction(self, tmp_path):
        """One root cause, one corrective task (section 18).

        A failing criterion also fails the aggregate qa.verdict; the aggregate
        must not raise a second corrective task for the same root cause.
        """
        original = QAEngine.run_regression
        calls = {"n": 0}

        def flaky(self, project_dir):
            calls["n"] += 1
            ev = original(self, project_dir)
            if calls["n"] == 1:
                ev.status = models.EvidenceStatus.FAIL
            return ev

        QAEngine.run_regression = flaky
        try:
            app = TodoApp(str(tmp_path))
            goal = app.build()
        finally:
            QAEngine.run_regression = original

        # Only the failing criterion produced diagnostics; qa.verdict is derived.
        assert calls["n"] >= 1
        assert all(f.failure_event != "qa.verdict" for f in app.fb.feedback)
        # ...and the correction is unique.
        corrective = [t for t in goal.task_graph if t.inputs.get("corrective")]
        assert len(corrective) == 1

    def test_unrecoverable_goal_escalates_and_stops(self, tmp_path):
        original = QAEngine.run_security

        def always_fails(self, project_dir):
            ev = original(self, project_dir)
            ev.status = models.EvidenceStatus.FAIL
            return ev

        QAEngine.run_security = always_fails
        try:
            goal = TodoApp(str(tmp_path)).build()
        finally:
            QAEngine.run_security = original

        assert goal.status == models.GoalStatus.BLOCKED
        guard = goal.state["escalation"]["guard"]
        # Bounded: never spins, and detected within the locked <=5 limit.
        assert guard["attempts"] <= TodoApp.MAX_ATTEMPTS
        assert max(guard["equivalent_failure_counts"].values()) <= 5
        assert goal.state["escalation"]["reason"] in (
            "attempt_budget_exhausted", "equivalent_failure_limit")

    def test_escalation_preserves_evidence(self, tmp_path):
        original = QAEngine.run_security

        def always_fails(self, project_dir):
            ev = original(self, project_dir)
            ev.status = models.EvidenceStatus.FAIL
            return ev

        QAEngine.run_security = always_fails
        try:
            goal = TodoApp(str(tmp_path)).build()
        finally:
            QAEngine.run_security = original

        assert goal.state["dod_history"], "failed attempts must be recorded"
        assert goal.verification_requirements  # criteria still reported
        assert goal.state["escalation"]["guard"]["failure_signatures"]

    def test_completed_tasks_are_not_re_executed(self, tmp_path):
        """Idempotent resume: a replanned goal runs only its new work."""
        original = QAEngine.run_regression
        calls = {"n": 0}

        def flaky(self, project_dir):
            calls["n"] += 1
            ev = original(self, project_dir)
            if calls["n"] == 1:
                ev.status = models.EvidenceStatus.FAIL
            return ev

        QAEngine.run_regression = flaky
        try:
            app = TodoApp(str(tmp_path))
            goal = app.build()
        finally:
            QAEngine.run_regression = original

        started = {}
        for e in app.bus.get(event_type=models.EventType.TASK_STARTED):
            started[e.task_id] = started.get(e.task_id, 0) + 1
        assert started, "tasks must have executed"
        assert all(n == 1 for n in started.values()), started

    def test_assign_skips_completed_tasks(self, tmp_path):
        from jaxir.orchestrator import AgentModel, Orchestrator
        from jaxir.sandbox import Sandbox, SandboxConfig

        bus = EventBus()
        sb = Sandbox(SandboxConfig(project_root=str(tmp_path),
                                   workdir=str(tmp_path)), bus)
        og = Orchestrator(bus, sb)
        og.register(AgentModel(agent_id="coder", agent_type="coder",
                               identity="c", capabilities=["coding"],
                               tools=[], environment={}))
        done = models.Task(goal_id="g", owner_agent_id="p", agent_type="coder",
                           capabilities=["coding"],
                           status=models.TaskStatus.COMPLETED)
        todo = models.Task(goal_id="g", owner_agent_id="p", agent_type="coder",
                           capabilities=["coding"],
                           status=models.TaskStatus.PENDING)
        goal = models.Goal(project_id="p", user_intent="x", title="t")
        goal.task_graph = [done, todo]
        assigned = og.assign(goal)
        assert assigned == [todo]
        assert done.status == models.TaskStatus.COMPLETED


# ----------------------------------------------------------------------
# Auditable state transitions (locked: 100%)
# ----------------------------------------------------------------------

class TestAuditableTransitions:
    def test_every_transition_is_recorded_and_matches_final_state(self, tmp_path):
        goal = TodoApp(str(tmp_path)).build()
        trail = goal.state["transitions"]
        assert [t["to"] for t in trail] == [
            "ANALYZING", "PLANNED", "EXECUTING", "VERIFYING", "PASSED",
            "COMPLETED",
        ]
        assert trail[-1]["to"] == goal.status.value
        # Each entry chains onto the previous one: no silent jumps.
        chain = ["CREATED"] + [t["to"] for t in trail]
        for prev, nxt in zip(chain, [t["from"] for t in trail]):
            assert prev == nxt

    def test_no_transition_is_skipped_by_direct_assignment(self, tmp_path):
        """The slice must not assign goal.status outside the state machine."""
        import inspect
        from jaxir import todoslice
        src = inspect.getsource(todoslice)
        # The only status writes allowed are the state machine's own.
        assert "self._goal.status = models.GoalStatus" not in src

    def test_illegal_transition_rejected(self):
        from jaxir.state import GoalStateMachine
        sm = GoalStateMachine(EventBus())
        goal = models.Goal(project_id="p", user_intent="x", title="t")
        with pytest.raises(ValueError):
            sm.transition(goal, models.GoalStatus.COMPLETED)

    def test_completed_is_reachable_from_passed(self):
        from jaxir.state import GoalStateMachine
        sm = GoalStateMachine(EventBus())
        goal = models.Goal(project_id="p", user_intent="x", title="t")
        for status in (models.GoalStatus.ANALYZING, models.GoalStatus.PLANNED,
                       models.GoalStatus.EXECUTING, models.GoalStatus.VERIFYING,
                       models.GoalStatus.PASSED, models.GoalStatus.COMPLETED):
            sm.transition(goal, status)
        assert goal.status == models.GoalStatus.COMPLETED

    def test_transition_event_reports_true_previous_state(self):
        from jaxir.state import GoalStateMachine
        bus = EventBus()
        sm = GoalStateMachine(bus)
        goal = models.Goal(project_id="p", user_intent="x", title="t")
        sm.transition(goal, models.GoalStatus.ANALYZING)
        payload = bus.get(event_type=models.EventType.GOAL_ANALYZED)[0].payload
        assert payload == {"from": "CREATED", "to": "ANALYZING"}
