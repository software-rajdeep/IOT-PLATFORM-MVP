-- =============================================================================
-- 001 — MVP: signal catalog, mapping engine, generic measurements, widgets,
-- per-instance dashboards, alerts. Builds on schema.sql + seed.sql (never
-- replaces them). Idempotent.
-- =============================================================================

-- ------------------------------------------------------------- units + signals
CREATE TABLE IF NOT EXISTS core.units (
    unit_key       VARCHAR(32) PRIMARY KEY,           -- mm
    label          VARCHAR(64) NOT NULL,
    dimension      VARCHAR(32) NOT NULL,              -- length
    to_base_factor NUMERIC(20,10) NOT NULL DEFAULT 1, -- value_in_base = value * factor + offset
    to_base_offset NUMERIC(20,10) NOT NULL DEFAULT 0
);

INSERT INTO core.units (unit_key, label, dimension, to_base_factor, to_base_offset) VALUES
  ('mm',    'millimetre',        'length', 1,       0),
  ('um',    'micrometre',        'length', 0.001,   0),
  ('cm',    'centimetre',        'length', 10,      0),
  ('m',     'metre',             'length', 1000,    0),
  ('in',    'inch',              'length', 25.4,    0),
  ('m/min', 'metre per minute',  'speed',  1,       0),
  ('m/s',   'metre per second',  'speed',  60,      0),
  ('ft/min','foot per minute',   'speed',  0.3048,  0),
  ('degC',  'degree Celsius',    'temperature', 1,  0),
  ('degF',  'degree Fahrenheit', 'temperature', 0.5555555556, -17.7777777778),
  ('count', 'count',             'count',  1,       0),
  ('%',     'percent',           'ratio',  1,       0)
ON CONFLICT (unit_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS core.signal_kinds (
    kind         VARCHAR(64) PRIMARY KEY,             -- displacement
    name         VARCHAR(128) NOT NULL,
    dimension    VARCHAR(32),                         -- length (NULL = dimensionless / text)
    default_unit VARCHAR(32) REFERENCES core.units(unit_key),
    data_type    VARCHAR(32) NOT NULL DEFAULT 'decimal'
);

INSERT INTO core.signal_kinds (kind, name, dimension, default_unit, data_type) VALUES
  ('displacement', 'Displacement / distance', 'length',      'mm',    'decimal'),
  ('thickness',    'Thickness',               'length',      'mm',    'decimal'),
  ('speed',        'Line speed',              'speed',       'm/min', 'decimal'),
  ('temperature',  'Temperature',             'temperature', 'degC',  'decimal'),
  ('count',        'Count',                   'count',       'count', 'integer'),
  ('number',       'Any numeric value',       NULL,          NULL,    'decimal'),
  ('text',         'Text / code',             NULL,          NULL,    'text')
ON CONFLICT (kind) DO NOTHING;

-- ------------------------------------------------------------- device profiles: signals + preset
ALTER TABLE core.device_profiles ADD COLUMN IF NOT EXISTS signals JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE core.device_profiles ADD COLUMN IF NOT EXISTS preset_mapping JSONB NOT NULL DEFAULT '{}'::jsonb;

-- CD22 emits a displacement in mm; its connector reports a raw count scaled by the model resolution.
UPDATE core.device_profiles SET
  signals = '[{"kind":"displacement","unit":"mm","label":"Measured displacement"}]'::jsonb,
  preset_mapping = '{"fields":[{"signal":"displacement","path":"measurement_raw",
                     "transforms":[{"type":"cast","to":"decimal"},{"type":"scale","factor":"@model.resolution"}]}]}'::jsonb
WHERE profile_id = 'optex_cd22_rs485';

-- Correct model ranges (relative to the centre distance). Resolution: verify per model against the OPTEX manual.
UPDATE core.device_models SET range_min = -5,  range_max = 5  WHERE model_key = 'optex_cd22_15_485m12';
UPDATE core.device_models SET range_min = -15, range_max = 15 WHERE model_key = 'optex_cd22_35_485m12';
UPDATE core.device_models SET range_min = -50, range_max = 50 WHERE model_key = 'optex_cd22_100_485m12';

