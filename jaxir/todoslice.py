"""JaXir's first vertical slice: the Todo application.

End-to-end closed loop:

    Natural language goal -> Goal spec -> Plan -> Task graph -> Code ->
    Sandbox -> Build -> Preview -> QA -> Evidence -> Feedback -> Verify -> Done

Todos: add, complete, delete. Also builds a responsive web app for web preview.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import models, goalmode, orchestrator, sandbox, preview, qa, checkpoint, registry


class TodoPlanner:
    """Turns the Todo goal into a dependency-aware task graph."""

    @staticmethod
    def plan(goal: models.Goal, project_dir: Path) -> List[models.Task]:
        tasks = [
            models.Task(
                goal_id=goal.goal_id,
                owner_agent_id="planner",
                agent_type="planner",
                capabilities=["planning", "decomposition"],
                status=models.TaskStatus.PENDING,
                inputs={"project_dir": str(project_dir), "goal": goal.to_dict()},
                outputs={"todo_cli": "todo"},
                acceptance_criteria=[
                    {"id": "C1", "metric": "end_to_end_success", "threshold": True,
                     "measurement_method": "run_todo_add_complete_delete"},
                ],
                verification=None,
                retry_policy={},
            ),
            models.Task(
                goal_id=goal.goal_id,
                owner_agent_id="planner",
                agent_type="planner",
                capabilities=["task_graph", "dependency_schedule"],
                status=models.TaskStatus.PENDING,
                inputs={"project_dir": str(project_dir), "depends": ["planner"]},
                outputs={"task_graph": "planned"},
                acceptance_criteria=[],
                verification=None,
                retry_policy={},
            ),
            models.Task(
                goal_id=goal.goal_id,
                owner_agent_id="planner",
                agent_type="coder",
                capabilities=["coding", "build", "sandbox_exec"],
                status=models.TaskStatus.PENDING,
                inputs={"project_dir": str(project_dir), "todo_cli": True},
                outputs={"todo_cli_source": "todo", "todo_bin": "todo"},
                acceptance_criteria=[
                    {"id": "C0", "metric": "todo_cli_written", "threshold": True,
                     "measurement_method": "file_exists_and_executable"},
                ],
                verification={"suite": "build.verify"},
                retry_policy={"max_attempts": 2, "strategy": "replan"},
            ),
        ]
        return tasks


class TodoCoder:
    """Coder agent that writes the todo CLI application into the sandbox."""

    TODO_CLI = r'''#!/usr/bin/env python3
# JaXir-built Todo application (single-file CLI).

import json, os, sys, argparse

TODO_STORE = os.environ.get("TODO_STORE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "todos.json"))
STORE = TODO_STORE

def load():
    if os.path.exists(STORE):
        with open(STORE) as f:
            return json.load(f)
    return []

def save(todos):
    with open(STORE, "w") as f:
        json.dump(todos, f, indent=2)

def next_id(todos):
    return max((t["id"] for t in todos), default=0) + 1

def cmd_add(args):
    todos = load()
    todos.append({"id": next_id(todos), "text": args.text, "done": False})
    save(todos)
    print(f"added: {args.text}")

def cmd_list(args):
    todos = load()
    if not todos:
        print("(no todos)")
        return
    for t in todos:
        mark = "x" if t["done"] else " "
        print(f"{mark} {t['id']}: {t['text']}")

def cmd_complete(args):
    todos = load()
    for t in todos:
        if t["id"] == args.id:
            t["done"] = True
            save(todos)
            print(f"completed: {args.id}")
            return
    print(f"no todo {args.id}", file=sys.stderr)
    sys.exit(1)

def cmd_delete(args):
    todos = load()
    kept = [t for t in todos if t["id"] != args.id]
    if len(kept) == len(todos):
        print(f"no todo {args.id}", file=sys.stderr)
        sys.exit(1)
    save(kept)
    print(f"deleted: {args.id}")

def main():
    p = argparse.ArgumentParser(prog="todo")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("add")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_add)
    lp = sub.add_parser("list")
    lp.set_defaults(func=cmd_list)
    cp = sub.add_parser("complete")
    cp.add_argument("id", type=int)
    cp.set_defaults(func=cmd_complete)
    dp = sub.add_parser("delete")
    dp.add_argument("id", type=int)
    dp.set_defaults(func=cmd_delete)
    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
'''

    @staticmethod
    def write(project_dir: Path) -> Path:
        """Write the todo CLI into the sandbox workdir."""
        project_dir.mkdir(parents=True, exist_ok=True)
        cli_path = project_dir / "todo"
        cli_path.write_text(TodoCoder.TODO_CLI, encoding="utf-8")
        os.chmod(cli_path, 0o755)
        return cli_path


class WebTodoApp:
    """Coder artifact: a single-file, self-contained Todo web application.

    The page is dependency-free (no CDN, no build step) so it can be served
    from the sandbox and previewed over loopback: state lives in
    ``localStorage`` and the UI is responsive by construction.
    """

    INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JaXir Todo</title>
<style>
  :root { --bg:#0f1115; --card:#171a21; --fg:#e6e8ee; --muted:#8b93a7; --accent:#4f8cff; --done:#3ddc97; }
  * { box-sizing:border-box; }
  body { margin:0; font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--fg); display:flex; justify-content:center;
         padding:2rem 1rem; min-height:100vh; }
  main { width:100%; max-width:40rem; }
  h1 { font-size:1.5rem; margin:0 0 .25rem; }
  .sub { color:var(--muted); font-size:.875rem; margin-bottom:1.5rem; }
  form { display:flex; gap:.5rem; margin-bottom:1rem; }
  input[type=text] { flex:1; padding:.7rem .9rem; border-radius:.5rem;
                     border:1px solid #262b36; background:var(--card); color:var(--fg); font:inherit; }
  input[type=text]:focus { outline:2px solid var(--accent); outline-offset:1px; }
  button { padding:.7rem 1rem; border:0; border-radius:.5rem; background:var(--accent);
           color:#fff; font:inherit; font-weight:600; cursor:pointer; }
  button:hover { filter:brightness(1.1); }
  ul { list-style:none; margin:0; padding:0; }
  li { display:flex; align-items:center; gap:.75rem; padding:.75rem .9rem; margin-bottom:.5rem;
       background:var(--card); border:1px solid #21262f; border-radius:.5rem; }
  li.done .text { text-decoration:line-through; color:var(--muted); }
  .text { flex:1; word-break:break-word; }
  .del { background:transparent; color:var(--muted); padding:.25rem .5rem; font-size:1.1rem; }
  .del:hover { color:#ff6b6b; }
  .empty { color:var(--muted); text-align:center; padding:1.5rem 0; }
  footer { display:flex; justify-content:space-between; color:var(--muted);
           font-size:.8125rem; margin-top:1rem; }
  @media (max-width:480px) { body { padding:1rem .75rem; } h1 { font-size:1.25rem; } }
</style>
</head>
<body>
<main>
  <h1>Todo</h1>
  <div class="sub">Built by JaXir OS &middot; stored locally on this device</div>
  <form id="new-form">
    <input id="new-text" type="text" placeholder="What needs doing?" autocomplete="off" required>
    <button type="submit">Add</button>
  </form>
  <ul id="list"></ul>
  <footer>
    <span id="count">0 items</span>
    <span id="progress"></span>
  </footer>
</main>
<script>
  var KEY = "jaxir.todos";
  var todos = [];
  try { todos = JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { todos = []; }

  function persist() { localStorage.setItem(KEY, JSON.stringify(todos)); render(); }

  function add(text) {
    var id = todos.reduce(function (m, t) { return Math.max(m, t.id); }, 0) + 1;
    todos.push({ id: id, text: text, done: false });
    persist();
  }
  function toggle(id) {
    todos.forEach(function (t) { if (t.id === id) { t.done = !t.done; } });
    persist();
  }
  function remove(id) {
    todos = todos.filter(function (t) { return t.id !== id; });
    persist();
  }

  function render() {
    var list = document.getElementById("list");
    list.innerHTML = "";
    if (!todos.length) {
      var empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Nothing here yet.";
      list.appendChild(empty);
    }
    todos.forEach(function (t) {
      var li = document.createElement("li");
      if (t.done) { li.className = "done"; }
      var box = document.createElement("input");
      box.type = "checkbox";
      box.checked = t.done;
      box.addEventListener("change", function () { toggle(t.id); });
      var span = document.createElement("span");
      span.className = "text";
      span.textContent = t.text;
      var del = document.createElement("button");
      del.className = "del";
      del.type = "button";
      del.textContent = "\\u00d7";
      del.addEventListener("click", function () { remove(t.id); });
      li.appendChild(box); li.appendChild(span); li.appendChild(del);
      list.appendChild(li);
    });
    var done = todos.filter(function (t) { return t.done; }).length;
    document.getElementById("count").textContent = todos.length + " items";
    document.getElementById("progress").textContent = done + " done";
  }

  document.getElementById("new-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var input = document.getElementById("new-text");
    var text = input.value.trim();
    if (text) { add(text); input.value = ""; }
  });

  render();
</script>
</body>
</html>
"""

    #: Alias used by preview.WebContentGenerator for the web preview content.
    WEB_APP = INDEX_HTML

    @staticmethod
    def write(project_dir: Path) -> Path:
        """Write the todo web app into the sandbox workdir."""
        project_dir.mkdir(parents=True, exist_ok=True)
        index_path = project_dir / "index.html"
        index_path.write_text(WebTodoApp.INDEX_HTML, encoding="utf-8")
        return index_path


