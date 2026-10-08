"""Feedback engine.

Transforms failures -> deduplication -> clustering -> root cause ->
prioritization -> corrective tasks -> replanning. Feedback integrates
directly with Goal Mode. The system recognizes that many symptoms may
originate from one root cause (e.g. 47 failures -> 1 root cause).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import models


@dataclass
class Feedback:
    feedback_id: str
    source: str
    failure_event: str
    symptoms: List[str]
    cluster_id: Optional[str] = None
    root_cause: Optional[str] = None
    severity: str = "info"
    corrective_task_id: Optional[str] = None
    accepted: bool = False
    created_at: Optional[str] = None


class FeedbackEngine:
    def __init__(self, event_bus: Any):
        self.bus = event_bus
        self.feedback: List[Feedback] = []

    # ------------------------------------------------------------------
    # Create / accept
    # ------------------------------------------------------------------

    def create(self, project_id: str, goal_id: str, task_id: str,
               failure_event: str, symptoms: List[str],
               cluster_id: Optional[str] = None,
               root_cause: Optional[str] = None) -> Feedback:
        f = Feedback(
            feedback_id=self._uid(),
            source=f"goal:{goal_id}/task:{task_id}",
            failure_event=failure_event,
            symptoms=symptoms,
            cluster_id=cluster_id,
            root_cause=root_cause,
            created_at="2026-07-10T00:00:00Z",
        )
        self.feedback.append(f)
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.FEEDBACK_CREATED,
                    project_id=project_id,
                    goal_id=goal_id,
                    task_id=task_id,
                    payload={"feedback_id": f.feedback_id, "cluster": cluster_id,
                             "root_cause": root_cause},
                )
            )
        return f

    def accept(self, feedback_id: str) -> Feedback:
        for f in self.feedback:
            if f.feedback_id == feedback_id:
                f.accepted = True
                if self.bus is not None:
                    self.bus.publish(
                        models.Event(
                            event_type=models.EventType.FEEDBACK_ACCEPTED,
                            project_id="todo",
                            goal_id=None,
                            task_id=None,
                            payload={"feedback_id": f.feedback_id},
                        )
                    )
                return f
        raise KeyError(f"feedback {feedback_id} not found")

    # ------------------------------------------------------------------
    # Analysis: cluster / root cause / prioritize
    # ------------------------------------------------------------------

    def dedupe(self, feedback: List[Feedback]) -> List[Feedback]:
        """Deduplicate equivalent failure reports."""
        seen, out = set(), []
        for f in feedback:
            key = (f.failure_event, tuple(sorted(f.symptoms)))
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out

    def cluster(self, feedback: List[Feedback],
                similarity: float = 0.8) -> Dict[str, List[Feedback]]:
        """Simple symptom-based clustering."""
        clusters: Dict[str, List[Feedback]] = {}
        for f in feedback:
            placed = False
            for cid, members in clusters.items():
                if any(s in f.symptoms for s in members[0].symptoms):
                    clusters[cid].append(f)
                    placed = True
                    break
            if not placed:
                clusters[f.cluster_id] = [f]
        return clusters

    def root_cause(self, cluster: List[Feedback]) -> Optional[str]:
        if not cluster:
            return None
        return cluster[0].root_cause or "unknown"

    def prioritize(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(findings, key=lambda x: self._severity_rank(x.get("severity", "info")), reverse=True)

    @staticmethod
    def _severity_rank(s: str) -> int:
        return {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}.get(s, 0)

    @staticmethod
    def _uid() -> str:
        import uuid
        return str(uuid.uuid4())

    def get_clusters(self, feedback: List[Feedback]) -> List[Dict[str, Any]]:
        clusters = self.cluster(feedback)
        out = []
        for cid, members in clusters.items():
            rc = self.root_cause(members)
            out.append({"cluster_id": cid, "member_count": len(members),
                        "root_cause": rc})
        return out
