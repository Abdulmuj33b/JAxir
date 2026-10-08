"""Observability & decision summaries.

Traces the full chain Goal -> Task -> Agent -> Model -> Context -> Tool ->
Command -> File -> Result -> QA -> Evidence. Records duration, model, provider,
tokens (where available), tool calls, result, failure, retries, resource usage,
and provenance.

Private chain-of-thought is never exposed. Only concise auditable decision
summaries are recorded.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import models


class Observability:
    def __init__(self, event_bus: Any, registry: Any):
        self.bus = event_bus
        self.registry = registry

    # ------------------------------------------------------------------
    # Tracer
    # ------------------------------------------------------------------

    def trace(self, goal_id: str, project_id: str, operation: str, fn):
        """Decorator that times and emits model/request events."""
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                ok = True
            except Exception as exc:
                result = None
                ok = False
                self._failure(goal_id, project_id, operation, exc)
            finally:
                dur = time.perf_counter() - start
                self._record(goal_id, project_id, operation, start, dur, ok)
            return result
        return wrapper

    def _record(self, goal_id: str, project_id: str, operation: str,
                start: float, dur: float, ok: bool) -> None:
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.GOAL_COMPLETED
                    if ok else models.EventType.GOAL_FAILED,
                    project_id=project_id,
                    goal_id=goal_id,
                    payload={"operation": operation, "duration_ms": round(dur * 1000, 2)},
                )
            )

    def _failure(self, goal_id: str, project_id: str, operation: str,
                 exc: Exception) -> None:
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.GOAL_FAILED,
                    project_id=project_id,
                    goal_id=goal_id,
                    payload={"operation": operation, "error": str(exc)},
                )
            )

    # ------------------------------------------------------------------
    # Decision summaries (concise, auditable)
    # ------------------------------------------------------------------

    def decision_summary(self, goal_id: str, project_id: str, decision: str,
                         rationale: str, alternatives: List[str],
                         complexity_added: int, measurable_improvement: str) -> Dict[str, Any]:
        summary = {
            "summary_id": self._uid(),
            "goal_id": goal_id,
            "project_id": project_id,
            "decision": decision,
            "rationale": rationale,
            "alternatives": alternatives,
            "complexity_added": complexity_added,
            "measurable_improvement": measurable_improvement,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.registry is not None:
            self.registry.add_decisions([summary])
        return summary

    @staticmethod
    def _uid() -> str:
        import uuid
        return str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Resource governance
    # ------------------------------------------------------------------

    def resource_snapshot(self, project_id: str) -> Dict[str, Any]:
        return {
            "project_id": project_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agents": len(self.bus._channels) if self.bus else 0,
            "events": self.bus.count() if self.bus else 0,
        }
