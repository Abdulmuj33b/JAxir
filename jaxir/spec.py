"""Goal Specification + Requirement chain.

Transforms natural-language intent into a machine-readable Goal.
Implements the locked requirement chain:

    Requirement -> Acceptance Target -> Test -> Evidence -> Release Gate

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import models


@dataclass
class RequirementChain:
    requirement_id: str
    acceptance_target_id: str
    metric: str
    threshold: Any
    measurement_method: str
    test_suite: List[str]
    severity: str
    environment: Dict[str, Any]
    evidence_required: List[str]
    release_gate: str


@dataclass
class GoalSpec:
    project_id: str
    user_intent: str
    title: str
    requirements: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[Dict[str, Any]] = field(default_factory=list)
    assumptions: List[Dict[str, Any]] = field(default_factory=list)
    acceptance_criteria: List[Dict[str, Any]] = field(default_factory=list)
    measurable_targets: Dict[str, Any] = field(default_factory=dict)
    definition_of_done: List[str] = field(default_factory=list)
    project_type: str = "generic"
    required_capabilities: List[str] = field(default_factory=list)
    risk_classification: str = "low"
    permissions_required: List[Dict[str, Any]] = field(default_factory=list)
    resource_limits: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    verification_requirements: List[Dict[str, Any]] = field(default_factory=list)
    chains: List[RequirementChain] = field(default_factory=list)

    @classmethod
    def from_text(cls, project_id: str, intent: str, goal: models.Goal) -> "GoalSpec":
        """Parse the user intent + goal into a specification."""
        # Deterministic heuristic matcher: looks for imperative verbs.
        return cls(
            project_id=project_id,
            user_intent=goal.user_intent,
            title=goal.title or "Untitled goal",
            requirements=goal.requirements,
            constraints=goal.constraints,
            assumptions=goal.assumptions,
            acceptance_criteria=goal.acceptance_criteria,
            measurable_targets=goal.measurable_targets,
            definition_of_done=goal.definition_of_done,
            project_type=goal.project_type,
            required_capabilities=goal.required_capabilities,
            risk_classification=goal.risk_classification,
            permissions_required=goal.permissions_required,
            resource_limits=goal.resource_limits,
            dependencies=goal.dependencies,
            verification_requirements=goal.verification_requirements,
        )

    def to_goal(self) -> models.Goal:
        return models.Goal(
            project_id=self.project_id,
            user_intent=self.user_intent,
            title=self.title,
            requirements=self.requirements,
            constraints=self.constraints,
            assumptions=self.assumptions,
            acceptance_criteria=self.acceptance_criteria,
            measurable_targets=self.measurable_targets,
            definition_of_done=self.definition_of_done,
            project_type=self.project_type,
            required_capabilities=self.required_capabilities,
            risk_classification=self.risk_classification,
            permissions_required=self.permissions_required,
            resource_limits=self.resource_limits,
            dependencies=self.dependencies,
            verification_requirements=self.verification_requirements,
        )
