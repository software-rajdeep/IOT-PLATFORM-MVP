-- =============================================================================
-- Universal Industrial Platform — seed (V1 CD22 -> Rubber Thickness)
-- Data only. Adding a device/purpose/template is a row, not a code change.
-- Idempotent.
-- =============================================================================

-- ------------------------------------------------------------- applications
INSERT INTO core.applications (application_key, application_name, description, version, status) VALUES
  ('platform', 'Platform Core', 'Shared identity, catalog and configuration services.', '1.0.0', 'active'),
  ('cd22',     'CD22 Thickness Monitoring', 'Optex CD22 laser displacement module.', '1.0.0', 'active')
ON CONFLICT (application_key) DO NOTHING;

-- ------------------------------------------------------------- purposes
INSERT INTO core.purposes (purpose_key, name, description, icon, sort_order) VALUES
  ('distance',         'Distance Measurement',        'Measure distance to a target surface.',        'ruler',  10),
  ('height_thickness', 'Height / Thickness Measurement','Measure height or thickness of a material.',  'layers', 20),
  ('position',         'Position Measurement',        'Track the position of a target.',              'target', 30),
  ('surface_flatness', 'Surface / Flatness Inspection','Check surface profile and flatness.',          'waves',  40),
  ('quality',          'Quality Inspection',          'Inspect against a reference for pass/fail.',   'badge',  50)
ON CONFLICT (purpose_key) DO NOTHING;

-- ------------------------------------------------------------- BLOCK 2 profiles
INSERT INTO core.device_profiles (profile_id, manufacturer, series, device_type, connector_type, decoding, description) VALUES
  ('optex_cd22_rs485', 'OPTEX FA', 'CD22', 'laser_displacement_sensor', 'cd22_rs485',
   '{"encoding":"signed_int16_be","unit":"mm","note":"value_mm = raw * resolution"}'::jsonb,
   'OPTEX FA CD22 laser displacement sensor family over half-duplex RS-485.')
ON CONFLICT (profile_id) DO NOTHING;

-- BLOCK 2 models (variants). Resolution 0.01 mm per raw count for all CD22.
INSERT INTO core.device_models (model_key, profile_id, model_name, centre_distance, range_min, range_max, unit, resolution, spec) VALUES
  ('optex_cd22_15_485m12',  'optex_cd22_rs485', 'CD22-15-485M12',  15, -15, 15, 'mm', 0.01, '{"sampling_periods_us":[500,1000,2000,4000]}'::jsonb),
  ('optex_cd22_35_485m12',  'optex_cd22_rs485', 'CD22-35-485M12',  35, -15, 15, 'mm', 0.01, '{"sampling_periods_us":[500,1000,2000,4000]}'::jsonb),
  ('optex_cd22_100_485m12', 'optex_cd22_rs485', 'CD22-100-485M122',100, -15, 15, 'mm', 0.01, '{"sampling_periods_us":[500,1000,2000,4000]}'::jsonb)
ON CONFLICT (model_key) DO NOTHING;

INSERT INTO core.device_model_purposes (model_key, purpose_key)
SELECT m.model_key, p.purpose_key
FROM core.device_models m CROSS JOIN core.purposes p
WHERE m.profile_id = 'optex_cd22_rs485'
ON CONFLICT DO NOTHING;

-- ------------------------------------------------------------- BLOCK 4 templates
-- Three selectable sensor layouts drive the same rubber-thickness pipeline.
-- Adding a layout is a row here, not a code change.
INSERT INTO core.application_templates
  (template_id, name, version, category, sensor_layout, description, purpose_key, model_key, processor_key, inputs, dashboard, status, sort_order)
SELECT
  v.template_id, v.name, '1.0', 'quality_measurement', v.sensor_layout, v.description,
  'height_thickness', NULL,
  CASE WHEN v.template_id IN ('side_by_side_sensors_v1', 'sbs_reference_v1')
       THEN 'sbs_thickness'
       WHEN v.template_id = 'opposite_side_sensors_v1' THEN 'opposite_thickness'
       ELSE 'rubber_thickness' END,
  '[
     {"key":"thickness.target_mm","label":"Nominal thickness","type":"decimal","default":8.5,"unit":"mm"},
     {"key":"thickness.lower_limit_mm","label":"Lower limit","type":"decimal","default":8.3,"unit":"mm"},
     {"key":"thickness.upper_limit_mm","label":"Upper limit","type":"decimal","default":8.7,"unit":"mm"},
     {"key":"thickness.warning_lower_mm","label":"Warning lower (optional)","type":"decimal","default":null,"unit":"mm"},
     {"key":"thickness.warning_upper_mm","label":"Warning upper (optional)","type":"decimal","default":null,"unit":"mm"},
     {"key":"calibration.scale","label":"Calibration scale (A)","type":"decimal","default":-1.0},
     {"key":"calibration.offset","label":"Calibration offset (B)","type":"decimal","default":10.0}
   ]'::jsonb,
  jsonb_set('{
     "widgets":[
       {"type":"current_value","title":"Current thickness","field":"thickness.actual_mm","unit":"mm","x":0,"y":0,"w":3,"h":2},
       {"type":"target","title":"Target","field":"thickness.target_mm","deviation_field":"thickness.deviation_mm","x":3,"y":0,"w":3,"h":2},
       {"type":"trend","title":"Thickness vs time","field":"thickness.actual_mm","zones":["thickness.upper_limit_mm","thickness.target_mm","thickness.lower_limit_mm"],"x":0,"y":2,"w":8,"h":4},
       {"type":"statistics","title":"Statistics","x":0,"y":6,"w":4,"h":2},
       {"type":"quality","title":"Quality","x":4,"y":6,"w":2,"h":2},
       {"type":"device_health","title":"Device health","x":6,"y":6,"w":2,"h":2}
     ]
   }'::jsonb, '{template}', to_jsonb(v.template_id)),
  'active', v.sort_order
