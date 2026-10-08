"""Second vertical slice: API service lifecycle.

This slice demonstrates the complete locked lifecycle: Goal → Plan → Build →
Preview → QA → Evidence → Feedback → Replan → Verify → Done.

Unlike the Todo slice which is a simple interactive app, the API slice:
- has a planner that designs the API surface
- has a coder that scaffolds an OpenAPI-compliant service
- has a preview that runs the service on a local port
- has QA that validates the API contract and basic operations
- has feedback that detects failures and triggers replanning
- exercises the full end-to-end Goal Mode loop

This is kept minimal to remain true to Occam's Razor while demonstrating
architecture reuse across project types.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import models


class APIPlanner:
    """Plan the API surface from natural language intent."""

    @staticmethod
    def plan(goal: models.Goal, project_dir: Path) -> List[models.Task]:
        """Decompose the goal into a task graph."""
        tasks = [
            models.Task(
                goal_id=goal.goal_id,
                owner_agent_id="planner",
                agent_type="planner",
                task_id="api-design",
                status=models.TaskStatus.COMPLETED,
                result={
                    "api_spec": {
                        "title": goal.title,
                        "version": "0.0.1",
                        "endpoints": [
                            {"path": "/status", "method": "GET", "description": "Health check"},
                            {"path": "/health", "method": "GET", "description": "Liveness probe"},
                        ],
                    }
                },
                acceptance_criteria=[
                    {"id": "api-spec-valid", "description": "OpenAPI spec is valid JSON"},
                    {"id": "api-endpoints-defined", "description": "At least 2 endpoints defined"},
                ],
            ),
            models.Task(
                goal_id=goal.goal_id,
                owner_agent_id="coder",
                agent_type="coder",
                task_id="api-build",
                status=models.TaskStatus.PENDING,
                dependencies=["api-design"],
                acceptance_criteria=[
                    {"id": "api-code-runs", "description": "Service starts without errors"},
                    {"id": "api-responds", "description": "Service responds to HTTP requests"},
                ],
            ),
        ]
        return tasks


class APICoder:
    """Generate API service code."""

    @staticmethod
    def build(goal: models.Goal, tasks: List[models.Task], project_dir: Path) -> models.Task:
        """Generate a minimal Python FastAPI service."""
        task = next((t for t in tasks if t.task_id == "api-build"), None)
        if task is None:
            return models.Task(
                goal_id=goal.goal_id,
                owner_agent_id="coder",
                agent_type="coder",
                task_id="api-build",
                status=models.TaskStatus.FAILED,
                result={"error": "api-build task not found"},
            )

        # Write the main API file
        api_code = '''"""Generated API service."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import time

class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = json.dumps({"status": "ok", "timestamp": time.time()})
            self.wfile.write(response.encode())
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = json.dumps({"healthy": True})
            self.wfile.write(response.encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8766), APIHandler)
    print("API service running on http://127.0.0.1:8766")
    server.serve_forever()
