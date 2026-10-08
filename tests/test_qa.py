"""QA suite tests.

Constitution sections 16/17/19: QA is independent of the implementation agent,
evidence is first-class, and a suite that is not implemented must not report
PASS. These tests assert the evidence is real - i.e. that each suite fails when
the artifact under test is genuinely broken.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from jaxir import models
from jaxir.events import EventBus
from jaxir.qa import QAEngine
from jaxir.todoslice import TodoCoder, WebTodoApp


@pytest.fixture()
def project(tmp_path):
    """A freshly built, healthy todo artifact."""
    workdir = tmp_path / "todo_slice"
    TodoCoder.write(workdir)
    WebTodoApp.write(workdir)
    return workdir


def engine():
    return QAEngine(EventBus(), None, None)


class TestRegressionSuite:
    def test_healthy_artifact_passes_contract(self, project):
        ev = engine().run_regression(str(project))
        assert ev.status == models.EvidenceStatus.PASS
        assert ev.actual["failed"] == 0
        assert ev.actual["passed"] == len(QAEngine.GOLDEN_CONTRACT)
        assert ev.provenance["method"] == "golden_contract_replay"

    def test_every_contract_case_is_exercised(self, project):
        ev = engine().run_regression(str(project))
        assert ev.actual["cases"] == len(QAEngine.GOLDEN_CONTRACT)

    def test_unstable_id_assignment_is_a_regression(self, project):
        """A CLI that reuses ids breaks the contract and must be caught."""
        (project / "todo").write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "store = os.environ['TODO_STORE']\n"
            "def load():\n"
            "    return json.load(open(store)) if os.path.exists(store) else []\n"
            "def save(t):\n"
            "    json.dump(t, open(store, 'w'))\n"
            "cmd, *rest = sys.argv[1:]\n"
            "t = load()\n"
            "if cmd == 'add':\n"
            "    t.append({'id': 1, 'text': rest[0], 'done': False}); save(t)\n"
            "elif cmd == 'list':\n"
            "    print('(no todos)' if not t else 'ok')\n"
            "elif cmd in ('complete', 'delete'):\n"
            "    pass\n"
        )
        (project / "todo").chmod(0o755)
        ev = engine().run_regression(str(project))
        assert ev.status == models.EvidenceStatus.FAIL
        assert ev.actual["failed"] > 0
        names = " ".join(f["name"] for f in ev.actual["failures"])
        assert "add_assigns_next_id" in names

    def test_missing_artifact_fails(self, tmp_path):
        ev = engine().run_regression(str(tmp_path))
        assert ev.status == models.EvidenceStatus.FAIL

    def test_regression_defect_is_recorded(self, tmp_path):
        eng = engine()
        eng.run_regression(str(tmp_path))
        assert any(d.test_id == "qa.regression" for d in eng.defects)


class TestSecuritySuite:
    def test_healthy_artifact_has_no_blocking_findings(self, project):
        ev = engine().run_security(str(project))
        assert ev.status == models.EvidenceStatus.PASS
        assert ev.actual["blocking"] == []
        assert ev.provenance["method"] == "static_audit"
        assert ev.provenance["ruleset_version"] == QAEngine.AUDIT_RULESET_VERSION
        assert ev.actual["audited"], "must actually audit the artifacts"

    @pytest.mark.parametrize("payload,rule", [
        ("import os\nos.system('rm -rf /')\n", "SEC-002"),
        ("eval('1+1')\n", "SEC-004"),
        ("import pickle\npickle.loads(b'')\n", "SEC-006"),
        ("password = 'hunter2secret'\n", "SEC-009"),
        ("import subprocess\nsubprocess.run('ls', shell=True)\n", "SEC-001"),
    ])
    def test_dangerous_patterns_are_detected(self, project, payload, rule):
        (project / "danger.py").write_text(payload)
        ev = engine().run_security(str(project))
        assert ev.status == models.EvidenceStatus.FAIL
        found = {f["rule"] for f in ev.actual["blocking"]}
        assert rule in found

    def test_world_writable_artifact_detected(self, project):
        (project / "loose.py").write_text("x = 1\n")
        (project / "loose.py").chmod(0o666)
        ev = engine().run_security(str(project))
        assert ev.status == models.EvidenceStatus.FAIL
        assert any(f["rule"] == "SEC-050" for f in ev.actual["blocking"])

    def test_findings_carry_file_and_line(self, project):
        (project / "danger.py").write_text("# ok\nimport os\nos.system('x')\n")
        ev = engine().run_security(str(project))
        hit = [f for f in ev.actual["blocking"] if f["rule"] == "SEC-002"][0]
        assert hit["line"] == 3
        assert hit["file"].endswith("danger.py")

    def test_test_fixtures_are_not_audited_as_artifacts(self, project):
        # QA's own fixture file would otherwise be reported as a finding.
        (project / "test_todos.json").write_text('[{"id":1}]')
        ev = engine().run_security(str(project))
        assert not any("test_todos.json" in f["file"] for f in ev.actual["blocking"])


class TestPerformanceSuite:
    def test_records_measured_values_not_zeros(self, project):
        ev = engine().run_performance(str(project), iterations=3)
        assert ev.actual["cli"]["max"] > 0
        assert ev.actual["cli"]["samples"] == 3
        assert ev.provenance["method"] == "measured_latency"

    def test_event_budget_is_the_locked_target(self, project):
        ev = engine().run_performance(str(project), iterations=2, events=50)
        assert ev.expected["event_p95_ms"] == 250.0
        assert ev.actual["event"]["samples"] == 50

    def test_healthy_artifact_within_budget(self, project):
        ev = engine().run_performance(str(project), iterations=3)
        assert ev.status == models.EvidenceStatus.PASS
        assert ev.actual["cli"]["within_budget"] is True

    def test_slow_artifact_exceeds_budget(self, project):
        (project / "todo").write_text(
            "#!/usr/bin/env python3\nimport time\ntime.sleep(1.2)\n")
        (project / "todo").chmod(0o755)
        ev = engine().run_performance(str(project), iterations=3)
        assert ev.status == models.EvidenceStatus.FAIL
        assert ev.actual["cli"]["within_budget"] is False


class TestSuiteCoverageDisclosure:
    def test_not_implemented_suites_are_declared(self):
        coverage = engine().suite_coverage()
        assert coverage["complete"] is False
        assert "visual" in coverage["not_implemented"]
        assert "accessibility" in coverage["not_implemented"]
        assert "hardware" in coverage["not_implemented"]

    def test_verdict_carries_coverage_so_gaps_are_visible(self):
        eng = engine()
        eng.run_regression(str(Path(tempfile.mkdtemp())))
        v = eng.verdict([])
        assert v.provenance["coverage"]["complete"] is False


class TestEvidenceContract:
    def test_goal_context_is_stamped_on_evidence_and_events(self, project):
        bus = EventBus()
        eng = QAEngine(bus, None, None, goal_id="g-1", project_id="p-1")
        ev = eng.run_regression(str(project))
        assert ev.goal_id == "g-1" and ev.project_id == "p-1"
        passed = bus.get(event_type=models.EventType.TEST_PASSED)
        assert passed and passed[0].goal_id == "g-1"

    def test_each_suite_emits_qa_started(self, project):
        bus = EventBus()
        eng = QAEngine(bus, None, None)
        eng.run_regression(str(project))
        eng.run_security(str(project))
        eng.run_performance(str(project), iterations=1, events=1)
        assert bus.count(models.EventType.QA_STARTED) == 3

    def test_provenance_present_on_every_suite(self, project):
        eng = engine()
        for ev in (eng.run_regression(str(project)),
                   eng.run_security(str(project)),
                   eng.run_performance(str(project), iterations=1, events=1)):
            assert ev.provenance, f"{ev.test_id} lacks provenance"


class TestDefinitionOfDoneIntegrity:
    """Section 19: COMPLETED requires all evidence PASS, 0 critical defects,
    provenance present - never an agent claim or a successful build."""

    def test_goal_completes_with_all_evidence_passing(self, tmp_path):
        from jaxir.todoslice import TodoApp
        goal = TodoApp(str(tmp_path)).build()
        assert goal.status == models.GoalStatus.COMPLETED
        assert goal.state["definition_of_done"]["all_evidence_passed"] is True
        assert goal.state["definition_of_done"]["critical_defects"] == 0

    def test_security_finding_blocks_completion(self, tmp_path):
        """A critical artifact finding must prevent COMPLETED."""
        from jaxir.todoslice import TodoApp

        app = TodoApp(str(tmp_path))
        original = QAEngine.run_security

        def tainted(self, project_dir):
            ev = original(self, project_dir)
            ev.status = models.EvidenceStatus.FAIL
            return ev

        QAEngine.run_security = tainted
        try:
            goal = app.build()
        finally:
            QAEngine.run_security = original
        assert goal.status == models.GoalStatus.FAILED

    def test_blocking_defect_blocks_completion(self, tmp_path):
        from jaxir.todoslice import TodoApp

        app = TodoApp(str(tmp_path))
        original = QAEngine.run_performance

        def tainted(self, project_dir, **kwargs):
            ev = original(self, project_dir, **kwargs)
            from jaxir.qa import Defect
            self.defects.append(Defect(
                evidence_id=ev.evidence_id, test_id=ev.test_id,
                severity="critical", expected={}, actual={}))
            return ev

        QAEngine.run_performance = tainted
        try:
            goal = app.build()
        finally:
            QAEngine.run_performance = original
        assert goal.status == models.GoalStatus.FAILED
        assert goal.state["definition_of_done"]["critical_defects"] >= 1
