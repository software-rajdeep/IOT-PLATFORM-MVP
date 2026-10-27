-- =============================================================================
-- Universal Industrial Platform — schema (V1: CD22 -> Rubber Thickness)
-- The six blocks, strictly separated:
--   [1] connector      (code, not a table)
--   [2] device profile  core.device_profiles / core.device_models
--   [3] normalization   (code) -> standard displacement.value_mm
--   [4] template        core.application_templates / core.template_fields
--   [5] db + rules      core.template_instances / core.template_config
--                       metrics.measurements / metrics.raw_device_messages
--   [6] dashboard       core.application_templates.dashboard (widget metadata)
--
-- Two schemas in one database:
--   core     master data, profiles, devices, templates, instances, config
--   metrics  high-frequency telemetry (kept out of core)
-- Idempotent. Physical names never change; customer labels are config.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS metrics;

-- =============================================================================
-- CORE · catalog: applications & purposes (the onboarding catalogue)
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.applications (
    application_key  VARCHAR(128) PRIMARY KEY,
    application_name VARCHAR(255) NOT NULL,
    description      TEXT,
    version          VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    status           VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.purposes (
    purpose_key VARCHAR(128) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    icon        VARCHAR(64),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    status      VARCHAR(32) NOT NULL DEFAULT 'active'
);

-- =============================================================================
-- CORE · BLOCK 2 — device profiles and models
-- A profile answers "what is this hardware?" (manufacturer, series, connector).
-- A model answers "which exact variant?" (centre distance, range, resolution).
-- Customer tolerances / uses never live here.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.device_profiles (
    profile_id     VARCHAR(128) PRIMARY KEY,          -- optex_cd22_rs485
    manufacturer   VARCHAR(128) NOT NULL,             -- OPTEX FA
    series         VARCHAR(128) NOT NULL,             -- CD22
    device_type    VARCHAR(128) NOT NULL,             -- laser_displacement_sensor
    connector_type VARCHAR(128) NOT NULL,             -- cd22_rs485 (code registry key)
    decoding       JSONB NOT NULL DEFAULT '{}'::jsonb,-- per-profile decode hints
    description    TEXT,
    status         VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.device_models (
    model_key       VARCHAR(128) PRIMARY KEY,         -- optex_cd22_35_485m12
    profile_id      VARCHAR(128) NOT NULL REFERENCES core.device_profiles(profile_id) ON DELETE CASCADE,
    model_name      VARCHAR(255) NOT NULL,            -- CD22-35-485M12
    centre_distance NUMERIC(10,4),                    -- 35 mm
    range_min       NUMERIC(10,4),                    -- -15 mm (relative to centre)
    range_max       NUMERIC(10,4),                    -- +15 mm
    unit            VARCHAR(32) NOT NULL DEFAULT 'mm',
    resolution      NUMERIC(12,6) NOT NULL DEFAULT 0.01, -- mm per raw count (decode step)
    spec            JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(32) NOT NULL DEFAULT 'active'
);

-- Which purposes a device model can serve (catalogue wiring).
CREATE TABLE IF NOT EXISTS core.device_model_purposes (
    model_key   VARCHAR(128) NOT NULL REFERENCES core.device_models(model_key) ON DELETE CASCADE,
    purpose_key VARCHAR(128) NOT NULL REFERENCES core.purposes(purpose_key) ON DELETE CASCADE,
    PRIMARY KEY (model_key, purpose_key)
);

-- =============================================================================
-- CORE · BLOCK 4 — application templates and their standard fields
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.application_templates (
    template_id   VARCHAR(128) PRIMARY KEY,           -- rubber_thickness_v1
    name          VARCHAR(255) NOT NULL,
    version       VARCHAR(32) NOT NULL DEFAULT '1.0',
    category      VARCHAR(128) NOT NULL,              -- quality_measurement
    sensor_layout VARCHAR(64),                        -- side_by_side | opposite | sbs_reference
    description   TEXT,                               -- short layout description shown in onboarding
    purpose_key   VARCHAR(128) REFERENCES core.purposes(purpose_key) ON DELETE SET NULL,
    model_key     VARCHAR(128) REFERENCES core.device_models(model_key) ON DELETE SET NULL, -- null = any
    processor_key VARCHAR(128) NOT NULL,              -- rubber_thickness (code registry)
    inputs        JSONB NOT NULL DEFAULT '[]'::jsonb, -- configuration fields (rendered dynamically)
    dashboard     JSONB NOT NULL DEFAULT '{}'::jsonb, -- BLOCK 6 widget metadata (x/y/w/h)
    status        VARCHAR(32) NOT NULL DEFAULT 'active',
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Upgrade path for databases created before sensor layouts were first-class.
ALTER TABLE core.application_templates ADD COLUMN IF NOT EXISTS sensor_layout VARCHAR(64);
ALTER TABLE core.application_templates ADD COLUMN IF NOT EXISTS description TEXT;

-- The standardized fields a template consumes/produces.
--   source = device | calculated | configuration
CREATE TABLE IF NOT EXISTS core.template_fields (
    template_id VARCHAR(128) NOT NULL REFERENCES core.application_templates(template_id) ON DELETE CASCADE,
    field_key   VARCHAR(191) NOT NULL,                -- displacement.value_mm
    source      VARCHAR(32) NOT NULL,                 -- device | calculated | configuration
    data_type   VARCHAR(32) NOT NULL DEFAULT 'decimal',
    unit        VARCHAR(32),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (template_id, field_key)
);

-- =============================================================================
-- CORE · tenancy, identity, access
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.companies (
    company_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name VARCHAR(255) NOT NULL,
    address      TEXT,
    email        VARCHAR(320),
    phone        VARCHAR(64),
    industry     VARCHAR(128),
    status       VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.plants (
    plant_key  VARCHAR(128) PRIMARY KEY,              -- MUMBAI
    company_id UUID NOT NULL REFERENCES core.companies(company_id) ON DELETE CASCADE,
    plant_name VARCHAR(255) NOT NULL,
    location   VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.roles (
    role_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES core.companies(company_id) ON DELETE CASCADE,
    role_name   VARCHAR(128) NOT NULL,
    description TEXT,
    is_system   BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, role_name)
);

CREATE TABLE IF NOT EXISTS core.users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id    UUID REFERENCES core.companies(company_id) ON DELETE CASCADE,
    user_id       VARCHAR(128),
    username      VARCHAR(128) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    email         VARCHAR(320),
    phone         VARCHAR(64),
    role_id       UUID REFERENCES core.roles(role_id) ON DELETE SET NULL,
    status        VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, username)
);
CREATE INDEX IF NOT EXISTS idx_users_company ON core.users(company_id);

-- =============================================================================
-- CORE · installed devices + their connection settings
-- A device is a physical sensor in a plant. It references a profile+model and
-- carries ONLY connection identity (never tolerances).
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.devices (
    device_id      VARCHAR(128) PRIMARY KEY,          -- DEV001
    company_id     UUID NOT NULL REFERENCES core.companies(company_id) ON DELETE CASCADE,
    plant_key      VARCHAR(128) REFERENCES core.plants(plant_key) ON DELETE SET NULL,
    asset_id       VARCHAR(128),                      -- EXTRUSION_LINE_2
    profile_id     VARCHAR(128) NOT NULL REFERENCES core.device_profiles(profile_id),
    model_key      VARCHAR(128) NOT NULL REFERENCES core.device_models(model_key),
    gateway_id     VARCHAR(128),                      -- EDGE001
    device_address INTEGER,                           -- RS-485 address
    serial_number  VARCHAR(128),
    ingest_key     VARCHAR(128) NOT NULL,             -- gateway/device auth for ingest
    status         VARCHAR(32) NOT NULL DEFAULT 'provisioning', -- provisioning/active/inactive/fault
    last_seen      TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_devices_company ON core.devices(company_id);

CREATE TABLE IF NOT EXISTS core.device_connections (
    device_id            VARCHAR(128) PRIMARY KEY REFERENCES core.devices(device_id) ON DELETE CASCADE,
    interface            VARCHAR(32) NOT NULL DEFAULT 'RS485',
    baud_rate            INTEGER,
    address              INTEGER,
    poll_interval_ms     INTEGER NOT NULL DEFAULT 100,
    connection_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- CORE · BLOCK 5 — template instances, config and mappings
-- A template instance = one customer's configured use of a generic template.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.template_instances (
    instance_id VARCHAR(128) PRIMARY KEY,             -- THICKNESS001
    template_id VARCHAR(128) NOT NULL REFERENCES core.application_templates(template_id),
    company_id  UUID NOT NULL REFERENCES core.companies(company_id) ON DELETE CASCADE,
    plant_key   VARCHAR(128) REFERENCES core.plants(plant_key) ON DELETE SET NULL,
    asset_id    VARCHAR(128),
    device_id   VARCHAR(128) REFERENCES core.devices(device_id) ON DELETE SET NULL,
    name        VARCHAR(255) NOT NULL,
    status      VARCHAR(32) NOT NULL DEFAULT 'draft', -- draft/calibrating/active/paused
    created_by  UUID REFERENCES core.users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_instances_company ON core.template_instances(company_id);

-- Instance configuration (target, limits, calibration.*). Values are text;
-- typed by the template's input definitions.
CREATE TABLE IF NOT EXISTS core.template_config (
    instance_id VARCHAR(128) NOT NULL REFERENCES core.template_instances(instance_id) ON DELETE CASCADE,
    config_key  VARCHAR(191) NOT NULL,
    value       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instance_id, config_key)
);

-- Maps a device's standardized field to a template instance's input.
CREATE TABLE IF NOT EXISTS core.data_mappings (
    mapping_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instance_id  VARCHAR(128) NOT NULL REFERENCES core.template_instances(instance_id) ON DELETE CASCADE,
    source_device_id VARCHAR(128) REFERENCES core.devices(device_id) ON DELETE CASCADE,
    source_field VARCHAR(191) NOT NULL DEFAULT 'displacement.value_mm',
    target_field VARCHAR(191) NOT NULL DEFAULT 'displacement.value_mm',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mappings_device ON core.data_mappings(source_device_id);

-- =============================================================================
-- CORE · audit
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.audit_logs (
    audit_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES core.companies(company_id) ON DELETE CASCADE,
    user_id    UUID REFERENCES core.users(id) ON DELETE SET NULL,
    action     VARCHAR(128) NOT NULL,
    entity_type VARCHAR(128),
    entity_id  VARCHAR(128),
    old_value  JSONB,
    new_value  JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- METRICS · BLOCK 5 — telemetry store
-- =============================================================================

CREATE TABLE IF NOT EXISTS metrics.metric_definitions (
    metric_key   VARCHAR(191) PRIMARY KEY,
    metric_name  VARCHAR(255) NOT NULL,
    data_type    VARCHAR(32) NOT NULL DEFAULT 'decimal',
    default_unit VARCHAR(32)
);

-- Processed measurement (one per device read once calibrated).
CREATE TABLE IF NOT EXISTS metrics.measurements (
    measurement_id     BIGSERIAL PRIMARY KEY,
    company_id         UUID NOT NULL,
    instance_id        VARCHAR(128),
    device_id          VARCHAR(128),
    ts                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_displacement_mm NUMERIC(16,6),               -- decoded displacement (mm)
    displacement_value_mm NUMERIC(16,6),             -- standardized field
    thickness_mm       NUMERIC(16,6),
    deviation_mm       NUMERIC(16,6),
    quality_status     VARCHAR(16),                  -- PASS / FAIL
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_measurements_instance_time ON metrics.measurements(instance_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_measurements_device_time ON metrics.measurements(device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_measurements_instance_device_time
    ON metrics.measurements(instance_id, device_id, ts DESC);

-- Raw connector messages, kept from day 1 for reprocessing/debugging.
CREATE TABLE IF NOT EXISTS metrics.raw_device_messages (
    message_id BIGSERIAL PRIMARY KEY,
    company_id UUID,
    device_id  VARCHAR(128),
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_raw_messages_device_time ON metrics.raw_device_messages(device_id, ts DESC);
