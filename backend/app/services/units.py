"""Unit registry (core.units), cached in memory."""
from __future__ import annotations

from ..db import fetch_all

_cache: dict | None = None


def units() -> dict:
    global _cache
    if _cache is None:
        rows = fetch_all("SELECT unit_key, label, dimension, to_base_factor, to_base_offset FROM core.units")
        _cache = {r["unit_key"]: {**r, "to_base_factor": float(r["to_base_factor"]),
                                  "to_base_offset": float(r["to_base_offset"])} for r in rows}
    return _cache
