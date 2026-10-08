from jaxir import mission_control, traceability
from jaxir.models import Evidence, EvidenceStatus, Goal, GoalStatus, Task, TaskStatus


def test_mission_control_snapshot_and_render():
    goal = Goal(
        project_id="proj-demo",
        user_intent="Build a Todo app",
        title="Todo app",
        goal_id="goal-1",
        status=GoalStatus.EXECUTING,
    )
    task = Task(
        goal_id="goal-1",
        owner_agent_id="coder",
        task_id="task-1",
        agent_type="coder",
        status=TaskStatus.RUNNING,
    )
    evidence = [
        Evidence(
            test_id="qa.preview",
            status=EvidenceStatus.PASS,
            goal_id="goal-1",
            project_id="proj-demo",
        )
    ]

    control = mission_control.MissionControl(goal=goal, tasks=[task], evidence=evidence)
    snapshot = control.snapshot()

    assert snapshot.goal_id == "goal-1"
    assert snapshot.status == "EXECUTING"
    assert snapshot.active_agent == "coder"
    assert snapshot.preview_status == "passed"
    assert "pause" in snapshot.human_actions
    assert "JaXir Mission Control" in control.render_text()


def test_traceability_registry_has_requirement_chain():
    trace = traceability.default_traceability()
    summary = trace.summary()

    assert "RFC-001" in summary["requirements"]
    assert "AT-PREV-001" in summary["acceptance_targets"]
    assert "Gate 0" in summary["release_gates"]
    assert trace.by_target("AT-PREV-001")[0]["test_suite"] == "tests/test_kernel.py"