class TodoApp:
    """Runs the full end-to-end Todo vertical slice through JaXir's kernel.

    First vertical slice: a Todo application (CLI + web app) executed through
    the complete JaXir kernel lifecycle with evidence-backed completion.
    """

    def __init__(self, root: Optional[str] = None):
        self.root = Path(root) if root else Path(__file__).resolve().parent.parent
        self.project_dir = self.root / "todo_slice"
        self._goal: Optional[models.Goal] = None
        self.bus = None

    def build(self) -> models.Goal:
        """Assemble the kernel and run the Todo slice end-to-end."""
        from .events import EventBus
        from .state import GoalStateMachine
        from .taskgraph import TaskGraph
        from .orchestrator import Orchestrator
        from .checkpoint import CheckpointManager
        from .registry import Registry
        from .observability import Observability
        from .omniroute import OmniRoute, RoutingPolicy
        from .sandbox import Sandbox, SandboxConfig, NetworkMode
        from .preview import PreviewEngine
        from .qa import QAEngine
        from .feedback import FeedbackEngine
        from . import spec

        self.bus = EventBus()
        self.checkpoint_mgr = CheckpointManager(str(self.project_dir), self.bus)
        self.registry = Registry(str(self.project_dir), self.bus)
        self.sb = Sandbox(
            SandboxConfig(project_root=str(self.project_dir), workdir=str(self.project_dir)),
            self.bus,
        )
        self.og = Orchestrator(self.bus, self.sb)
        self.pm = GoalStateMachine(self.bus)
        self.tg = TaskGraph(self.bus)
        self.ob = Observability(self.bus, None)
        self.omni = OmniRoute(RoutingPolicy(), self.bus)
        self.prv = PreviewEngine(self.sb, self.bus)
        self.qae = QAEngine(self.bus, self.registry, None)
        self.fb = FeedbackEngine(self.bus)

        # Register replaceable agents (planner + coder).
        planner = orchestrator.AgentModel(
            agent_id="planner", agent_type="planner", identity="JaXir Planner",
            capabilities=["planning", "decomposition", "task_graph"],
            tools=["spec", "taskgraph", "planning"],
            environment={"project_type": "todo"},
        )
        coder = orchestrator.AgentModel(
            agent_id="coder", agent_type="coder", identity="JaXir Coder",
            capabilities=["coding", "build", "sandbox_exec"], tools=["write", "run"],
            environment={"project_type": "todo"},
        )
        self.og.register(planner)
        self.og.register(coder)

        # Step 1: create -> analyze -> plan
        self._goal = models.Goal(
            project_id="todo",
            user_intent="Build a production-ready Todo application",
            title="Todo application",
            project_type="terminal",
        )
        self.goalmode = goalmode.GoalMode(self.bus, self.omni, self.tg, self.og,
                                          self.sb, self.checkpoint_mgr)
        spec1 = spec.GoalSpec.from_text(
            "todo", "Build a production-ready Todo application with add, complete, and delete.",
            self._goal,
        )
        self.goalmode.create_goal("todo", "Build a production-ready Todo application",
                                  "Todo application", spec1)
        self.pm.transition(self._goal, models.GoalStatus.ANALYZING)
        self.pm.transition(self._goal, models.GoalStatus.PLANNED)

        # Decompose into tasks.
        self._goal.task_graph = TodoPlanner.plan(self._goal, self.project_dir)
        self._goal.status = models.GoalStatus.EXECUTING

        # Step 2: execute task graph via orchestrator (planner + coder)
        self.goalmode.execute(self._goal, self.project_dir)

        # Step 3: preview (web adapter) - generates the web app artifact and
        # verifies it is really served (AT-PREV-001).
        preview_evidence = self._preview()

        # Step 4: QA - functional tests -> evidence
        qa_evidence = self._qa(preview_evidence)

        # Step 5: feedback - evidence-backed, feeds replanning
        self._feedback(qa_evidence)

        # Step 6: verify (Definition of Done)
        self._verify(qa_evidence)

        return self._goal

    def _generate_web_app(self) -> None:
        """Generate the web app into the sandbox workdir for preview."""
        index = WebTodoApp.write(self.project_dir)
        coder_task = self._goal.task_graph[-1]
        # Merge, never clobber: the coder task already carries the CLI result
        # (path + sha256) that the evidence trail depends on.
        coder_task.result = {
            **(coder_task.result or {}),
            "ok": True,
            "web_app": str(index),
            "preview_type": "web",
            "summary": "todo CLI + web app built",
        }
        self.registry.register(
            registry.Artifact(name="todo_web_app", kind="web_page",
                              path=str(index),
                              linked_goal_id=self._goal.goal_id)
        )

    def _preview(self) -> models.Evidence:
        """Preview the todo app (web adapter) and verify it is really served.

        AT-PREV-001: the preview must come up and serve the built app. The
        served HTTP response - not the file on disk - is the evidence, so a
        preview that silently fails to start cannot pass verification.
        """
        self._generate_web_app()
        # Ephemeral port: never collide with a real service during tests.
        status = self.prv.start(
            "web", 0, self._goal.goal_id, self._goal.project_id,
            {"workdir": str(self.project_dir)},
        )
        capture = self.prv.capture("web") if status.running else {}
        evidence = self._preview_evidence(status, capture)

        for t in self._goal.task_graph:
            if t.status == models.TaskStatus.COMPLETED and t.result:
                t.result = {**t.result, "preview": status.to_dict()}
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=models.EventType.PREVIEW_UPDATED,
                    project_id=self._goal.project_id,
                    goal_id=self._goal.goal_id,
                    payload={"preview_type": "web", "url": status.url,
                             "http_status": capture.get("http_status")},
                )
            )
        # Resource cleanup: the preview server must not outlive the build.
        self.prv.stop("web")
        return evidence

    def _preview_evidence(self, status: Any, capture: Dict[str, Any]) -> models.Evidence:
        """Evidence that the preview served the app (AT-PREV-001)."""
        dom = capture.get("dom_snapshot") or {}
        ids = dom.get("ids") or []
        checks = {
            "preview_running": bool(status.running),
            "http_200": capture.get("http_status") == 200,
            "served_title": dom.get("title") == "JaXir Todo",
            "add_form_present": "new-form" in ids,
            "list_present": "list" in ids,
            "interactive": bool(dom.get("interactive")),
        }
        passed = all(checks.values())
        evid = models.Evidence(test_id="qa.preview", goal_id=self._goal.goal_id,
                               project_id=self._goal.project_id)
        evid.status = (models.EvidenceStatus.PASS if passed
                       else models.EvidenceStatus.FAIL)
        evid.expected = {"preview_running": True, "http_status": 200,
                         "title": "JaXir Todo",
                         "required_ids": ["new-form", "list"]}
        evid.actual = {"preview": status.to_dict(), "capture": capture,
                       "checks": checks}
        evid.artifacts = [str(self.project_dir / "index.html")]
        evid.provenance = {"test_id": "qa.preview",
                           "method": "http_serve_and_dom_inspect",
                           "url": status.url,
                           "project_dir": str(self.project_dir)}
        if self.bus is not None:
            self.bus.publish(
                models.Event(
                    event_type=(models.EventType.TEST_PASSED if passed
                                else models.EventType.TEST_FAILED),
                    project_id=self._goal.project_id,
                    goal_id=self._goal.goal_id,
                    payload={"test_id": evid.test_id, "ok": passed,
                             "checks": checks},
                )
            )
        return evid

    def _qa(self, preview_evidence: models.Evidence) -> List[models.Evidence]:
        """Preview + functional + regression + security + performance evidence."""
        ev = self.qae.test_todo(str(self.project_dir), expected_todos=1,
                                expected_completed=1)
        ev.goal_id = self._goal.goal_id
        ev.project_id = self._goal.project_id
        reg = self.qae.run_regression(str(self.project_dir))
        sec = self.qae.run_security()
        perf = self.qae.run_performance(str(self.project_dir))
        for e in (reg, sec, perf):
            e.goal_id = self._goal.goal_id
            e.project_id = self._goal.project_id
        items = [preview_evidence, ev, reg, sec, perf]
        verdict = self.qae.verdict(items)
        verdict.goal_id = self._goal.goal_id
        verdict.project_id = self._goal.project_id
        if self.bus is not None:
            passed = verdict.status == models.EvidenceStatus.PASS
            self.bus.publish(
                models.Event(
                    event_type=(models.EventType.QA_PASSED if passed
                                else models.EventType.QA_FAILED),
                    project_id=self._goal.project_id,
                    goal_id=self._goal.goal_id,
                    payload={"verdict": verdict.test_id, "ok": passed,
                             "evidence": [e.test_id for e in items],
                             "checks": (ev.actual or {}).get("checks", [])},
                )
            )
        return items + [verdict]

    def _feedback(self, evidence: List[models.Evidence]) -> None:
        """Feedback from failures -> deduplication -> clustering -> root cause."""
        failed = [e for e in evidence if e.status == models.EvidenceStatus.FAIL]
        if not failed:
            return
        for e in failed:
            self.fb.create(self._goal.project_id, self._goal.goal_id, None,
                           e.test_id, ["todo_app_failure"], root_cause="todo app bug")
        deduped = self.fb.dedupe(self.fb.feedback)
        self._goal.state["feedback_clusters"] = self.fb.get_clusters(deduped)

    def _verify(self, evidence: List[models.Evidence]) -> None:
        """Verify Definition of Done; completing evidence-backed."""
        passed = all(e.status == models.EvidenceStatus.PASS for e in evidence)
        self._goal.verification_requirements = [
            {"id": v.test_id, "status": v.status.value}
            for v in evidence
        ]
        if passed:
            self._goal.status = models.GoalStatus.COMPLETED
            if self.bus is not None:
                self.bus.publish(
                    models.Event(event_type=models.EventType.GOAL_COMPLETED,
                                 project_id=self._goal.project_id,
                                 goal_id=self._goal.goal_id,
                                 payload={"dod": "all_evidence_passed"}))
        else:
            self._goal.status = models.GoalStatus.FAILED
            if self.bus is not None:
                self.bus.publish(
                    models.Event(event_type=models.EventType.GOAL_FAILED,
                                 project_id=self._goal.project_id,
                                 goal_id=self._goal.goal_id,
                                 payload={"dod": "evidence_failed",
                                          "failed": [e.test_id for e in evidence
                                                     if e.status != models.EvidenceStatus.PASS]}))
