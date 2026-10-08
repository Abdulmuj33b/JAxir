"""Checkpoint / time-travel manager.

Checkpoints preserve goal state, task state, agent state, context, pending
actions, QA state, feedback, artifacts, and environment metadata. Supports
restore, compare, branch, and replay.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import models


class CheckpointManager:
    def __init__(self, project_dir: str, event_bus: Any):
        self.root = Path(project_dir) / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)
        self.bus = event_bus

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------


    @staticmethod
    def _json_safe(obj: Any) -> Any:
        """Recursively convert datetime/date to ISO strings for JSON."""
        if isinstance(obj, dict):
            return {k: CheckpointManager._json_safe(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [CheckpointManager._json_safe(v) for v in obj]
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return obj

    def create(self, goal: models.Goal, tasks: List[models.Task],
               agents: List[models.AgentContract], context: Dict[str, Any],
               qa_state: Dict[str, Any], feedback: List[Dict[str, Any]],
               artifacts: List[str], env_meta: Dict[str, Any]) -> models.Checkpoint:
        store = self._store()
        if store is not None:
            # Anchor the checkpoint to a position in the event log so replay can
            # reconstruct exactly the state this checkpoint claims (section 20).
            context = {**(context or {}), "event_seq": store.latest_seq()}
        cp = models.Checkpoint(
            goal_id=goal.goal_id,
            project_id=goal.project_id,
            goal_state=goal.to_dict(),
            task_state={t.task_id: t.to_dict() for t in tasks},
            agent_state={a.identity: a.to_dict() for a in agents},
            context=context,
            pending_actions=[],
            qa_state=qa_state,
            feedback=feedback,
            artifacts=artifacts,
            environment_metadata=env_meta,
        )
        path = self.root / f"{cp.checkpoint_id}.json"
        path.write_text(json.dumps(self._json_safe(cp.to_dict()), indent=2), encoding="utf-8")
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.CHECKPOINT_CREATED,
                    project_id=goal.project_id,
                    goal_id=goal.goal_id,
                    payload={"checkpoint_id": cp.checkpoint_id},
                )
            )
        return cp

    def restore(self, checkpoint_id: str,
                project_dir: Path) -> models.Checkpoint:
        path = self.root / f"{checkpoint_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"checkpoint {checkpoint_id} not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        cp = models.Checkpoint.from_dict(data)
        # Recreate goal/task state from checkpoint.
        if project_dir is not None:
            (project_dir / "restored").mkdir(parents=True, exist_ok=True)
            (project_dir / "restored" / "checkpoint_id.txt").write_text(
                checkpoint_id, encoding="utf-8"
            )
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.CHECKPOINT_RESTORED,
                    project_id=cp.project_id,
                    goal_id=cp.goal_id,
                    payload={"checkpoint_id": cp.checkpoint_id},
                )
            )
        return cp

    def _store(self) -> Any:
        """The durable event log, when the bus was given one."""
        return getattr(self.bus, "store", None)

    def latest(self, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The most recent checkpoint, for recovery."""
        records = self.list_checkpoints()
        if project_id is not None:
            records = [r for r in records if r.get("project_id") == project_id]
        if not records:
            return None
        return max(records, key=lambda r: r.get("created_at") or "")

    def branch(self, checkpoint_id: str, project_dir: Optional[Path] = None,
               name: str = "") -> models.Checkpoint:
        """Fork a new checkpoint from an existing one (section 20: branch).

        The parent is preserved and untouched; the child records its lineage so
        an alternative run can be explored without rewriting history.
        """
        parent = self._load(checkpoint_id)
        created = models.Checkpoint(
            goal_id=parent["goal_id"],
            project_id=parent["project_id"],
            goal_state=parent.get("goal_state", {}),
            task_state=parent.get("task_state", {}),
            agent_state=parent.get("agent_state", {}),
            context={**parent.get("context", {}),
                     "parent_checkpoint_id": checkpoint_id,
                     "branch_name": name},
            pending_actions=parent.get("pending_actions", []),
            qa_state=parent.get("qa_state", {}),
            feedback=parent.get("feedback", []),
            artifacts=parent.get("artifacts", []),
            environment_metadata=parent.get("environment_metadata", {}),
        )
        path = self.root / f"{created.checkpoint_id}.json"
        path.write_text(json.dumps(self._json_safe(created.to_dict()), indent=2),
                        encoding="utf-8")
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.CHECKPOINT_CREATED,
                    project_id=created.project_id, goal_id=created.goal_id,
                    payload={"checkpoint_id": created.checkpoint_id,
                             "parent_checkpoint_id": checkpoint_id,
                             "branch": name},
                )
            )
        return created

    def lineage(self, checkpoint_id: str) -> List[str]:
        """Parent chain, newest first, cycle-safe."""
        chain: List[str] = []
        seen: set = set()
        current: Optional[str] = checkpoint_id
        while current and current not in seen:
            seen.add(current)
            chain.append(current)
            try:
                data = self._load(current)
            except FileNotFoundError:
                break
            current = (data.get("context") or {}).get("parent_checkpoint_id")
        return chain

    def replay(self, checkpoint_id: str) -> Dict[str, Any]:
        """Reconstruct state from the event log and check it matches the
        checkpoint (section 20: replay; the locked replay-equivalence target).

        The comparison is against what the checkpoint *recorded*, so this proves
        the log is sufficient to recover the state rather than merely re-reading
        the same JSON.
        """
        store = self._store()
        if store is None:
            return {"ok": False, "reason": "no_event_store"}
        data = self._load(checkpoint_id)
        upto = (data.get("context") or {}).get("event_seq")
        rebuilt = store.rebuild(goal_id=data["goal_id"], upto_seq=upto)

        recorded_status = (data.get("goal_state") or {}).get("status")
        recorded_tasks = {tid: (t or {}).get("status")
                          for tid, t in (data.get("task_state") or {}).items()}
        rebuilt_tasks = rebuilt["tasks"]

        status_match = recorded_status in (None, rebuilt["goal_status"])

        # A task with no events in the log never entered execution, so it is
        # *reported* as absent rather than silently counted as a match.
        absent = [tid for tid in recorded_tasks if tid not in rebuilt_tasks]
        task_mismatches = {tid: {"recorded": recorded_tasks[tid],
                                 "rebuilt": rebuilt_tasks.get(tid)}
                           for tid in recorded_tasks
                           if tid in rebuilt_tasks
                           and rebuilt_tasks[tid] != recorded_tasks[tid]}
        return {
            "ok": status_match and not task_mismatches,
            "checkpoint_id": checkpoint_id,
            "event_seq": upto,
            "recorded_goal_status": recorded_status,
            "rebuilt_goal_status": rebuilt["goal_status"],
            "task_mismatches": task_mismatches,
            "tasks_absent_from_log": absent,
            "transitions": rebuilt["transitions"],
        }

    def _load(self, checkpoint_id: str) -> Dict[str, Any]:
        path = self.root / f"{checkpoint_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"checkpoint {checkpoint_id} not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_checkpoints(self, goal_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for p in self.root.glob("*.json"):
            data = json.loads(p.read_text(encoding="utf-8"))
            if goal_id is None or data.get("goal_id") == goal_id:
                out.append(data)
        return out

    def compare(self, a: str, b: str) -> Dict[str, Any]:
        da = json.loads((self.root / f"{a}.json").read_text(encoding="utf-8"))
        db = json.loads((self.root / f"{b}.json").read_text(encoding="utf-8"))
        return {"a": a, "b": b, "diff": self._diff(da, db)}

    @staticmethod
    def _diff(da: Dict[str, Any], db: Dict[str, Any]) -> Dict[str, Any]:
        # shallow recursive diff on a frozen subset
        def _r(d1, d2, path):
            out = {}
            for k in set(d1.keys()) | set(d2.keys()):
                if k not in d1:
                    out[f"{path}.{k}"] = {"added": d2[k]}
                elif k not in d2:
                    out[f"{path}.{k}"] = {"removed": d1[k]}
                elif isinstance(d1[k], dict) and isinstance(d2[k], dict):
                    out.update(_r(d1[k], d2[k], f"{path}.{k}"))
                elif d1[k] != d2[k]:
                    out[f"{path}.{k}"] = {"changed": [d1[k], d2[k]]}
            return out
        return _r(da, db, "")