-- ------------------------------------------------------------- templates: contract, roles, defaults
ALTER TABLE core.application_templates ADD COLUMN IF NOT EXISTS requires      JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE core.application_templates ADD COLUMN IF NOT EXISTS optional      JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE core.application_templates ADD COLUMN IF NOT EXISTS outputs       JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE core.application_templates ADD COLUMN IF NOT EXISTS field_roles   JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE core.application_templates ADD COLUMN IF NOT EXISTS commissioning JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE core.application_templates ADD COLUMN IF NOT EXISTS alert_defaults JSONB NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO core.purposes (purpose_key, name, description, icon, sort_order) VALUES
  ('monitoring', 'General Monitoring', 'Watch any numeric value with limits, trends and alerts.', 'activity', 60)
ON CONFLICT (purpose_key) DO NOTHING;

-- MVP thickness template = the single-sensor layout. The opposite/reference layouts need
-- multi-sensor processing (ROADMAP 2.3) and are shown as "coming soon" until then.
UPDATE core.application_templates SET
  name = 'Rubber Thickness (single sensor)',
  description = 'One displacement sensor above the material; thickness from a two-point calibration, PASS/FAIL against limits.',
  requires = '[{"key":"displacement.value_mm","label":"Displacement","kind":"displacement","unit":"mm","required":true,"valid_min":-60,"valid_max":60}]'::jsonb,
  optional = '[{"key":"context.product_code","label":"Product / recipe code","kind":"text"},
               {"key":"line.speed_m_min","label":"Line speed","kind":"speed","unit":"m/min"}]'::jsonb,
  outputs  = '[{"key":"thickness.actual_mm","label":"Thickness","unit":"mm","primary":true},
               {"key":"thickness.deviation_mm","label":"Deviation","unit":"mm"},
               {"key":"quality.status","label":"Quality status","kind":"text"}]'::jsonb,
  field_roles = '{"primary":"thickness.actual_mm","deviation":"thickness.deviation_mm","status":"quality.status",
                  "input":"displacement.value_mm","target":"thickness.target_mm","lower":"thickness.lower_limit_mm",
                  "upper":"thickness.upper_limit_mm","group":"context.product_code","x_speed":"line.speed_m_min",
                  "calibration":"calibration.scale","unit":"mm","label":"Thickness"}'::jsonb,
  commissioning = '{"calibration":"two_point","filter_field":"displacement.value_mm"}'::jsonb,
  alert_defaults = '[{"type":"fail_streak","params":{"count":5}},{"type":"offline","params":{"seconds":15}},
                     {"type":"mapping_failure","params":{}}]'::jsonb,
  dashboard = '{"widgets":[
     {"type":"current_value","x":0,"y":0,"w":3,"h":2},
     {"type":"target_deviation","x":3,"y":0,"w":3,"h":2},
     {"type":"pass_fail","x":6,"y":0,"w":3,"h":2},
     {"type":"device_health","x":9,"y":0,"w":3,"h":2},
     {"type":"trend_band","x":0,"y":2,"w":8,"h":4},
     {"type":"statistics","x":8,"y":2,"w":4,"h":2},
     {"type":"capability","x":8,"y":4,"w":4,"h":2},
     {"type":"calibration_status","x":0,"y":6,"w":6,"h":2},
     {"type":"limits_editor","x":6,"y":6,"w":6,"h":2},
     {"type":"history_table","x":0,"y":8,"w":12,"h":4}
   ]}'::jsonb,
  status = 'active'
WHERE template_id = 'side_by_side_sensors_v1';

UPDATE core.application_templates SET status = 'coming_soon'
WHERE template_id IN ('opposite_side_sensors_v1', 'sbs_reference_v1');

INSERT INTO core.application_templates
  (template_id, name, version, category, sensor_layout, description, purpose_key, model_key, processor_key,
   inputs, dashboard, status, sort_order, requires, optional, outputs, field_roles, commissioning, alert_defaults)
