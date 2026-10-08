"""Context Compiler (constitution section 10).

JaXir must not blindly send the entire project context to every agent. The
compiler selects relevant requirements, files, architecture decisions, previous
attempts, QA failures, acceptance criteria, tools, task state, project memory
and relevant experience; compresses redundant context; and preserves the
information actually required to do the work.

Two properties are guaranteed and measured:

- **Retention.** Items marked *required* are never dropped, even when they alone
  exceed the budget. Optimization must not sacrifice engineering quality
  (section 10), so an over-budget bundle reports ``over_budget`` rather than
  silently losing a mandatory requirement.
- **Reduction.** The achieved reduction is measured, not asserted, against the
  locked target of >=50% context reduction while retaining required information.

Compression is real: identical items are deduplicated, and equivalent failure
reports - which have many symptoms but few root causes (section 18) - are
collapsed into one item carrying the member count.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .loopguard import failure_signature

#: Locked acceptance target: context reduction >=50% while retaining
#: required information.
REDUCTION_TARGET = 0.50

#: Baseline relevance by item kind. Deterministic and inspectable; called out
#: in ``ContextBundle.to_dict`` so a reader can see what drove the ranking.
KIND_WEIGHT: Dict[str, float] = {
    "acceptance_criterion": 1.00,
    "requirement": 0.95,
    "task": 0.90,
    "failure": 0.80,
    "decision": 0.60,
    "file": 0.50,
    "memory": 0.40,
    "experience": 0.35,
}

#: Items of these kinds are mandatory: dropping them would lose the criteria
#: the work is judged against, or the state needed to resume it.
REQUIRED_KINDS: Tuple[str, ...] = ("acceptance_criterion", "requirement", "task")


def estimate_tokens(text: str) -> int:
    """Cheap deterministic token estimate (~4 chars/token).

    Deliberately not a real tokenizer: the compiler needs a stable, dependency-
    free cost model, and over/under-estimating uniformly does not change which
    items fit a budget.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


@dataclass
class ContextItem:
    kind: str
    key: str
    content: str
    required: bool = False
    relevance: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "key": self.key, "required": self.required,
            "relevance": round(self.relevance, 4), "tokens": self.tokens,
            "content": self.content if len(self.content) <= 200
                       else self.content[:200] + "...",
        }


@dataclass
class ContextBundle:
    """The compiled context handed to an agent, with its own accounting."""

    goal_id: str
    task_id: str
    items: List[ContextItem] = field(default_factory=list)
    dropped: List[ContextItem] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    operations: List[Dict[str, Any]] = field(default_factory=list)
    token_budget: int = 0

    @property
    def reduction(self) -> float:
        if self.tokens_before <= 0:
            return 0.0
        return 1.0 - (self.tokens_after / self.tokens_before)

    @property
    def meets_target(self) -> bool:
        return self.reduction >= REDUCTION_TARGET

    @property
    def over_budget(self) -> bool:
        """Required items alone exceed the budget.

        Reported, never resolved by dropping required information.
        """
        return self.tokens_after > self.token_budget > 0

    def render(self) -> str:
        """Flatten the bundle to the text an agent would receive."""
        blocks = []
        for item in self.items:
            blocks.append(f"[{item.kind}:{item.key}]\n{item.content}")
        return "\n\n".join(blocks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id, "task_id": self.task_id,
            "items": [i.to_dict() for i in self.items],
            "dropped_count": len(self.dropped),
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "reduction": round(self.reduction, 4),
            "reduction_target": REDUCTION_TARGET,
            "meets_target": self.meets_target,
            "token_budget": self.token_budget,
            "over_budget": self.over_budget,
            "operations": self.operations,
            "required_retained": all(
                i.required for i in self.items if i.kind in REQUIRED_KINDS
            ),
        }


