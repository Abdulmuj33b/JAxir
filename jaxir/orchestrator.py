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
    def __init__(self, event_bus: Any, sandbox: Any, router: Any = None,
                 context_compiler: Any = None):
        self.bus = event_bus
        self.sandbox = sandbox
        self.router = router
        self.compiler = context_compiler
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

    def run_task(self, task: models.Task,
                 goal: Optional[models.Goal] = None) -> models.Task:
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

        # 2. route: capability + complexity + quota. A refusal stops the task
        #    honestly instead of pretending a provider is available (section 11).
        route = self._route(task, goal)
        if route is not None and not route.get("available", True):
            task.status = models.TaskStatus.BLOCKED
            task.failure_state = {"stage": "routing", **route}
            task.completed_at = self._now()
            if self.bus is not None:
                self.bus.publish(
                    models.Event(event_type=models.EventType.TASK_FAILED,
                                 project_id=task.goal_id, goal_id=task.goal_id,
                                 task_id=task.task_id,
                                 payload={"ok": False, "reason": route.get("reason"),
                                          "provider": route.get("provider")})
                )
            return task

        # 3. compile the task's context (section 10) and record the reduction.
        task.inputs["context"] = self._compile_context(task, goal)

        # 4. sandbox isolation + environment provisioning
        self._provision_env(task)

        # 5. coder executes (real: todo app). TODO: plug in real codex runtime
        result = self._execute_task(task)
        task.result = result

        # 6. close out the model lifecycle for this task.
        self._record_model_outcome(task, route, result)

        # 7. mark complete / failed
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

    # ------------------------------------------------------------------
    # Routing / context (sections 9, 10, 11)
    # ------------------------------------------------------------------

    def _route(self, task: models.Task, goal: models.Goal) -> Optional[Dict[str, Any]]:
        """Ask the router for a provider; None when no router is configured."""
        if self.router is None:
            return None
        return self.router.select(task, goal)

    def _compile_context(self, task: models.Task,
                         goal: models.Goal) -> Dict[str, Any]:
        """Compile the task's context with the Context Compiler (section 10)."""
        if self.compiler is None:
            return {}
        bundle = self.compiler.compile(goal=goal, task=task,
                                       sources=self._context_sources(task, goal))
        return bundle.to_dict()

    def _context_sources(self, task: models.Task,
                         goal: models.Goal) -> Dict[str, Any]:
        """Sources the orchestrator can see.

        Project files are read through the sandbox (path-confined) so file
        selection is exercised for real; Memory/Registry feed the rest when a
        caller supplies them.
        """
        sources: Dict[str, Any] = {
            "requirements": list(getattr(goal, "requirements", []) or []),
            "acceptance_criteria": list(getattr(goal, "acceptance_criteria", []) or []),
        }
        inputs = task.inputs or {}
        if inputs.get("corrective"):
            # The root cause this corrective task must address is mandatory
            # context; the guard already collapsed equivalent symptoms.
            sources["failures"] = [{
                "failure_event": inputs.get("root_cause", ""),
                "symptoms": [str(inputs.get("symptom_signature", ""))],
                "severity": inputs.get("severity", "info"),
            }]
        sources["files"] = self._read_project_files(inputs.get("project_dir"))
        return sources

    def _read_project_files(self, project_dir: Any,
                            max_bytes: int = 20000) -> Dict[str, str]:
        """Read text artifacts from the task's project dir, sandbox-confined."""
        if not project_dir:
            return {}
        try:
            root = self.sandbox.resolve(project_dir)
        except Exception:
            return {}
        if not root.exists():
            return {}
        files: Dict[str, str] = {}
        for path in sorted(root.iterdir()):
            if not path.is_file():
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
                files[path.name] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binary or unreadable: not context
        return files

    def _record_model_outcome(self, task: models.Task, route: Optional[Dict[str, Any]],
                              result: Dict[str, Any]) -> None:
        """Emit model.completed/model.failed and account for the provider."""
        if route is None:
            return
        ok = bool(result.get("ok", False))
        payload = {
            "model_id": route.get("model_id"), "provider": route.get("provider"),
            "ok": ok, "task_id": task.task_id,
        }
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=(models.EventType.MODEL_COMPLETED if ok
                                else models.EventType.MODEL_FAILED),
                    project_id=task.goal_id, goal_id=task.goal_id,
                    task_id=task.task_id, payload=payload,
                )
            )
        # Only account when a real quota manager is wired in; a refusal already
        # published model.failed and must not be counted twice.
        quota = getattr(self.router, "quota", None)
        if quota is None:
            return
        provider = route.get("provider")
        if provider not in getattr(quota, "specs", {}):
            return
        if ok:
            quota.record_success(provider)
        else:
            quota.record_failure(provider, error=str(result.get("reason", "")))

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
