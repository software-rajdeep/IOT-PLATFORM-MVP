import json
from datetime import datetime, timedelta, timezone

from app.services.event_engine import BELOW
from app.services.incidents import (
    IncidentEngine,
    MemoryIncidentStore,
    Monitor,
    connectivity_status,
    heartbeat_seconds,
    monitor_specs,
    thresholds_for,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
TEMPLATE = {
    "field_roles": {
        "primary": "thickness.actual_mm",
        "lower": "thickness.lower_limit_mm",
        "upper": "thickness.upper_limit_mm",
        "unit": "mm",
    }
}


def config(**overrides):
    base = {
        "thickness.lower_limit_mm": "8.0",
        "thickness.upper_limit_mm": "9.0",
        "thickness.limit_active": "True",
        "event.debounce_readings": 1,
        "event.recovery_readings": 1,
        "event.hysteresis_mm": 0.1,
    }
    base.update(overrides)
    return base


def monitor(field="thickness.actual_mm"):
    return Monitor(company_id="company-1", instance_id="THICKNESS001", machine_id="LINE-1",
                   device_id="DEV001", sensor_id="DEV001", sensor_field=field)


def make_engine(cfg):
    store = MemoryIncidentStore()
    thresholds = thresholds_for(cfg, TEMPLATE, "thickness.actual_mm", "DEV001")
    return store, IncidentEngine(store), thresholds


def feed(engine, thresholds, value, minutes, measurement_id=None, key=None):
    return engine.process(monitor(), thresholds, value, NOW + timedelta(minutes=minutes),
                          measurement_id=measurement_id, key=key)


def test_consecutive_violations_open_one_incident_and_recovery_closes_it():
    cfg = config(**{"event.debounce_readings": 2, "event.recovery_readings": 2})
    store, engine, thresholds = make_engine(cfg)

    assert feed(engine, thresholds, 8.5, 0)["action"] == "none"
    first = feed(engine, thresholds, 9.5, 1)
    assert first["opened"] is False
    assert not store.incidents

    opened = feed(engine, thresholds, 9.6, 2)
    assert opened["opened"] is True
    incident = store.incidents[opened["incident_id"]]
    assert incident["status"] == "open"
    assert incident["started_at"] == NOW + timedelta(minutes=1)
    assert incident["direction"] == "above"
    assert incident["threshold_value"] == 9.0

    updated = feed(engine, thresholds, 9.7, 3)
    assert updated["incident_id"] == opened["incident_id"]
    assert updated["updated"] is True
    assert store.incidents[opened["incident_id"]]["max_deviation"] == 0.7

    # Inside the hysteresis band: still abnormal, not a recovery yet.
    feed(engine, thresholds, 8.95, 4)
    assert store.incidents[opened["incident_id"]]["status"] == "open"

    feed(engine, thresholds, 8.85, 5)
    assert store.incidents[opened["incident_id"]]["status"] == "open"
    closed = feed(engine, thresholds, 8.80, 6)
    assert closed["closed"] is True
    incident = store.incidents[opened["incident_id"]]
    assert incident["status"] == "closed"
    assert incident["ended_at"] == NOW + timedelta(minutes=6)

    feed(engine, thresholds, 9.2, 7)
    second = feed(engine, thresholds, 9.3, 8)
    assert second["opened"] is True
    assert second["incident_id"] != opened["incident_id"]
    assert len(store.incidents) == 2


def test_missing_readings_never_open_or_close_an_incident():
    store, engine, thresholds = make_engine(config())
    opened = feed(engine, thresholds, 9.2, 0)
    incident_id = opened["incident_id"]

    for offset in (1, 2, 3):
        result = feed(engine, thresholds, None, offset)
        assert result["action"] == "none"
        assert store.incidents[incident_id]["status"] == "open"
        assert store.incidents[incident_id]["reading_count"] == 1

    # A later real normal reading recovers normally.
    closed = feed(engine, thresholds, 8.5, 4)
    assert closed["closed"] is True
    assert store.incidents[incident_id]["status"] == "closed"


def test_threshold_change_keeps_an_open_incident_and_uses_new_recovery_rules():
    store, engine, thresholds = make_engine(config())
    first = feed(engine, thresholds, 9.5, 0)
    incident_id = first["incident_id"]

    changed = thresholds_for(config(**{"thickness.upper_limit_mm": "10.0",
                                       "event.recovery_readings": 2}),
                             TEMPLATE, "thickness.actual_mm", "DEV001")
    still_bad = feed(engine, changed, 10.5, 1)
    assert still_bad["incident_id"] == incident_id
    assert still_bad["opened"] is False
    assert len(store.incidents) == 1

    feed(engine, changed, 9.5, 2)
    assert store.incidents[incident_id]["status"] == "open"
    closed = feed(engine, changed, 9.4, 3)
    assert closed["closed"] is True
    assert store.incidents[incident_id]["status"] == "closed"

    second = feed(engine, changed, 10.5, 4)
    assert second["opened"] is True
    assert second["incident_id"] != incident_id


def test_open_state_and_debounce_survive_an_engine_restart():
    cfg = config(**{"event.debounce_readings": 2})
    store = MemoryIncidentStore()
    thresholds = thresholds_for(cfg, TEMPLATE, "thickness.actual_mm", "DEV001")
    first_engine = IncidentEngine(store)

    assert feed(first_engine, thresholds, 9.2, 0)["opened"] is False
    assert not store.incidents

    # Restart: a fresh engine reads the durable state from the same store.
    second_engine = IncidentEngine(store)
    reopened = feed(second_engine, thresholds, 9.3, 1)
    assert reopened["opened"] is True
    incident_id = reopened["incident_id"]

    third_engine = IncidentEngine(store)
    assert feed(third_engine, thresholds, 9.4, 2)["incident_id"] == incident_id
    assert store.incidents[incident_id]["status"] == "open"

    closed = feed(third_engine, thresholds, 8.5, 3)
    assert closed["closed"] is True
    assert len(store.incidents) == 1


def test_duplicate_replayed_readings_do_not_duplicate_incidents():
    store, engine, thresholds = make_engine(config())
    opened = feed(engine, thresholds, 9.3, 0)
    incident_id = opened["incident_id"]
    count = store.incidents[incident_id]["reading_count"]

    duplicate = feed(engine, thresholds, 9.3, 0)
    assert duplicate["status"] == "duplicate"
    assert len(store.incidents) == 1
    assert store.incidents[incident_id]["reading_count"] == count

    closed = feed(engine, thresholds, 8.5, 1)
    assert closed["closed"] is True
    duplicate_close = feed(engine, thresholds, 8.5, 1)
    assert duplicate_close["status"] == "duplicate"
    assert store.incidents[incident_id]["status"] == "closed"
    assert len(store.incidents) == 1


def test_above_and_below_are_separate_incidents():
    store, engine, thresholds = make_engine(config())
    above = feed(engine, thresholds, 9.2, 0)
    assert above["opened"] is True
    feed(engine, thresholds, 8.5, 1)

    below = feed(engine, thresholds, 7.8, 2)
    assert below["opened"] is True
    assert below["incident_id"] != above["incident_id"]
    assert store.incidents[below["incident_id"]]["direction"] == BELOW
    assert store.incidents[below["incident_id"]]["threshold_value"] == 8.0


def test_thresholds_use_existing_config_and_allow_per_sensor_overrides():
    cfg = config(**{
        "event.sensor_limits": json.dumps({
            "thickness.sensor_mm.2": {"lower": 1.0, "upper": 2.0,
                                       "severity": "warning", "hysteresis": 0.05},
        }),
        "event.recovery_seconds": 3,
        "event.debounce_seconds": 2,
    })
    default = thresholds_for(cfg, TEMPLATE, "thickness.sensor_mm", "DEV001")
    custom = thresholds_for(cfg, TEMPLATE, "thickness.sensor_mm.2", "DEV001")
    assert (default.lower, default.upper) == (8.0, 9.0)
    assert (custom.lower, custom.upper) == (1.0, 2.0)
    assert custom.severity == "warning"
    assert custom.hysteresis == 0.05
    assert custom.recovery_seconds == 3
    assert custom.debounce_seconds == 2

    specs = monitor_specs(TEMPLATE, cfg, {
        "thickness.sensor_mm": 8.5,
        "thickness.sensor_mm.2": 1.5,
        "quality.sensor_status": "PASS",
    }, "DEV001")
    assert [field for field, _ in specs] == ["thickness.sensor_mm", "thickness.sensor_mm.2"]


def test_incident_close_queues_channel_neutral_notification_once():
    store, engine, thresholds = make_engine(config())
    opened = feed(engine, thresholds, 9.2, 0)
    feed(engine, thresholds, 8.5, 1)

    assert [n["transition"] for n in store.notifications] == ["opened", "closed"]
    assert all(n["status"] == "queued" and n["channel"] == "whatsapp"
               for n in store.notifications)
    assert store.notifications[0]["payload"]["company_id"] == "company-1"


def test_connectivity_uses_configured_heartbeat_or_freshness():
    assert heartbeat_seconds({"connectivity.heartbeat_seconds": "30"}) == 30
    assert heartbeat_seconds({"connectivity.freshness_seconds": "45"}) == 45
    assert heartbeat_seconds({}, {"seconds": 12}) == 12
    assert heartbeat_seconds({}) == 60

    last_seen = NOW - timedelta(seconds=20)
    assert connectivity_status(last_seen, NOW, 30)["status"] == "online"
    assert connectivity_status(last_seen, NOW, 10)["status"] == "offline"
    assert connectivity_status(None, NOW, 30)["status"] == "unknown"
