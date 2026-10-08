# JaXir OS — Requirement Traceability & Implementation Status

Implements constitution §37 (machine-readable requirement chain) and enforces §36
(a target that is not yet done is **NOT YET IMPLEMENTED**, never **PASSED**).

**Status legend**

| Status | Meaning |
|---|---|
| `IMPLEMENTED` | Real behaviour exists and is covered by a test that would fail if it broke. |
| `PARTIAL` | Some real behaviour exists; the stated scope is not fully met. |
| `STUB` | Explicitly labelled placeholder. No real behaviour. |
| `NOT YET IMPLEMENTED` | Required by the constitution; no implementation yet. |

`NOT YET IMPLEMENTED` is a legitimate status in an early milestone. `PASSED` is not
available to it.

---

## 1. Locked Requirement Chain (§37)

```text
Requirement → Acceptance Target → Test → Evidence → Release Gate
```

The chain is expressed in code as `arl_trp`-style contracts in `jaxir/spec.py`
(`RequirementChain`). Every P0/P1 requirement must eventually be traceable through
it. The table below is the current, honest state of that chain.

---

## 2. RFC-001 Subsystems (§5)

| Subsystem | Module | Status | Notes |
|---|---|---|---|
| Goal Engine | `jaxir/state.py`, `jaxir/spec.py`, `jaxir/goalmode.py` | PARTIAL | Locked 13 goal states, invalid-transition rejection, idempotent no-op, and a per-goal transition audit log (every accepted transition recorded, so 100% auditable is testable). `COMPLETED` is reachable via `PASSED`. `GoalSpec.from_text` is a deterministic heuristic placeholder, not a real NL analyser. |
| Task Graph | `jaxir/taskgraph.py` | PARTIAL | Kahn topological order, cycle rejection, environment/resource conflict detection. File conflicts and duplicate non-idempotent execution are **NOT YET IMPLEMENTED**. |
| Agent Orchestrator | `jaxir/orchestrator.py` | PARTIAL | Agent registry, capability-intersection routing, task execution + evidence slots. Does not yet assign model/provider/permissions/resource limits per task. |
| Model Runtime | `jaxir/omniroute.py` | PARTIAL | Deterministic routing policy + `model.requested` events. No Model Runtime abstraction, **no Codex adapter**, no quota manager. |
| Execution Sandbox | `jaxir/sandbox.py` | PARTIAL | Real filesystem confinement, rlimits, network policy, secret scrubbing, permission broker, cleanup + orphan detection. See §4 below for what remains. |
| Preview / Simulation | `jaxir/preview.py` | PARTIAL | Real web (HTTP-served + DOM-inspected) and terminal adapters + full `start/stop/reload/inspect/capture/status` interface. mobile/desktop/game/video/3D/embedded/hardware/API adapters are labelled `None` placeholders. |
| QA / QC | `jaxir/qa.py` | PARTIAL | Real functional, regression, security (static audit) and performance (measured latency) evidence. Visual/accessibility/chaos/adversarial/synthetic-user/hardware QA are **NOT YET IMPLEMENTED**. |
| Feedback Engine | `jaxir/feedback.py` | IMPLEMENTED | Dedupe → cluster by root cause → prioritize by severity then size → one corrective task spec per root cause; feedback/specs are linked to the corrective task that addressed them. |
| Replanning | `jaxir/goalmode.py` | IMPLEMENTED | `replan()` consumes failures, materialises corrective tasks into the task graph, records the attempt/strategy, and transitions REPLANNING → PLANNED. |
| Autonomous Loop Protection | `jaxir/loopguard.py` | IMPLEMENTED | Bounded attempts, equivalent-failure limit (≤5, locked), oscillation and duplicate-task detection, strategy escalation ladder, escalation to BLOCKED with evidence preserved. |
| Event Bus | `jaxir/events.py` | PARTIAL | Ordered, versioned envelopes, pub/sub, bounded history, replay, query. **In-memory only — Event Store persistence is NOT YET IMPLEMENTED.** |
| Artifact Registry | `jaxir/registry.py` | PARTIAL | Artifacts with sha256 + provenance + goal linkage; decisions; memory indices with search. Artifact versioning is a static default (`0.0.0`). |
| Checkpoint Manager | `jaxir/checkpoint.py` | PARTIAL | create / restore / list / compare. **branch and replay are NOT YET IMPLEMENTED.** |
| Observability | `jaxir/observability.py` | PARTIAL | Timer traces, decision summaries, resource snapshot. The `Goal → Task → Agent → Model → Tool → Command → File` span chain is **NOT YET IMPLEMENTED**. |
| Memory | `jaxir/registry.py` | PARTIAL | Project/QA/agent/session indices are generic `MemoryIndex` objects. Per-level schema and Engineering Experience Memory are **NOT YET IMPLEMENTED**. |
| Context Compiler | — | NOT YET IMPLEMENTED | §10. |
| Quota Manager | — | NOT YET IMPLEMENTED | §11. |
| Permission Broker | `jaxir/sandbox.py` | PARTIAL | Capability grants, `permission.requested/granted/denied` events, protected-operation classification. No persistent policy file / UI approval flow. |
| Project-Type Adapters | — | NOT YET IMPLEMENTED | §24. |
| Mission Control | — | NOT YET IMPLEMENTED | §39/§40. |
| Learning (Phase 9) | — | NOT YET IMPLEMENTED | §28–§30. |
| Evolution (Phase 10) | — | NOT YET IMPLEMENTED | §31–§34. |
| Digital Twin | — | NOT YET IMPLEMENTED | §25. |
| Synthetic Users | — | NOT YET IMPLEMENTED | §26. |
| Parallel Worlds | — | NOT YET IMPLEMENTED | §27. |

