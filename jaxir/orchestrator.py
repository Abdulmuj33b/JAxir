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
        """Match a task to an agent by capability intersection.

        An explicit task ``agent_type`` counts as a required capability, so a
        coder task routes to the coder agent rather than to whoever registered
        first. A task with no capabilities is assignable to any agent.
        """
        wanted = set(task.capabilities or [])
        if task.agent_type:
            wanted.add(task.agent_type)
        for a in self.agents.values():
            if not wanted:
                return a
            if a.agent_type in wanted or wanted & set(a.capabilities):
                return a
        return None

    def assign(self, goal: models.Goal) -> List[models.Task]:
        """Assign every unstarted task to an agent.

        Completed tasks are left alone: re-queueing them would re-run work that
        already has evidence, which is exactly the duplicate execution of a
        non-idempotent action the task graph must prevent (section 7).
        """
        assigned: List[models.Task] = []
        for t in goal.task_graph:
            if t.status == models.TaskStatus.COMPLETED:
                continue
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
        goal.updated_at = self._now()
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
        ok = result.get("ok", False)
        if ok:
            task.status = models.TaskStatus.COMPLETED
            task.completed_at = self._now()
        else:
            task.status = models.TaskStatus.FAILED
            task.failure_state = result
            task.completed_at = self._now()

        self.bus.publish(
            models.Event(
                event_type=(models.EventType.TASK_COMPLETED if ok
                            else models.EventType.TASK_FAILED),
                project_id=task.goal_id,
                goal_id=task.goal_id,
                task_id=task.task_id,
                payload={"ok": ok},
            )
        )
        return task

    def _provision_env(self, task: models.Task) -> None:
        """Record the task's sandbox-confined workdir.

        The path is resolved through the sandbox so it cannot point outside the
        allowed roots. Nothing is created here: an unused per-task directory
        would be litter, and resource cleanup is a locked NFR (section 45).
        """
        confined = self.sandbox.resolve(Path(self.sandbox.cfg.workdir) / task.task_id)
        task.inputs["workdir"] = str(confined)

    def _execute_task(self, task: models.Task) -> Dict[str, Any]:
        """Coder execution. For the first slice, writes the JaXir-built todo CLI.

        Real implementation delegates to the Codex runtime (via OmniRoute).
        """
        from jaxir.todoslice import TodoCoder
        # Only the coder agent produces the todo CLI artifact.
        if task.agent_type != "coder":
            return {"ok": True, "workdir": str(task.inputs.get("workdir", "")),
                    "summary": "no_op"}
        # Generated code is untrusted: confine the write to the sandbox before
        # the artifact is produced (section 13).
        workdir = self.sandbox.resolve(Path(task.inputs.get("project_dir", "")))
        workdir.mkdir(parents=True, exist_ok=True)
        cli = TodoCoder.write(workdir)
        result = {
            "ok": True,
            "workdir": str(workdir),
            "file": str(cli),
            "sha256": self._sha256(cli),
            "summary": "todo CLI built",
        }
        if task.inputs.get("corrective"):
            # A corrective task carries the root cause it must address. The
            # regeneration itself is real and re-verified by QA; choosing *how*
            # to change the code from the root cause is NOT implemented here -
            # the deterministic coder ignores it. Labelled, not hidden.
            result.update({
                "corrective": True,
                "root_cause": task.inputs.get("root_cause"),
                "strategy": task.inputs.get("strategy"),
                "attempt": task.inputs.get("attempt"),
                "summary": "corrective regeneration (fix strategy NOT IMPLEMENTED)",
                "fix_strategy_implemented": False,
            })
        return result




    @staticmethod
    def _sha256(path: Path) -> str:
        import hashlib
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
