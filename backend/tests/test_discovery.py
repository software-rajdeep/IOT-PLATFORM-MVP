from app.services.discovery import (
    accepted_categories, json_category, match_score, matches_for, scope_discovery, sql_category,
)

RUBBER_CONTRACT = [
    {"key": "displacement.value_mm", "kind": "displacement", "unit": "mm", "required": True},
    {"key": "context.product_code", "kind": "text"},
    {"key": "line.speed_m_min", "kind": "speed", "unit": "m/min"},
]

RUBBER_TABLE = {
    "table": "public.rubber",
    "columns": [
        {"column": "id", "type": "bigint"},
        {"column": "timestamp", "type": "timestamp with time zone"},
        {"column": "sensor_a", "type": "double precision"},
        {"column": "sensor_b", "type": "double precision"},
        {"column": "sensor_c", "type": "double precision"},
        {"column": "active", "type": "boolean"},
        {"column": "meta", "type": "jsonb"},
    ],
}

FLAGS_TABLE = {
    "table": "public.flags",
    "columns": [
        {"column": "enabled", "type": "boolean"},
        {"column": "payload", "type": "jsonb"},
    ],
}


def test_sql_category_classifies_common_types():
    assert sql_category("double precision") == "number"
    assert sql_category("bigint") == "number"
    assert sql_category("timestamp with time zone") == "datetime"
    assert sql_category("date") == "datetime"
    assert sql_category("character varying") == "text"
    assert sql_category("boolean") == "other"
    assert sql_category("jsonb") == "other"
    assert sql_category("interval") == "other"  # contains "int" but is not numeric


def test_json_category_maps_flatten_types():
    assert json_category("number") == "number"
    assert json_category("numeric_text") == "number"
    assert json_category("datetime_text") == "datetime"
    assert json_category("text") == "text"
    assert json_category("boolean") == "other"


def test_accepted_categories_tracks_contract_kinds():
    assert accepted_categories(RUBBER_CONTRACT) == {"datetime", "number", "text"}
    assert accepted_categories([{"key": "signal.value", "kind": "number"}]) == {"datetime", "number"}
    assert accepted_categories([{"key": "context.product_code", "kind": "text"}]) == {"datetime", "text"}


def test_matches_by_name_and_type():
    assert "displacement.value_mm" in matches_for("laser_value_um", "number", RUBBER_CONTRACT)
    assert "displacement.value_mm" in matches_for("sensor_a", "number", RUBBER_CONTRACT)  # generic sensor column
    assert matches_for("value", "text", RUBBER_CONTRACT) == []  # numeric field cannot take a text column
    assert match_score("timestamp", RUBBER_CONTRACT[1]) < 0.6


def test_internal_scoping_keeps_related_tables_and_columns():
    result = {"mode": "internal", "tables": [RUBBER_TABLE, FLAGS_TABLE]}
    scoped = scope_discovery(result, RUBBER_CONTRACT)
    assert scoped["scoped"] is True
    assert [t["table"] for t in scoped["tables"]] == ["public.rubber"]
    assert scoped["hidden_tables"] == 1
    cols = {c["column"]: c for c in scoped["tables"][0]["columns"]}
    assert cols["timestamp"]["category"] == "datetime"
    assert cols["sensor_a"]["category"] == "number"
    assert "active" not in cols and "meta" not in cols  # boolean/json dropped


def test_internal_scoping_suggests_named_columns():
    table = {"table": "public.measurements",
             "columns": [{"column": "reading_ts", "type": "timestamp"},
                         {"column": "displacement_mm", "type": "numeric"},
                         {"column": "product", "type": "text"}]}
    scoped = scope_discovery({"mode": "internal", "tables": [table]}, RUBBER_CONTRACT)
    cols = {c["column"]: c for c in scoped["tables"][0]["columns"]}
    assert "displacement.value_mm" in cols["displacement_mm"]["matches"]
    assert "context.product_code" in cols["product"]["matches"]


def test_customer_scoping_filters_fields():
    sample = {"machine": "EXT-07", "ts": "2026-10-12T10:00:00+05:30",
              "laser": {"value_um": 1502}, "prod": "RUB-3", "active": True, "meta": {"a": 1}}
    scoped = scope_discovery({"mode": "customer", "sample": sample}, RUBBER_CONTRACT)
    assert scoped["scoped"] is True
    paths = {f["path"]: f for f in scoped["fields"]}
    assert "laser.value_um" in paths and "displacement.value_mm" in paths["laser.value_um"]["matches"]
    assert "prod" in paths
    assert "active" not in paths  # boolean dropped


def test_no_contract_is_not_scoped():
    result = {"mode": "internal", "tables": [RUBBER_TABLE, FLAGS_TABLE]}
    assert scope_discovery(result, None)["tables"] == [RUBBER_TABLE, FLAGS_TABLE]
    assert scope_discovery(result, [])["scoped"] is False


def test_tables_without_a_required_column_are_hidden():
    # FLAGS_TABLE has no column for the required displacement.value_mm -> not shown.
    scoped = scope_discovery({"mode": "internal", "tables": [FLAGS_TABLE]}, RUBBER_CONTRACT)
    assert scoped["scoped"] is True
    assert scoped["tables"] == []
    assert "displacement.value_mm" in scoped["note"]


def test_unrelated_number_table_is_hidden():
    # An actuator table with numeric columns but no measurement column is not related.
    actuator = {"table": "public.actuator",
                "columns": [{"column": "id", "type": "bigint"},
                            {"column": "position", "type": "double precision"},
                            {"column": "status", "type": "text"}]}
    scoped = scope_discovery({"mode": "internal", "tables": [RUBBER_TABLE, actuator]}, RUBBER_CONTRACT)
    assert [t["table"] for t in scoped["tables"]] == ["public.rubber"]
    assert scoped["hidden_tables"] == 1


def test_customer_metadata_listing_is_scoped_like_a_database():
    # A customer API that exposes /tables returns the same {table, columns} shape
    # as an introspected database, and is scoped the same way.
    result = {"mode": "customer", "source": "metadata", "tables": [RUBBER_TABLE, FLAGS_TABLE]}
    scoped = scope_discovery(result, RUBBER_CONTRACT)
    assert scoped["scoped"] is True
    assert [t["table"] for t in scoped["tables"]] == ["public.rubber"]
    assert scoped["hidden_tables"] == 1
    cols = {c["column"]: c for c in scoped["tables"][0]["columns"]}
    assert cols["timestamp"]["category"] == "datetime"
    assert cols["sensor_a"]["category"] == "number"