---

## 3. Acceptance Targets (§36)

| Target | Status | Evidence path |
|---|---|---|
| AT-PREV-001 (preview passes) | IMPLEMENTED | `todoslice.TodoApp._preview_evidence` — asserts the **served HTTP response**, not the file on disk. `tests/test_kernel.py::TestTodoVerticalSlice::test_preview_is_verified_not_just_built`, `::TestWebPreviewAdapter` |
| AT-QA-001 (functional) | IMPLEMENTED | `qa.QAEngine.test_todo` — asserts persisted store state after every command, so a no-op CLI cannot pass. `::test_noop_cli_cannot_produce_pass_evidence` |
| AT-QA-002 (regression) | PARTIAL | `qa.QAEngine.run_regression` — real golden-contract replay of the artifact under test. Not a cross-version regression suite. |
| AT-QA-003 (security / performance) | PARTIAL | `qa.QAEngine.run_security` (static audit, real rules) and `run_performance` (measured latency percentiles). Neither is a substitute for a full SAST/profiling toolchain. |
| AT-SEC-001 | PARTIAL | Sandbox isolation tests: `tests/test_sandbox.py`. Kernel-level (namespace) isolation is not claimed — see §4. |
| AT-GM-001 (autonomous slice completion) | IMPLEMENTED | `TodoApp._run_loop` completes the Todo goal against evidence. `tests/test_loop.py::TestExecutionLoop::test_healthy_goal_completes_on_first_attempt` |
| AT-GM-002 (autonomous feedback correction) | PARTIAL | Fails → feedback → corrective task → replan → re-QA → complete, verified by `::test_goal_recovers_after_a_failed_attempt`. **The repair strategy itself is a stub** — see §7; the deterministic coder regenerates the artifact rather than changing code from the root cause. |
| AT-GM-004 (autonomous-loop detection ≤5) | IMPLEMENTED | `LoopGuard` equivalence limit + attempt budget + oscillation. `::test_unrecoverable_goal_escalates_and_stops`, `TestLoopGuard::test_equivalent_failure_limit_triggers_escalation` |
| AT-GM-003 (24 h Goal Mode endurance) | NOT YET IMPLEMENTED | Requires a long-running harness. |
| AT-001…AT-030 (general), AT-HW-001 | NOT YET IMPLEMENTED | No traceable test/evidence binding yet. AT-HW-001 requires hardware QA (§16) and the Digital Twin (§25). |

> The constitution lists AT-GM-001…004 without defining each one. The mapping
> above is this repository's reading of them and **should be confirmed before
> being treated as authoritative** — the behaviours required by §18/§41/§42 are
> implemented regardless.

---

## 4. Sandbox: what is real, and what is explicitly not claimed (§13)

Real and tested:

