"""core.audit_logs writer (calibration, limits, mapping, activation, clone)."""
from __future__ import annotations

import json
from typing import Any, Optional

from ..db import execute


def log(user: Optional[dict], action: str, entity_type: str, entity_id: str,
        old_value: Any = None, new_value: Any = None) -> None:
    execute(
        "INSERT INTO core.audit_logs (company_id, user_id, action, entity_type, entity_id, old_value, new_value) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        ((user or {}).get("company_id"), (user or {}).get("id"), action, entity_type, entity_id,
         json.dumps(old_value, default=str) if old_value is not None else None,
         json.dumps(new_value, default=str) if new_value is not None else None),
    )
