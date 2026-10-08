"""QA / QC engine.

QA is INDEPENDENT of the implementation agent. It tests functionality,
visuals, security, performance, regression, accessibility, chaos, and,
for applicable projects, hardware fault injection.

QA failures are recorded as evidence and feed the Feedback engine.

Suites are either real or explicitly declared missing - see
``QAEngine.suite_coverage()``. A suite that is not implemented reports
NOT_RUN, never PASS.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


@dataclass
class AuditRule:
    """A static security rule applied to built artifacts."""

    rule_id: str
    severity: str
    pattern: str
    description: str
    flags: int = 0


class QAEngine:
    #: Static audit rule set. Version is recorded in every security evidence.
    AUDIT_RULESET_VERSION = "1"
    AUDIT_RULES: Tuple[AuditRule, ...] = (
        AuditRule("SEC-001", "critical", r"\bshell\s*=\s*True",
                  "subprocess invoked with shell=True (command injection surface)"),
        AuditRule("SEC-002", "high", r"\bos\.system\s*\(",
                  "os.system() executes a shell command"),
        AuditRule("SEC-003", "high", r"\bos\.popen\s*\(",
                  "os.popen() executes a shell command"),
        AuditRule("SEC-004", "critical", r"(?<![\w.])eval\s*\(",
                  "eval() executes arbitrary code"),
        AuditRule("SEC-005", "critical", r"(?<![\w.])exec\s*\(",
                  "exec() executes arbitrary code"),
        AuditRule("SEC-006", "high", r"\bpickle\.loads?\s*\(",
                  "unsafe deserialization via pickle"),
        AuditRule("SEC-007", "high", r"\byaml\.load\s*\(",
                  "yaml.load without a safe loader"),
        AuditRule("SEC-008", "medium", r"verify\s*=\s*False",
                  "TLS certificate verification disabled"),
        AuditRule("SEC-009", "critical",
                  r"(?i)\b(password|passwd|secret|api_key|apikey|token|private_key)"
                  r"\s*[:=]\s*[\"'][^\"']{4,}[\"']",
                  "hardcoded credential in source"),
        AuditRule("SEC-010", "medium",
                  r"<script[^>]+src\s*=\s*[\"']https?://",
                  "remote script include (supply-chain surface)"),
        AuditRule("SEC-011", "medium", r"\b__import__\s*\(",
                  "dynamic import"),
    )

    #: Suites with real implementations.
    IMPLEMENTED_SUITES: Tuple[str, ...] = (
        "functional", "regression", "security", "performance", "preview",
    )
    #: Suites required by constitution section 16 with no implementation yet.
    NOT_IMPLEMENTED_SUITES: Tuple[str, ...] = (
        "visual", "accessibility", "chaos", "adversarial",
        "synthetic_user", "hardware", "embedded_fault_injection",
    )

    #: Budgets (ms). The event-propagation budget is the locked AT target.
    CLI_P95_BUDGET_MS = 1000.0
    EVENT_P95_BUDGET_MS = 250.0

    #: Artifact files the static audit must not scan (QA's own fixtures).
    AUDIT_SKIP_PREFIXES = ("test_",)

    def __init__(self, event_bus: Any, registry: Any, feedback_engine: Any,
                 goal_id: Optional[str] = None,
                 project_id: Optional[str] = None):
        self.bus = event_bus
        self.registry = registry
        self.feedback = feedback_engine
        self.goal_id = goal_id
        self.project_id = project_id
        self.evidence: Dict[str, models.Evidence] = {}
        self.defects: List[Defect] = []

    # ------------------------------------------------------------------
    # Suite coverage disclosure
    # ------------------------------------------------------------------

    def suite_coverage(self) -> Dict[str, Any]:
        """What QA actually runs today, and what is missing (constitution 16)."""
        return {
            "implemented": list(self.IMPLEMENTED_SUITES),
            "not_implemented": list(self.NOT_IMPLEMENTED_SUITES),
            "complete": not self.NOT_IMPLEMENTED_SUITES,
        }

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
        evid.timestamp = datetime.datetime.now(datetime.timezone.utc)

        if not all_ok:
            self.defects.append(
                Defect(evidence_id=evid.evidence_id, test_id=evid.test_id,
                       severity="high", expected=evid.expected, actual=evid.actual)
            )
        return self._record(evid)

    # ------------------------------------------------------------------
    # Regression: golden-contract replay of the built artifact
    # ------------------------------------------------------------------

    #: The CLI's behavioural contract. A change here is a regression.
    GOLDEN_CONTRACT: Tuple[Dict[str, Any], ...] = (
        {"name": "list_empty_reports_no_todos", "args": ["list"],
         "returncode": 0, "stdout_contains": ["no todos"], "store": {"len": 0}},
        {"name": "add_creates_first_item", "args": ["add", "Buy milk"],
         "returncode": 0, "stdout_contains": ["Buy milk"],
         "store": {"len": 1, "ids": [1], "done": 0}},
        {"name": "add_assigns_next_id", "args": ["add", "Ship it"],
         "returncode": 0, "store": {"len": 2, "ids": [1, 2]}},
        {"name": "list_shows_both", "args": ["list"],
         "returncode": 0, "stdout_contains": ["Buy milk", "Ship it"]},
        {"name": "complete_marks_done_without_removing",
         "args": ["complete", "1"], "returncode": 0, "stdout_contains": ["1"],
         "store": {"len": 2, "done": 1, "ids": [1, 2]}},
        {"name": "complete_is_idempotent_on_repeat", "args": ["complete", "1"],
         "returncode": 0, "store": {"len": 2, "done": 1}},
        {"name": "delete_removes_only_target", "args": ["delete", "1"],
         "returncode": 0, "store": {"len": 1, "ids": [2]}},
        {"name": "complete_unknown_id_fails", "args": ["complete", "999"],
         "returncode": 1, "store": {"len": 1, "ids": [2]}},
        {"name": "delete_unknown_id_fails", "args": ["delete", "999"],
         "returncode": 1, "store": {"len": 1, "ids": [2]}},
        {"name": "add_after_delete_does_not_reuse_id",
         "args": ["add", "Third"], "returncode": 0,
         "store": {"len": 2, "ids": [2, 3]}},
    )

    def run_regression(self, project_dir: str) -> models.Evidence:
        """Replay the artifact's behavioural contract and diff the results.

        Detects regressions in the built artifact: a change in CLI semantics,
        id assignment, or error handling shows up as a failed case.
        """
        store = os.path.join(project_dir, "regression_todos.json")
        if os.path.exists(store):
            os.remove(store)
        env = dict(os.environ, TODO_STORE=store)
        binary = os.path.join(project_dir, "todo")

        cases: List[Dict[str, Any]] = []

        def read_store() -> List[Dict[str, Any]]:
            if not os.path.exists(store):
                return []
            try:
                with open(store, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return []

        def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
            outcome: Dict[str, Any] = {"name": case["name"], "ok": True,
                                       "failures": []}
            if not os.path.exists(binary):
                outcome["ok"] = False
                outcome["failures"].append(f"artifact missing: {binary}")
                return outcome
            try:
                r = subprocess.run([binary, *case["args"]], capture_output=True,
                                   text=True, timeout=10, cwd=project_dir, env=env)
                rc, out = r.returncode, (r.stdout + r.stderr).strip()
            except subprocess.TimeoutExpired:
                outcome["ok"] = False
                outcome["failures"].append("timed out after 10s")
                return outcome
            outcome["returncode"] = rc

            if rc != case["returncode"]:
                outcome["ok"] = False
                outcome["failures"].append(
                    f"exit code {rc} != contract {case['returncode']}")
            for needle in case.get("stdout_contains", ()):
                if needle not in out:
                    outcome["ok"] = False
                    outcome["failures"].append(f"output missing {needle!r}")

            expected_store = case.get("store")
            if expected_store:
                items = read_store()
                if "len" in expected_store and len(items) != expected_store["len"]:
                    outcome["ok"] = False
                    outcome["failures"].append(
                        f"store len {len(items)} != {expected_store['len']}")
                if "ids" in expected_store:
                    ids = [t.get("id") for t in items]
                    if ids != expected_store["ids"]:
                        outcome["ok"] = False
                        outcome["failures"].append(
                            f"store ids {ids} != {expected_store['ids']}")
                if "done" in expected_store:
                    done = sum(1 for t in items if t.get("done"))
                    if done != expected_store["done"]:
                        outcome["ok"] = False
                        outcome["failures"].append(
                            f"store done {done} != {expected_store['done']}")
            return outcome

        for case in self.GOLDEN_CONTRACT:
            cases.append(run_case(case))

        passed = [c for c in cases if c["ok"]]
        failed = [c for c in cases if not c["ok"]]
        evid = models.Evidence(test_id="qa.regression")
        evid.status = (models.EvidenceStatus.PASS if not failed
                       else models.EvidenceStatus.FAIL)
        evid.expected = {"cases": len(self.GOLDEN_CONTRACT), "failed": 0}
        evid.actual = {"cases": len(cases), "passed": len(passed),
                       "failed": len(failed), "failures": failed}
        evid.artifacts = [binary] if os.path.exists(binary) else []
        evid.provenance = {"test_id": "qa.regression",
                           "method": "golden_contract_replay",
                           "contract_cases": len(self.GOLDEN_CONTRACT),
                           "project_dir": project_dir}
        if failed:
            self.defects.append(
                Defect(evidence_id=evid.evidence_id, test_id=evid.test_id,
                       severity="high", expected=evid.expected,
                       actual={"failures": failed},
                       reproduction="python3 -m pytest tests/test_kernel.py")
            )
        return self._record(evid)

    # ------------------------------------------------------------------
    # Security: static audit of the built artifacts
    # ------------------------------------------------------------------

    def run_security(self, project_dir: str) -> models.Evidence:
        """Static audit of the built artifacts against ``AUDIT_RULES``.

        Release-blocking severity is critical/high, matching the locked target of
        0 critical/high findings. This is a pattern audit of the artifact's own
        source - not a substitute for a full SAST toolchain.
        """
        findings: List[Dict[str, Any]] = []
        audited: List[str] = []

        for path in sorted(Path(project_dir).iterdir()):
            if not path.is_file():
                continue
            if path.name.startswith(self.AUDIT_SKIP_PREFIXES):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binary artifact: not source-auditable
            audited.append(str(path))

            for rule in self.AUDIT_RULES:
                for lineno, line in enumerate(text.splitlines(), 1):
                    if re.search(rule.pattern, line, rule.flags):
                        findings.append({
                            "rule": rule.rule_id, "severity": rule.severity,
                            "file": str(path), "line": lineno,
                            "description": rule.description,
                            "evidence": line.strip()[:200],
                        })

            # Permissions: artifact must not be world-writable.
            mode = path.stat().st_mode
            if mode & stat.S_IWOTH:
                findings.append({
                    "rule": "SEC-050", "severity": "high",
                    "file": str(path), "line": 0,
                    "description": "artifact is world-writable",
                    "evidence": oct(mode & 0o777),
                })

        blocking = [f for f in findings if f["severity"] in ("critical", "high")]
        by_severity: Dict[str, int] = {}
        for f in findings:
            by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

        evid = models.Evidence(test_id="qa.security")
        evid.status = (models.EvidenceStatus.PASS if not blocking
                       else models.EvidenceStatus.FAIL)
        evid.expected = {"critical_high": 0, "ruleset": self.AUDIT_RULESET_VERSION}
        evid.actual = {"findings": len(findings), "by_severity": by_severity,
                       "blocking": blocking, "audited": audited}
        evid.artifacts = audited
        evid.provenance = {"test_id": "qa.security", "method": "static_audit",
                           "ruleset_version": self.AUDIT_RULESET_VERSION,
                           "rules": [r.rule_id for r in self.AUDIT_RULES],
                           "project_dir": project_dir}
        if blocking:
            self.defects.append(
                Defect(evidence_id=evid.evidence_id, test_id=evid.test_id,
                       severity=blocking[0]["severity"], expected=evid.expected,
                       actual={"blocking": blocking},
                       artifacts=audited)
            )
        return self._record(evid)

    # ------------------------------------------------------------------
    # Performance: measured latency against explicit budgets
    # ------------------------------------------------------------------

    def run_performance(self, project_dir: str, iterations: int = 15,
                        events: int = 200) -> models.Evidence:
        """Measure real latency: artifact CLI cold-start and event propagation.

        The event budget is the locked acceptance target (<=250 ms). Measured
        values are recorded, so evidence carries data rather than a zero.
        """
        binary = os.path.join(project_dir, "todo")
        store = os.path.join(project_dir, "perf_todos.json")
        env = dict(os.environ, TODO_STORE=store)
        cli_samples: List[float] = []

        if os.path.exists(binary):
            for _ in range(iterations):
                start = time.perf_counter()
                try:
                    subprocess.run([binary, "list"], capture_output=True,
                                   text=True, timeout=10, cwd=project_dir, env=env)
                except subprocess.TimeoutExpired:
                    continue
                cli_samples.append((time.perf_counter() - start) * 1000)

        event_samples: List[float] = []
        if self.bus is not None:
            seen = {"n": 0}

            def _subscriber(event: Any) -> None:
                seen["n"] += 1

            self.bus.subscribe(_subscriber)
            try:
                for i in range(events):
                    start = time.perf_counter()
                    self.bus.publish(
                        models.Event(event_type=models.EventType.TEST_STARTED,
                                     project_id="qa.performance",
                                     payload={"seq": i})
                    )
                    event_samples.append((time.perf_counter() - start) * 1000)
            finally:
                self.bus.unsubscribe(_subscriber)

        cli_stats = _percentiles(cli_samples)
        event_stats = _percentiles(event_samples)
        cli_ok = bool(cli_samples) and cli_stats["p95"] <= self.CLI_P95_BUDGET_MS
        event_ok = (bool(event_samples)
                    and event_stats["p95"] <= self.EVENT_P95_BUDGET_MS)
        passed = cli_ok and event_ok

        evid = models.Evidence(test_id="qa.performance")
        evid.status = (models.EvidenceStatus.PASS if passed
                       else models.EvidenceStatus.FAIL)
        evid.expected = {
            "cli_p95_ms": self.CLI_P95_BUDGET_MS,
            "event_p95_ms": self.EVENT_P95_BUDGET_MS,
        }
        evid.actual = {
            "cli": {**cli_stats, "budget_ms": self.CLI_P95_BUDGET_MS,
                    "within_budget": cli_ok, "samples": len(cli_samples)},
            "event": {**event_stats, "budget_ms": self.EVENT_P95_BUDGET_MS,
                      "within_budget": event_ok, "samples": len(event_samples)},
        }
        evid.provenance = {"test_id": "qa.performance",
                           "method": "measured_latency",
                           "iterations": iterations, "events": events,
                           "project_dir": project_dir}
        if not passed:
            self.defects.append(
                Defect(evidence_id=evid.evidence_id, test_id=evid.test_id,
                       severity="medium", expected=evid.expected,
                       actual=evid.actual)
            )
        return self._record(evid)

    # ------------------------------------------------------------------
    # Acceptance verdicts
    # ------------------------------------------------------------------

    def verdict(self, qa_evidence: List[models.Evidence]) -> models.Evidence:
        """Binary QA verdict across the evidence set."""
        passed = all(e.status == models.EvidenceStatus.PASS for e in qa_evidence)
        v = models.Evidence(test_id="qa.verdict", goal_id=None, project_id=None)
        v.status = models.EvidenceStatus.PASS if passed else models.EvidenceStatus.FAIL
        v.expected = {"all_pass": True}
        v.actual = {"passed": len(qa_evidence),
                    "failed": sum(1 for e in qa_evidence
                                  if e.status != models.EvidenceStatus.PASS),
                    "evidence": [e.test_id for e in qa_evidence]}
        v.provenance = {"method": "aggregated_evidence",
                        "coverage": self.suite_coverage()}
        return v

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _record(self, evid: models.Evidence) -> models.Evidence:
        """Stamp goal context, publish qa.started + the result, index it."""
        evid.goal_id = evid.goal_id or self.goal_id
        evid.project_id = evid.project_id or self.project_id
        ok = evid.status == models.EvidenceStatus.PASS
        if self.bus is not None:
            self.bus.publish(
                models.Event(event_type=models.EventType.QA_STARTED,
                             project_id=evid.project_id or "todo",
                             goal_id=evid.goal_id,
                             payload={"test_id": evid.test_id})
            )
            self.bus.publish(
                models.Event(
                    event_type=(models.EventType.TEST_PASSED if ok
                                else models.EventType.TEST_FAILED),
                    project_id=evid.project_id or "todo",
                    goal_id=evid.goal_id,
                    payload={"test_id": evid.test_id, "ok": ok,
                             "severity": evid.severity},
                )
            )
        self.evidence[evid.test_id] = evid
        if self.registry is not None:
            self.registry.add_evidence([evid])
        return evid


def _percentiles(samples: List[float]) -> Dict[str, float]:
    """Nearest-rank percentiles; zeroed when there are no samples."""
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0, "min": 0.0, "mean": 0.0}
    ordered = sorted(samples)

    def rank(pct: float) -> float:
        idx = min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))
        return ordered[idx]

    return {
        "p50": round(rank(0.50), 3),
        "p95": round(rank(0.95), 3),
        "max": round(ordered[-1], 3),
        "min": round(ordered[0], 3),
        "mean": round(sum(ordered) / len(ordered), 3),
    }
