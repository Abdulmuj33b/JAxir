# JaXir OS — Master Implementation Constitution

> **Status: LOCKED BASELINE.** This document is the implementation constitution for
> JaXir OS. It is reproduced here so the locked requirements are versioned with the
> code they govern. Do not weaken, silently reinterpret, remove, or bypass locked
> requirements. If an implementation decision conflicts with a locked requirement,
> stop and resolve the conflict explicitly rather than silently choosing the easier
> implementation.
>
> Change control: see §54. Any change to the locked baseline requires an explicit,
> documented change proposal and authorization — not an edit in passing.

---

## 1. PRODUCT IDENTITY

Product: **JaXir OS**
Core intelligence: **JaXir Agent**
Autonomous execution mode: **Goal Mode**
Initial coding runtime: **Codex Runtime**
Initial model-routing infrastructure: **OmniRoute**

JaXir OS must remain model/provider/runtime agnostic. Codex and OmniRoute are
implementation choices for the initial system, not architectural dependencies that
permanently define the OS.

The architecture must allow future integration of: other coding agents, reasoning
models, vision models, audio models, video models, local models, specialized
engineering models, robotics models, simulation systems, CAD/EDA systems, external
tools, MCP servers, cloud execution systems.

---

## 2. CORE MISSION

JaXir receives a user goal such as: *"Build a production-ready application for X."*

It must be capable of transforming that goal into:

**Goal → Requirements → Acceptance Criteria → Plan → Tasks → Execution → Build →
Preview/Simulation → QA/QC → Evidence → Feedback → Replanning → Verification → Completion**

The long-term system must also support:

**Experience → Learning → Improvement Hypothesis → Experiment → Benchmark → QA →
Promotion → Better JaXir**

Therefore JaXir contains two major loops.

**Execution Loop:** Goal → Plan → Build → Preview → QA/QC → Feedback → Replan → Verify → Done

**Evolution Loop:** Experience → Learn → Discover Pattern → Propose Improvement →
Experiment → Benchmark → QA → Promote → Experience

---

## 3. FUNDAMENTAL ARCHITECTURAL PRINCIPLES

The following principles are locked.

- **3.1 Goal-centric** — Everything important ultimately belongs to a goal.
- **3.2 Event-driven** — Important state changes produce events.
- **3.3 Evidence-driven** — JaXir must never consider work complete merely because an agent claims success.
- **3.4 Model-independent** — Models and providers are replaceable.
- **3.5 Agent-independent** — Agents are replaceable components implementing explicit contracts.
- **3.6 Environment-independent** — Projects may execute locally, remotely, in containers, simulators, virtual hardware environments, or physical environments subject to authorization.
- **3.7 Human-controllable** — Users can inspect, pause, resume, redirect, approve, reject, cancel, rollback, and take control.
- **3.8 Recoverable** — Long-running autonomous execution must survive failures and resume from valid checkpoints.
- **3.9 Secure by default** — Generated code and autonomous actions must be treated as potentially untrusted.
- **3.10 Occam Engineering** — JaXir must always prefer the **minimum necessary complexity** capable of satisfying the requirements, acceptance criteria, NFRs, safety constraints, and measurable acceptance targets. Simple does not mean simplistic. Do not remove necessary complexity merely because it makes the architecture smaller.

---

## 4. OCCAM'S RAZOR — GLOBAL ENGINEERING LAW

This is a first-class JaXir architectural rule.

For competing solutions:
**Requirements satisfied → Acceptance targets satisfied → Compare complexity → Prefer simpler solution.**

Evaluate complexity across: components, services, dependencies, code surface,
configuration, state, interfaces, agent interactions, model calls, infrastructure,
deployment, operational burden, security surface, failure modes, maintenance burden.

JaXir must continuously ask: *What is the minimum complexity required to reliably
satisfy this goal?*

Do not introduce: unnecessary microservices, agents, databases, frameworks,
abstractions, dependencies, model calls, infrastructure, or orchestration.