VALUES (
  'generic_monitoring_v1', 'Generic Monitoring', '1.0', 'monitoring', NULL,
  'Any numeric signal: live value, trend, limits, statistics and alerts. Works for every product.',
  'monitoring', NULL, 'generic_monitoring',
  '[{"key":"signal.target","label":"Target","type":"decimal","default":null},
    {"key":"signal.lower_limit","label":"Lower limit","type":"decimal","default":null},
    {"key":"signal.upper_limit","label":"Upper limit","type":"decimal","default":null}]'::jsonb,
  '{"widgets":[
     {"type":"current_value","x":0,"y":0,"w":4,"h":2},
     {"type":"statistics","x":4,"y":0,"w":4,"h":2},
     {"type":"device_health","x":8,"y":0,"w":4,"h":2},
     {"type":"trend_band","x":0,"y":2,"w":12,"h":4},
     {"type":"limits_editor","x":0,"y":6,"w":6,"h":2},
     {"type":"history_table","x":0,"y":8,"w":12,"h":4}
   ]}'::jsonb,
  'active', 90,
  '[{"key":"signal.value","label":"Value","kind":"number","required":true}]'::jsonb,
  '[{"key":"context.product_code","label":"Product / batch code","kind":"text"}]'::jsonb,
  '[{"key":"signal.actual","label":"Value","primary":true},
    {"key":"signal.deviation","label":"Deviation"},
    {"key":"quality.status","label":"Status","kind":"text"}]'::jsonb,
  '{"primary":"signal.actual","deviation":"signal.deviation","status":"quality.status","input":"signal.value",
    "target":"signal.target","lower":"signal.lower_limit","upper":"signal.upper_limit",
    "group":"context.product_code","label":"Value"}'::jsonb,
  '{"filter_field":"signal.value"}'::jsonb,
  '[{"type":"fail_streak","params":{"count":5}},{"type":"offline","params":{"seconds":15}},
    {"type":"mapping_failure","params":{}}]'::jsonb
) ON CONFLICT (template_id) DO NOTHING;

-- Curated catalog listings. Computed (product x application) pairs are listed automatically;
-- these rows add presentation and "coming soon" cards for the final-product look.
CREATE TABLE IF NOT EXISTS core.solution_listings (
    listing_id   VARCHAR(128) PRIMARY KEY,
    title        VARCHAR(255) NOT NULL,
    subtitle     TEXT,
    product      VARCHAR(255),
    application  VARCHAR(255),
    icon         VARCHAR(64),
    status       VARCHAR(32) NOT NULL DEFAULT 'coming_soon',  -- available | coming_soon | hidden
    model_key    VARCHAR(128) REFERENCES core.device_models(model_key) ON DELETE CASCADE,
    template_id  VARCHAR(128) REFERENCES core.application_templates(template_id) ON DELETE CASCADE,
    sort_order   INTEGER NOT NULL DEFAULT 100
);

INSERT INTO core.solution_listings (listing_id, title, subtitle, product, application, icon, status, model_key, template_id, sort_order) VALUES
  ('cd22_35_thickness', 'Rubber Thickness', 'Live thickness, PASS/FAIL, SPC and shift reports for extrusion and calendering lines.',
   'OPTEX CD22-35-485M12', 'Rubber Thickness (single sensor)', 'layers', 'available', 'optex_cd22_35_485m12', 'side_by_side_sensors_v1', 10),
  ('cd22_opposite', 'Opposite-side Thickness', 'Two sensors facing each other; thickness from the gap. Needs multi-sensor support.',
   'OPTEX CD22 (pair)', 'Opposite Side Sensors', 'layers', 'coming_soon', NULL, NULL, 40),
  ('cd22_100_edge', 'Edge / Web Position', 'Track sheet or web edge position and wander.',
   'OPTEX CD22-100-485M122', 'Position Measurement', 'target', 'coming_soon', NULL, NULL, 50),
  ('oee_state_count', 'Machine OEE', 'Run/stop state, part count, downtime reasons and OEE per shift.',
   'Photoelectric / PLC signal', 'Machine State + Count', 'gauge', 'coming_soon', NULL, NULL, 60),
  ('energy_meter', 'Line Energy', 'kW and kWh per line, per shift and per product.',
   'Energy meter (Modbus)', 'Energy Monitoring', 'bolt', 'coming_soon', NULL, NULL, 70),
  ('vision_inspection', 'Vision Inspection', 'Pass/fail and measurements from smart cameras.',
   'Cognex / smart camera', 'Inspection Results', 'eye', 'coming_soon', NULL, NULL, 80)
