-- =============================================================================
-- 007 - Side-by-side baseline calibration, per-sensor thickness and the
-- reference-sensor error (ported from the field-proven thickness-monitoring
-- app). Data only: the same processor reads these configuration keys.
-- Idempotent.
-- =============================================================================

UPDATE core.application_templates
SET processor_key = 'sbs_thickness',
    status = 'active',
    requires = '[{"key":"displacement.value_mm","label":"Displacement","kind":"displacement","unit":"mm","required":true,"valid_min":-60,"valid_max":60}]'::jsonb,
    optional = '[]'::jsonb,
    outputs = '[{"key":"thickness.actual_mm","label":"Thickness","unit":"mm","primary":true},
                {"key":"thickness.deviation_mm","label":"Deviation","unit":"mm"},
                {"key":"quality.status","label":"Quality status","kind":"text"},
                {"key":"reference.error_mm","label":"Reference error","unit":"mm"},
                {"key":"quality.reference_status","label":"Reference status","kind":"text"}]'::jsonb,
    field_roles = '{"primary":"thickness.actual_mm","deviation":"thickness.deviation_mm","status":"quality.status",
                    "input":"displacement.value_mm","target":"thickness.target_mm","lower":"thickness.lower_limit_mm",
                    "upper":"thickness.upper_limit_mm","reference":"reference.error_mm",
                    "reference_status":"quality.reference_status","calibration":"calibration.calibrated_at",
                    "unit":"mm","label":"Thickness"}'::jsonb,
    commissioning = '{"calibration":"baseline","filter_field":"displacement.value_mm"}'::jsonb,
    inputs = '[
      {"key":"thickness.target_mm","label":"Nominal thickness","type":"decimal","default":8.5,"unit":"mm"},
      {"key":"thickness.lower_limit_mm","label":"Lower limit","type":"decimal","default":8.3,"unit":"mm"},
      {"key":"thickness.upper_limit_mm","label":"Upper limit","type":"decimal","default":8.7,"unit":"mm"},
      {"key":"thickness.warning_lower_mm","label":"Warning lower (optional)","type":"decimal","default":null,"unit":"mm"},
      {"key":"thickness.warning_upper_mm","label":"Warning upper (optional)","type":"decimal","default":null,"unit":"mm"},
      {"key":"calibration.scale","label":"Calibration scale (A, legacy)","type":"decimal","default":-1.0},
      {"key":"calibration.offset","label":"Calibration offset (B, legacy)","type":"decimal","default":10.0},
      {"key":"calibration.mode","label":"Calibration method","type":"text","default":"auto"},
      {"key":"calibration.reference_thickness","label":"Calibration reference thickness","type":"decimal","default":0,"unit":"mm"},
      {"key":"reference.enabled","label":"Use a reference sensor","type":"boolean","default":false},
      {"key":"reference.sensor_key","label":"Reference sensor field (blank = last sensor)","type":"text","default":""},
      {"key":"reference.limit_active","label":"Reference error limit enabled","type":"boolean","default":false},
      {"key":"reference.lower_limit_mm","label":"Reference error lower limit","type":"decimal","default":null,"unit":"mm"},
      {"key":"reference.upper_limit_mm","label":"Reference error upper limit","type":"decimal","default":null,"unit":"mm"}
    ]'::jsonb
WHERE template_id = 'side_by_side_sensors_v1';