class ContextCompiler:
    """Gathers, deduplicates, compresses, ranks and selects context.

    The pipeline is deterministic: the same inputs always produce the same
    bundle, which keeps agent behaviour reproducible (section 46).
    """

    def __init__(self, token_budget: int = 4000,
                 reduction_target: float = REDUCTION_TARGET):
        if token_budget < 1:
            raise ValueError("token_budget must be >= 1")
        self.token_budget = token_budget
        self.reduction_target = reduction_target

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def compile(self, goal: Any = None, task: Any = None,
                sources: Optional[Dict[str, Any]] = None) -> ContextBundle:
        """Compile a bundle for ``task`` within ``goal``.

        ``sources`` may carry any of: ``requirements``, ``acceptance_criteria``,
        ``decisions``, ``failures``, ``files``, ``memory``, ``experience``.
        """
        sources = sources or {}
        task_id = str(getattr(task, "task_id", "") or "")
        goal_id = str(getattr(goal, "goal_id", "") or "")
        bundle = ContextBundle(goal_id=goal_id, task_id=task_id,
                               token_budget=self.token_budget)

        items = self._gather(goal, task, sources)
        bundle.tokens_before = sum(i.tokens for i in items)

        items = self._dedupe(items, bundle)
        items = self._collapse_failures(items, bundle)
        self._score(items, goal, task)

        kept, dropped = self._select(items)
        bundle.items, bundle.dropped = kept, dropped
        bundle.tokens_after = sum(i.tokens for i in kept)
        return bundle

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _gather(self, goal: Any, task: Any,
                sources: Dict[str, Any]) -> List[ContextItem]:
        items: List[ContextItem] = []

        # The task definition itself is mandatory context.
        if task is not None:
            items.append(ContextItem(
                kind="task", key=str(getattr(task, "task_id", "task")),
                content=json.dumps({
                    "agent_type": getattr(task, "agent_type", ""),
                    "capabilities": getattr(task, "capabilities", []),
                    "inputs": _as_text_map(getattr(task, "inputs", {})),
                    "outputs": getattr(task, "outputs", {}),
                }, sort_keys=True, default=str),
                required=True,
            ))

        for criterion in _iter_dicts(sources.get("acceptance_criteria"),
                                     getattr(task, "acceptance_criteria", None),
                                     getattr(goal, "acceptance_criteria", None)):
            items.append(ContextItem(
                kind="acceptance_criterion",
                key=str(criterion.get("id") or criterion.get("metric") or "criterion"),
                content=json.dumps(criterion, sort_keys=True, default=str),
                required=True,
            ))

        for req in _iter_dicts(sources.get("requirements"),
                               getattr(goal, "requirements", None)):
            items.append(ContextItem(
                kind="requirement",
                key=str(req.get("id") or req.get("title") or "requirement"),
                content=json.dumps(req, sort_keys=True, default=str),
                required=True,
            ))

        for idx, decision in enumerate(_iter_dicts(sources.get("decisions"))):
            items.append(ContextItem(
                kind="decision",
                key=str(decision.get("summary_id") or f"decision-{idx}"),
                content=json.dumps(decision, sort_keys=True, default=str),
            ))

        for idx, failure in enumerate(_iter_dicts(sources.get("failures"))):
            # ``_failure_text`` keeps the signature contract used by the guard
            # so equivalent failures cluster identically in both engines.
            event, symptoms = _failure_parts(failure)
            items.append(ContextItem(
                kind="failure", key=str(failure.get("cluster_id") or f"failure-{idx}"),
                content=json.dumps(failure, sort_keys=True, default=str),
                metadata={"failure_event": event, "symptoms": symptoms},
            ))

        for path, content in _iter_files(sources.get("files")):
            items.append(ContextItem(kind="file", key=str(path), content=str(content)))

        for idx, entry in enumerate(_iter_dicts(sources.get("memory"))):
            items.append(ContextItem(
                kind="memory", key=str(entry.get("id") or f"memory-{idx}"),
                content=json.dumps(entry, sort_keys=True, default=str),
            ))

        for idx, entry in enumerate(_iter_dicts(sources.get("experience"))):
            items.append(ContextItem(
                kind="experience", key=str(entry.get("pattern_id") or f"exp-{idx}"),
                content=json.dumps(entry, sort_keys=True, default=str),
            ))
        return items

    def _dedupe(self, items: List[ContextItem],
                bundle: ContextBundle) -> List[ContextItem]:
        """Drop items with identical content, keeping the first occurrence."""
        seen: Dict[str, ContextItem] = {}
        kept: List[ContextItem] = []
        removed = 0
        for item in items:
            digest = f"{item.kind}|{item.content}"
            if digest in seen:
                removed += 1
                continue
            seen[digest] = item
            kept.append(item)
        if removed:
            bundle.operations.append({"op": "dedupe", "removed": removed})
        return kept

    def _collapse_failures(self, items: List[ContextItem],
                           bundle: ContextBundle) -> List[ContextItem]:
        """Collapse equivalent failures into one item with a member count.

        Many symptoms, few root causes (section 18): 20 reports of the same
        failure should cost the agent one item, not twenty.
        """
        failures = [i for i in items if i.kind == "failure"]
        others = [i for i in items if i.kind != "failure"]
        if len(failures) < 2:
            return items

        groups: Dict[str, List[ContextItem]] = {}
        for item in failures:
            sig = failure_signature(item.metadata.get("failure_event", ""),
                                    item.metadata.get("symptoms", []))
            groups.setdefault(sig, []).append(item)

        collapsed: List[ContextItem] = []
        collapsed_count = 0
        for sig, members in groups.items():
            if len(members) == 1:
                collapsed.append(members[0])
                continue
            collapsed_count += len(members) - 1
            first = members[0]
            collapsed.append(ContextItem(
                kind="failure", key=first.key,
                content=(f"{len(members)} equivalent failures "
                         f"({first.metadata.get('failure_event', '')}): "
                         f"symptoms={sorted({s for m in members for s in m.metadata.get('symptoms', [])})}"),
                metadata={**first.metadata,
                          "member_count": len(members),
                          "signature": sig,
                          "collapsed_from": [m.key for m in members]},
            ))
        if collapsed_count:
            bundle.operations.append({"op": "collapse_equivalent_failures",
                                      "collapsed": collapsed_count,
                                      "groups": len(groups)})
        return others + collapsed

    def _score(self, items: List[ContextItem], goal: Any, task: Any) -> None:
        """Deterministic relevance: kind weight + lexical overlap + task hit."""
        task_text = " ".join([
            str(getattr(task, "agent_type", "")),
            " ".join(getattr(task, "capabilities", None) or []),
            json.dumps(_as_text_map(getattr(task, "inputs", {})), default=str),
        ]).lower()
        goal_text = " ".join([
            str(getattr(goal, "user_intent", "")),
            str(getattr(goal, "title", "")),
        ]).lower()
        task_input_values = {str(v).lower()
                             for v in _as_text_map(getattr(task, "inputs", {})).values()}

        for item in items:
            score = KIND_WEIGHT.get(item.kind, 0.3)
            haystack = (item.content + " " + item.key).lower()
            words = {w for w in _words(haystack) if len(w) > 3}
            if words:
                overlap = len(words & _words(task_text)) / len(words)
                score += 0.20 * overlap
                score += 0.10 * (len(words & _words(goal_text)) / len(words))
            if item.key.lower() in task_input_values:
                score += 0.15
            item.relevance = round(min(score, 1.5), 4)

    def _select(self, items: List[ContextItem]
                ) -> Tuple[List[ContextItem], List[ContextItem]]:
        """Required items always; then highest relevance until the budget fills."""
        required = [i for i in items if i.required or i.kind in REQUIRED_KINDS]
        optional = [i for i in items if not (i.required or i.kind in REQUIRED_KINDS)]

        kept = list(required)
        used = sum(i.tokens for i in kept)

        # Highest relevance first; cheaper items win ties (same information
        # value at lower cost).
        optional.sort(key=lambda i: (-i.relevance, i.tokens, i.key))
        dropped: List[ContextItem] = []
        for item in optional:
            if used + item.tokens <= self.token_budget:
                kept.append(item)
                used += item.tokens
            else:
                dropped.append(item)
        return kept, dropped

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    @staticmethod
    def evidence(bundle: ContextBundle) -> Dict[str, Any]:
        """Evidence payload for the reduction acceptance target."""
        return {
            "test_id": "context.reduction",
            "reduction": round(bundle.reduction, 4),
            "target": REDUCTION_TARGET,
            "meets_target": bundle.meets_target,
            "tokens_before": bundle.tokens_before,
            "tokens_after": bundle.tokens_after,
            "required_retained": bundle.to_dict()["required_retained"],
            "operations": bundle.operations,
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _words(text: str) -> set:
    out = set()
    current = []
    for ch in text:
        if ch.isalnum() or ch == "_":
            current.append(ch)
        elif current:
            out.add("".join(current))
            current = []
    if current:
        out.add("".join(current))
    return out


def _as_text_map(mapping: Any) -> Dict[str, str]:
    if not isinstance(mapping, dict):
        return {}
    return {str(k): v for k, v in mapping.items() if isinstance(v, (str, int, float, bool))}


def _iter_dicts(*sources: Any) -> Iterable[Dict[str, Any]]:
    for source in sources:
        if not source:
            continue
        if isinstance(source, dict):
            yield source
        else:
            for entry in source:
                if isinstance(entry, dict):
                    yield entry
                elif hasattr(entry, "to_dict"):
                    yield entry.to_dict()


def _iter_files(sources: Any) -> Iterable[Tuple[str, str]]:
    if not sources:
        return
    if isinstance(sources, dict):
        for path, content in sources.items():
            yield path, content
        return
    for entry in sources:
        if isinstance(entry, dict):
            yield entry.get("path", ""), entry.get("content", "")
        else:
            yield str(getattr(entry, "path", "")), getattr(entry, "content", "")


def _failure_parts(failure: Dict[str, Any]) -> Tuple[str, List[str]]:
    event = str(failure.get("failure_event") or failure.get("test_id")
                or failure.get("event") or "")
    symptoms = failure.get("symptoms") or failure.get("checks") or []
    if isinstance(symptoms, dict):
        symptoms = [k for k, ok in symptoms.items() if not ok]
    return event, [str(s) for s in symptoms]