ON CONFLICT (listing_id) DO NOTHING;

-- ------------------------------------------------------------- connections: secrets + runtime state
ALTER TABLE core.device_connections ADD COLUMN IF NOT EXISTS secrets JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE core.device_connections ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE core.device_connections ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ;
ALTER TABLE core.device_connections ADD COLUMN IF NOT EXISTS last_cursor TEXT;

-- ------------------------------------------------------------- mapping versions
CREATE TABLE IF NOT EXISTS core.mapping_versions (
    mapping_id  BIGSERIAL PRIMARY KEY,
    instance_id VARCHAR(128) NOT NULL REFERENCES core.template_instances(instance_id) ON DELETE CASCADE,
    device_id   VARCHAR(128) REFERENCES core.devices(device_id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    origin      VARCHAR(32) NOT NULL DEFAULT 'manual',   -- preset | manual | clone
    definition  JSONB NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT true,
    created_by  UUID REFERENCES core.users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (instance_id, device_id, version)
);
CREATE INDEX IF NOT EXISTS idx_mapping_active ON core.mapping_versions(instance_id, device_id) WHERE active;

ALTER TABLE core.data_mappings ADD COLUMN IF NOT EXISTS role VARCHAR(64) NOT NULL DEFAULT 'primary';

-- ------------------------------------------------------------- generic measurements + failures
ALTER TABLE metrics.measurements ADD COLUMN IF NOT EXISTS fields JSONB;
ALTER TABLE metrics.measurements ADD COLUMN IF NOT EXISTS mapping_id BIGINT;
ALTER TABLE metrics.measurements ADD COLUMN IF NOT EXISTS raw_message_id BIGINT;
ALTER TABLE metrics.raw_device_messages ADD COLUMN IF NOT EXISTS source_ts TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS metrics.mapping_failures (
    failure_id     BIGSERIAL PRIMARY KEY,
    company_id     UUID,
    instance_id    VARCHAR(128),
    device_id      VARCHAR(128),
    mapping_id     BIGINT,
    raw_message_id BIGINT,
    errors         JSONB NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mapping_failures_instance ON metrics.mapping_failures(instance_id, created_at DESC);

-- ------------------------------------------------------------- widgets + dashboards
CREATE TABLE IF NOT EXISTS core.widget_types (
    widget_type VARCHAR(64) PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    description TEXT,
    category    VARCHAR(64) NOT NULL DEFAULT 'value',
    requires    JSONB NOT NULL DEFAULT '[]'::jsonb,   -- template field roles that must be present
    default_w   INTEGER NOT NULL DEFAULT 4,
    default_h   INTEGER NOT NULL DEFAULT 2,
    sort_order  INTEGER NOT NULL DEFAULT 100
);

INSERT INTO core.widget_types (widget_type, name, description, category, requires, default_w, default_h, sort_order) VALUES
  ('current_value',      'Current value',          'Latest value with PASS/FAIL.',                         'value',   '["primary"]', 3, 2, 10),
  ('target_deviation',   'Target & deviation',     'Configured target and the latest deviation.',          'value',   '["primary","target","deviation"]', 3, 2, 20),
  ('pass_fail',          'Quality (pass / fail)',  'Pass percentage and pass/fail counts.',                'quality', '["status"]', 3, 2, 30),
  ('statistics',         'Statistics',             'Average, min, max and standard deviation.',            'quality', '["primary"]', 4, 2, 40),
  ('capability',         'Process capability',     'Cp and Cpk against the limits.',                       'quality', '["primary","lower","upper"]', 4, 2, 50),
  ('trend_band',         'Trend with limits',      'Value over time with target and limit lines.',         'chart',   '["primary"]', 8, 4, 60),
  ('deviation_trend',    'Deviation over time',    'Deviation from target over time.',                     'chart',   '["deviation"]', 6, 4, 70),
  ('histogram',          'Distribution',           'Histogram of recent values, in-spec vs out-of-spec.',  'chart',   '["primary"]', 6, 4, 80),
  ('recent_bars',        'Recent readings',        'Last readings as pass/fail bars.',                     'chart',   '["primary","status"]', 6, 4, 90),
  ('group_summary',      'Per-product summary',    'Average, pass % and count per product / recipe code.', 'quality', '["primary","group"]', 6, 3, 100),
  ('speed_scatter',      'Value vs line speed',    'How the value changes with line speed.',               'chart',   '["primary","x_speed"]', 6, 4, 110),
  ('device_health',      'Device health',          'Source online/offline and last reading age.',          'status',  '[]', 3, 2, 120),
  ('calibration_status', 'Calibration',            'Calibration state, factors and a recalibrate button.', 'control', '["calibration"]', 6, 2, 130),
  ('limits_editor',      'Limits',                 'View and edit target and limits.',                     'control', '["target","lower","upper"]', 6, 2, 140),
  ('alerts_list',        'Alerts',                 'Recent alerts with acknowledge.',                      'status',  '[]', 6, 3, 150),
  ('history_table',      'Reading history',        'Table of the latest readings.',                        'table',   '["primary"]', 12, 4, 160)
ON CONFLICT (widget_type) DO NOTHING;

CREATE TABLE IF NOT EXISTS core.instance_dashboards (
    instance_id VARCHAR(128) PRIMARY KEY REFERENCES core.template_instances(instance_id) ON DELETE CASCADE,
    layout      JSONB NOT NULL,
    updated_by  UUID REFERENCES core.users(id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------- alerts
CREATE TABLE IF NOT EXISTS core.alert_rules (
    rule_id     BIGSERIAL PRIMARY KEY,
    instance_id VARCHAR(128) NOT NULL REFERENCES core.template_instances(instance_id) ON DELETE CASCADE,
    rule_type   VARCHAR(64) NOT NULL,                  -- fail_streak | offline | mapping_failure
    params      JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled     BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (instance_id, rule_type)
);

CREATE TABLE IF NOT EXISTS metrics.alert_events (
    event_id    BIGSERIAL PRIMARY KEY,
    company_id  UUID,
    instance_id VARCHAR(128) NOT NULL,
    device_id   VARCHAR(128),
    rule_type   VARCHAR(64) NOT NULL,
    severity    VARCHAR(16) NOT NULL DEFAULT 'warning',
    message     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    ack_at      TIMESTAMPTZ,
    ack_by      VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_alert_events_instance ON metrics.alert_events(instance_id, created_at DESC);

-- ------------------------------------------------------------- second demo tenant (README promised it)
INSERT INTO core.companies (company_id, company_name, industry, status)
VALUES ('44444444-4444-4444-4444-444444444444', 'XYZ Polymers', 'Rubber & polymer products', 'active')
ON CONFLICT (company_id) DO NOTHING;

INSERT INTO core.plants (plant_key, company_id, plant_name, location)
VALUES ('PUNE', '44444444-4444-4444-4444-444444444444', 'Pune Plant', 'Pune')
ON CONFLICT (plant_key) DO NOTHING;

INSERT INTO core.roles (role_id, company_id, role_name, description, is_system)
VALUES ('55555555-5555-5555-5555-555555555555', '44444444-4444-4444-4444-444444444444',
        'superadmin', 'Company administrator with full access.', true)
ON CONFLICT (company_id, role_name) DO NOTHING;

-- same demo password as admin@demo.local (demo1234)
INSERT INTO core.users (id, company_id, user_id, username, password_hash, email, role_id, status)
VALUES ('66666666-6666-6666-6666-666666666666', '44444444-4444-4444-4444-444444444444',
        'U-0001', 'admin', '$2b$12$X2kl51cA8d3P1wOBYsSltOEXhVCFvUG4NQRldvqQrQ86F38agwCUK',
        'admin@xyz.local', '55555555-5555-5555-5555-555555555555', 'active')
ON CONFLICT (company_id, username) DO NOTHING;
