"""Input filtering before the processor (config: filter.window, filter.trim_pct).

A trimmed mean over the last N readings, like the field app's 5 Hz filter, so single
noisy samples don't cause false FAILs. window <= 1 disables filtering.
"""
from __future__ import annotations

import threading
from collections import deque

_buffers: dict[tuple, deque] = {}
_lock = threading.Lock()


def trimmed_mean(values: list[float], trim_pct: float) -> float:
    ordered = sorted(values)
    cut = int(len(ordered) * max(0.0, min(trim_pct, 45.0)) / 100.0)
    kept = ordered[cut:len(ordered) - cut] or ordered
    return sum(kept) / len(kept)


def apply(key: tuple, value: float, config: dict) -> float:
    try:
        window = int(float(config.get("filter.window") or 1))
    except (TypeError, ValueError):
        window = 1
    if window <= 1:
        return value
    try:
        trim = float(config.get("filter.trim_pct") or 0)
    except (TypeError, ValueError):
        trim = 0.0
    with _lock:
        buf = _buffers.get(key)
        if buf is None or buf.maxlen != window:
            buf = deque(buf or [], maxlen=window)
            _buffers[key] = buf
        buf.append(value)
        snapshot = list(buf)
    return round(trimmed_mean(snapshot, trim), 6)