A material increase in complexity requires justification. Every significant
architectural proposal should contain:

```yaml
problem:
current_solution:
proposed_solution:

benefit:
  metric:
  expected_improvement:

complexity:
  components_added:
  dependencies_added:
  interfaces_added:
  operational_burden:

simpler_alternatives:
  - solution:
    reason_rejected:

evidence:
  benchmark:
  qa:
  regression:

decision:
  complexity_justified:
```

---

## 5. LOCKED RFC-001 ARCHITECTURE

```text
                         ┌─────────────────────┐
                         │    Mission Control  │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │     Goal Engine     │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Agent Orchestrator  │
                         └───────┬─────┬───────┘
                                 │     │
                     ┌───────────┘     └────────────┐
                     ▼                              ▼
               Planner Agent                  Coder Agent
                     │                              │
                     └────────────┬─────────────────┘
                                  ▼
                         ┌─────────────────────┐
                         │ Execution Sandbox   │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Preview / Simulation│
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │      QA / QC        │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Feedback Engine     │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Replanning / Goal   │
                         │       Engine        │
                         └─────────────────────┘
```

Cross-cutting infrastructure: Event Bus, Memory, Context Compiler, Quota Manager,
Permission Broker, Checkpoint Manager, Observability, Artifact Registry, Tool/MCP
Layer, Project-Type Adapters.

---

## 6. GOAL ENGINE

The Goal Engine transforms natural-language intent into a structured Goal Specification.

A goal must contain, where applicable: goal ID, project ID, user intent,
requirements, constraints, assumptions, acceptance criteria, measurable targets,
definition of done, project type, required capabilities, risk classification,
permissions required, resource limits, dependencies, task graph, verification
requirements.

Goal states are locked:

```text
CREATED, ANALYZING, PLANNED, EXECUTING, VERIFYING, FAILED, PASSED,
REPLANNING, COMPLETED, PAUSED, BLOCKED, CANCELLED, ROLLED_BACK
```

Invalid state transitions must be rejected.

---

## 7. TASK GRAPH

Goals must be decomposable into a dependency-aware task graph.

Tasks must support: dependencies, concurrency, status, owner/agent, required
capabilities, resources, permissions, inputs, outputs, acceptance criteria,
verification, retry policy, failure state.

The scheduler must prevent: dependency violations, unsafe concurrency, resource
conflicts, file conflicts, environment conflicts, duplicate execution of
non-idempotent actions.

---

## 8. AGENT ORCHESTRATOR

The Orchestrator must assign: task, agent, model, provider, tools, environment,
permissions, resource limits, context.

Agent contract must expose: identity, capabilities, status, current task, tools,
environment, permissions, model runtime, result, evidence.

Agents must be replaceable. Do not hard-code the system around one agent.

---

## 9. MODEL RUNTIME

```text
Agent → Model Runtime → Routing Policy → OmniRoute → Provider
```

Codex is the initial coding runtime. The system must support future runtime adapters.

Model selection must consider: capability, task complexity, quality, reliability,
latency, availability, cost/capacity, quota, context requirements.

Never bypass provider restrictions or quotas.

---

## 10. CONTEXT COMPILER

JaXir must not blindly send the entire project context to every agent. The Context
Compiler should select relevant: requirements, files, architecture, decisions,
previous attempts, QA failures, acceptance criteria, tools, task state, project
memory, relevant experience. It should compress redundant context while preserving
required information. Optimization must not sacrifice engineering quality.

---

## 11. QUOTA ECONOMY

Track: provider availability, rate limits, token usage, capacity, latency,
failures, model capability, task importance, routing state.

Support: context compaction, summarization, deduplication, result reuse,
historical compression, relevant-file selection, task-state compression.

Goal Mode may continue across available legitimate provider capacity. Do not
implement mechanisms intended to bypass provider limits or terms.

---

## 12. EVENT BUS

Important operations must emit events.

Core events:

```text
goal.created, goal.analyzed, goal.planned, goal.paused, goal.resumed,
goal.completed, goal.failed

task.created, task.assigned, task.started, task.completed, task.failed

agent.started, agent.paused, agent.resumed, agent.completed, agent.failed

build.started, build.completed, build.failed

preview.started, preview.updated, preview.failed

test.started, test.passed, test.failed

qa.started, qa.passed, qa.failed

feedback.created, feedback.accepted

checkpoint.created, checkpoint.restored

model.requested, model.completed, model.failed

provider.healthy, provider.degraded, provider.exhausted

permission.requested, permission.granted, permission.denied
```

Event envelope:

```json
{
  "event_id": "...", "event_type": "...", "timestamp": "...",
  "project_id": "...", "goal_id": "...", "task_id": "...", "agent_id": "...",
  "source": "...", "correlation_id": "...", "causation_id": "...", "payload": {}
}
```

Events must be versioned.

---

## 13. SANDBOX

Generated code is untrusted by default. Sandbox isolation must control: filesystem,
processes, network, CPU, memory, ports, devices, secrets, credentials.

Use a permission broker for sensitive actions. The security boundary must exist
outside the agent. Never assume an LLM will obey the sandbox policy.

---

## 14. HUMAN SAFETY BOUNDARY

The following require the configured authorization policy and, where required,
human approval: production deployment, destructive infrastructure changes,
permanent deletion, financial transactions, external communications, physical
hardware operations, manufacturing, production firmware flashing, high-risk
electrical/power operations.

Never allow autonomous reasoning to override these boundaries.

---

## 15. PREVIEW ENGINE

Preview is a first-class subsystem. It is not merely an iframe.

Adapters must eventually support:

- **Web** — browser, responsive views, hot reload, visual inspection
- **Mobile** — device simulation
- **Desktop** — application runtime
- **Games** — interactive viewport, FPS, scene, console, assets, inspector, automated playtesting
- **Video** — timeline, script, assets, render preview
- **3D** — viewport, scene inspection
- **Embedded** — virtual hardware, telemetry, device state
- **Hardware** — Digital Twin interface
- **API** — API explorer
- **CLI** — terminal

Preview interface:

```text
start()  stop()  reload()  inspect()  capture()  status()
```

Preview failures must become observable QA/Feedback events.

---

## 16. QA/QC ENGINE

QA is independent from the implementation agent.

Support: functional testing, visual testing, security testing, performance testing,
regression testing, accessibility testing, chaos testing, adversarial testing,
synthetic-user testing, hardware QA, embedded fault injection.

Hardware QA includes, where applicable: pin conflicts, voltage, current, timing,
communication, thermal, power, watchdog, sensor disconnect, network loss, power
interruption.

---

## 17. EVIDENCE

Verification evidence must be first-class data.

```yaml
evidence_id:
test_id:
status:
severity:
expected:
actual:
reproduction:
artifacts:
logs:
screenshots:
environment:
provenance:
```

Completion must never be based solely on: *"The agent says it works."*

---

## 18. FEEDBACK ENGINE

**Failures → Deduplication → Clustering → Root Cause → Prioritization →
Corrective Tasks → Replanning**

The system should recognize that many symptoms may originate from one root cause.

```text
47 failures → cluster → 3 root causes → 3 corrective tasks
```

Feedback must integrate directly with Goal Mode.

---

## 19. DEFINITION OF DONE

A goal may become `COMPLETED` only when:

- mandatory requirements pass
- mandatory acceptance criteria pass
- required QA passes
- required regression tests pass
- required security tests pass
- required preview verification passes
- required performance targets pass
- critical defects = 0
- verification evidence exists
- provenance exists

Agent claims, successful builds, or passing compilation alone are insufficient.

---

## 20. CHECKPOINT / TIME TRAVEL

Checkpoints must preserve, where applicable: source, goal state, task state, agent
state, context, pending actions, QA state, feedback, artifacts, environment metadata.

Support: restore, compare, branch, replay.

---

## 21. MEMORY

