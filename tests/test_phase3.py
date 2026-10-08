"""Tests for the Context Compiler (section 10) and Quota Manager (section 11).

The locked acceptance target under test is a >=50% context reduction while
retaining required information. Both halves are asserted: the reduction is
measured, and the required items are proven never to be dropped.
"""

import sys

import pytest

sys.path.insert(0, ".")

from jaxir import models
from jaxir.context import (
    REDUCTION_TARGET,
    ContextCompiler,
    ContextItem,
    estimate_tokens,
)
from jaxir.events import EventBus
from jaxir.quota import (
    DEGRADED,
    EXHAUSTED,
    HEALTHY,
    ProviderSpec,
    QuotaExhausted,
    QuotaManager,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _realistic_sources(failure_reports: int = 40, files: int = 30):
    """A corpus with the redundancy a real project produces.

    Many equivalent failure reports (section 18: many symptoms, few root
    causes), duplicated decisions, and more files than the budget allows.
    """
    return {
        "requirements": [{"id": "R1", "text": "todo add/complete/delete"}],
        "acceptance_criteria": [{"id": "C1", "metric": "e2e", "threshold": True},
                                {"id": "C2", "metric": "cli_contract", "threshold": True}],
        # 40 reports of ONE root cause.
        "failures": [{"cluster_id": f"c{i}", "failure_event": "qa.regression",
                      "symptoms": ["qa.regression:FAIL"], "severity": "high",
                      "detail": "id assignment reused an existing id"}
                     for i in range(failure_reports)],
        # Repeated architecture decisions (identical content).
        "decisions": [{"summary_id": "d1", "decision": "stdlib-only dependencies",
                       "rationale": "Occam Engineering"}] * 10,
        "files": {f"module_{i}.py": f"# module {i}\n" + ("x = 1\n" * 20)
                  for i in range(files)},
        "memory": [{"id": f"m{i}", "note": "prior attempt note"} for i in range(10)],
    }


def _goal():
    return models.Goal(project_id="p", user_intent="Build a Todo application",
                       title="Todo", goal_id="g-1")


def _task():
    return models.Task(goal_id="g-1", owner_agent_id="planner", agent_type="coder",
                       task_id="t-1", capabilities=["coding"],
                       inputs={"project_dir": "/tmp/x"},
                       acceptance_criteria=[{"id": "C1", "metric": "e2e"}])


# ----------------------------------------------------------------------
# Context Compiler
# ----------------------------------------------------------------------

class TestContextCompilerReduction:
    """Locked target: >=50% reduction while retaining required information."""

    def test_meets_locked_reduction_target(self):
        bundle = ContextCompiler(token_budget=4000).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        assert bundle.reduction >= REDUCTION_TARGET, (
            f"reduction {bundle.reduction:.2%} < target {REDUCTION_TARGET:.0%}")
        assert bundle.meets_target is True

    def test_reduction_is_measured_not_asserted(self):
        bundle = ContextCompiler(token_budget=4000).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        # Independently recompute from the reported token counts.
        expected = 1.0 - (bundle.tokens_after / bundle.tokens_before)
        assert bundle.reduction == pytest.approx(expected)
        assert bundle.tokens_after < bundle.tokens_before

    def test_evidence_payload_reports_the_target(self):
        bundle = ContextCompiler().compile(goal=_goal(), task=_task(),
                                           sources=_realistic_sources())
        ev = ContextCompiler.evidence(bundle)
        assert ev["test_id"] == "context.reduction"
        assert ev["meets_target"] is True
        assert ev["target"] == REDUCTION_TARGET
        assert ev["required_retained"] is True

    def test_smaller_budget_reduces_further(self):
        small = ContextCompiler(token_budget=200).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        large = ContextCompiler(token_budget=8000).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        assert small.tokens_after <= large.tokens_after
        assert small.reduction >= large.reduction


class TestContextCompilerRetention:
    """Optimization must not sacrifice engineering quality (section 10)."""

    def test_required_items_are_never_dropped(self):
        bundle = ContextCompiler(token_budget=50).compile(  # absurdly small
            goal=_goal(), task=_task(), sources=_realistic_sources())
        required = [i for i in bundle.items if i.required]
        assert required, "required items must survive an impossibly small budget"
        kinds = {i.kind for i in required}
        assert "requirement" in kinds
        assert "acceptance_criterion" in kinds
        assert "task" in kinds

    def test_acceptance_criteria_always_survive(self):
        bundle = ContextCompiler(token_budget=10).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        kept = {i.key for i in bundle.items if i.kind == "acceptance_criterion"}
        assert "C1" in kept

    def test_over_budget_is_reported_not_hidden(self):
        bundle = ContextCompiler(token_budget=10).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        assert bundle.over_budget is True
        assert bundle.to_dict()["required_retained"] is True

    def test_dropped_items_are_accounted(self):
        bundle = ContextCompiler(token_budget=300).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        assert bundle.dropped
        kept_keys = {i.key for i in bundle.items}
        assert not (kept_keys & {i.key for i in bundle.dropped})


class TestContextCompilerCompression:
    def test_identical_items_are_deduplicated(self):
        bundle = ContextCompiler(token_budget=4000).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        ops = [o["op"] for o in bundle.operations]
        assert "dedupe" in ops

    def test_equivalent_failures_collapse_to_one_item(self):
        """40 reports of one root cause must not cost 40 context items."""
        bundle = ContextCompiler(token_budget=4000).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        failures = [i for i in bundle.items if i.kind == "failure"]
        assert len(failures) == 1
        assert failures[0].metadata["member_count"] == 40
        collapse = [o for o in bundle.operations
                    if o["op"] == "collapse_equivalent_failures"]
        assert collapse and collapse[0]["collapsed"] == 39

    def test_distinct_failures_are_not_merged(self):
        sources = _realistic_sources(failure_reports=0)
        sources["failures"] = [
            {"cluster_id": "a", "failure_event": "qa.regression",
             "symptoms": ["qa.regression:FAIL"]},
            {"cluster_id": "b", "failure_event": "qa.security",
             "symptoms": ["qa.security:FAIL"]},
        ]
        bundle = ContextCompiler(token_budget=4000).compile(
            goal=_goal(), task=_task(), sources=sources)
        assert len([i for i in bundle.items if i.kind == "failure"]) == 2

    def test_compression_operations_are_reported(self):
        bundle = ContextCompiler(token_budget=4000).compile(
            goal=_goal(), task=_task(), sources=_realistic_sources())
        assert bundle.operations, "compression must be auditable"


class TestContextCompilerRanking:
    def test_cheaper_items_win_ties(self):
        items = [ContextItem("file", "big", "x" * 100),   # ~25 tokens
                 ContextItem("file", "small", "xx")]      # 1 token
        for i in items:
            i.relevance = 0.5
        kept, dropped = ContextCompiler(token_budget=10)._select(items)
        assert [i.key for i in kept] == ["small"]
        assert [i.key for i in dropped] == ["big"]

    def test_higher_relevance_wins(self):
        items = [ContextItem("file", "irrelevant", "x" * 40),
                 ContextItem("decision", "important", "x" * 40)]
        items[0].relevance, items[1].relevance = 0.1, 0.9
        kept, _ = ContextCompiler(token_budget=10)._select(items)
        assert [i.key for i in kept] == ["important"]

    def test_scoring_is_deterministic(self):
        a = ContextCompiler().compile(goal=_goal(), task=_task(),
                                      sources=_realistic_sources())
        b = ContextCompiler().compile(goal=_goal(), task=_task(),
                                      sources=_realistic_sources())
        assert [i.key for i in a.items] == [i.key for i in b.items]
        assert a.tokens_after == b.tokens_after

    def test_task_definition_boosts_matching_context(self):
        compiler = ContextCompiler()
        task = models.Task(goal_id="g", owner_agent_id="p", agent_type="coder",
                           task_id="t", capabilities=["coding"],
                           inputs={"project_dir": "/proj"})
        items = [ContextItem("memory", "related", "coding project_dir artifact"),
                 ContextItem("memory", "unrelated", "gardening weather")]
        compiler._score(items, _goal(), task)
        assert items[0].relevance > items[1].relevance

    def test_empty_sources_produce_empty_bundle(self):
        bundle = ContextCompiler().compile(goal=None, task=None, sources={})
        assert bundle.items == []
        assert bundle.reduction == 0.0  # nothing to reduce, not a silent pass


class TestBundleRendering:
    def test_render_is_stable_and_labelled(self):
        bundle = ContextCompiler().compile(goal=_goal(), task=_task(),
                                           sources=_realistic_sources())
        text = bundle.render()
        assert "[task:t-1]" in text
        assert "[acceptance_criterion:C1]" in text

    def test_to_dict_is_json_serialisable(self):
        import json
        bundle = ContextCompiler().compile(goal=_goal(), task=_task(),
                                           sources=_realistic_sources())
        json.dumps(bundle.to_dict())

    def test_token_estimate_is_sane(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("a" * 400) == 100


class TestCompilerValidation:
    def test_invalid_budget_rejected(self):
        with pytest.raises(ValueError):
            ContextCompiler(token_budget=0)


# ----------------------------------------------------------------------
# Quota Manager
# ----------------------------------------------------------------------

def _quota(bus=None, **spec_kwargs):
    q = QuotaManager(bus)
    q.register(ProviderSpec(name="local", capabilities=["code"], **spec_kwargs))
    return q


class TestQuotaAccounting:
    def test_unregistered_provider_is_not_available(self):
        assert QuotaManager().available("nope") is False

    def test_acquire_and_record_success(self):
        q = _quota(rate_limit=10)
        q.acquire("local", tokens=100)
        state = q.record_success("local", latency_ms=12.0, tokens=50)
        assert state.requests == 1 and state.tokens == 150
        assert state.successes == 1 and state.health == HEALTHY

    def test_acquire_refuses_unknown_provider(self):
        with pytest.raises(KeyError):
            QuotaManager().acquire("nope")

    def test_latency_percentiles_are_recorded(self):
        q = _quota()
        for ms in (10, 20, 30, 40, 50):
            q.record_success("local", latency_ms=ms)
        state = q.states["local"]
        assert state.latency_p50_ms == 30.0   # middle of five samples
        assert state.latency_p95_ms == 50.0   # nearest-rank ceiling
        assert q.states["local"].latencies_ms == [10.0, 20.0, 30.0, 40.0, 50.0]


class TestQuotaLimitsNeverBypassed:
    """Section 11: never bypass provider restrictions or quotas."""

    def test_rate_limit_refuses_rather_than_exceeding(self):
        q = _quota(rate_limit=2)
        q.acquire("local")
        q.acquire("local")
        with pytest.raises(QuotaExhausted):
            q.acquire("local")
        assert q.states["local"].requests == 2  # not 3
        assert q.states["local"].health == EXHAUSTED

    def test_token_limit_refuses(self):
        q = _quota(token_limit=100)
        with pytest.raises(QuotaExhausted):
            q.acquire("local", tokens=101)
        assert q.states["local"].requests == 0

    def test_refusals_are_recorded_with_a_reason(self):
        q = _quota(rate_limit=1)
        q.acquire("local")
        with pytest.raises(QuotaExhausted):
            q.acquire("local")
        assert q.refusals()[0]["reason"] == "rate_limit_reached"

    def test_provider_reported_exhaustion_is_honoured(self):
        q = _quota(rate_limit=1000)
        q.mark_reported_exhausted("local")
        assert q.available("local") is False
        with pytest.raises(QuotaExhausted):
            q.acquire("local")
        assert q.states["local"].health == EXHAUSTED

    def test_window_rollover_restores_capacity(self):
        clock = {"t": 0.0}
        q = QuotaManager(clock=lambda: clock["t"])
        q.register(ProviderSpec(name="local", capabilities=["code"],
                                rate_limit=1, window_s=60))
        q.acquire("local")
        assert q.available("local") is False
        clock["t"] = 61.0
        assert q.available("local") is True
        q.acquire("local")  # new window, capacity restored


class TestQuotaHealth:
    def test_failure_ratio_degrades_then_refuses(self):
        q = _quota(rate_limit=100, )
        q.degrade_failure_ratio = 0.5
        q.min_samples = 2
        q.record_success("local")
        state = q.record_failure("local", error="boom")
        assert state.health == DEGRADED
        with pytest.raises(QuotaExhausted):
            q.acquire("local")

    def test_health_events_are_emitted(self):
        bus = EventBus()
        q = _quota(bus, rate_limit=100)
        q.degrade_failure_ratio = 0.5
        q.min_samples = 2
        q.record_success("local")
        q.record_failure("local", error="boom")
        assert bus.count(models.EventType.PROVIDER_DEGRADED) == 1

    def test_exhaustion_event_emitted(self):
        bus = EventBus()
        q = _quota(bus, rate_limit=1)
        q.acquire("local")
        with pytest.raises(QuotaExhausted):
            q.acquire("local")
        assert bus.count(models.EventType.PROVIDER_EXHAUSTED) == 1

    def test_recovery_to_healthy_is_emitted(self):
        bus = EventBus()
        q = _quota(bus, rate_limit=100)
        q.degrade_failure_ratio = 0.5
        q.min_samples = 2
        q.record_failure("local")
        q.record_failure("local")
        assert q.states["local"].health == DEGRADED
        for _ in range(6):
            q.record_success("local")
        assert q.states["local"].health == HEALTHY
        assert bus.count(models.EventType.PROVIDER_HEALTHY) >= 1

    def test_model_failed_event_on_failure(self):
        bus = EventBus()
        q = _quota(bus)
        q.record_failure("local", error="timeout")
        assert bus.count(models.EventType.MODEL_FAILED) == 1

    def test_no_event_when_health_unchanged(self):
        bus = EventBus()
        q = _quota(bus, rate_limit=100)
        q.record_success("local")
        q.record_success("local")
        assert bus.count(models.EventType.PROVIDER_HEALTHY) == 0

    def test_degraded_provider_is_not_selected(self):
        q = _quota(rate_limit=100)
        q.degrade_failure_ratio = 0.5
        q.min_samples = 2
        q.record_success("local")
        q.record_failure("local")
        assert q.healthy_providers("code") == []

    def test_snapshot_is_honest_about_its_limits(self):
        snap = _quota().snapshot()
        assert "cannot see" in snap["note"]
        assert "never bypasses" in snap["note"]


class TestQuotaRoutingSupport:
    def _multi(self):
        q = QuotaManager()
        q.register(ProviderSpec(name="premium", capabilities=["code"],
                                rate_limit=10, unit_cost=10.0))
        q.register(ProviderSpec(name="cheap", capabilities=["code"],
                                rate_limit=10, unit_cost=0.5))
        return q

    def test_cheapest_adequate_provider_is_preferred(self):
        assert self._multi().cheapest("code") == "cheap"

    def test_healthy_providers_filtered_by_capability(self):
        q = self._multi()
        assert sorted(q.healthy_providers("code")) == ["cheap", "premium"]
        assert q.healthy_providers("vision") == []

    def test_cheapest_returns_none_when_none_available(self):
        q = self._multi()
        q.mark_reported_exhausted("premium")
        q.mark_reported_exhausted("cheap")
        assert q.cheapest("code") is None


# ----------------------------------------------------------------------
# Integration: routing is capacity-aware
# ----------------------------------------------------------------------

class TestCapacityAwareRouting:
    def _router(self, quota):
        from jaxir.omniroute import OmniRoute, RoutingPolicy
        return OmniRoute(RoutingPolicy(), EventBus(), quota=quota)

    def test_selects_an_available_provider(self):
        q = _quota(rate_limit=10)
        decision = self._router(q).select(_task(), _goal())
        assert decision["available"] is True
        assert decision["provider"] == "local"

    def test_refuses_when_no_provider_is_available(self):
        q = _quota(rate_limit=10)
        q.mark_reported_exhausted("local")
        decision = self._router(q).select(_task(), _goal())
        assert decision["available"] is False
        assert decision["reason"] == "no_provider_available"

    def test_refusal_emits_model_failed_not_requested(self):
        bus = EventBus()
        from jaxir.omniroute import OmniRoute, RoutingPolicy
        q = QuotaManager(bus)
        q.register(ProviderSpec(name="local", capabilities=["code"], rate_limit=1))
        q.mark_reported_exhausted("local")
        OmniRoute(RoutingPolicy(), bus, quota=q).select(_task(), _goal())
        assert bus.count(models.EventType.MODEL_FAILED) == 1
        assert bus.count(models.EventType.MODEL_REQUESTED) == 0

    def test_picks_the_cheapest_available(self):
        q = QuotaManager()
        q.register(ProviderSpec(name="premium", capabilities=["code"],
                                rate_limit=10, unit_cost=9.0))
        q.register(ProviderSpec(name="cheap", capabilities=["code"],
                                rate_limit=10, unit_cost=1.0))
        assert self._router(q).select(_task(), _goal())["provider"] == "cheap"

    def test_complexity_escalates_the_model(self):
        q = _quota(rate_limit=100)
        task = _task()
        task.acceptance_criteria = [{"id": f"C{i}"} for i in range(9)]
        decision = self._router(q).select(task, _goal())
        assert decision["model_id"] == "codex-max"

    def test_no_quota_manager_keeps_legacy_behaviour(self):
        from jaxir.omniroute import OmniRoute, RoutingPolicy
        decision = OmniRoute(RoutingPolicy(), EventBus(), quota=None).select(
            _task(), _goal())
        assert decision["available"] is True


# ----------------------------------------------------------------------
# Integration: the slice exercises both
# ----------------------------------------------------------------------

class TestSliceIntegration:
    def test_slice_completes_and_accounts_for_providers(self, tmp_path):
        from jaxir.todoslice import TodoApp
        app = TodoApp(str(tmp_path))
        goal = app.build()
        assert goal.status == models.GoalStatus.COMPLETED
        assert app.bus.count(models.EventType.MODEL_REQUESTED) >= 1
        assert app.bus.count(models.EventType.MODEL_COMPLETED) >= 1
        # Every task carries a compiled context bundle with its accounting.
        for task in goal.task_graph:
            ctx = (task.inputs or {}).get("context")
            assert ctx and "reduction" in ctx and "tokens_before" in ctx

    def test_slice_context_never_drops_required_items(self, tmp_path):
        from jaxir.todoslice import TodoApp
        goal = TodoApp(str(tmp_path)).build()
        for task in goal.task_graph:
            ctx = (task.inputs or {}).get("context") or {}
            if ctx:
                assert ctx["required_retained"] is True

    def test_a_refused_provider_blocks_the_task_honestly(self, tmp_path,
                                                         monkeypatch):
        """No capacity must not silently look like success (section 11)."""
        from jaxir import todoslice
        from jaxir.todoslice import TodoApp

        class _ExhaustedQuota(QuotaManager):
            def register(self, spec):
                super().register(spec)
                self.mark_reported_exhausted(spec.name)
                return spec

        monkeypatch.setattr(todoslice, "QuotaManager", _ExhaustedQuota)
        app = TodoApp(str(tmp_path))
        goal = app.build()

        # Every task was refused rather than run against unavailable capacity.
        assert all(t.status == models.TaskStatus.BLOCKED for t in goal.task_graph)
        assert app.bus.count(models.EventType.MODEL_FAILED) >= 1
        assert app.bus.count(models.EventType.MODEL_REQUESTED) == 0
        # And the goal did not report success it could not evidence.
        assert goal.status != models.GoalStatus.COMPLETED

    def test_provider_outcomes_are_accounted_in_the_slice(self, tmp_path):
        from jaxir.todoslice import TodoApp
        app = TodoApp(str(tmp_path))
        app.build()
        state = app.quota.states["codex"]
        assert state.successes >= 1
        assert state.health == HEALTHY
