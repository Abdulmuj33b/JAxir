"""Goal Specification + Requirement chain.

Transforms natural-language intent into a machine-readable Goal.
Implements the locked requirement chain:

    Requirement -> Acceptance Target -> Test -> Evidence -> Release Gate

"""

from __future__ import annotations

import re
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

    @staticmethod
    def _infer_project_type(intent: str) -> str:
        lowered = intent.lower()
        if any(token in lowered for token in ["api", "service", "endpoint", "backend"]):
            return "service"
        if any(token in lowered for token in ["cli", "command line", "terminal"]):
            return "cli"
        if any(token in lowered for token in ["web", "ui", "frontend", "dashboard", "site", "app", "todo"]):
            return "web"
        return "generic"

    @staticmethod
    def _infer_title(intent: str, fallback: str) -> str:
        cleaned = intent.strip()
        if not cleaned:
            return fallback
        cleaned = re.sub(r"^(build|create|implement|ship|deliver|make|add|fix)\s+(?:a|an|the)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.split(r"\s+with\s+|\s+and\s+", cleaned, maxsplit=1)[0]
        cleaned = cleaned.strip(" .,:;-")
        if not cleaned:
            return fallback
        if len(cleaned.split()) > 8:
            cleaned = " ".join(cleaned.split()[:8])
        return cleaned.title()

    @staticmethod
    def _default_requirements(intent: str) -> List[Dict[str, Any]]:
        lowered = intent.lower()
        requirements: List[Dict[str, Any]] = []
        if "secure" in lowered:
            requirements.append({
                "id": "REQ-SEC",
                "description": "The solution must be secure and protect sensitive operations.",
                "priority": "high",
            })
        if "preview" in lowered or "ui" in lowered or "web" in lowered:
            requirements.append({
                "id": "REQ-PREVIEW",
                "description": "The feature must be previewable and verifiable in a live UI or served output.",
                "priority": "high",
            })
        if "error" in lowered or "handling" in lowered or "failure" in lowered:
            requirements.append({
                "id": "REQ-ERROR",
                "description": "The workflow must handle failure and error states without silent corruption.",
                "priority": "medium",
            })
        if not requirements:
            requirements.append({
                "id": "REQ-PRIMARY",
                "description": "Deliver the requested functionality described by the user intent.",
                "priority": "high",
            })
        return requirements

    @staticmethod
    def _requirement_chains(requirements: List[Dict[str, Any]],
                           acceptance_criteria: List[Dict[str, Any]],
                           project_type: str,
                           verification_requirements: List[Dict[str, Any]]) -> List[RequirementChain]:
        if not requirements:
            requirements = [{"id": "REQ-PRIMARY", "description": "Deliver the requested functionality", "priority": "high"}]

        requirement_items: List[Dict[str, Any]] = []
        for idx, requirement in enumerate(requirements, start=1):
            entry = dict(requirement)
            entry.setdefault("id", f"REQ-{idx:03d}")
            entry.setdefault("priority", "medium")
            requirement_items.append(entry)

        chains: List[RequirementChain] = []
        suite = [item.get("id", "qa.functional") for item in verification_requirements] or [
            "qa.preview",
            "qa.functional",
            "qa.security",
            "qa.performance",
        ]

        for idx, requirement in enumerate(requirement_items, start=1):
            req_id = str(requirement.get("id", f"REQ-{idx:03d}"))
            if not req_id.startswith("REQ-"):
                req_id = f"REQ-{req_id}"
            acceptance = acceptance_criteria[idx - 1] if idx - 1 < len(acceptance_criteria) else {
                "id": f"AC-{idx:03d}",
                "metric": requirement.get("description", "requirement_satisfied"),
                "threshold": True,
                "measurement_method": "qa",
            }
            target_id = acceptance.get("id", f"AT-{project_type.upper()}-{idx:03d}")
            if not str(target_id).startswith("AT-"):
                target_id = f"AT-{project_type.upper()}-{idx:03d}"
            chains.append(RequirementChain(
                requirement_id=req_id,
                acceptance_target_id=str(target_id),
                metric=str(acceptance.get("metric", requirement.get("description", "requirement_satisfied"))),
                threshold=acceptance.get("threshold", True),
                measurement_method=str(acceptance.get("measurement_method", "qa")),
                test_suite=suite,
                severity=str(requirement.get("priority", "medium")),
                environment={"project_type": project_type},
                evidence_required=["verification","provenance","release_gate"],
                release_gate="Gate 2" if str(requirement.get("priority", "medium")).lower() in {"high", "critical"} else "Gate 1",
            ))

        return chains

    @classmethod
    def from_text(cls, project_id: str, intent: str, goal: models.Goal) -> "GoalSpec":
        """Parse the user intent + goal into a specification."""
        intent_text = intent or goal.user_intent or ""
        lowered = intent_text.lower()
        project_type = cls._infer_project_type(intent_text)
        title = goal.title or cls._infer_title(intent_text, "Untitled goal")

        requirements = list(goal.requirements) or cls._default_requirements(intent_text)
        constraints = list(goal.constraints)
        if "secure" in lowered and not constraints:
            constraints.append({"id": "CON-SEC", "description": "Security-sensitive behavior must not silently bypass verification."})
        if "fast" in lowered and not constraints:
            constraints.append({"id": "CON-PERF", "description": "User-facing operations must remain responsive."})

        acceptance_criteria = list(goal.acceptance_criteria)
        if not acceptance_criteria:
            acceptance_criteria = [{
                "id": "AC-001",
                "metric": "goal_satisfied",
                "threshold": True,
                "description": "The outcome described by the user intent is produced and verified.",
            }]

        measurable_targets = dict(goal.measurable_targets)
        if not measurable_targets:
            measurable_targets = {"verification": "all required checks pass"}

        definition_of_done = list(goal.definition_of_done) or [
            "all mandatory requirements pass",
            "all mandatory acceptance criteria pass",
            "QA passes",
            "security checks pass",
            "preview verification passes",
            "critical defects = 0",
        ]

        capabilities = list(goal.required_capabilities)
        if not capabilities:
            capabilities = ["coding"]
            if project_type == "web":
                capabilities.append("preview")
            if "secure" in lowered or "auth" in lowered:
                capabilities.append("security")
            if "error" in lowered or "fault" in lowered:
                capabilities.append("fault_tolerance")
            if "api" in lowered or project_type == "service":
                capabilities.append("api")
            if "verify" in lowered or "verification" in lowered or "qa" in lowered:
                capabilities.append("verification")
            capabilities.append("verification")

        risk_classification = goal.risk_classification
        if risk_classification == "unknown":
            risk_classification = "high" if any(token in lowered for token in ["secure", "sensitive", "production", "confidential"]) else "medium" if any(token in lowered for token in ["fast", "performance", "critical"]) else "low"

        permissions_required = list(goal.permissions_required)
        if "secure" in lowered and not permissions_required:
            permissions_required.append({"id": "PERM-SEC", "permission": "restricted_write_access"})
        if "preview" in lowered and not permissions_required:
            permissions_required.append({"id": "PERM-PREVIEW", "permission": "preview_access"})

        verification_requirements = list(goal.verification_requirements) or [
            {"id": "qa.preview", "status": "NOT_RUN", "kind": "preview"},
            {"id": "qa.functional", "status": "NOT_RUN", "kind": "functional"},
            {"id": "qa.security", "status": "NOT_RUN", "kind": "security"},
            {"id": "qa.performance", "status": "NOT_RUN", "kind": "performance"},
        ]

        chains = cls._requirement_chains(requirements, acceptance_criteria, project_type, verification_requirements)

        return cls(
            project_id=project_id,
            user_intent=intent_text,
            title=title,
            requirements=requirements,
            constraints=constraints,
            assumptions=list(goal.assumptions),
            acceptance_criteria=acceptance_criteria,
            measurable_targets=measurable_targets,
            definition_of_done=definition_of_done,
            project_type=project_type,
            required_capabilities=capabilities,
            risk_classification=risk_classification,
            permissions_required=permissions_required,
            resource_limits=dict(goal.resource_limits),
            dependencies=list(goal.dependencies),
            verification_requirements=verification_requirements,
            chains=chains,
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
