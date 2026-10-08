"""Agent Orchestrator.

Assigns tasks to agents, routes models through OmniRoute, provisions
environments, approves permissions, and records evidence. Agents are
replaceable components implementing the AgentContract contract — no agent is
hard-coded into the kernel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import models


@dataclass
class AgentModel:
    agent_id: str
    agent_type: str
    identity: str
    capabilities: List[str]
    tools: List[str]
    environment: Dict[str, Any] = field(default_factory=dict)
    permissions: List[Dict[str, Any]] = field(default_factory=list)
    model_runtime: Optional[str] = None
    status: models.TaskStatus = models.TaskStatus.PENDING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "identity": self.identity,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "environment": self.environment,
            "permissions": self.permissions,
            "model_runtime": self.model_runtime,
            "status": self.status.value,
        }


class Orchestrator:
    def __init__(self, event_bus: Any, sandbox: Any):
        self.bus = event_bus
        self.sandbox = sandbox
        self.agents: Dict[str, AgentModel] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register(self, agent: AgentModel) -> None:
        self.agents[agent.agent_id] = agent
        self.bus.publish(
            models.Event(
                event_type=models.EventType.AGENT_STARTED,
                project_id=agent.environment.get("project_id", ""),
                agent_id=agent.agent_id,
                payload={"identity": agent.identity, "capabilities": agent.capabilities},
            )
        )

    def agent_for(self, task: models.Task) -> Optional[AgentModel]:
        for aid, a in self.agents.items():
            if a.agent_type in task.capabilities or not task.capabilities:
                return a
        return None

    def assign(self, goal: models.Goal) -> List[models.Task]:
        """Assign every task to an agent; create per-task evidence slots."""
        assigned: List[models.Task] = []
        for t in goal.task_graph:
            agent = self.agent_for(t)
            if agent is None:
                continue
            t.agent_id = agent.agent_id
            t.agent_type = agent.agent_type
            t.status = models.TaskStatus.QUEUED
            assigned.append(t)
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.TASK_ASSIGNED,
                    project_id=goal.project_id,
                    goal_id=goal.goal_id,
                    task_id=t.task_id,
                    payload={"agent_id": agent.agent_id, "agent_type": agent.agent_type},
                )
            )
        goal.updated_at = models.ModelBase.id_of(self._now())
        return assigned

    @staticmethod
    def _now():
        import datetime as _dt
        return _dt.datetime.now(_dt.timezone.utc)

    def run_task(self, task: models.Task) -> models.Task:
        """Execute a task through the sandbox + coder agent."""
        # 1. engine switches to EXECUTING
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.TASK_STARTED,
                    project_id=task.goal_id,
                    goal_id=task.goal_id,
                    task_id=task.task_id,
                    payload={"agent_id": task.agent_id},
                )
            )
        task.status = models.TaskStatus.RUNNING
        task.started_at = self._now()
        task.updated_at = self._now()

        # 2. sandbox isolation + environment provisioning
        self._provision_env(task)

        # 3. coder executes (real: todo app). TODO: plug in real codex runtime
        result = self._execute_task(task)
        task.result = result

        # 4. mark complete / failed
        if result.get("ok", False):
            task.status = models.TaskStatus.COMPLETED
            task.completed_at = self._now()
        else:
            task.status = models.TaskStatus.FAILED
            task.failure_state = result
            task.completed_at = self._now()

        self.bus.publish(
            models.Event(
                event_type=models.EventType.TASK_COMPLETED,
                project_id=task.goal_id,
                goal_id=task.goal_id,
                task_id=task.task_id,
                payload={"ok": result.get("ok", False)},
            )
        )
        return task

    def _provision_env(self, task: models.Task) -> None:
        # In a real system the sandbox provisions a fresh dir + toolchain.
        # Here we give the task a working directory.
        task.inputs.setdefault("workdir", f"/tmp/jaxir/{task.task_id}")

    def _execute_task(self, task: models.Task) -> Dict[str, Any]:
        """Coder execution. For the first slice, writes the JaXir-built todo CLI.

        Real implementation delegates to the Codex runtime (via OmniRoute).
        """
        from jaxir.todoslice import TodoCoder
        # Only the coder agent produces the todo CLI artifact.
        if task.agent_type != "coder":
            return {"ok": True, "workdir": str(task.inputs.get("project_dir", "/tmp/jaxir")), "summary": "no_op"}
        workdir = str(task.inputs.get("project_dir", "/tmp/jaxir"))
        self.sandbox.prepare_workdir(task)
        cli = TodoCoder.write(Path(workdir))
        return {
            "ok": True,
            "workdir": workdir,
            "file": str(cli),
            "sha256": self._sha256(cli),
            "summary": "todo CLI built",
        }




    @staticmethod
    def _sha256(path: Path) -> str:
        import hashlib
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
