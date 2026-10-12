"""Pure matching rules: which applications fit a product, and which graphs fit an instance.

    solution catalog = (product, application) pairs where the product's signals cover the
                       application's required inputs (kind "number" accepts any numeric signal)
    graph options    = widgets whose required field roles are present for this instance
"""
from __future__ import annotations

NUMERIC_KINDS_EXCLUDED = {"text"}


def compatible(profile_signals: list[dict], template: dict) -> bool:
    kinds = [s.get("kind") for s in profile_signals or []]
    if not kinds:
        return False
    available = list(kinds)
    for spec in template.get("requires") or []:
        want = spec.get("kind")
        if want in available:
            available.remove(want)
        elif want == "number":
            match = next((k for k in available if k not in NUMERIC_KINDS_EXCLUDED), None)
            if match is None:
                return False
            available.remove(match)
        else:
            return False
    return True


def present_roles(template: dict, mapped_targets: set) -> tuple[set, set]:
    """Return (present, locked) field roles for an instance.

    Output and config roles are always present when the template declares them.
    Roles bound to OPTIONAL inputs are present only when that input is mapped;
    otherwise they are "locked" (the customer can unlock them in the mapping step).
    """
    roles = template.get("field_roles") or {}
    optional_keys = {o["key"] for o in template.get("optional") or []}
    present, locked = {"device"}, set()
    for role, field in roles.items():
        if role in ("unit", "label"):
            continue
        if field in optional_keys and field not in mapped_targets:
            locked.add(role)
        else:
            present.add(role)
    return present, locked


def resolve_widgets(widget_types: list[dict], template: dict, mapped_targets: set) -> list[dict]:
    """Each widget type -> status available | locked (with the inputs to map) | not_applicable."""
    present, locked = present_roles(template, mapped_targets)
    roles = template.get("field_roles") or {}
    labels = {o["key"]: o.get("label") or o["key"] for o in template.get("optional") or []}
    out = []
    for w in widget_types:
        needs = [r for r in (w.get("requires") or []) if r != "device"]
        missing = [r for r in needs if r not in present]
        if not missing:
            status, unlock = "available", []
        elif all(r in locked for r in missing):
            status, unlock = "locked", [labels.get(roles[r], roles[r]) for r in missing]
        else:
            status, unlock = "not_applicable", []
        out.append({**w, "status": status, "unlock_with": unlock})
    return out


def unlocks_by_input(widget_types: list[dict], template: dict) -> dict:
    """{optional_input_key: [widget names it unlocks]} — shown as hints in the mapping step."""
    roles = template.get("field_roles") or {}
    by_field: dict = {}
    for o in template.get("optional") or []:
        role_names = [r for r, f in roles.items() if f == o["key"]]
        names = [w["name"] for w in widget_types if any(r in (w.get("requires") or []) for r in role_names)]
        if names:
            by_field[o["key"]] = names
    return by_field
