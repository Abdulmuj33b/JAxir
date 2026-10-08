"""Artifact Registry + Evidence + Memory.

Tracks lineage for source code, images, video, audio, 3D assets, CAD,
schematics, PCB artifacts, firmware, binaries, datasets, telemetry, and
simulations. Artifacts carry provenance and version info.

Memory levels:
- project memory (architecture, requirements, decisions)
- goal memory (attempts, progress, failures)
- qa memory (defects, tests, regressions)
- agent memory (performance, behavior)
- session memory (current execution context)
- engineering experience memory (cross-project knowledge)

Memory is structured and searchable.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import models


@dataclass
class Artifact:
    name: str
    kind: str
    artifact_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: str = "0.0.0"
    path: str = ""
    sha256: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)
    linked_goal_id: Optional[str] = None


@dataclass
class Decision:
    goal_id: str
    project_id: str
    decision: str
    rationale: str
    alternatives: List[str]
    complexity_added: int
    measurable_improvement: str
    summary_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class MemoryIndex:
    project_id: str
    memory_type: str  # goal, qa, agent, session, experience
    goal_id: Optional[str] = None
    items: List[Dict[str, Any]] = field(default_factory=list)


class Registry:
    def __init__(self, project_dir: str, event_bus: Any):
        self.root = Path(project_dir) / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.bus = event_bus
        self.artifacts: List[Artifact] = []
        self.decisions: List[Decision] = []
        self.indices: Dict[str, MemoryIndex] = {}

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def register(self, artifact: Artifact) -> Artifact:
        if artifact.path:
            p = Path(artifact.path)
            if p.exists():
                artifact.sha256 = self._sha256(p)
        self.artifacts.append(artifact)
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.CHECKPOINT_CREATED,
                    project_id=artifact.linked_goal_id or "",
                    goal_id=artifact.linked_goal_id,
                    payload={"artifact_id": artifact.artifact_id, "kind": artifact.kind},
                )
            )
        return artifact

    @staticmethod
    def _sha256(p: Path) -> str:
        import hashlib
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def list_artifacts(self, goal_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for a in self.artifacts:
            if goal_id is None or a.linked_goal_id == goal_id:
                out.append({"artifact_id": a.artifact_id, "name": a.name,
                            "kind": a.kind, "version": a.version,
                            "sha256": a.sha256})
        return out

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def add_decisions(self, decisions: List[Decision]) -> List[Decision]:
        self.decisions.extend(decisions)
        return decisions

    # ------------------------------------------------------------------
    # Memory indices
    # ------------------------------------------------------------------

    def add_memory(self, memory_type: str, items: List[Dict[str, Any]]) -> MemoryIndex:
        idx = self.indices.get(memory_type)
        if idx is None:
            idx = MemoryIndex(project_id="", memory_type=memory_type, items=[])
            self.indices[memory_type] = idx
        idx.items.extend(items)
        return idx

    def search(self, memory_type: str, query: str) -> List[Dict[str, Any]]:
        idx = self.indices.get(memory_type)
        if idx is None:
            return []
        q = query.lower()
        return [it for it in idx.items if q in json.dumps(it).lower()]

    def add_evidence(self, evidence: List[models.Evidence]) -> List[models.Evidence]:
        """Store evidence in the registry (evidence = first-class data)."""
        # In a fuller system: persist to evidence store + index by goal_id.
        # The events/QA record the evidence; registry keeps the reference.
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.CHECKPOINT_CREATED,
                    project_id="registry",
                    payload={"evidence_ids": [e.evidence_id for e in evidence]},
                )
            )
        return evidence

    def export(self, goal_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "artifacts": self.list_artifacts(goal_id),
            "decisions": [d.to_dict() for d in self.decisions],
            "indices": {k: {"type": v.memory_type, "items": v.items}
                        for k, v in self.indices.items()},
        }
