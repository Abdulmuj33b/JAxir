# JaXir OS

AI-native engineering operating system (initial vertical slice: Todo application).

Python 3.12 + stdlib only. pytest for kernel tests and acceptance evidence.

## Documentation

- [`docs/CONSTITUTION.md`](docs/CONSTITUTION.md) — the locked implementation
  baseline (RFC-001/002, NFR-001–050, acceptance targets, release gates). Change
  control is defined in §54; do not weaken locked requirements in passing.
- [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md) — the
  Requirement → Acceptance Target → Test → Evidence → Release Gate chain, with an
  honest per-subsystem status (`IMPLEMENTED` / `PARTIAL` / `STUB` /
  `NOT YET IMPLEMENTED`). Unfinished work is `NOT YET IMPLEMENTED`, never `PASSED`.

## Architecture at a glance

```
Goal Engine (state.py, spec.py, goalmode.py)
    → Task Graph (taskgraph.py)
    → Agent Orchestrator (orchestrator.py)
    → Sandbox (sandbox.py)
    → Preview (preview.py)
    → QA/QC (qa.py)
    → Feedback (feedback.py)
    → Replanning / Verify
```

Cross-cutting: Event Bus (`events.py`), Checkpoint Manager (`checkpoint.py`),
Artifact Registry + Memory (`registry.py`), Observability (`observability.py`),
Model Routing (`omniroute.py`), Permission Broker (`sandbox.py`).

## The vertical slice

`jaxir/todoslice.py` (`TodoApp`) drives a goal through the full lifecycle and
completes it only against evidence:

```python
from jaxir.todoslice import TodoApp
goal = TodoApp("/path/to/workspace").build()
goal.status                       # COMPLETED only if every evidence item passes
goal.verification_requirements    # per-criterion PASS/FAIL
```

Completion (`_verify`) requires all evidence `PASS`, zero critical/high defects,
and provenance on every item. A goal cannot be completed because an agent claimed
success or because the build compiled.

## Tests

```bash
python3 -m pytest -q
```

- `tests/test_kernel.py` — contracts, state machine, task graph, checkpoint, slice
- `tests/test_sandbox.py` — filesystem confinement, secrets, rlimits, network mode, permissions
- `tests/test_qa.py` — regression/security/performance suites, DoD integrity

