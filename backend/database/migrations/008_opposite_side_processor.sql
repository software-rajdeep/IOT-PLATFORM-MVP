-- =============================================================================
-- 008 - Opposite-side sensor layout: gap / auto-gap calibration and limits.
-- Ported from the field-proven thickness-monitoring app:
--   thickness = gap - (35 + A) - (35 + B)
--   gap       = 2 * 35 + A + B + known object thickness  (auto-gap)
--   limits    = object thickness +/- tolerance
-- Data only: the processor reads these configuration keys.
-- Idempotent.
-- =============================================================================

UPDATE core.application_templates
SET processor_key = 'opposite_thickness',
    status = 'active',
    requires = '[{"key":"displacement.value_mm","label":"Displacement","kind":"displacement","unit":"mm","required":true,"valid_min":-60,"valid_max":60}]'::jsonb,
    optional = '[]'::jsonb,
    outputs = '[{"key":"thickness.actual_mm","label":"Thickness","unit":"mm","primary":true},
                {"key":"thickness.deviation_mm","label":"Deviation","unit":"mm"},
                {"key":"quality.status","label":"Quality status","kind":"text"},
                {"key":"opposite.gap_mm","label":"Sensor gap","unit":"mm"},
                {"key":"opposite.distance_a_mm","label":"Sensor A distance","unit":"mm"},
                {"key":"opposite.distance_b_mm","label":"Sensor B distance","unit":"mm"}]'::jsonb,
    field_roles = '{"primary":"thickness.actual_mm","deviation":"thickness.deviation_mm","status":"quality.status",
                    "input":"displacement.value_mm","target":"thickness.target_mm","lower":"thickness.lower_limit_mm",
                    "upper":"thickness.upper_limit_mm","gap":"opposite.gap_mm","distance_a":"opposite.distance_a_mm",
                    "distance_b":"opposite.distance_b_mm","calibration":"calibration.calibrated_at",
                    "unit":"mm","label":"Thickness"}'::jsonb,
    commissioning = '{"calibration":"gap","filter_field":"displacement.value_mm"}'::jsonb,
    alert_defaults = '[{"type":"fail_streak","params":{"count":5}},{"type":"offline","params":{"seconds":15}},
                       {"type":"mapping_failure","params":{}}]'::jsonb,
    inputs = '[
      {"key":"thickness.target_mm","label":"Nominal thickness","type":"decimal","default":5.0,"unit":"mm"},
      {"key":"thickness.lower_limit_mm","label":"Lower limit","type":"decimal","default":null,"unit":"mm"},
      {"key":"thickness.upper_limit_mm","label":"Upper limit","type":"decimal","default":null,"unit":"mm"},
      {"key":"thickness.warning_lower_mm","label":"Warning lower (optional)","type":"decimal","default":null,"unit":"mm"},
      {"key":"thickness.warning_upper_mm","label":"Warning upper (optional)","type":"decimal","default":null,"unit":"mm"},
      {"key":"thickness.limit_active","label":"Thickness limit enabled","type":"boolean","default":true},
      {"key":"calibration.mode","label":"Calibration method","type":"text","default":"gap"},
      {"key":"calibration.gap_distance","label":"Gap between sensor faces","type":"decimal","default":0,"unit":"mm"},
      {"key":"calibration.object_thickness","label":"Calibration object thickness","type":"decimal","default":0,"unit":"mm"},
      {"key":"calibration.tolerance_mm","label":"Auto-gap tolerance (+/-)","type":"decimal","default":null,"unit":"mm"}
    ]'::jsonb,
    dashboard = '{"widgets":[
       {"type":"current_value","title":"Current thickness","field":"thickness.actual_mm","unit":"mm","x":0,"y":0,"w":3,"h":2},
       {"type":"target","title":"Target","field":"thickness.target_mm","deviation_field":"thickness.deviation_mm","x":3,"y":0,"w":3,"h":2},
       {"type":"pass_fail","x":6,"y":0,"w":3,"h":2},
       {"type":"device_health","x":9,"y":0,"w":3,"h":2},
       {"type":"trend_band","x":0,"y":2,"w":8,"h":4},
       {"type":"statistics","x":8,"y":2,"w":4,"h":2},
       {"type":"raw_current","x":8,"y":4,"w":4,"h":2},
       {"type":"calibration_status","x":0,"y":6,"w":6,"h":2},
       {"type":"limits_editor","x":6,"y":6,"w":6,"h":2},
       {"type":"sensor_list","x":0,"y":8,"w":6,"h":2},
       {"type":"history_table","x":0,"y":10,"w":12,"h":4}
     ]}'::jsonb
WHERE template_id = 'opposite_side_sensors_v1';

UPDATE core.solution_listings
SET title = 'Opposite-side Thickness',
    subtitle = 'Two sensors facing each other; thickness from the measured gap, with auto-gap and tolerance limits.',
    product = 'OPTEX CD22 (pair)',
    application = 'Opposite Side Sensors',
    icon = 'layers',
    status = 'available',
    model_key = 'optex_cd22_35_485m12',
    template_id = 'opposite_side_sensors_v1',
    sort_order = 20
WHERE listing_id = 'cd22_opposite';

INSERT INTO core.template_fields (template_id, field_key, source, data_type, unit, sort_order)
VALUES
  ('opposite_side_sensors_v1', 'opposite.gap_mm',        'calculated', 'decimal', 'mm', 150),
  ('opposite_side_sensors_v1', 'opposite.distance_a_mm', 'calculated', 'decimal', 'mm', 160),
  ('opposite_side_sensors_v1', 'opposite.distance_b_mm', 'calculated', 'decimal', 'mm', 170)
ON CONFLICT (template_id, field_key) DO NOTHING;

INSERT INTO core.template_config (instance_id, config_key, value)
SELECT t.instance_id, v.config_key, v.value
FROM core.template_instances t
CROSS JOIN (VALUES
  ('calibration.mode', 'gap'),
  ('calibration.gap_distance', '0'),
  ('calibration.object_thickness', '0'),
  ('thickness.limit_active', 'True')
) AS v(config_key, value)
WHERE t.template_id = 'opposite_side_sensors_v1'
ON CONFLICT (instance_id, config_key) DO NOTHING;
