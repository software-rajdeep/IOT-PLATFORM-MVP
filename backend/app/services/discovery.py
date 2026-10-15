"""Scope a source-discovery result to what an application template can consume.

The connector (BLOCK 1) reports everything the customer's database or API
exposes. The application only understands the fields its template declares, so
before the customer sees the list we keep only the tables that can actually feed
it: a table must expose a column for the template's REQUIRED inputs (e.g.
displacement.value_mm). A table with no such column (an actuator, a flags table)
is unrelated and is hidden. Every column kept is annotated with the standard
field it most likely feeds.

    raw discovery (all tables/columns) --(application contract)--> related only

Pure functions, no database access, so the relevance rules are unit-testable.
This is the "the customer sees only what is related to their application" step;
the chosen table/column is then mapped in the mapping step (services/mapping.py).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Optional

from .mapping import _SYNONYMS, flatten

MATCH_THRESHOLD = 0.6

# SQL type name fragments -> category. Checked in this order (datetime first,
# because "timestamp" also contains "time").
_DATETIME = ("timestamp", "date", "time")
_NUMERIC = ("int", "numeric", "decimal", "real", "double", "float", "serial", "money", "oid")
_TEXT = ("char", "text", "name", "uuid", "citext")


def sql_category(dtype: Any) -> str:
    """Classify a SQL column type into number / text / datetime / other."""
    t = str(dtype or "").lower()
    if "interval" in t:  # "interval" contains "int" — keep it out of numeric
        return "other"
    if any(k in t for k in _DATETIME):
        return "datetime"
    if any(k in t for k in _NUMERIC):
        return "number"
    if any(k in t for k in _TEXT):
        return "text"
    return "other"


def json_category(ptype: Any) -> str:
    """Classify a flattened JSON path type (services.mapping.flatten) into a category."""
    if ptype == "datetime_text":
        return "datetime"
    if ptype == "text":
        return "text"
    if ptype in ("number", "numeric_text"):
        return "number"
    return "other"


def accepted_categories(contract: list[dict]) -> set:
    """Which column categories can feed the template's declared inputs."""
    kinds = {c.get("kind") or "number" for c in contract}
    wanted: set = {"datetime"}  # a timestamp is always useful (read the newest row)
    if any(k != "text" for k in kinds):
        wanted.add("number")
    if any(k == "text" for k in kinds):
        wanted.add("text")
    return wanted


def match_score(name: str, spec: dict) -> float:
    """Name similarity between a source column and a template field (0..1)."""
    words = list(_SYNONYMS.get(spec.get("kind") or "number", []))
    key = str(spec.get("key") or "")
    words += [key.split(".")[-1], key.split(".")[0]]
    leaf = re.split(r"[.\[\]]", str(name).lower())
    joined = "_".join(x for x in leaf if x)
    parts = joined.split("_") + [joined]
    return max(SequenceMatcher(None, w, p).ratio() for w in words for p in parts if w)


def matches_for(name: str, category: str, contract: list[dict]) -> list[str]:
    """Template fields this column can plausibly feed (name match + type compatible)."""
    out: list[str] = []
    for spec in contract:
        kind = spec.get("kind") or "number"
        if kind != "text" and category != "number":
            continue
        if match_score(name, spec) >= MATCH_THRESHOLD:
            out.append(spec["key"])
    return out


def required_keys(contract: list[dict]) -> set:
    """Template fields the application cannot work without."""
    return {c["key"] for c in contract if c.get("required")}


def _scope_tables(tables: list[dict], contract: list[dict]) -> tuple[list[dict], int]:
    accepted = accepted_categories(contract)
    required = required_keys(contract)
    kept: list[dict] = []
    hidden = 0
    for table in tables:
        columns: list[dict] = []
        matched: set = set()
        for col in table.get("columns") or []:
            category = sql_category(col.get("type"))
            if category not in accepted:
                continue
            col_matches = matches_for(col.get("column"), category, contract)
            matched.update(col_matches)
            columns.append({**col, "category": category, "matches": col_matches})
        # Show a table only if it can actually feed the application: it must expose a
        # column for every required input (e.g. displacement.value_mm). A table with
        # no such column (an actuator, flags, ...) is not related and is hidden.
        if not columns or (required and not (matched & required)):
            hidden += 1
            continue
        score = len(matched) + 0.01 * len(columns) + (0.25 if any(c["category"] == "datetime" for c in columns) else 0)
        kept.append({"table": table.get("table"), "columns": columns,
                     "matched_fields": sorted(matched), "score": round(score, 3)})
    kept.sort(key=lambda t: (-t["score"], str(t["table"])))
    return kept, hidden


def _scope_fields(sample: Any, contract: list[dict]) -> list[dict]:
    accepted = accepted_categories(contract)
    kept: list[dict] = []
    for path in flatten(sample):
        category = json_category(path.get("type"))
        if category not in accepted:
            continue
        kept.append({**path, "category": category,
                     "matches": matches_for(path.get("path"), category, contract)})
    return kept


def scope_discovery(result: dict, contract: Optional[list[dict]]) -> dict:
    """Keep only the tables/columns/fields that can feed the application's inputs.

    Works for both source modes: an introspected database (tables/columns) and an
    API that exposes a `/tables` listing, plus the fallback of a raw JSON sample.
    Falls back to the full listing (scoped=False) when nothing matches, so a
    customer is never left with an empty screen.
    """
    contract = contract or []
    out = dict(result)
    if not contract:
        out["scoped"] = False
        return out

    tables = result.get("tables") or []
    if tables:
        kept, hidden = _scope_tables(tables, contract)
        out["tables"] = kept
        out["hidden_tables"] = hidden
        out["scoped"] = True
        if not kept:
            req = sorted(required_keys(contract))
            out["note"] = (
                "No table has a column for the required field(s): " + ", ".join(req)
                if req else "No source tables matched the application."
            )
        return out

    all_fields = flatten(result.get("sample"))
    kept = _scope_fields(result.get("sample"), contract)
    if not kept and all_fields:
        out["scoped"] = False
        out["note"] = "No source fields matched the application; showing all fields."
        return out
    out["fields"] = kept
    out["hidden_fields"] = len(all_fields) - len(kept)
    out["scoped"] = True
    return out
