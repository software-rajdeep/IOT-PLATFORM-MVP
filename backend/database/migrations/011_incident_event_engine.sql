-- =============================================================================
-- 010 - Industrial IoT event engine: out-of-range incidents, connectivity
-- incidents, durable monitor state, replay idempotency and a delivery outbox.
--
-- Incidents are deliberately independent of metrics.measurements so circular
-- reading buffers / retention jobs can never delete the incident history.
-- Channel-neutral outbox rows are queued here; no WhatsApp/email is sent yet.
-- Idempotent.
-- =============================================================================

CREATE TABLE IF NOT EXISTS metrics.incidents (
    incident_id       BIGSERIAL PRIMARY KEY,
    company_id        UUID NOT NULL,
    instance_id       VARCHAR(128),
    machine_id        VARCHAR(191),
    device_id         VARCHAR(128),
    sensor_id         VARCHAR(191),                  -- physical source (device)
    sensor_field      VARCHAR(191),                  -- canonical measured field
    incident_type     VARCHAR(32) NOT NULL DEFAULT 'out_of_range', -- out_of_range | offline
    direction         VARCHAR(16),                   -- above | below | offline
    severity          VARCHAR(16) NOT NULL DEFAULT 'critical',
    status            VARCHAR(16) NOT NULL DEFAULT 'open',         -- open | closed
    lower_limit       NUMERIC(20,6),
    upper_limit       NUMERIC(20,6),
    threshold_value   NUMERIC(20,6),
    unit              VARCHAR(32),
    started_at        TIMESTAMPTZ NOT NULL,
    ended_at          TIMESTAMPTZ,
    first_value       NUMERIC(20,6),
    last_value        NUMERIC(20,6),
    max_deviation     NUMERIC(20,6) NOT NULL DEFAULT 0,
    reading_count     INTEGER NOT NULL DEFAULT 0,
    last_measurement_id BIGINT,
    recovery_reason   VARCHAR(64),
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One open incident per monitor. Expression + partial index is required because
-- device_id / sensor_field can be NULL for a machine-level incident.
CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_open_monitor
    ON metrics.incidents (instance_id, COALESCE(device_id, ''),
                          COALESCE(sensor_field, ''), incident_type)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_incidents_company_started
    ON metrics.incidents (company_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_instance_started
    ON metrics.incidents (instance_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_device_started
    ON metrics.incidents (device_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_type_status
    ON metrics.incidents (incident_type, status);

-- Durable monitor state. Keeping debounce/recovery counters here is what makes
-- an open incident survive an application restart with the same semantics.
CREATE TABLE IF NOT EXISTS metrics.incident_monitor_state (
    monitor_key          VARCHAR(512) PRIMARY KEY,
    company_id           UUID,
    instance_id          VARCHAR(128),
    machine_id           VARCHAR(191),
    device_id            VARCHAR(128),
    sensor_id            VARCHAR(191),
    sensor_field         VARCHAR(191),
    status               VARCHAR(16) NOT NULL DEFAULT 'normal', -- normal | abnormal
    threshold_fingerprint VARCHAR(64),
    violation_count      INTEGER NOT NULL DEFAULT 0,
    violation_since      TIMESTAMPTZ,
    recovery_count       INTEGER NOT NULL DEFAULT 0,
    recovery_since       TIMESTAMPTZ,
    incident_id          BIGINT REFERENCES metrics.incidents(incident_id) ON DELETE SET NULL,
    direction            VARCHAR(16),
    max_deviation        NUMERIC(20,6) NOT NULL DEFAULT 0,
    first_value          NUMERIC(20,6),
    last_value           NUMERIC(20,6),
    last_ts              TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_incident_state_incident
    ON metrics.incident_monitor_state (incident_id);

-- Replay ledger. A reading is identified by (instance, device, sensor, source
-- timestamp, value). Re-posting the same reading can never create/close a second
-- incident, even after a process restart or a raw-buffer replay.
CREATE TABLE IF NOT EXISTS metrics.incident_processing (
    reading_key      CHAR(64) PRIMARY KEY,
    monitor_key      VARCHAR(512) NOT NULL,
    company_id       UUID,
    instance_id      VARCHAR(128),
    device_id        VARCHAR(128),
    sensor_field     VARCHAR(191),
    measurement_id   BIGINT,
    reading_ts       TIMESTAMPTZ NOT NULL,
    value            NUMERIC(20,6),
    processed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_incident_processing_monitor
    ON metrics.incident_processing (monitor_key, reading_ts DESC);

-- WhatsApp/email-neutral outbox. Rows are queued only; delivery workers decide
-- the channel and recipient later. UNIQUE prevents duplicate notifications when
-- an event is replayed.
CREATE TABLE IF NOT EXISTS metrics.event_notifications (
    notification_id BIGSERIAL PRIMARY KEY,
    incident_id     BIGINT NOT NULL REFERENCES metrics.incidents(incident_id) ON DELETE CASCADE,
    company_id      UUID,
    instance_id     VARCHAR(128),
    device_id       VARCHAR(128),
    transition      VARCHAR(24) NOT NULL,            -- opened | updated | closed
    channel         VARCHAR(32) NOT NULL DEFAULT 'whatsapp',
    status          VARCHAR(16) NOT NULL DEFAULT 'queued', -- queued | sent | failed | skipped
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    UNIQUE (incident_id, transition)
);

CREATE INDEX IF NOT EXISTS idx_event_notifications_queue
    ON metrics.event_notifications (status, created_at);
