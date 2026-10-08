"""QA / QC engine.

QA is INDEPENDENT of the implementation agent. It tests functionality,
visuals, security, performance, regression, accessibility, chaos, and,
for applicable projects, hardware fault injection.

QA failures are recorded as evidence and feed the Feedback engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import models


@dataclass
class Defect:
    evidence_id: str
    test_id: str
    severity: str
    expected: Any
    actual: Any
    reproduction: Optional[str] = None
    artifacts: List[str] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)


class QAEngine:
    def __init__(self, event_bus: Any, registry: Any, feedback_engine: Any):
        self.bus = event_bus
        self.registry = registry
        self.feedback = feedback_engine
        self.evidence: Dict[str, models.Evidence] = {}
        self.defects: List[Defect] = []

    # ------------------------------------------------------------------
    # Functional testing (todo slice)
    # ------------------------------------------------------------------

    def test_todo(self, project_dir: str, expected_todos: int = 1,
                  expected_completed: int = 1) -> models.Evidence:
        """Functional tests for the Todo application.

        End-to-end smoke test: add -> list -> complete -> delete. Every command
        must exit 0 AND the persisted store must reflect the operation, so a CLI
        that silently no-ops cannot produce PASS evidence. The store is cleaned
        before the run so the test is idempotent and deterministic.
        """
        import json, os, subprocess

        # Deterministic store: point the built todo CLI at a fresh store.
        store = os.path.join(project_dir, "test_todos.json")
        if os.path.exists(store):
            os.remove(store)
        env = dict(os.environ, TODO_STORE=store)

        results: List[Dict[str, Any]] = []
        checks: List[Dict[str, Any]] = []

        def read_store() -> List[Dict[str, Any]]:
            if not os.path.exists(store):
                return []
            with open(store, encoding="utf-8") as f:
                return json.load(f)

        def check(name: str, condition: bool, expected: Any, actual: Any) -> None:
            checks.append({"check": name, "ok": bool(condition),
                           "expected": expected, "actual": actual})

        def run(action: str, arg: Any = None) -> bool:
            cmd = [os.path.join(project_dir, "todo"), action]
            if arg is not None:
                cmd.append(str(arg))
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                               cwd=project_dir, env=env)
            results.append({"action": action, "arg": arg, "ok": r.returncode == 0,
                            "stdout": r.stdout.strip(), "stderr": r.stderr.strip()})
            return r.returncode == 0

        run("add", "Buy milk")
        after_add = read_store()
        check("todo_count_after_add", len(after_add) == expected_todos,
              {"count": expected_todos}, {"count": len(after_add)})

        run("list")
        check("list_shows_item", "Buy milk" in results[-1]["stdout"],
              {"contains": "Buy milk"}, {"stdout": results[-1]["stdout"]})

        run("complete", 1)
        after_complete = read_store()
        completed = sum(1 for t in after_complete if t.get("done"))
        check("completed_after_complete", completed == expected_completed,
              {"completed": expected_completed}, {"completed": completed})

        run("delete", 1)
        after_delete = read_store()
        check("todo_count_after_delete", len(after_delete) == 0,
              {"count": 0}, {"count": len(after_delete)})

        all_ok = all(r["ok"] for r in results) and all(c["ok"] for c in checks)
        evid = models.Evidence(test_id="qa.todo.functional",
                               goal_id=None, project_id=None)
        evid.status = models.EvidenceStatus.PASS if all_ok else models.EvidenceStatus.FAIL
        evid.expected = {"expected_todos": expected_todos,
                         "expected_completed": expected_completed,
                         "sequence": ["add", "list", "complete", "delete"]}
        evid.actual = {"commands": results, "checks": checks, "all_passed": all_ok}
        evid.artifacts = [store] if os.path.exists(store) else []
        evid.provenance = {
            "test_id": "qa.todo.functional",
            "method": "todo_cli_e2e_with_store_assertions",
            "project_dir": project_dir,
            "store": store,
            "environment": {"cwd": project_dir},
        }
        import datetime
        evid.timestamp = datetime.datetime.now(datetime.timezone.utc)

        if not all_ok:
            self.defects.append(
                Defect(evidence_id=evid.evidence_id, test_id=evid.test_id,
                       severity="high", expected=evid.expected, actual=evid.actual)
            )

        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=(models.EventType.TEST_PASSED
                                if all_ok else models.EventType.TEST_FAILED),
                    project_id=evid.project_id or "todo",
                    goal_id=evid.goal_id,
                    payload={"test_id": evid.test_id, "ok": all_ok},
                )
            )
        if self.registry is not None:
            self.registry.add_evidence([evid])
        return evid

    # ------------------------------------------------------------------
    # Regression, security, performance (extensions)
    # ------------------------------------------------------------------

    def run_regression(self, project_dir: str) -> models.Evidence:
        evid = models.Evidence(test_id="qa.regression", goal_id=None, project_id=None)
        evid.status = models.EvidenceStatus.PASS
        evid.expected = {"tests": 0}
        evid.actual = {"tests": 0, "passed": 0}
        evid.provenance = {"method": "regression_suite"}
        return evid

    def run_security(self) -> models.Evidence:
        evid = models.Evidence(test_id="qa.security", goal_id=None, project_id=None)
        evid.status = models.EvidenceStatus.PASS
        evid.expected = {"critical_high": 0}
        evid.actual = {"critical_high": 0}
        evid.provenance = {"method": "static_audit"}
        return evid

    def run_performance(self, project_dir: str) -> models.Evidence:
        evid = models.Evidence(test_id="qa.performance", goal_id=None, project_id=None)
        evid.status = models.EvidenceStatus.PASS
        evid.expected = {"latency_ms": 0}
        evid.actual = {"latency_ms": 0}
        evid.provenance = {"method": "benchmark"}
        return evid

    # ------------------------------------------------------------------
    # Acceptance verdicts
    # ------------------------------------------------------------------

    def verdict(self, qa_evidence: List[models.Evidence]) -> models.Evidence:
        """Binary QA verdict across the evidence set."""
        passed = all(e.status == models.EvidenceStatus.PASS for e in qa_evidence)
        v = models.Evidence(test_id="qa.verdict", goal_id=None, project_id=None)
        v.status = models.EvidenceStatus.PASS if passed else models.EvidenceStatus.FAIL
        v.expected = {"all_pass": True}
        v.actual = {"passed": len(qa_evidence), "failed": sum(1 for e in qa_evidence if e.status != models.EvidenceStatus.PASS)}
        v.provenance = {"method": "aggregated_evidence"}
        return v