- **Project Memory** — architecture, requirements, decisions
- **Goal Memory** — attempts, progress, failures
- **QA Memory** — defects, tests, regressions
- **Agent Memory** — agent performance and behavior
- **Session Memory** — current execution context
- **Engineering Experience Memory** — cross-project engineering knowledge

Memory must be structured and searchable.

---

## 22. ARTIFACT REGISTRY

Track lineage for: source code, images, video, audio, 3D assets, CAD, schematics,
PCB artifacts, firmware, binaries, datasets, telemetry, simulations.

Artifacts must have provenance and version information.

---

## 23. OBSERVABILITY

```text
Goal → Task → Agent → Model → Context → Tool → Command → File → Result → QA → Evidence
```

Record: duration, model, provider, tokens where available, tool calls, result,
failure, retries, resource usage, relevant provenance.

Do not expose private chain-of-thought. Provide concise auditable decision
summaries instead.

---

## 24. PROJECT-TYPE ADAPTERS

JaXir must eventually support: web, mobile, desktop, games, AI applications,
video/media, data/research, APIs, CLI, 3D, embedded systems, IoT, robotics,
electronics, hardware, simulation.

Each adapter defines: build, run, preview, QA, tools, environment, acceptance
criteria extensions, definition-of-done extensions.

Do not pollute the core kernel with project-specific logic.

---

## 25. DIGITAL TWIN

Hardware projects must eventually have a Digital Twin abstraction capable of
representing: devices, components, connections, sensors, actuators, power,
communications, firmware, telemetry, state, faults.

The Digital Twin should allow testing before physical deployment whenever practical.

---

## 26. SYNTHETIC USERS

For applicable applications, JaXir should eventually create synthetic user
personas to test: usability, workflows, accessibility, confusion, edge cases,
failure recovery.

Synthetic testing is evidence, not a replacement for all human validation.

---

## 27. PARALLEL WORLDS

JaXir should eventually support isolated alternative implementations (World A /
B / C), run them independently, compare quality, reliability, performance,
complexity, security, maintainability, and promote the best evidence-backed
solution. This capability must reinforce Occam's Razor rather than encourage
unnecessary experimentation.

---

## 28. RFC-002 — LEARNING & EVOLUTION SYSTEM

```text
Build → Observe → QA → Record Experience → Identify Patterns →
Generate Improvement Hypothesis → Experimental Branch → Benchmark → QA →
Compare Against Baseline → Promotion Gate → Production
```

---

## 29. ENGINEERING EXPERIENCE MEMORY

```yaml
experience:
  project_type:
  architecture:
  stack:
  outcome:
    completion_time:
    qa_iterations:
    critical_defects:
    regression_rate:
  successful_patterns:
  failures:
  lessons:
```

JaXir must learn from both successes and failures. A failure should record:

**Attempt → Failure → Root Cause → Correction → Verification → Outcome**

Do not merely store the final successful solution.

---

## 30. ENGINEERING GENOME

Create an eventual **JaXir Engineering Genome** containing evidence-backed patterns:

```text
Engineering Genome
├── Architecture Patterns
├── Coding Patterns
├── Failure Patterns
├── QA Patterns
├── Security Patterns
├── Performance Patterns
├── Agent Strategies
├── Model Routing Strategies
├── Planning Strategies
├── Tool Strategies
├── Project-Type Knowledge
└── Evolution Proposals
```

Patterns must include evidence:

```yaml
pattern_id:
claim:
evidence_count:
success_rate:
baseline:
confidence:
last_validated:
status:
```

---

## 31. EVOLUTION ENGINE

JaXir may identify: architecture bottlenecks, recurring failures, poor task
decomposition, inefficient context usage, unnecessary model calls, QA blind spots,
routing inefficiencies, preview problems, memory inefficiencies, orchestration
problems. It may then generate improvement proposals.

But: **Proposal ≠ Production Change.** Every proposal must enter an experimental
environment first.

---

## 32. EVOLUTION LAB

Maintain isolated experimental versions of JaXir. A proposal should be:

1. formulated
2. implemented in an isolated branch/environment
3. benchmarked
4. QA tested
5. security tested
6. regression tested
7. complexity evaluated
8. compared against baseline
9. accepted/rejected
10. archived or promoted

JaXir must never freely rewrite its production kernel without the promotion process.

---

## 33. SELF-IMPROVEMENT RULE

JaXir may autonomously: identify problems, analyze evidence, propose improvements,
implement experimental changes, run benchmarks, run QA, compare alternatives,
recommend promotion.

Production modification must obey the configured promotion policy. The system must
retain rollback capability.

---

## 34. OCCAM + EVOLUTION

Before promoting an improvement, ask:

1. What problem does it solve?
2. Is the problem measurable?
3. Can the existing architecture solve it?
4. Can a smaller change solve it?
5. What complexity does it add?
6. What failure modes does it introduce?
7. What operational burden does it introduce?
8. What measurable improvement does it provide?
9. Does the improvement justify the complexity?

If the answer is insufficient: **Reject the improvement.**

---

## 35. LOCKED NONFUNCTIONAL REQUIREMENTS

Preserve all NFR-001 through NFR-050. The implementation must cover, at minimum:

reliability, fault recovery, deterministic state, idempotency, observability,
auditability, security, secret protection, sandbox isolation, human safety
boundaries, availability, performance, scalability, concurrency, resource
governance, quota awareness, model independence, provider failure tolerance,
context efficiency, data integrity, reproducibility, testability, extensibility,
versioned contracts, backward compatibility, UI responsiveness, accessibility,
explainability, user control, graceful degradation, multimodal extensibility,
environment portability, privacy, network control, resource cleanup, crash
consistency, rate control, cost/capacity transparency, quality preservation,
autonomous-loop protection, evidence preservation, agent-runtime security, safe
hardware interaction, migration/export, internationalization, maintainability,
developer experience, local development, operational transparency,
Definition-of-Done integrity.

Do not remove or weaken any locked NFR.

---

## 36. LOCKED ACCEPTANCE TARGETS

Preserve all acceptance targets: AT-001 through AT-030 (general), AT-GM-001
through AT-GM-004 (Goal Mode), AT-QA-001 through AT-QA-003 (QA), AT-PREV-001
(Preview), AT-SEC-001 (Security), AT-HW-001 (Hardware).

Measurable targets include, among others:

- ≥99.5% reliability target for specified kernel tests
- ≥99% recoverable-run recovery target
- 100% auditable state transitions
- 10× idempotent replay equivalence testing
- 0 critical/high release-blocking security findings
- mandatory sandbox isolation tests
- protected-operation authorization
- ≥99.5% availability target where specified
- ≤100 ms typical local UI interaction target
- ≤250 ms event propagation target
- 100 concurrent goals / 1,000 active tasks / 100 active agents benchmark
- context reduction target ≥50% while retaining required information
- 10,000 artifact integrity operations
- ≥90% P0 kernel test coverage
- ≥95% reproducibility benchmark target
- autonomous Todo vertical slice ≥90% successful completion across 100 independent runs
- autonomous feedback correction targets
- 24+ hour Goal Mode endurance target
- autonomous-loop detection within ≤5 equivalent failures
- 100% evidence-backed completion
- 0 critical hardware safety findings

Do not lower targets merely because implementation is difficult. If a target is
temporarily impossible during an early milestone, mark it as **NOT YET
IMPLEMENTED**, not PASSED.

---

## 37. MACHINE-READABLE REQUIREMENT CHAIN

```text
Requirement → Acceptance Target → Test → Evidence → Release Gate
```

Every P0/P1 requirement must eventually be traceable through this chain.

```yaml
requirement_id:
acceptance_target_id:
metric:
threshold:
measurement_method:
test_suite:
severity:
environment:
evidence_required:
release_gate:
```

---

## 38. RELEASE GATES