UPDATE core.application_templates
SET processor_key = 'sbs_thickness',
    status = 'active',
    requires = '[{"key":"displacement.value_mm","label":"Displacement","kind":"displacement","unit":"mm","required":true,"valid_min":-60,"valid_max":60}]'::jsonb,
    optional = '[]'::jsonb,
    outputs = '[{"key":"thickness.actual_mm","label":"Thickness","unit":"mm","primary":true},
                {"key":"thickness.deviation_mm","label":"Deviation","unit":"mm"},
                {"key":"quality.status","label":"Quality status","kind":"text"},
                {"key":"reference.error_mm","label":"Reference error","unit":"mm"},
                {"key":"quality.reference_status","label":"Reference status","kind":"text"}]'::jsonb,
    field_roles = '{"primary":"thickness.actual_mm","deviation":"thickness.deviation_mm","status":"quality.status",
                    "input":"displacement.value_mm","target":"thickness.target_mm","lower":"thickness.lower_limit_mm",
                    "upper":"thickness.upper_limit_mm","reference":"reference.error_mm",
                    "reference_status":"quality.reference_status","calibration":"calibration.calibrated_at",
                    "unit":"mm","label":"Thickness"}'::jsonb,
    commissioning = '{"calibration":"baseline","filter_field":"displacement.value_mm"}'::jsonb,
    inputs = '[
      {"key":"thickness.target_mm","label":"Nominal thickness","type":"decimal","default":8.5,"unit":"mm"},
      {"key":"thickness.lower_limit_mm","label":"Lower limit","type":"decimal","default":8.3,"unit":"mm"},
      {"key":"thickness.upper_limit_mm","label":"Upper limit","type":"decimal","default":8.7,"unit":"mm"},
      {"key":"thickness.warning_lower_mm","label":"Warning lower (optional)","type":"decimal","default":null,"unit":"mm"},
      {"key":"thickness.warning_upper_mm","label":"Warning upper (optional)","type":"decimal","default":null,"unit":"mm"},
      {"key":"calibration.scale","label":"Calibration scale (A, legacy)","type":"decimal","default":-1.0},
      {"key":"calibration.offset","label":"Calibration offset (B, legacy)","type":"decimal","default":10.0},
      {"key":"calibration.mode","label":"Calibration method","type":"text","default":"auto"},
      {"key":"calibration.reference_thickness","label":"Calibration reference thickness","type":"decimal","default":0,"unit":"mm"},
      {"key":"reference.enabled","label":"Use a reference sensor","type":"boolean","default":true},
      {"key":"reference.sensor_key","label":"Reference sensor field (blank = last sensor)","type":"text","default":""},
      {"key":"reference.limit_active","label":"Reference error limit enabled","type":"boolean","default":false},
      {"key":"reference.lower_limit_mm","label":"Reference error lower limit","type":"decimal","default":null,"unit":"mm"},
      {"key":"reference.upper_limit_mm","label":"Reference error upper limit","type":"decimal","default":null,"unit":"mm"}
    ]'::jsonb
WHERE template_id = 'sbs_reference_v1';

INSERT INTO core.solution_listings
  (listing_id, title, subtitle, product, application, icon, status, model_key, template_id, sort_order)
VALUES
  ('cd22_sbs_reference', 'Side-by-Side with Reference',
   'Several sensors over the same surface plus a reference sensor that corrects baseline drift.',
   'OPTEX CD22', 'Side by Side with Reference', 'layers', 'available',
   'optex_cd22_35_485m12', 'sbs_reference_v1', 20)
ON CONFLICT (listing_id) DO UPDATE
SET title = EXCLUDED.title, subtitle = EXCLUDED.subtitle, status = EXCLUDED.status,
    model_key = EXCLUDED.model_key, template_id = EXCLUDED.template_id, sort_order = EXCLUDED.sort_order;

INSERT INTO core.template_config (instance_id, config_key, value)
SELECT t.instance_id, v.config_key, v.value
FROM core.template_instances t
CROSS JOIN (VALUES
  ('calibration.mode', 'auto'),
  ('calibration.reference_thickness', '0'),
  ('reference.enabled', 'False'),
  ('reference.sensor_key', ''),
  ('reference.limit_active', 'False')
) AS v(config_key, value)
WHERE t.template_id = 'side_by_side_sensors_v1'
ON CONFLICT (instance_id, config_key) DO NOTHING;

INSERT INTO core.template_config (instance_id, config_key, value)
SELECT t.instance_id, v.config_key, v.value
FROM core.template_instances t
CROSS JOIN (VALUES
  ('calibration.mode', 'auto'),
  ('calibration.reference_thickness', '0'),
  ('reference.enabled', 'True'),
  ('reference.sensor_key', ''),
  ('reference.limit_active', 'False')
) AS v(config_key, value)
WHERE t.template_id = 'sbs_reference_v1'
ON CONFLICT (instance_id, config_key) DO NOTHING;
