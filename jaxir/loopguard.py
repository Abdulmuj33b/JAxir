"""Autonomous loop protection (constitution sections 41 and 42).

Detects the failure modes that make an autonomous loop dangerous: repeated
equivalent failures, oscillating plans, duplicate tasks, and ineffective
retries. The system must eventually do

    retry -> analyze -> change strategy -> replan -> escalate if necessary

rather than ``retry forever``.

The guard is deliberately pure: it makes no model or provider calls, owns no
state outside itself, and returns a verdict. Callers publish events. This keeps
it trivially testable and free of hidden behaviour (Occam Engineering).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

#: Locked acceptance target: autonomous-loop detection within <=5 equivalent
#: failures. Lowering this is allowed; raising it is a baseline change.
EQUIVALENT_FAILURE_LIMIT = 5

#: Ordered strategies tried as equivalent failures accumulate. Mirrors the
#: section 41 escalation ladder.
DEFAULT_STRATEGIES: tuple = (
    "retry",
    "change_strategy",
    "change_agent",
    "change_model",
    "rollback",
)

#: Verdict actions.
CONTINUE = "continue"
CHANGE_STRATEGY = "change_strategy"
ESCALATE = "escalate"


@dataclass
class LoopVerdict:
    """The guard's decision for one failed attempt."""

    action: str
    reason: str
    attempt: int
    equivalent_failures: int
    strategy: str
    signature: str = ""

    @property
    def should_continue(self) -> bool:
        return self.action != ESCALATE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action, "reason": self.reason, "attempt": self.attempt,
            "equivalent_failures": self.equivalent_failures,
            "strategy": self.strategy, "signature": self.signature,
        }


def failure_signature(failure_event: str, symptoms: Iterable[str]) -> str:
    """Stable fingerprint of an equivalent failure.

    Two failures are 'equivalent' when they have the same failure event and the
    same symptom set regardless of order - so 47 reports of one root cause
    collapse to a single signature.
    """
    normalised = tuple(sorted({s.strip().lower() for s in symptoms if s}))
    return f"{failure_event}::{','.join(normalised)}"


class LoopGuard:
    """Tracks attempts and equivalent failures, and decides what to do next."""

    def __init__(self, max_attempts: int = 3,
                 equivalent_failure_limit: int = EQUIVALENT_FAILURE_LIMIT,
                 strategies: Optional[Iterable[str]] = None):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if equivalent_failure_limit < 1:
            raise ValueError("equivalent_failure_limit must be >= 1")
        self.max_attempts = max_attempts
        self.equivalent_failure_limit = equivalent_failure_limit
        self.strategies = tuple(strategies or DEFAULT_STRATEGIES)
        self.attempt = 0
        self.failures: List[str] = []          # signatures, in order
        self.counts: Dict[str, int] = {}
        self.plan_signatures: List[str] = []
        self.escalated: Optional[LoopVerdict] = None

    # ------------------------------------------------------------------
    # Attempts
    # ------------------------------------------------------------------

    def begin_attempt(self) -> int:
        self.attempt += 1
        return self.attempt

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempt)

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def record_failure(self, signature: str) -> LoopVerdict:
        """Record a failed attempt and decide the next move."""
        self.failures.append(signature)
        self.counts[signature] = self.counts.get(signature, 0) + 1
        count = self.counts[signature]

        if self.attempt >= self.max_attempts:
            verdict = LoopVerdict(ESCALATE, "attempt_budget_exhausted",
                                  self.attempt, count, "escalate", signature)
        elif count >= self.equivalent_failure_limit:
            verdict = LoopVerdict(ESCALATE, "equivalent_failure_limit",
                                  self.attempt, count, "escalate", signature)
        elif count == 1:
            verdict = LoopVerdict(CONTINUE, "first_occurrence",
                                  self.attempt, count, self._strategy(0), signature)
        else:
            verdict = LoopVerdict(CHANGE_STRATEGY, "equivalent_failure_recurred",
                                  self.attempt, count, self._strategy(count - 1),
                                  signature)
        if verdict.action == ESCALATE:
            self.escalated = verdict
        return verdict

    def _strategy(self, index: int) -> str:
        if not self.strategies:
            return "retry"
        return self.strategies[min(index, len(self.strategies) - 1)]

    # ------------------------------------------------------------------
    # Oscillation / duplicates
    # ------------------------------------------------------------------

    @staticmethod
    def plan_signature(task_specs: Iterable[Any]) -> str:
        """Fingerprint a plan so repetition can be detected.

        Accepts task objects or plain dicts, and ignores identity so an
        equivalent plan built twice hashes the same.
        """
        items = []
        for t in task_specs:
            if isinstance(t, dict):
                agent = t.get("agent_type", "")
                caps = sorted(t.get("capabilities") or [])
                root = t.get("root_cause") or ""
            else:
                agent = getattr(t, "agent_type", "")
                caps = sorted(getattr(t, "capabilities", None) or [])
                root = (getattr(t, "inputs", None) or {}).get("root_cause", "")
            items.append(f"{agent}|{','.join(caps)}|{root}")
        return json.dumps(sorted(items))

    def record_plan(self, signature: str) -> bool:
        """Record a plan; returns True when the plan is oscillating."""
        self.plan_signatures.append(signature)
        if len(self.plan_signatures) < 2:
            return False
        return self.detect_oscillation() is not None

    def detect_oscillation(self) -> Optional[str]:
        """Two consecutive identical replans will not converge."""
        if len(self.plan_signatures) >= 2 and (
                self.plan_signatures[-1] == self.plan_signatures[-2]):
            return "identical_replan"
        return None

    @staticmethod
    def detect_duplicate_tasks(task_specs: Iterable[Any]) -> List[str]:
        """Duplicate execution of a non-idempotent action (section 7)."""
        seen: Dict[str, int] = {}
        for t in task_specs:
            if isinstance(t, dict):
                key = json.dumps({k: t.get(k) for k in ("agent_type", "inputs")},
                                 sort_keys=True, default=str)
            else:
                key = json.dumps({"agent_type": getattr(t, "agent_type", ""),
                                  "inputs": getattr(t, "inputs", None)},
                                 sort_keys=True, default=str)
            seen[key] = seen.get(key, 0) + 1
        return [k for k, n in seen.items() if n > 1]

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        return {
            "attempts": self.attempt,
            "max_attempts": self.max_attempts,
            "equivalent_failure_limit": self.equivalent_failure_limit,
            "failure_signatures": list(self.failures),
            "equivalent_failure_counts": dict(self.counts),
            "plan_signatures": list(self.plan_signatures),
            "oscillation": self.detect_oscillation(),
            "escalated": self.escalated.to_dict() if self.escalated else None,
        }
