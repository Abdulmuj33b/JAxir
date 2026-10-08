"""JaXir OS -- AI-native engineering operating system.

Initial vertical slice: autonomous execution of a small goal (Todo application)
through the complete kernel lifecycle: Goal -> Plan -> Task -> Agent -> Build ->
Preview -> QA -> Evidence -> Feedback -> Replan -> Verify -> Done.

Usage (no eager import cycles)::

    from jaxir import state, events, models
    from jaxir import goalemode, orchestrator, checkpoint
    from jaxir import spec, omniroute, preview, qa, feedback, sandbox
"""

__all__ = [
    # contracts
    "models",
    # cross-cutting
    "events",
    "observability",
    "checkpoint",
    "mission_control",
    "traceability",
    "cli",
    # kernel
    "state",
    "taskgraph",
    "orchestrator",
    # goal mode
    "goalmode",
    "spec",
    # model runtime
    "omniroute",
    # sandbox
    "sandbox",
    # build/preview
    "preview",
    # qa
    "qa",
    # feedback
    "feedback",
]
