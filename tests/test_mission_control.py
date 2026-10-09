from jaxir import mission_control, traceability
from jaxir.events import EventBus
from jaxir.models import Evidence, EvidenceStatus, Event, EventType, Goal, GoalStatus, Task, TaskStatus


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


def test_mission_control_event_summary_is_scoped_to_goal():
    bus = EventBus()
    bus.publish(Event(event_type=EventType.GOAL_CREATED, project_id="proj-a", goal_id="goal-1", payload={}))
    bus.publish(Event(event_type=EventType.GOAL_CREATED, project_id="proj-b", goal_id="goal-2", payload={}))
    bus.publish(Event(event_type=EventType.TASK_STARTED, project_id="proj-a", goal_id="goal-1", task_id="task-1", payload={}))

    control = mission_control.MissionControl.from_event_bus(bus, goal_id="goal-1")
    snapshot = control.snapshot()

    assert snapshot.goal_id == "goal-1"
    assert snapshot.project_id == "proj-a"
    assert snapshot.event_counts.get(EventType.GOAL_CREATED.value, 0) == 1
    assert snapshot.event_counts.get(EventType.TASK_STARTED.value, 0) == 1
    assert snapshot.last_event == EventType.TASK_STARTED.value


def test_traceability_registry_has_requirement_chain():
    trace = traceability.default_traceability()
    summary = trace.summary()

    assert "RFC-001" in summary["requirements"]
    assert "AT-PREV-001" in summary["acceptance_targets"]
    assert "Gate 0" in summary["release_gates"]
    assert trace.by_target("AT-PREV-001")[0]["test_suite"] == "tests/test_kernel.py"