- **Gate 0 — Kernel Integrity:** event integrity, state transitions, idempotency, recovery, checkpoint foundations
- **Gate 1 — Autonomous Execution:** Goal → Plan → Task → Agent → Execution, Codex runtime, recovery, basic Goal Mode
- **Gate 2 — Engineering Quality:** Preview, QA/QC, Feedback, Replanning, evidence, regression, security
- **Gate 3 — Production Readiness:** all P0 requirements, ≥95% P1 requirements, no release-blocking defects, automated evidence package

---

## 39. HUMAN CONTROL

Mission Control must eventually allow: pause, resume, cancel, approve, reject,
redirect, rollback, inspect, take control.

The system should clearly answer:

1. What am I trying to accomplish?
2. What is JaXir doing?
3. Is it working?
4. What has been verified?
5. What failed?
6. What is JaXir doing about it?
7. What needs my attention?

---

## 40. OBSERVABILITY UI

Mission Control should eventually visualize: goal progress, task graph, active
agents, model/provider, execution traces, preview, QA status, failures, feedback,
checkpoints, resource usage, quota state, evidence, evolution experiments.

Do not make the interface visually impressive at the expense of engineering
clarity. The UI itself follows Occam's Razor.

---

## 41. FAILURE HANDLING

- **Recoverable** — retry, fallback, restore, or replan.
- **Non-recoverable** — escalate, preserve evidence, stop unsafe execution.
- **Repeated failure** — detect loop/oscillation.

After repeated equivalent failures, JaXir should consider: changing strategy,
changing agent, changing model, changing provider, changing architecture, rolling
back, requesting intervention. Do not blindly retry forever.

---

## 42. AUTONOMOUS LOOP PROTECTION

Detect: repeated identical actions, repeated failures, oscillating plans,
duplicate tasks, ineffective retries, circular replanning.

The system must eventually do:

```text
retry → analyze → change strategy → replan → escalate if necessary
```

rather than: `retry forever`

---

## 43. SECURITY MODEL

Use: least privilege, sandboxing, capability-based permissions, scoped secrets,
secret redaction, revocation, network policies, audit logs, approval boundaries.

Secrets must not appear in model context unless explicitly required and
policy-authorized.

---

## 44. NETWORK MODES

```text
OFFLINE  LOCAL_ONLY  ALLOWLIST  UNRESTRICTED
```

Network access must be policy-controlled.

---

## 45. RESOURCE MANAGEMENT

Govern: CPU, RAM, disk, processes, execution time, agents, model requests, tokens,
concurrency, external requests, storage.

Always clean up resources after execution. Detect orphaned processes and environments.

---

## 46. REPRODUCIBILITY

Record enough provenance to reproduce important engineering results: source
version, dependency versions, environment, tools, agent, model, provider, task,
test suite, configuration, artifacts.

Exact LLM output determinism is not required. Engineering-result reproducibility is.

---

## 47. TESTING STRATEGY

Use: unit tests, integration tests, contract tests, end-to-end tests,
failure-recovery tests, security tests, performance tests, concurrency tests,
adversarial tests, regression tests.

Test the system itself as aggressively as JaXir will eventually test user projects.

---

## 48. EXTENSIBILITY

Prefer interfaces and versioned contracts where they genuinely reduce coupling.
Do not abstract prematurely. **Occam's Razor applies to abstraction.** Do not
create interfaces merely because an interface might someday be useful. Create them
where replaceability is an actual architectural requirement.

---

## 49. DEVELOPMENT PRINCIPLES

1. Inspect the existing repository before modifying anything.
2. Never overwrite existing work blindly.
3. Preserve existing functionality.
4. Implement one coherent vertical slice at a time.
5. Keep changes small and testable.
6. Run tests after meaningful changes.
7. Fix failures before proceeding.
8. Maintain architecture documentation.
9. Maintain requirement traceability.
10. Never claim a feature is complete without evidence.
11. Do not fake integrations.
12. Do not use placeholders where the locked architecture requires real behavior.
13. Clearly label temporary stubs.
14. Replace stubs progressively.
15. Prefer the simplest solution satisfying the requirements.

---

## 50. IMPLEMENTATION ORDER

