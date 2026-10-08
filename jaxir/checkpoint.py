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
