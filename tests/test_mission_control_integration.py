"""Integration tests for Mission Control against live checkpoint state.

This test suite validates that Mission Control can:
- load live goal/task state from generated checkpoints
- reconstruct evidence from verification requirements
- render operational dashboards from real execution state
- fallback gracefully when no checkpoint exists
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from jaxir import models, cli, mission_control
from jaxir.checkpoint import CheckpointManager
from jaxir.events import EventBus


@pytest.fixture
def temp_project():
    """Create a temporary project directory with a durable event log."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "test_project"
        project_path.mkdir(parents=True, exist_ok=True)
        yield project_path


def _create_sample_checkpoint(project_path: Path) -> dict:
    """Create a sample checkpoint with goal, tasks, and evidence."""
    bus = EventBus()
    cp_mgr = CheckpointManager(str(project_path), bus)

    goal = models.Goal(
        project_id="test",
        user_intent="Test goal",
        title="Test Goal",
        goal_id="test-goal-1",
        status=models.GoalStatus.EXECUTING,
    )

    tasks = [
        models.Task(
            goal_id="test-goal-1",
            owner_agent_id="planner",
            task_id="task-1",
            agent_type="planner",
            status=models.TaskStatus.COMPLETED,
        ),
        models.Task(
            goal_id="test-goal-1",
            owner_agent_id="coder",
            task_id="task-2",
            agent_type="coder",
            status=models.TaskStatus.RUNNING,
        ),
    ]

    agents = []
    context = {"project_dir": str(project_path)}
    qa_state = {}
    feedback = []
    artifacts = []
    env_meta = {}

    goal.verification_requirements = [
        {"id": "qa.preview", "status": "PASS"},
        {"id": "qa.functional", "status": "PASS"},
        {"id": "qa.security", "status": "PASS"},
    ]

    cp = cp_mgr.create(
        goal=goal,
        tasks=tasks,
        agents=agents,
        context=context,
        qa_state=qa_state,
        feedback=feedback,
        artifacts=artifacts,
        env_meta=env_meta,
    )

    return {
        "checkpoint_id": cp.checkpoint_id,
        "goal": goal,
        "tasks": tasks,
    }


def test_load_live_goal_from_checkpoint(temp_project):
    """Test that Mission Control can load live goal state from a checkpoint."""
    checkpoint_data = _create_sample_checkpoint(temp_project)

    goal, tasks, evidence = cli._load_live_goal(str(temp_project))

    assert goal is not None
    assert goal.goal_id == "test-goal-1"
    assert goal.status == models.GoalStatus.EXECUTING
    assert len(tasks) == 2
    assert tasks[0].status == models.TaskStatus.COMPLETED
    assert tasks[1].status == models.TaskStatus.RUNNING
    assert len(evidence) == 3
    assert all(e.status == models.EvidenceStatus.PASS for e in evidence)


def test_mission_control_snapshot_from_live_checkpoint(temp_project):
    """Test that Mission Control snapshot is accurate from live checkpoint."""
    _create_sample_checkpoint(temp_project)

    goal, tasks, evidence = cli._load_live_goal(str(temp_project))
    control = mission_control.MissionControl(goal=goal, tasks=tasks, evidence=evidence)
    snapshot = control.snapshot()

    assert snapshot.goal_id == "test-goal-1"
    assert snapshot.status == "EXECUTING"
    assert snapshot.tasks_total == 2
    assert snapshot.tasks_completed == 1
    assert snapshot.evidence_passed == 3
    assert snapshot.progress_percent == 50.0
    assert snapshot.active_agent == "coder"


def test_mission_control_render_from_live_checkpoint(temp_project):
    """Test that the human-readable rendering works from live checkpoint."""
    _create_sample_checkpoint(temp_project)

    goal, tasks, evidence = cli._load_live_goal(str(temp_project))
    control = mission_control.MissionControl(goal=goal, tasks=tasks, evidence=evidence)
    text = control.render_text()

    assert "JaXir Mission Control" in text
    assert "test-goal-1" in text
    assert "EXECUTING" in text
    assert "50.0%" in text
    assert "1/2" in text
    assert "coder" in text


def test_mission_control_json_output_from_live_checkpoint(temp_project):
    """Test that JSON output format works from live checkpoint."""
    _create_sample_checkpoint(temp_project)

    goal, tasks, evidence = cli._load_live_goal(str(temp_project))
    control = mission_control.MissionControl(goal=goal, tasks=tasks, evidence=evidence)
    snapshot = control.snapshot()
    json_output = snapshot.to_json()

    data = json.loads(json_output)
    assert data["goal_id"] == "test-goal-1"
    assert data["status"] == "EXECUTING"
    assert data["progress_percent"] == 50.0
    assert data["evidence_passed"] == 3


def test_cli_load_live_goal_no_checkpoint(temp_project):
    """Test that CLI gracefully handles missing checkpoints."""
    goal, tasks, evidence = cli._load_live_goal(str(temp_project))

    assert goal is None
    assert tasks == []
    assert evidence == []


def test_cli_main_with_live_checkpoint(temp_project, capsys):
    """Test that CLI outputs live mission summary when checkpoint exists."""
    _create_sample_checkpoint(temp_project)

    result = cli.main(["--project", str(temp_project)])

    assert result == 0
    captured = capsys.readouterr()
    assert "JaXir Mission Control" in captured.out
    assert "test-goal-1" in captured.out
    assert "[source: live]" in captured.out


def test_cli_main_json_output(temp_project, capsys):
    """Test that CLI outputs JSON when --json flag is used."""
    _create_sample_checkpoint(temp_project)

    result = cli.main(["--project", str(temp_project), "--json"])

    assert result == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["goal_id"] == "test-goal-1"
    assert data["source"] == "live"


def test_cli_main_traceability_output(capsys):
    """Test that --traceability flag outputs requirement registry."""
    result = cli.main(["--traceability"])

    assert result == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "requirements" in data
    assert "RFC-001" in data["requirements"]
    assert "acceptance_targets" in data
    assert "release_gates" in data