- **Filesystem confinement** — paths are resolved (symlinks followed) and must fall inside `allowed_dirs`; traversal and symlink escapes outside the root are rejected. `Orchestrator._execute_task` resolves the coder's output directory through the sandbox, so generated code cannot be written outside the boundary.
- **Environment / secret scrubbing** — child processes receive an allowlisted environment; secret-looking variables are dropped, and values are redacted in any logged output.
- **Resource limits** — `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_NOFILE`, `RLIMIT_FSIZE`, `RLIMIT_NPROC` applied via `preexec_fn`; wall-clock timeout enforced; on timeout the child's whole **process group** is SIGKILLed so grandchildren are not orphaned; violations surface as `ok=False` with the reason.
- **Network policy** — `OFFLINE` / `LOCAL_ONLY` / `ALLOWLIST` / `UNRESTRICTED` with host/scheme/port matching, enforced on every outbound call that goes through the sandbox's `open_url` gate.
- **Permission broker** — capability-based grants; §14 protected operations are denied unless granted, and denial is an event, not a silent failure.
- **Cleanup / orphan detection** — spawned processes are tracked and terminated; `detect_orphans()` reports any that outlive their task.

**Explicitly not claimed.** There is no kernel-level isolation. Network policy is
enforced at the *JaXir* call boundary (`sandbox.open_url`) and by poisoning proxy
variables for child processes — it cannot stop a determined subprocess from opening
a raw socket. Full enforcement requires OS facilities (network namespaces,
seccomp, containers), which are environment-dependent and therefore **NOT YET
IMPLEMENTED**. This limitation is stated here rather than papered over, per §49.11
and §49.13.

---

## 5. Release Gates (§38)

| Gate | Status | Blocking gaps |
|---|---|---|
| Gate 0 — Kernel Integrity | PARTIAL | Event/integrity/state/idempotency yes; **Event Store persistence** and checkpoint branch/replay outstanding. |
| Gate 1 — Autonomous Execution | PARTIAL | Goal → Plan → Task → Agent → Execution works end-to-end. The runtime is a deterministic in-process coder, **not the Codex Runtime**; recovery mid-run is not implemented. |
| Gate 2 — Engineering Quality | PARTIAL | Preview, QA, evidence, feedback and replanning exist. Feedback → corrective tasks → replanning loop is **NOT YET IMPLEMENTED**. |
| Gate 3 — Production Readiness | NOT YET IMPLEMENTED | Requires all P0 and ≥95% P1 requirements plus an automated evidence package. |

---

## 6. Definition of Done integrity (§19)

`TodoApp._verify` marks a goal `COMPLETED` only when **all** collected evidence items
report `PASS`, and records the per-criterion outcomes in
`goal.verification_requirements`. Build success and compilation are never sufficient:
each item is an executed check whose `expected`/`actual` are recorded.

Currently `COMPLETED` implies six evidence items: `qa.preview`,
`qa.todo.functional`, `qa.regression`, `qa.security`, `qa.performance`,
`qa.verdict`.

---

## 7. Known stubs (explicitly labelled)

| Location | Stub | Label in code |
|---|---|---|
| `preview.WebPreview` | status-only web preview, no server | docstring: "Non-serving web placeholder" |
| `preview.PreviewEngine.ADAPTERS` | `None` for future project types | inline `# future … adapter` |
| `sandbox.NetworkPolicy` (OFFLINE) | proxy poisoning + call-boundary gate | §4 above and docstring |
| `qa.QAEngine.run_regression` | golden-contract replay, artefact-scoped | returns `provenance.method = "golden_contract_replay"` |
| `qa.QAEngine.run_security` | static pattern audit | returns `provenance.method = "static_audit"` and the rule set used |
| `goalmode.GoalMode.replan` | state move only | docstring |
| `sandbox.Sandbox.prepare_workdir` | per-task workdir primitive; implemented and tested, not yet used to isolate each agent run | docstring |
| `spec.GoalSpec.from_text` | deterministic heuristic | docstring |
| `orchestrator.Orchestrator._execute_task` | in-process todo coder | inline `# TODO: plug in real codex runtime` |
| corrective fix strategy | corrective tasks record `fix_strategy_implemented: False`; the deterministic coder regenerates the artifact instead of changing code from the root cause | result field + `docs/TRACEABILITY.md` §5 |

---

## 8. Reproducing the current state

```bash
cd .cline/data/workspaces/chat/jaxir-os
python3 -m pytest -q          # 127 tests
```

| Test file | Covers |
|---|---|
| `tests/test_kernel.py` | contracts, state machine, task graph, checkpoint, vertical slice, preview |
| `tests/test_sandbox.py` | filesystem confinement, secrets, rlimits, network mode, permissions, orchestrator integration |
| `tests/test_qa.py` | regression/security/performance suites, suite-coverage disclosure, Definition-of-Done integrity |
| `tests/test_loop.py` | failure equivalence, loop guard, corrective tasks, replanning, execution-loop recovery/escalation, transition auditability |
