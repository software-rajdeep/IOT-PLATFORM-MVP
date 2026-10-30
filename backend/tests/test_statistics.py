"""Statistics card: Cp/Cpk maths and SQL aggregation.

The dashboard shows avg / min / max / σ / samples from the last readings and derives
Cp and Cpk from the same window. These tests pin the formulas (pure) and, when a
database is available, check that missing or non-numeric values are skipped instead
of being counted as samples or breaking the query.
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from app.services.dashboard import _NUMERIC_RE, capability_indices

NUMERIC = re.compile(_NUMERIC_RE)


def test_capability_centred_process():
    # mean 8.5, σ 0.1, limits 8.3 / 8.7 -> 0.4 / (6 * 0.1) = 0.667
    assert capability_indices(8.5, 0.1, 8.3, 8.7) == {"cp": 0.667, "cpk": 0.667}


def test_capability_off_centre_is_limited_by_the_near_side():
    # mean 8.6 -> nearer limit is the upper one: 0.1 / (3 * 0.1) = 0.333
    assert capability_indices(8.6, 0.1, 8.3, 8.7) == {"cp": 0.667, "cpk": 0.333}


def test_capability_negative_when_mean_is_outside_the_limits():
    assert capability_indices(8.9, 0.1, 8.3, 8.7) == {"cp": 0.667, "cpk": -0.667}


def test_capability_is_none_without_a_usable_spread_or_limits():
    none = {"cp": None, "cpk": None}
    assert capability_indices(8.5, 0, 8.3, 8.7) == none       # constant process
    assert capability_indices(8.5, None, 8.3, 8.7) == none    # single sample
    assert capability_indices(8.5, float("nan"), 8.3, 8.7) == none
    assert capability_indices(8.5, 0.1, None, 8.7) == none     # no lower limit
    assert capability_indices(8.5, 0.1, 8.3, None) == none     # no upper limit
    assert capability_indices(8.5, 0.1, 8.7, 8.3) == none     # inverted limits
    assert capability_indices(None, 0.1, 8.3, 8.7) == none


def test_numeric_guard_accepts_numbers_and_rejects_garbage():
    for value in ("8.5", "-1.5", "+2", ".5", "1e-3", "0"):
        assert NUMERIC.match(value), value
    for value in ("", " ", "abc", "8.5mm", "NaN", "Infinity", "--1"):
        assert not NUMERIC.match(value), value


# --------------------------------------------------------------------------- DB path
def _db():
    try:
        import psycopg2
        from app.config import settings

        return psycopg2.connect(settings.database_url)
    except Exception:
        return None


def test_dashboard_samples_ignore_rows_without_a_value():
    conn = _db()
    if conn is None:
        pytest.skip("database not available")
    conn.autocommit = True
    cur = conn.cursor()
    marker = f"stats-test-{uuid.uuid4()}"
    try:
        # Use an instance that has no readings yet, so the assertions are exact and
        # the test can never delete or mix in real production rows.
        cur.execute(
            "SELECT t.instance_id, t.company_id, t.device_id, t.template_id "
            "FROM core.template_instances t "
            "WHERE t.device_id IS NOT NULL AND NOT EXISTS ("
            "  SELECT 1 FROM metrics.measurements m "
            "  WHERE m.instance_id = t.instance_id AND m.device_id = t.device_id) LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            pytest.skip("no empty instance available in the database")
        instance_id, company_id, device_id, template_id = row

        from app.services.templates import load_template

        roles = load_template(template_id).get("field_roles") or {}
        primary = roles.get("primary") or "thickness.actual_mm"
        status = roles.get("status") or "quality.status"
        rows = [
            {primary: 8.40, status: "PASS"},
            {primary: 8.45, status: "PASS"},
            {primary: 8.50, status: "PASS"},
            {primary: 8.55, status: "PASS"},
            {primary: 8.60, status: "FAIL"},
            {status: "PASS"},                                       # no primary value
            {primary: "", status: "PASS"},                          # blank value
            {primary: "abc", status: "PASS"},                       # non-numeric
        ]
        for fields in rows:
            fields["test.marker"] = marker
            cur.execute(
                "INSERT INTO metrics.measurements (company_id, instance_id, device_id, ts, fields) "
                "VALUES (%s, %s, %s, now(), %s)",
                (company_id, instance_id, device_id, json.dumps(fields)),
            )

        from app.services import dashboard as dash

        dash.invalidate_dashboards()
        data = dash.build_dashboard(instance_id, str(company_id))

        stats = data["statistics"]
        assert stats["samples"] == 5                     # only the numeric values
        assert stats["readings"] == 8                    # every row in the window
        assert stats["average"] == pytest.approx(8.5)
        assert stats["minimum"] == pytest.approx(8.4)
        assert stats["maximum"] == pytest.approx(8.6)
        assert stats["std_dev"] == pytest.approx(0.0791, rel=1e-3)

        quality = data["quality"]
        assert quality["pass_count"] == 4                # value-less PASS rows not counted
        assert quality["fail_count"] == 1
        assert quality["pass_percentage"] == 80.0        # 4 / (4 + 1), not 5 / 8

        config = data["config"]
        assert data["capability"] == capability_indices(
            stats["average"], stats["std_dev"], config["lower"], config["upper"]
        )
    finally:
        cur.execute("DELETE FROM metrics.measurements WHERE fields->>'test.marker' = %s", (marker,))
        from app.services import dashboard as dash

        dash.invalidate_dashboards()
        conn.close()
