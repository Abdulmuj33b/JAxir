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
from .loopguard import failure_signature


@dataclass
class Feedback:
    feedback_id: str
    source: str
    failure_event: str
    symptoms: List[str]
    project_id: str = "todo"
    goal_id: Optional[str] = None
    task_id: Optional[str] = None
    cluster_id: Optional[str] = None
    root_cause: Optional[str] = None
    severity: str = "info"
    corrective_task_id: Optional[str] = None
    accepted: bool = False
    created_at: Optional[str] = None
    attempt: int = 1


class FeedbackEngine:
    def __init__(self, event_bus: Any):
        self.bus = event_bus
        self.feedback: List[Feedback] = []

    # ------------------------------------------------------------------
    # Create / accept
    # ------------------------------------------------------------------

    def create(self, project_id: str, goal_id: str, task_id: Optional[str],
               failure_event: str, symptoms: List[str],
               cluster_id: Optional[str] = None,
               root_cause: Optional[str] = None,
               severity: str = "info",
               attempt: int = 1) -> Feedback:
        f = Feedback(
            feedback_id=self._uid(),
            source=f"goal:{goal_id}/task:{task_id}",
            failure_event=failure_event,
            symptoms=symptoms,
            project_id=project_id,
            goal_id=goal_id,
            task_id=task_id,
            cluster_id=cluster_id,
            root_cause=root_cause,
            severity=severity,
            attempt=attempt,
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
                             "root_cause": root_cause, "severity": severity,
                             "severity_rank": self._severity_rank(severity),
                             "signature": failure_signature(failure_event, symptoms)},
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
                            project_id=f.project_id,
                            goal_id=f.goal_id,
                            task_id=f.task_id,
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

    # ------------------------------------------------------------------
    # Corrective tasks (section 18: clustering -> root cause -> corrective tasks)
    # ------------------------------------------------------------------

    def prioritized_clusters(self, failures: List[Feedback]) -> List[Dict[str, Any]]:
        """Group equivalent failures and rank them by severity then size.

        Equivalent failures share a signature, so many symptoms that trace to
        one root cause collapse into a single corrective task (the section 18
        '47 failures -> 3 root causes' behaviour).
        """
        grouped: Dict[str, List[Feedback]] = {}
        for f in failures:
            sig = failure_signature(f.failure_event, f.symptoms)
            grouped.setdefault(sig, []).append(f)

        clusters: List[Dict[str, Any]] = []
        for sig, members in grouped.items():
            severity = max((m.severity for m in members),
                           key=self._severity_rank)
            symptoms = sorted({s for m in members for s in m.symptoms})
            clusters.append({
                "cluster_id": members[0].cluster_id or sig,
                "signature": sig,
                "failure_event": members[0].failure_event,
                "root_cause": self.root_cause(members) or "unknown",
                "severity": severity,
                "severity_rank": self._severity_rank(severity),
                "member_count": len(members),
                "symptoms": symptoms,
                "feedback_ids": [m.feedback_id for m in members],
            })
        return sorted(clusters,
                      key=lambda c: (c["severity_rank"], c["member_count"]),
                      reverse=True)

    def corrective_tasks(self, goal_id: str, failures: List[Feedback],
                         project_dir: Any = "", strategy: str = "retry",
                         attempt: int = 1) -> List[Dict[str, Any]]:
        """Turn prioritized clusters into corrective task *specs*.

        Specs, not ``models.Task`` objects: the Feedback engine stays a
        failure-analysis component, and Goal Mode (which owns the task graph)
        materialises them. One spec per root cause.
        """
        specs: List[Dict[str, Any]] = []
        for cluster in self.prioritized_clusters(failures):
            root = cluster["root_cause"]
            specs.append({
                "corrective": True,
                "goal_id": goal_id,
                "cluster_id": cluster["cluster_id"],
                "root_cause": root,
                "severity": cluster["severity"],
                "symptoms": cluster["symptoms"],
                "symptom_signature": cluster["signature"],
                "feedback_ids": cluster["feedback_ids"],
                "member_count": cluster["member_count"],
                "strategy": strategy,
                "attempt": attempt,
                "project_dir": str(project_dir),
                "title": f"Correct {root}",
                "description": (f"{cluster['member_count']} symptom(s) from "
                                f"{cluster['failure_event']}: {root}"),
            })
        return specs

    def resolved(self, failure_event: str,
                 symptoms: List[str]) -> List[Feedback]:
        """Feedback for an equivalent failure (used to close the loop)."""
        sig = failure_signature(failure_event, symptoms)
        return [f for f in self.feedback
                if failure_signature(f.failure_event, f.symptoms) == sig]