FROM (VALUES
  ('side_by_side_sensors_v1',  'Side by Side Sensors',        'side_by_side',
   'Two or more sensors mounted side by side over the same surface; each measures thickness locally.', 10),
  ('opposite_side_sensors_v1', 'Opposite Side Sensors',       'opposite',
   'Sensors facing each other on opposite sides of the material; thickness = gap minus both readings.', 20),
  ('sbs_reference_v1',         'Side by Side with Reference', 'sbs_reference',
   'Side-by-side measurement sensors plus a reference sensor that continuously monitors baseline drift.', 30)
) AS v(template_id, name, sensor_layout, description, sort_order)
ON CONFLICT (template_id) DO NOTHING;

-- Retire the original single-template entry (kept for existing instances, but
-- hidden from onboarding so only the three sensor layouts are offered).
UPDATE core.application_templates SET status = 'inactive' WHERE template_id = 'rubber_thickness_v1';

-- Standard fields (device / calculated / configuration) for every layout.
INSERT INTO core.template_fields (template_id, field_key, source, data_type, unit, sort_order)
SELECT v.template_id, f.field_key, f.source, f.data_type, f.unit, f.sort_order
FROM (VALUES
  ('side_by_side_sensors_v1'),
  ('opposite_side_sensors_v1'),
  ('sbs_reference_v1')
) AS v(template_id)
CROSS JOIN (VALUES
  ('displacement.value_mm',    'device',        'decimal', 'mm', 10),
  ('thickness.target_mm',      'configuration', 'decimal', 'mm', 20),
  ('thickness.lower_limit_mm', 'configuration', 'decimal', 'mm', 30),
  ('thickness.upper_limit_mm', 'configuration', 'decimal', 'mm', 40),
  ('thickness.actual_mm',      'calculated',    'decimal', 'mm', 50),
  ('thickness.deviation_mm',   'calculated',    'decimal', 'mm', 60),
  ('quality.status',           'calculated',    'text',    NULL::varchar, 70),
  ('statistics.minimum',       'calculated',    'decimal', 'mm', 80),
  ('statistics.maximum',       'calculated',    'decimal', 'mm', 90),
  ('statistics.average',       'calculated',    'decimal', 'mm', 100),
  ('statistics.std_dev',       'calculated',    'decimal', 'mm', 110),
  ('quality.pass_count',       'calculated',    'integer', NULL::varchar, 120),
  ('quality.fail_count',       'calculated',    'integer', NULL::varchar, 130),
  ('quality.pass_percentage',  'calculated',    'decimal', '%',  140)
) AS f(field_key, source, data_type, unit, sort_order)
ON CONFLICT (template_id, field_key) DO NOTHING;

-- ------------------------------------------------------------- metric defs
INSERT INTO metrics.metric_definitions (metric_key, metric_name, data_type, default_unit) VALUES
  ('displacement.value_mm', 'Displacement', 'decimal', 'mm'),
  ('thickness.actual_mm',   'Thickness',    'decimal', 'mm'),
  ('thickness.deviation_mm','Deviation',    'decimal', 'mm')
ON CONFLICT (metric_key) DO NOTHING;

-- ------------------------------------------------------------- demo tenant
INSERT INTO core.companies (company_id, company_name, industry, status)
VALUES ('11111111-1111-1111-1111-111111111111', 'ABC Rubber', 'Rubber manufacturing', 'active')
ON CONFLICT (company_id) DO NOTHING;

INSERT INTO core.plants (plant_key, company_id, plant_name, location)
VALUES ('MUMBAI', '11111111-1111-1111-1111-111111111111', 'Mumbai Plant', 'Mumbai')
ON CONFLICT (plant_key) DO NOTHING;

INSERT INTO core.roles (role_id, company_id, role_name, description, is_system)
VALUES ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111',
        'superadmin', 'Company administrator with full access.', true)
ON CONFLICT (company_id, role_name) DO NOTHING;

INSERT INTO core.users (id, company_id, user_id, username, password_hash, email, role_id, status)
VALUES ('33333333-3333-3333-3333-333333333333', '11111111-1111-1111-1111-111111111111',
        'U-0001', 'admin',
        '$2b$12$X2kl51cA8d3P1wOBYsSltOEXhVCFvUG4NQRldvqQrQ86F38agwCUK',
        'admin@demo.local', '22222222-2222-2222-2222-222222222222', 'active')
ON CONFLICT (company_id, username) DO NOTHING;
