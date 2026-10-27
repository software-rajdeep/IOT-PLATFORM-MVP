"""In-process notifications for live measurement updates."""
from __future__ import annotations

import json
import queue
import threading
from contextlib import contextmanager
from typing import Iterator

_subscribers: dict[str, set[queue.Queue]] = {}
_lock = threading.Lock()


@contextmanager
def subscribe(instance_id: str) -> Iterator[queue.Queue]:
    events: queue.Queue = queue.Queue(maxsize=10)
    with _lock:
        _subscribers.setdefault(instance_id, set()).add(events)
    try:
        yield events
    finally:
        with _lock:
            listeners = _subscribers.get(instance_id)
            if listeners:
                listeners.discard(events)
                if not listeners:
                    _subscribers.pop(instance_id, None)


def publish(instance_id: str, device_id: str, timestamp: str) -> None:
    event = json.dumps({"instance_id": instance_id, "device_id": device_id, "timestamp": timestamp})
    with _lock:
        listeners = list(_subscribers.get(instance_id, ()))
    for events in listeners:
        try:
            events.put_nowait(event)
        except queue.Full:
            try:
                events.get_nowait()
                events.put_nowait(event)
            except queue.Empty:
                pass
