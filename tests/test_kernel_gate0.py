"""Gate 0 — Kernel Integrity tests (constitution sections 12, 20, 38).

Covers event integrity (durable, append-only, tamper-evident), checkpoint
foundations (branch / replay), idempotency (the locked 10x replay-equivalence
target), and recovery (resume an interrupted run).
"""

import json
import sys

import pytest

sys.path.insert(0, ".")

from jaxir import models
from jaxir.checkpoint import CheckpointManager
from jaxir.events import EventBus
from jaxir.eventstore import (
    EventStore,
    EventStoreCorruption,
    GENESIS,
    json_safe,
    replay_equivalence_report,
)


def _event(kind=models.EventType.GOAL_CREATED, goal_id="g1", project_id="p1",
           payload=None, task_id=None):
    return models.Event(event_type=kind, project_id=project_id, goal_id=goal_id,
                        task_id=task_id, payload=payload or {})


def _bus_with_store(tmp_path):
    store = EventStore(tmp_path / "log")
    return EventBus(store=store), store


# ----------------------------------------------------------------------
# Event integrity (section 12)
# ----------------------------------------------------------------------

class TestEventStoreAppend:
    def test_records_are_appended_in_order(self, tmp_path):
        store = EventStore(tmp_path / "log")
        for i in range(3):
            store.append(_event(payload={"i": i}))
        records = store.records()
        assert [r["seq"] for r in records] == [1, 2, 3]

    def test_roundtrip_preserves_event_fields(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        original = models.Event(event_type=models.EventType.QA_PASSED,
                                project_id="p", goal_id="g", task_id="t",
                                agent_id="a", source="qa", payload={"k": "v"},
                                correlation_id="c1", causation_id="c0")
        bus.publish(original)  # the bus stamps schema_version and timestamp
        restored = store.events()[0]
        assert restored.event_type is models.EventType.QA_PASSED
        assert restored.payload == {"k": "v"}
        assert restored.correlation_id == "c1"
        assert restored.causation_id == "c0"
        assert restored.agent_id == "a"
        assert restored.source == "qa"
        assert restored.schema_version == EventBus.SCHEMA_VERSION

    def test_event_appended_without_publish_keeps_default_version(self, tmp_path):
        """A raw Event is version 0 until the bus stamps it (envelope, §12)."""
        store = EventStore(tmp_path / "log")
        store.append(models.Event(event_type=models.EventType.GOAL_CREATED,
                                  project_id="p", goal_id="g"))
        assert store.events()[0].schema_version == 0

    def test_first_record_links_to_genesis(self, tmp_path):
        store = EventStore(tmp_path / "log")
        store.append(_event())
        assert store.records()[0]["prev"] == GENESIS

    def test_chain_links_each_record_to_the_previous(self, tmp_path):
        store = EventStore(tmp_path / "log")
        store.append(_event())
        store.append(_event())
        records = store.records()
        assert records[1]["prev"] == records[0]["checksum"]

    def test_bus_persists_every_published_event(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        for _ in range(5):
            bus.publish(_event())
        assert len(store) == 5
        assert store.latest_seq() == 5

    def test_reopening_a_store_continues_the_chain(self, tmp_path):
        path = tmp_path / "log"
        first = EventStore(path)
        first.append(_event())
        head = first.records()[0]["checksum"]
        second = EventStore(path)
        second.append(_event())
        records = second.records()
        assert len(records) == 2
        assert records[1]["prev"] == head
        assert second.verify()["ok"] is True

    def test_events_are_historical_not_mutated(self, tmp_path):
        """Appending must not rewrite earlier records."""
        store = EventStore(tmp_path / "log")
        store.append(_event(payload={"n": 1}))
        before = store.records()[0]
        for _ in range(3):
            store.append(_event(payload={"n": 2}))
        assert store.records()[0] == before


class TestEventStoreIntegrity:
    def test_verified_chain(self, tmp_path):
        store = EventStore(tmp_path / "log")
        for _ in range(4):
            store.append(_event())
        verdict = store.verify()
        assert verdict["ok"] is True and verdict["count"] == 4

    def test_tampering_with_a_record_is_detected_and_located(self, tmp_path):
        store = EventStore(tmp_path / "log")
        for _ in range(3):
            store.append(_event())
        lines = store.path.read_text().splitlines()
        record = json.loads(lines[1])
        record["event"]["payload"] = {"tampered": True}
        lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
        store.path.write_text("\n".join(lines) + "\n")

        verdict = EventStore(tmp_path / "log").verify()
        assert verdict["ok"] is False
        assert verdict["reason"] == "checksum_mismatch"
        assert verdict["at_seq"] == 2

    def test_deleting_a_record_breaks_the_chain(self, tmp_path):
        store = EventStore(tmp_path / "log")
        for _ in range(3):
            store.append(_event())
        lines = store.path.read_text().splitlines()
        del lines[1]
        store.path.write_text("\n".join(lines) + "\n")

        verdict = EventStore(tmp_path / "log").verify()
        assert verdict["ok"] is False
        assert verdict["reason"] == "sequence_gap"

    def test_reordering_records_is_detected(self, tmp_path):
        store = EventStore(tmp_path / "log")
        for _ in range(3):
            store.append(_event())
        lines = store.path.read_text().splitlines()
        lines[0], lines[2] = lines[2], lines[0]
        store.path.write_text("\n".join(lines) + "\n")
        assert EventStore(tmp_path / "log").verify()["ok"] is False

    def test_unreadable_record_raises_corruption(self, tmp_path):
        store = EventStore(tmp_path / "log")
        store.append(_event())
        with store.path.open("a", encoding="utf-8") as f:
            f.write("{not json\n")
        with pytest.raises(EventStoreCorruption):
            store.records()

    def test_empty_store_verifies(self, tmp_path):
        assert EventStore(tmp_path / "log").verify()["ok"] is True


class TestEventStoreRebuild:
    def _record_run(self, bus, goal_id="g1"):
        bus.publish(models.Event(event_type=models.EventType.GOAL_CREATED,
                                 project_id="p", goal_id=goal_id,
                                 payload={"title": "t"}))
        bus.publish(models.Event(event_type=models.EventType.GOAL_ANALYZED,
                                 project_id="p", goal_id=goal_id,
                                 payload={"from": "CREATED", "to": "ANALYZING"}))
        bus.publish(models.Event(event_type=models.EventType.GOAL_PLANNED,
                                 project_id="p", goal_id=goal_id,
                                 payload={"from": "ANALYZING", "to": "PLANNED"}))
        bus.publish(models.Event(event_type=models.EventType.TASK_ASSIGNED,
                                 project_id="p", goal_id=goal_id, task_id="t1",
                                 payload={"agent_id": "coder"}))
        bus.publish(models.Event(event_type=models.EventType.TASK_STARTED,
                                 project_id="p", goal_id=goal_id, task_id="t1"))
        bus.publish(models.Event(event_type=models.EventType.TASK_COMPLETED,
                                 project_id="p", goal_id=goal_id, task_id="t1"))

    def test_rebuild_folds_goal_status(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        self._record_run(bus)
        state = store.rebuild(goal_id="g1")
        assert state["goal_status"] == "PLANNED"

    def test_rebuild_folds_task_status(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        self._record_run(bus)
        assert store.rebuild(goal_id="g1")["tasks"]["t1"] == "COMPLETED"

    def test_rebuild_records_transitions(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        self._record_run(bus)
        assert [t["to"] for t in store.rebuild(goal_id="g1")["transitions"]] == \
            ["ANALYZING", "PLANNED"]

    def test_rebuild_isolates_goals(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        self._record_run(bus, goal_id="g1")
        self._record_run(bus, goal_id="g2")
        assert store.rebuild(goal_id="g1")["goal_id"] == "g1"

    def test_point_in_time_replay(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        self._record_run(bus)
        early = store.rebuild(goal_id="g1", upto_seq=2)
        late = store.rebuild(goal_id="g1")
        assert early["goal_status"] == "ANALYZING"
        assert late["goal_status"] == "PLANNED"


# ----------------------------------------------------------------------
# Idempotency: locked 10x replay equivalence
# ----------------------------------------------------------------------

class TestReplayEquivalence:
    def test_ten_times_replay_equivalence(self, tmp_path):
        """Locked acceptance target: 10x idempotent replay equivalence."""
        bus, store = _bus_with_store(tmp_path)
        for i in range(20):
            bus.publish(_event(payload={"i": i}))
        verdict = store.replay_equivalent(runs=10, goal_id="g1")
        assert verdict["equivalent"] is True
        assert verdict["runs"] == 10

    def test_rebuild_is_a_function_of_the_log(self, tmp_path):
        """Same log, fresh store object, same result."""
        path = tmp_path / "log"
        bus = EventBus(store=EventStore(path))
        for i in range(5):
            bus.publish(_event(payload={"i": i}))
        assert EventStore(path).rebuild("g1") == EventStore(path).rebuild("g1")

    def test_evidence_report_states_integrity_and_equivalence(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        bus.publish(_event())
        report = replay_equivalence_report(store, goal_id="g1", runs=10)
        assert report["test_id"] == "kernel.replay_equivalence"
        assert report["equivalent"] is True
        assert report["integrity_ok"] is True
        assert report["runs"] == 10

    def test_hydrate_rebuilds_bus_history(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        for i in range(4):
            bus.publish(_event(payload={"i": i}))
        fresh = EventBus(store=store)
        loaded = fresh.hydrate()
        assert loaded == 4
        assert fresh.count() == 4
        assert len(store) == 4  # hydration must not duplicate the log

    def test_hydrate_is_idempotent_against_the_log(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        bus.publish(_event())
        EventBus(store=store).hydrate()
        EventBus(store=store).hydrate()
        assert len(store) == 1


# ----------------------------------------------------------------------
# Checkpoint foundations: branch / replay (section 20)
# ----------------------------------------------------------------------

def _goal():
    return models.Goal(project_id="todo", user_intent="build", title="t",
                       goal_id="g1")


def _task(task_id="t1", status=models.TaskStatus.PENDING):
    return models.Task(goal_id="g1", owner_agent_id="p", task_id=task_id,
                       agent_type="coder", capabilities=["coding"], status=status)


class TestCheckpointBranch:
    def test_branch_creates_an_independent_checkpoint(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        parent = mgr.create(_goal(), [_task()], [], {}, {}, [], [], {})
        child = mgr.branch(parent.checkpoint_id, name="world-b")
        assert child.checkpoint_id != parent.checkpoint_id
        assert child.goal_id == parent.goal_id

    def test_branch_records_lineage(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        parent = mgr.create(_goal(), [_task()], [], {}, {}, [], [], {})
        child = mgr.branch(parent.checkpoint_id, name="world-b")
        assert child.context["parent_checkpoint_id"] == parent.checkpoint_id
        assert child.context["branch_name"] == "world-b"
        assert mgr.lineage(child.checkpoint_id) == [child.checkpoint_id,
                                                    parent.checkpoint_id]

    def test_branch_does_not_modify_the_parent(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        parent = mgr.create(_goal(), [_task()], [], {"a": 1}, {}, [], [], {})
        original = mgr._load(parent.checkpoint_id)
        mgr.branch(parent.checkpoint_id)
        assert mgr._load(parent.checkpoint_id) == original

    def test_branch_preserves_state(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        parent = mgr.create(_goal(), [_task(status=models.TaskStatus.COMPLETED)],
                            [], {}, {}, [], [], {})
        child = mgr.branch(parent.checkpoint_id)
        assert child.task_state["t1"]["status"] == "COMPLETED"

    def test_branch_emits_a_checkpoint_event(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        parent = mgr.create(_goal(), [_task()], [], {}, {}, [], [], {})
        before = bus.count(models.EventType.CHECKPOINT_CREATED)
        mgr.branch(parent.checkpoint_id, name="b")
        payload = bus.get(event_type=models.EventType.CHECKPOINT_CREATED)[-1].payload
        assert payload["parent_checkpoint_id"] == parent.checkpoint_id
        assert bus.count(models.EventType.CHECKPOINT_CREATED) == before + 1

    def test_lineage_survives_multiple_generations(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        cp = mgr.create(_goal(), [_task()], [], {}, {}, [], [], {})
        ids = [cp.checkpoint_id]
        for i in range(3):
            cp = mgr.branch(cp.checkpoint_id, name=f"b{i}")
            ids.append(cp.checkpoint_id)
        assert mgr.lineage(ids[-1]) == list(reversed(ids))


class TestCheckpointReplay:
    def test_checkpoint_is_anchored_to_an_event_seq(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        bus.publish(_event())
        cp = mgr.create(_goal(), [_task()], [], {}, {}, [], [], {})
        assert cp.context["event_seq"] == 1

    def test_replay_reconstructs_recorded_state(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        goal = _goal()
        bus.publish(models.Event(event_type=models.EventType.GOAL_CREATED,
                                 project_id="todo", goal_id="g1",
                                 payload={"to": "CREATED"}))
        bus.publish(models.Event(event_type=models.EventType.GOAL_PLANNED,
                                 project_id="todo", goal_id="g1",
                                 payload={"from": "CREATED", "to": "PLANNED"}))
        goal.status = models.GoalStatus.PLANNED
        cp = mgr.create(goal, [_task()], [], {}, {}, [], [], {})
        bus.publish(models.Event(event_type=models.EventType.TASK_STARTED,
                                 project_id="todo", goal_id="g1", task_id="t1"))
        result = mgr.replay(cp.checkpoint_id)
        assert result["ok"] is True
        assert result["rebuilt_goal_status"] == "PLANNED"

    def test_replay_ignores_events_after_the_checkpoint(self, tmp_path):
        """Replay is point-in-time: later events must not leak into the result."""
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        goal = _goal()
        bus.publish(models.Event(event_type=models.EventType.GOAL_CREATED,
                                 project_id="todo", goal_id="g1",
                                 payload={"to": "CREATED"}))
        cp = mgr.create(goal, [], [], {}, {}, [], [], {})
        bus.publish(models.Event(event_type=models.EventType.GOAL_COMPLETED,
                                 project_id="todo", goal_id="g1",
                                 payload={"from": "CREATED", "to": "COMPLETED"}))
        result = mgr.replay(cp.checkpoint_id)
        assert result["rebuilt_goal_status"] == "CREATED"

    def test_replay_reports_discrepancy(self, tmp_path):
        """If the log cannot reproduce the checkpoint, replay says so."""
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        goal = _goal()
        goal.status = models.GoalStatus.COMPLETED  # but the log says otherwise
        bus.publish(models.Event(event_type=models.EventType.GOAL_CREATED,
                                 project_id="todo", goal_id="g1",
                                 payload={"to": "CREATED"}))
        cp = mgr.create(goal, [], [], {}, {}, [], [], {})
        result = mgr.replay(cp.checkpoint_id)
        assert result["ok"] is False
        assert result["recorded_goal_status"] == "COMPLETED"

    def test_replay_without_a_store_is_reported_not_guessed(self, tmp_path):
        bus = EventBus()
        mgr = CheckpointManager(str(tmp_path), bus)
        cp = mgr.create(_goal(), [], [], {}, {}, [], [], {})
        assert mgr.replay(cp.checkpoint_id) == {"ok": False,
                                                "reason": "no_event_store"}

    def test_replay_is_repeatable(self, tmp_path):
        bus, store = _bus_with_store(tmp_path)
        mgr = CheckpointManager(str(tmp_path), bus)
        bus.publish(models.Event(event_type=models.EventType.GOAL_CREATED,
                                 project_id="todo", goal_id="g1"))
        cp = mgr.create(_goal(), [], [], {}, {}, [], [], {})
        first = mgr.replay(cp.checkpoint_id)
        assert all(mgr.replay(cp.checkpoint_id) == first for _ in range(10))


# ----------------------------------------------------------------------
# Recovery (section 3.8 / 41)
# ----------------------------------------------------------------------

class TestRecovery:
    def test_slice_writes_a_durable_event_log(self, tmp_path):
        from jaxir.todoslice import TodoApp
        app = TodoApp(str(tmp_path))
        app.build()
        assert app.store is not None
        assert len(app.store) > 0
        assert app.store.verify()["ok"] is True

    def test_recovered_goal_completes_and_rehydrates_history(self, tmp_path):
        """Interrupt after execution, then resume from the checkpoint."""
        from jaxir.todoslice import TodoApp

        class _Interrupt(Exception):
            pass

        app = TodoApp(str(tmp_path))
        from jaxir.todoslice import TodoApp as _T

        # Run the build but abort the first loop pass, leaving a checkpoint behind.
        target = _T._run_loop
        calls = {"n": 0}

        def crashing(self):
            calls["n"] += 1
            if calls["n"] == 1:
                # Execute, checkpoint, then die before verification completes.
                self.goalmode.execute(self._goal, self.project_dir)
                raise _Interrupt("simulated crash")
            return target(self)

        _T._run_loop = crashing
        try:
            with pytest.raises(_Interrupt):
                app.build()
        finally:
            _T._run_loop = target

        assert app.checkpoint_mgr.list_checkpoints(), "a checkpoint must survive"
        assert app.store.verify()["ok"] is True

        # Fresh process, same workspace: recover and finish.
        resumed = TodoApp(str(tmp_path))
        goal = resumed.recover()
        assert goal.status == models.GoalStatus.COMPLETED
        assert goal.state["recovery"]["tasks_completed"] >= 1
        assert goal.state["recovery"]["events_rehydrated"] > 0

    def test_recovery_does_not_redo_completed_tasks(self, tmp_path):
        from jaxir.todoslice import TodoApp

        class _Interrupt(Exception):
            pass

        target = TodoApp._run_loop
        calls = {"n": 0}

        def crashing(self):
            calls["n"] += 1
            if calls["n"] == 1:
                self.goalmode.execute(self._goal, self.project_dir)
                raise _Interrupt("boom")
            return target(self)

        TodoApp._run_loop = crashing
        try:
            with pytest.raises(_Interrupt):
                TodoApp(str(tmp_path)).build()
        finally:
            TodoApp._run_loop = target

        resumed = TodoApp(str(tmp_path))
        goal = resumed.recover()
        started = {}
        for e in resumed.bus.get(event_type=models.EventType.TASK_STARTED):
            started[e.task_id] = started.get(e.task_id, 0) + 1
        assert all(n == 1 for n in started.values()), started
        assert goal.status == models.GoalStatus.COMPLETED

    def test_recovery_without_a_checkpoint_is_an_error(self, tmp_path):
        from jaxir.todoslice import TodoApp
        with pytest.raises(FileNotFoundError):
            TodoApp(str(tmp_path)).recover()

    def test_recovery_preserves_an_escalation(self, tmp_path):
        """A BLOCKED goal must stay blocked: recovery may not clear escalation."""
        from jaxir.qa import QAEngine
        from jaxir.todoslice import TodoApp

        original = QAEngine.run_security

        def always_fails(self, project_dir):
            ev = original(self, project_dir)
            ev.status = models.EvidenceStatus.FAIL
            return ev

        QAEngine.run_security = always_fails
        try:
            blocked = TodoApp(str(tmp_path)).build()
        finally:
            QAEngine.run_security = original
        assert blocked.status == models.GoalStatus.BLOCKED

        resumed = TodoApp(str(tmp_path))
        recovered = resumed.recover()
        assert recovered.status == models.GoalStatus.BLOCKED
        assert recovered.state["escalation"]["reason"]

    def test_recovery_rehydrates_recorded_history(self, tmp_path):
        """Recovery must start from recorded history, not an empty bus."""
        from jaxir.todoslice import TodoApp

        class _Interrupt(Exception):
            pass

        target = TodoApp._run_loop
        calls = {"n": 0}

        def crashing(self):
            calls["n"] += 1
            if calls["n"] == 1:
                self.goalmode.execute(self._goal, self.project_dir)
                raise _Interrupt("boom")
            return target(self)

        TodoApp._run_loop = crashing
        try:
            with pytest.raises(_Interrupt):
                TodoApp(str(tmp_path)).build()
        finally:
            TodoApp._run_loop = target

        x = TodoApp(str(tmp_path))
        x._assemble()
        logged = len(x.store)

        resumed = TodoApp(str(tmp_path))
        goal = resumed.recover()
        # The count must reflect what was loaded from the log, and the log must
        # not have been rewritten by recovery.
        assert goal.state["recovery"]["events_rehydrated"] == logged
        assert logged > 2
        assert resumed.store.verify()["ok"] is True
        assert len(resumed.store) > logged  # only new events were appended

    def test_recovery_is_recorded_as_a_restore_event(self, tmp_path):
        from jaxir.todoslice import TodoApp
        app = TodoApp(str(tmp_path))
        app.build()
        resumed = TodoApp(str(tmp_path))
        resumed.recover()
        restores = resumed.bus.get(
            event_type=models.EventType.CHECKPOINT_RESTORED)
        assert restores and restores[-1].payload["tasks_total"] >= 1


class TestJsonSafety:
    def test_datetimes_and_enums_are_serialisable(self):
        from datetime import datetime, timezone
        payload = json_safe({"when": datetime.now(timezone.utc),
                             "kind": models.EventType.GOAL_CREATED, "n": 1})
        json.dumps(payload)  # must not raise
        assert payload["kind"] == "goal.created"

    def test_nested_structures_are_converted(self):
        from datetime import datetime, timezone
        payload = json_safe({"items": [{"d": datetime.now(timezone.utc)}]})
        assert isinstance(payload["items"][0]["d"], str)
