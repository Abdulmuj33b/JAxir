"""Event Bus.

Cross-cutting Event Bus: events are the nervous system of JaXir. Every
important state change is published, versioned, and stored for audit,
recovery, and replay. Event envelopes carry correlation/causation ids for
traceability.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import models


class EventBus:
    """In-memory event bus with versioning and bounded history.

    Guarantees:
    - ordered delivery per channel
    - at-least-once delivery (callers may deduplicate)
    - versioned envelope (schema_version)
    """

    SCHEMA_VERSION = 1

    def __init__(self, capacity: int = 100_000, store: Any = None):
        self.capacity = capacity
        #: Optional durable log. When present, every published event is appended
        #: so the bus survives process death (section 12 event integrity).
        self.store = store
        self._channels: Dict[str, deque] = {}
        self._subscribers: List[Callable[[models.Event], None]] = []
        self._history: List[models.Event] = []
        self._lock = None  # stdlib threading.Lock injected if needed

    # ------------------------------------------------------------------
    # Pub/sub
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[models.Event], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[models.Event], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def publish(self, event: models.Event) -> None:
        event.schema_version = self.SCHEMA_VERSION
        event.timestamp = datetime.now(timezone.utc)
        if self.store is not None:
            # Durable before it is observable: a crash must not lose an event
            # that a subscriber already acted on.
            self.store.append(event)
        self._history.append(event)
        if len(self._history) > self.capacity:
            self._history = self._history[-self.capacity:]
        chan = event.event_type.value
        if chan not in self._channels:
            self._channels[chan] = deque(maxlen=self.capacity)
        self._channels[chan].append(event)
        for cb in list(self._subscribers):
            try:
                cb(event)
            except Exception:
                # Observability must never take down the bus.
                pass

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, event_type: Optional[models.EventType] = None,
            goal_id: Optional[str] = None,
            project_id: Optional[str] = None,
            limit: int = 1000) -> List[models.Event]:
        events = list(self._history)
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        if goal_id is not None:
            events = [e for e in events if e.goal_id == goal_id]
        if project_id is not None:
            events = [e for e in events if e.project_id == project_id]
        return events[-limit:]

    def stream(self, event_type: models.EventType,
               after: Optional[str] = None,
               limit: int = 1000) -> List[models.Event]:
        chan = self._channels.get(event_type.value, deque())
        # oldest-first; reverse to return newest first (or use slice)
        items = list(chan)
        if after:
            try:
                idx = next(i for i, e in enumerate(items) if e.event_id == after)
                items = items[idx + 1:]
            except StopIteration:
                return []
        return items[-limit:]

    def count(self, event_type: Optional[models.EventType] = None) -> int:
        if event_type is None:
            return len(self._history)
        return len(self._channels.get(event_type.value, deque()))

    def replay(self) -> List[models.Event]:
        return list(self._history)

    def export_json(self, event_type: Optional[models.EventType] = None,
                    limit: int = 1000) -> str:
        events = self.get(event_type=event_type, limit=limit)
        return json.dumps([e.to_dict() for e in events], indent=2)

    def reset(self) -> None:
        self._channels.clear()
        self._history.clear()

    # ------------------------------------------------------------------
    # Durable recovery
    # ------------------------------------------------------------------

    def hydrate(self, upto_seq: Optional[int] = None) -> int:
        """Rebuild bus history from the durable store (crash recovery).

        Events are loaded *without* re-appending: the log is the source of
        truth, and rewriting it on recovery would duplicate history.
        """
        if self.store is None:
            return 0
        loaded = 0
        for event in self.store.events(upto_seq=upto_seq):
            event.schema_version = self.SCHEMA_VERSION
            self._history.append(event)
            chan = event.event_type.value
            if chan not in self._channels:
                self._channels[chan] = deque(maxlen=self.capacity)
            self._channels[chan].append(event)
            loaded += 1
        return loaded
