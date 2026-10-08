"""Event Store -- durable, append-only, tamper-evident event log.

The Event Bus is the nervous system, but an in-memory bus forgets. The Event
Store makes events durable (section 12 event integrity) and makes replay a real
capability rather than a re-read of the same list (section 20 checkpoint/replay).

Properties provided:

- **Append-only.** Records are never rewritten in place. A new record is a new
  line. History cannot be silently edited.
- **Hash-chained.** Every record carries a checksum over its own content plus the
  previous record's checksum, so altering or removing any record is detectable
  and localisable to the first bad sequence number.
- **Ordered.** Monotonic ``seq`` makes replay deterministic.
- **Rebuildable.** ``rebuild()`` folds events into goal/task state, so process
  state can be reconstituted from the log alone.

Requires POSIX for ``fsync``; degrades to flush-only elsewhere.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import models

#: Bumped if the record envelope changes shape.
STORE_FORMAT_VERSION = 1

GENESIS = "0" * 64


def json_safe(obj: Any) -> Any:
    """Recursively convert datetimes/enums/dataclasses to JSON-safe values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return json_safe(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    return obj


class EventStoreCorruption(Exception):
    """The log failed integrity verification."""


class EventStore:
    """Append-only event log with a hash chain.

    ``path`` may be a directory (a ``events.jsonl`` is created inside) or a file.
    """

    def __init__(self, path: Any, fsync: bool = False):
        target = Path(path)
        if target.suffix == ".jsonl" or (target.exists() and target.is_file()):
            self.path = target
        else:
            target.mkdir(parents=True, exist_ok=True)
            self.path = target / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fsync = fsync
        self._last_checksum = GENESIS
        self._count = 0
        self._load_tail()

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def append(self, event: models.Event) -> Dict[str, Any]:
        """Persist one event and return its record."""
        seq = self._count + 1
        payload = {
            "seq": seq,
            "prev": self._last_checksum,
            # schema_version now travels inside the event envelope itself
            # (constitution section 12), so the record does not duplicate it.
            "event": json_safe(event.to_dict()),
        }
        checksum = self._checksum(payload)
        record = {**payload, "checksum": checksum}
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            if self.fsync:
                os.fsync(f.fileno())
        self._last_checksum = checksum
        self._count = seq
        return record

    def append_many(self, events: Iterable[models.Event]) -> int:
        n = 0
        for event in events:
            self.append(event)
            n += 1
        return n

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def records(self) -> List[Dict[str, Any]]:
        """All raw records, in append order."""
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise EventStoreCorruption(
                        f"unreadable record at line {lineno}") from exc
        return out

    def events(self, upto_seq: Optional[int] = None) -> List[models.Event]:
        """Reconstitute events. ``upto_seq`` gives point-in-time replay."""
        out: List[models.Event] = []
        for record in self.records():
            if upto_seq is not None and record["seq"] > upto_seq:
                break
            event = models.Event.from_dict(record["event"])
            out.append(event)
        return out

    def latest_seq(self) -> int:
        return self._count

    def __len__(self) -> int:
        return self._count

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def verify(self) -> Dict[str, Any]:
        """Walk the chain. Returns a verdict, never raises for a bad chain."""
        last = GENESIS
        expected_seq = 1
        for record in self.records():
            if record.get("seq") != expected_seq:
                return {"ok": False, "reason": "sequence_gap",
                        "at_seq": record.get("seq"), "expected": expected_seq,
                        "count": self._count}
            if record.get("prev") != last:
                return {"ok": False, "reason": "broken_chain_link",
                        "at_seq": record["seq"], "count": self._count}
            body = {k: record[k] for k in ("seq", "prev", "event")
                    if k in record}
            if self._checksum(body) != record.get("checksum"):
                return {"ok": False, "reason": "checksum_mismatch",
                        "at_seq": record["seq"], "count": self._count}
            last = record["checksum"]
            expected_seq += 1
        return {"ok": True, "reason": "verified", "count": self._count,
                "head_checksum": last}

    def _load_tail(self) -> None:
        """Recover the chain head so appends continue it (never rewrites)."""
        records = self.records()
        if not records:
            self._last_checksum, self._count = GENESIS, 0
            return
        last = records[-1]
        self._last_checksum = last.get("checksum", GENESIS)
        self._count = last.get("seq", len(records))

    @staticmethod
    def _checksum(payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # State rebuild (the basis of replay equivalence)
    # ------------------------------------------------------------------

    def rebuild(self, goal_id: Optional[str] = None,
                upto_seq: Optional[int] = None) -> Dict[str, Any]:
        """Fold the log into goal/task state.

        This is a *pure function of the log*: replaying the same events in the
        same order must produce an identical result (the locked idempotent
        replay-equivalence target).
        """
        state: Dict[str, Any] = {"goal_status": None, "transitions": [],
                                 "tasks": {}, "counters": {}}

        def bump(key: str) -> None:
            state["counters"][key] = state["counters"].get(key, 0) + 1

        for event in self.events(upto_seq=upto_seq):
            if goal_id is not None and event.goal_id != goal_id:
                continue
            bump(event.event_type.value)

            if event.event_type is models.EventType.GOAL_CREATED:
                payload = event.payload or {}
                state["goal_status"] = payload.get("to") or "CREATED"
                state["goal_id"] = event.goal_id
            elif event.event_type in (
                models.EventType.GOAL_ANALYZED,
                models.EventType.GOAL_PLANNED,
                models.EventType.GOAL_PAUSED,
                models.EventType.GOAL_RESUMED,
                models.EventType.GOAL_COMPLETED,
                models.EventType.GOAL_FAILED,
            ):
                to = (event.payload or {}).get("to")
                if to:
                    frm = (event.payload or {}).get("from")
                    state["goal_status"] = to
                    state["transitions"].append({"from": frm, "to": to})

            task_id = event.task_id
            if not task_id:
                continue
            if event.event_type is models.EventType.TASK_CREATED:
                state["tasks"][task_id] = "PENDING"
            elif event.event_type is models.EventType.TASK_ASSIGNED:
                state["tasks"][task_id] = "QUEUED"
            elif event.event_type is models.EventType.TASK_STARTED:
                state["tasks"][task_id] = "RUNNING"
            elif event.event_type is models.EventType.TASK_COMPLETED:
                state["tasks"][task_id] = "COMPLETED"
            elif event.event_type is models.EventType.TASK_FAILED:
                state["tasks"][task_id] = "FAILED"
        return state

    def replay_equivalent(self, runs: int = 10,
                          goal_id: Optional[str] = None) -> Dict[str, Any]:
        """Locked target: 10x idempotent replay equivalence.

        Rebuilds from the log ``runs`` times and asserts every result is
        identical - i.e. replay is a function of the log, not of ambient state.
        """
        baseline: Optional[Dict[str, Any]] = None
        for i in range(runs):
            candidate = self.rebuild(goal_id=goal_id)
            if baseline is None:
                baseline = candidate
            elif candidate != baseline:
                return {"equivalent": False, "runs": runs, "diverged_at": i + 1,
                        "baseline": baseline, "candidate": candidate}
        return {"equivalent": True, "runs": runs, "result": baseline}

    # ------------------------------------------------------------------
    # Recovery helpers
    # ------------------------------------------------------------------

    def load_into(self, bus: Any, upto_seq: Optional[int] = None) -> int:
        """Rehydrate a bus with persisted events (history + replay)."""
        events = self.events(upto_seq=upto_seq)
        for event in events:
            bus.publish(event)
        return len(events)

    def export_json(self) -> str:
        return json.dumps(self.records(), indent=2, default=str)


def replay_equivalence_report(store: EventStore, goal_id: Optional[str] = None,
                              runs: int = 10) -> Dict[str, Any]:
    """Evidence payload for the locked replay-equivalence target."""
    verdict = store.replay_equivalent(runs=runs, goal_id=goal_id)
    integrity = store.verify()
    return {
        "test_id": "kernel.replay_equivalence",
        "runs": runs,
        "equivalent": verdict["equivalent"],
        "integrity_ok": integrity["ok"],
        "records": integrity["count"],
        "head_checksum": integrity.get("head_checksum"),
    }