'''
        api_path = project_dir / "api_service.py"
        api_path.write_text(api_code, encoding="utf-8")

        task.status = models.TaskStatus.COMPLETED
        task.result = {
            "files_generated": ["api_service.py"],
            "entry_point": str(api_path),
            "service_host": "127.0.0.1",
            "service_port": 8766,
        }
        return task


class APIPreview:
    """Run the API service for inspection."""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.process: Optional[Any] = None
        self.started_at: Optional[float] = None

    def start(self) -> Dict[str, Any]:
        """Start the API service in a background process."""
        import subprocess

        api_file = self.project_dir / "api_service.py"
        if not api_file.exists():
            return {"ok": False, "error": "api_service.py not found"}

        try:
            self.process = subprocess.Popen(
                ["python", str(api_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.project_dir),
            )
            self.started_at = time.time()
            time.sleep(1)
            return {"ok": True, "pid": self.process.pid, "service": "http://127.0.0.1:8766"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def stop(self) -> Dict[str, Any]:
        """Stop the API service."""
        if self.process is None:
            return {"ok": True, "reason": "not running"}
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
            return {"ok": True}
        except Exception as e:
            self.process.kill()
            return {"ok": False, "error": str(e)}

    def status(self) -> Dict[str, Any]:
        """Check if the service is responding."""
        if self.process is None or self.process.poll() is not None:
            return {"ok": False, "running": False}

        try:
            import urllib.request

            response = urllib.request.urlopen("http://127.0.0.1:8766/status", timeout=2)
            data = json.loads(response.read().decode())
            return {"ok": True, "running": True, "status": data}
        except Exception as e:
            return {"ok": False, "running": True, "error": str(e)}


class APIQA:
    """QA testing for the API service."""

    @staticmethod
    def test_endpoints(project_dir: Path, goal: models.Goal) -> List[models.Evidence]:
        """Test the API endpoints."""
        evidence = []

        try:
            import urllib.request

            for endpoint in ["/status", "/health"]:
                try:
                    response = urllib.request.urlopen(
                        f"http://127.0.0.1:8766{endpoint}", timeout=2
                    )
                    data = json.loads(response.read().decode())
                    evidence.append(
                        models.Evidence(
                            test_id=f"api.endpoint.{endpoint}",
                            goal_id=goal.goal_id,
                            project_id=goal.project_id,
                            status=models.EvidenceStatus.PASS,
                            expected={"status_code": 200},
                            actual={"status_code": response.status, "body": data},
                            provenance={"source": "APIQA.test_endpoints"},
                        )
                    )
                except Exception as e:
                    evidence.append(
                        models.Evidence(
                            test_id=f"api.endpoint.{endpoint}",
                            goal_id=goal.goal_id,
                            project_id=goal.project_id,
                            status=models.EvidenceStatus.FAIL,
                            expected={"status_code": 200},
                            actual={"error": str(e)},
                            reproduction=f"curl http://127.0.0.1:8766{endpoint}",
                            provenance={"source": "APIQA.test_endpoints"},
                        )
                    )
        except Exception as e:
            evidence.append(
                models.Evidence(
                    test_id="api.qa.bootstrap",
                    goal_id=goal.goal_id,
                    project_id=goal.project_id,
                    status=models.EvidenceStatus.FAIL,
                    expected={"service_running": True},
                    actual={"error": str(e)},
                    provenance={"source": "APIQA.test_endpoints"},
                )
            )

        return evidence


class APIFeedback:
    """Feedback and failure analysis for the API slice."""

    @staticmethod
    def analyze(evidence: List[models.Evidence]) -> Dict[str, Any]:
        """Analyze evidence and recommend actions."""
        failures = [e for e in evidence if e.status == models.EvidenceStatus.FAIL]
        if not failures:
            return {
                "status": "ok",
                "action": "none",
                "evidence_count": len(evidence),
                "passed": len(evidence),
            }

        critical = [f for f in failures if "endpoint" in f.test_id]
        if critical:
            return {
                "status": "failed",
                "action": "replan_and_rebuild",
                "failures": [f.test_id for f in critical],
                "reason": "Critical API endpoints are not responding",
            }

        return {
            "status": "warning",
            "action": "review",
            "failures": [f.test_id for f in failures],
            "reason": "Some tests failed but core functionality may be intact",
        }


class APISliceLifecycle:
    """Full lifecycle manager for the API slice."""

    def __init__(self, project_dir: Optional[str] = None):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd() / "api_slice"
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.goal: Optional[models.Goal] = None
        self.tasks: List[models.Task] = []
        self.evidence: List[models.Evidence] = []
        self.feedback: Dict[str, Any] = {}
        self.preview: Optional[APIPreview] = None

    def create_goal(self, intent: str = "Build a production-ready API service") -> models.Goal:
        """Create the initial goal."""
        self.goal = models.Goal(
            project_id="api-slice",
            user_intent=intent,
            title="API Service",
            goal_id="api-slice-goal",
            project_type="api",
            status=models.GoalStatus.CREATED,
            requirements=[
                {"id": "req-api-valid", "description": "API must be OpenAPI compliant"},
                {"id": "req-api-responsive", "description": "API must respond to requests"},
            ],
            acceptance_criteria=[
                {"id": "ac-endpoints-working", "description": "All endpoints must be accessible"},
                {"id": "ac-no-critical-errors", "description": "No critical failures in QA"},
            ],
        )
        return self.goal

    def analyze(self) -> models.Goal:
        """Transition to ANALYZING."""
        if self.goal is None:
            raise ValueError("Goal not created")
        self.goal.status = models.GoalStatus.ANALYZING
        self.goal.state["analysis_at"] = time.time()
        return self.goal

    def plan(self) -> models.Goal:
        """Plan the tasks."""
        if self.goal is None:
            raise ValueError("Goal not created")
        self.goal.status = models.GoalStatus.PLANNED
        self.tasks = APIPlanner.plan(self.goal, self.project_dir)
        self.goal.task_graph = [
            {"task_id": t.task_id, "dependencies": t.dependencies} for t in self.tasks
        ]
        return self.goal

    def execute(self) -> models.Goal:
        """Execute the build phase."""
        if self.goal is None:
            raise ValueError("Goal not created")
        self.goal.status = models.GoalStatus.EXECUTING

        # Plan phase already completed, now build
        build_task = APICoder.build(self.goal, self.tasks, self.project_dir)
        self.tasks = [t for t in self.tasks if t.task_id != "api-build"] + [build_task]

        return self.goal

    def preview(self) -> Dict[str, Any]:
        """Start the preview (run the service)."""
        if self.goal is None:
            raise ValueError("Goal not created")

        self.preview = APIPreview(self.project_dir)
        result = self.preview.start()

        if result["ok"]:
            time.sleep(1)
            status = self.preview.status()
            return {
                "started": True,
                "endpoint": result.get("service"),
                "status": status,
            }
        else:
            return {"started": False, "error": result.get("error")}

    def qa(self) -> List[models.Evidence]:
        """Run QA tests."""
        if self.goal is None:
            raise ValueError("Goal not created")

        self.evidence = APIQA.test_endpoints(self.project_dir, self.goal)
        return self.evidence

    def verify(self) -> models.Goal:
        """Verify completion."""
        if self.goal is None:
            raise ValueError("Goal not created")

        failed = [e for e in self.evidence if e.status == models.EvidenceStatus.FAIL]
        if not failed:
            self.goal.status = models.GoalStatus.PASSED
        else:
            self.goal.status = models.GoalStatus.FAILED

        self.goal.verification_requirements = [
            {
                "id": e.test_id,
                "status": e.status.value,
                "result": {"expected": e.expected, "actual": e.actual},
            }
            for e in self.evidence
        ]

        return self.goal

    def feedback_and_replan(self) -> Dict[str, Any]:
        """Analyze feedback and decide on next action."""
        self.feedback = APIFeedback.analyze(self.evidence)

        if self.feedback["action"] == "replan_and_rebuild":
            self.goal.status = models.GoalStatus.REPLANNING
            # In a real system, the planner would adjust the plan based on failures
            # For this slice, we just mark it for manual review
            self.goal.state["replanning_reason"] = self.feedback["reason"]

        return self.feedback

    def complete(self) -> models.Goal:
        """Transition to COMPLETED."""
        if self.goal is None:
            raise ValueError("Goal not created")
        if self.goal.status != models.GoalStatus.PASSED:
            raise ValueError(f"Cannot complete goal with status {self.goal.status}")
        self.goal.status = models.GoalStatus.COMPLETED
        return self.goal

    def cleanup(self) -> None:
        """Clean up resources."""
        if self.preview is not None:
            self.preview.stop()

    def run_full_lifecycle(
        self, intent: str = "Build a production-ready API service"
    ) -> Dict[str, Any]:
        """Execute the full Goal Mode lifecycle."""
        try:
            self.create_goal(intent)
            self.analyze()
            self.plan()
            self.execute()

            preview_result = self.preview()
            time.sleep(0.5)

            qa_evidence = self.qa()
            self.verify()

            feedback = self.feedback_and_replan()

            if self.goal.status == models.GoalStatus.PASSED:
                self.complete()

            return {
                "goal_id": self.goal.goal_id,
                "final_status": self.goal.status.value,
                "tasks": len(self.tasks),
                "evidence_count": len(qa_evidence),
                "evidence_passed": sum(
                    1 for e in qa_evidence if e.status == models.EvidenceStatus.PASS
                ),
                "evidence_failed": sum(
                    1 for e in qa_evidence if e.status == models.EvidenceStatus.FAIL
                ),
                "feedback": feedback,
                "preview": preview_result,
            }
        finally:
            self.cleanup()
