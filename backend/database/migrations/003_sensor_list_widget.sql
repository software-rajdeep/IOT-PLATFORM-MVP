-- =============================================================================
-- 003 — Sensor readings widget: shows every sensor mapped to one input
-- (displacement.value_mm, displacement.value_mm.2, .3, ...). Data, not code.
-- Idempotent.
-- =============================================================================

INSERT INTO core.widget_types (widget_type, name, description, category, requires, default_w, default_h, sort_order)
VALUES ('sensor_list', 'Sensor readings', 'One row per sensor mapped to an input (add sensors in Setup › Map).',
        'value', '["input"]', 4, 2, 165)
ON CONFLICT (widget_type) DO NOTHING;

UPDATE core.application_templates
SET dashboard = jsonb_set(dashboard, '{widgets}',
        (dashboard->'widgets') || '[{"type":"sensor_list","x":0,"y":12,"w":12,"h":2}]'::jsonb)
WHERE template_id = 'side_by_side_sensors_v1'
  AND dashboard IS NOT NULL
  AND dashboard::text NOT LIKE '%sensor_list%';
