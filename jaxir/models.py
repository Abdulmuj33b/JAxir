"""Machine-readable contracts for JaXir OS.

Locked contracts (RFC-001 / RFC-002 / NFR / AT chains) are expressed as
versioned state objects shared by every subsystem. Implemented on top of a
minimal base model so the kernel is self-contained (Occam's Razor: no
unnecessary dependencies).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class GoalStatus(str, Enum):
    CREATED = "CREATED"
    ANALYZING = "ANALYZING"
    PLANNED = "PLANNED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    FAILED = "FAILED"
    PASSED = "PASSED"
    REPLANNING = "REPLANNING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    ROLLED_BACK = "ROLLED_BACK"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class EventType(str, Enum):
    GOAL_CREATED = "goal.created"
    GOAL_ANALYZED = "goal.analyzed"
    GOAL_PLANNED = "goal.planned"
    GOAL_PAUSED = "goal.paused"
    GOAL_RESUMED = "goal.resumed"
    GOAL_COMPLETED = "goal.completed"
    GOAL_FAILED = "goal.failed"
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    AGENT_STARTED = "agent.started"
    AGENT_PAUSED = "agent.paused"
    AGENT_RESUMED = "agent.resumed"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    BUILD_STARTED = "build.started"
    BUILD_COMPLETED = "build.completed"
    BUILD_FAILED = "build.failed"
    PREVIEW_STARTED = "preview.started"
    PREVIEW_UPDATED = "preview.updated"
    PREVIEW_FAILED = "preview.failed"
    TEST_STARTED = "test.started"
    TEST_PASSED = "test.passed"
    TEST_FAILED = "test.failed"
    QA_STARTED = "qa.started"
    QA_PASSED = "qa.passed"
    QA_FAILED = "qa.failed"
    FEEDBACK_CREATED = "feedback.created"
    FEEDBACK_ACCEPTED = "feedback.accepted"
    CHECKPOINT_CREATED = "checkpoint.created"
    CHECKPOINT_RESTORED = "checkpoint.restored"
    MODEL_REQUESTED = "model.requested"
    MODEL_COMPLETED = "model.completed"
    MODEL_FAILED = "model.failed"
    PROVIDER_HEALTHY = "provider.healthy"
    PROVIDER_DEGRADED = "provider.degraded"
    PROVIDER_EXHAUSTED = "provider.exhausted"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_GRANTED = "permission.granted"
    PERMISSION_DENIED = "permission.denied"


class EvidenceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    PENDING = "PENDING"
    NOT_RUN = "NOT_RUN"


@dataclass
class ModelBase:
    """Minimal base model with to_dict/from_dict and id helpers."""

    @classmethod
    def id_of(cls, value: Any) -> str:
        return str(value) if value else str(uuid.uuid4())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelBase":
        return cls(**data)


@dataclass
class Evidence(ModelBase):
    test_id: str
    evidence_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: EvidenceStatus = EvidenceStatus.NOT_RUN
    severity: str = "info"
    expected: Any = None
    actual: Any = None
    reproduction: Optional[str] = None
    artifacts: List[str] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    environment: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    goal_id: Optional[str] = None
    project_id: Optional[str] = None


@dataclass
class Checkpoint(ModelBase):
    goal_id: str
    project_id: str
    checkpoint_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal_state: Dict[str, Any] = field(default_factory=dict)
    task_state: Dict[str, Any] = field(default_factory=dict)
    agent_state: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    pending_actions: List[Dict[str, Any]] = field(default_factory=list)
    qa_state: Dict[str, Any] = field(default_factory=dict)
    feedback: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    environment_metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Goal(ModelBase):
    project_id: str
    user_intent: str
    goal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    requirements: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[Dict[str, Any]] = field(default_factory=list)
    assumptions: List[Dict[str, Any]] = field(default_factory=list)
    acceptance_criteria: List[Dict[str, Any]] = field(default_factory=list)
    measurable_targets: Dict[str, Any] = field(default_factory=dict)
    definition_of_done: List[str] = field(default_factory=list)
    project_type: str = "generic"
    required_capabilities: List[str] = field(default_factory=list)
    risk_classification: str = "unknown"
    permissions_required: List[Dict[str, Any]] = field(default_factory=list)
    resource_limits: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    task_graph: List[Dict[str, Any]] = field(default_factory=list)
    verification_requirements: List[Dict[str, Any]] = field(default_factory=list)
    status: GoalStatus = GoalStatus.CREATED
    state: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["status"] = self.status.value
        d["goal_id"] = self.goal_id
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        if "status" in data and isinstance(data["status"], str):
            data["status"] = GoalStatus(data["status"])
        return cls(**data)


@dataclass
class Task(ModelBase):
    goal_id: str
    owner_agent_id: str
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = ""
    agent_type: str = ""
    model_id: str = ""
    provider: str = ""
    capabilities: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    dependencies: List[str] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    acceptance_criteria: List[Dict[str, Any]] = field(default_factory=list)
    verification: Optional[Dict[str, Any]] = None
    retry_policy: Dict[str, Any] = field(default_factory=dict)
    failure_state: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        if "status" in data and isinstance(data["status"], str):
            data["status"] = TaskStatus(data["status"])
        return cls(**data)


@dataclass
class Event(ModelBase):
    event_type: EventType
    project_id: str
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    goal_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    source: str = ""
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["event_type"] = self.event_type.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        if "event_type" in data and isinstance(data["event_type"], str):
            data["event_type"] = EventType(data["event_type"])
        return cls(**data)


@dataclass
class AgentContract(ModelBase):
    identity: str
    agent_type: str
    capabilities: List[str]
    status: TaskStatus
    current_task_id: Optional[str] = None
    tools: List[str] = field(default_factory=list)
    environment: Dict[str, Any] = field(default_factory=dict)
    permissions: List[Dict[str, Any]] = field(default_factory=list)
    model_runtime: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    evidence: List[Dict[str, Any]] = field(default_factory=list)