- **Phase 1 — Kernel:** Event Bus, Event Store, Goal state machine, Task graph, Agent contract, Orchestrator, Checkpoint foundation, observability foundation
- **Phase 2 — Goal Mode:** Goal specification, Planner, task decomposition, dependency scheduling, execution lifecycle, failure/retry, replanning
- **Phase 3 — Model Runtime:** Model abstraction, Codex adapter, OmniRoute integration, routing policy, quota manager, context compiler
- **Phase 4 — Sandbox:** filesystem isolation, process isolation, network policy, resource limits, permission broker, secret handling
- **Phase 5 — Preview:** web preview, terminal, browser interaction, screenshot/inspection, preview evidence
- **Phase 6 — QA/QC:** test orchestration, evidence, defect classification, regression, security, performance, visual QA
- **Phase 7 — Feedback:** failure clustering, root-cause analysis, corrective tasks, replanning
- **Phase 8 — Mission Control:** the operational UI around actual kernel state (not a decorative UI disconnected from the system)
- **Phase 9 — Learning:** Engineering Experience Memory, pattern extraction, failure learning, agent performance learning, routing learning
- **Phase 10 — Evolution:** improvement proposals, Evolution Lab, benchmark harness, parallel worlds, complexity analysis, promotion gate, rollback

---

## 51. FIRST VERTICAL SLICE

The first end-to-end milestone is: **"Build a Todo application."**

```text
Natural language goal → Goal specification → Acceptance criteria → Plan →
Task graph → Codex → Sandbox → Build → Preview → QA →
  Failure? YES → Feedback → Replan → Fix → QA
           NO  → Verify → Evidence → Definition of Done → COMPLETED
```

The Todo application should support at least: add todo, complete todo, delete todo.
The system must produce evidence for each acceptance criterion.

---

## 52. FIRST IMPLEMENTATION SUCCESS CRITERION

The first implementation milestone is NOT: *"We have created a lot of code."*

It is: **JaXir can autonomously execute a small goal through the complete lifecycle
and prove that it succeeded.**

The smallest complete closed loop is more valuable than a large collection of
disconnected features.

---

## 53. ARCHITECTURAL QUALITY FUNCTION

```text
Quality = Autonomy × Reliability × Safety × Engineering Quality × Extensibility
```

But do not optimize one dimension by destroying another. In addition, evaluate
`Value / Complexity` and prefer higher-value, lower-complexity solutions when
requirements remain satisfied.

---

## 54. CHANGE CONTROL

The following are locked:

- RFC-001, RFC-002
- NFR-001 → NFR-050
- AT-001 → AT-030, AT-GM-001 → AT-GM-004, AT-QA-001 → AT-QA-003, AT-PREV-001, AT-SEC-001, AT-HW-001
- Release Gates
- Goal/Task/Event contracts
- Evidence-backed Definition of Done
- Occam Engineering Principle

Do not silently weaken them. If implementation requires a change:

1. identify the conflict
2. explain it
3. propose alternatives
4. evaluate complexity
5. document the change as an RFC/change proposal
6. obtain explicit authorization before changing the locked baseline

---

## 55. FINAL OPERATING RULE

**JaXir is not a chatbot. JaXir is not merely a coding agent. JaXir is not merely
an agent orchestrator. JaXir is an AI-native engineering operating system.**

Its purpose is to transform goals into verified engineering outcomes.

- Execution loop: **Goal → Plan → Build → Preview → QA → Feedback → Replan → Verify → Done**
- Evolution loop: **Experience → Learn → Improve → Experiment → Benchmark → Promote**
- Architectural philosophy: **Use the minimum necessary complexity to achieve the maximum reliable engineering outcome.**
- Completion philosophy: **No evidence, no completion.**
- Evolution philosophy: **No measurable improvement, no promotion.**
- Safety philosophy: **Autonomy within explicit boundaries.**
- Engineering philosophy: **Build simply. Test rigorously. Measure everything important. Learn from failure. Improve from evidence.**
