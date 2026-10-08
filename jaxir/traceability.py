"""Requirement traceability for JaXir OS.

This module implements the locked requirement chain:

    Requirement -> Acceptance Target -> Test -> Evidence -> Release Gate

The goal is to keep engineering work auditable and evidence-backed without
introducing framework complexity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class TraceRecord:
    """Single requirement traceability entry."""

    requirement_id: str
    acceptance_target_id: str
    metric: str
    threshold: str
    measurement_method: str
    test_suite: str
    severity: str = "P1"
    environment: str = "local"
    evidence_required: str = "required"
    release_gate: str = "Gate 0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "acceptance_target_id": self.acceptance_target_id,
            "metric": self.metric,
            "threshold": self.threshold,
            "measurement_method": self.measurement_method,
            "test_suite": self.test_suite,
            "severity": self.severity,
            "environment": self.environment,
            "evidence_required": self.evidence_required,
            "release_gate": self.release_gate,
        }


class RequirementTraceability:
    """Minimal registry of RFCS/NFR/AT mapping for kernel-level auditability."""

    def __init__(self) -> None:
        self.records: List[TraceRecord] = [
            TraceRecord(
                requirement_id="RFC-001",
                acceptance_target_id="AT-001",
                metric="goal/task/event contract integrity",
                threshold="100% auditable state transitions",
                measurement_method="state machine transition audit + event replay",
                test_suite="tests/test_kernel.py",
                severity="P0",
                release_gate="Gate 0",
            ),
            TraceRecord(
                requirement_id="RFC-001",
                acceptance_target_id="AT-GM-001",
                metric="autonomous Todo slice completion",
                threshold=">=90% successful completion across 100 independent runs",
                measurement_method="goal execution loop verification",
                test_suite="tests/test_loop.py",
                severity="P0",
                release_gate="Gate 1",
            ),
            TraceRecord(
                requirement_id="RFC-001",
                acceptance_target_id="AT-PREV-001",
                metric="preview served artifact over HTTP",
                threshold="preview passes and serves page",
                measurement_method="HTTP response + DOM inspection",
                test_suite="tests/test_kernel.py",
                severity="P0",
                release_gate="Gate 2",
            ),
            TraceRecord(
                requirement_id="RFC-001",
                acceptance_target_id="AT-QA-001",
                metric="QA evidence and defect classification",
                threshold="all required tests pass and no blocking defects",
                measurement_method="QA engine verdict + evidence aggregation",
                test_suite="tests/test_qa.py",
                severity="P0",
                release_gate="Gate 2",
            ),
            TraceRecord(
                requirement_id="RFC-001",
                acceptance_target_id="AT-SEC-001",
                metric="security validation",
                threshold="0 critical/high release-blocking findings",
                measurement_method="security suite + sandbox policy tests",
                test_suite="tests/test_sandbox.py",
                severity="P0",
                release_gate="Gate 2",
            ),
            TraceRecord(
                requirement_id="NFR-001",
                acceptance_target_id="AT-001",
                metric="reliability",
                threshold=">=99.5% kernel test reliability target",
                measurement_method="kernel suite execution",
                test_suite="tests/test_kernel.py",
                severity="P1",
                release_gate="Gate 0",
            ),
            TraceRecord(
                requirement_id="NFR-025",
                acceptance_target_id="AT-001",
                metric="context efficiency",
                threshold="context reduction target >=50% while retaining required info",
                measurement_method="context compiler compression trace",
                test_suite="tests/test_phase3.py",
                severity="P1",
                release_gate="Gate 3",
            ),
        ]

    def add(self, record: TraceRecord) -> None:
        self.records.append(record)

    def by_requirement(self, requirement_id: str) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.records if r.requirement_id == requirement_id]

    def by_target(self, acceptance_target_id: str) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.records if r.acceptance_target_id == acceptance_target_id]

    def list(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.records]

    def summary(self) -> Dict[str, Any]:
        return {
            "count": len(self.records),
            "requirements": sorted({r.requirement_id for r in self.records}),
            "acceptance_targets": sorted({r.acceptance_target_id for r in self.records}),
            "release_gates": sorted({r.release_gate for r in self.records}),
        }


def default_traceability() -> RequirementTraceability:
    return RequirementTraceability()
