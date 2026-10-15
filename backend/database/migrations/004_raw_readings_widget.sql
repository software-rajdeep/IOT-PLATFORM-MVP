-- =============================================================================
-- 004 — Raw readings widget: raw sensor readings over time, switchable per
-- sensor column (displacement.value_mm, .2, .3, ...). The card reads straight
-- from the measurement fields for the selected column. Data, not code.
-- Idempotent.
-- =============================================================================

INSERT INTO core.widget_types (widget_type, name, description, category, requires, default_w, default_h, sort_order)
VALUES ('raw_readings', 'Raw readings', 'Raw sensor readings over time; switch between sensor columns.',
        'table', '["input"]', 12, 4, 170)
ON CONFLICT (widget_type) DO NOTHING;

UPDATE core.application_templates
SET dashboard = jsonb_set(dashboard, '{widgets}',
        (dashboard->'widgets') || '[{"type":"raw_readings","x":0,"y":14,"w":12,"h":4}]'::jsonb)
WHERE template_id = 'side_by_side_sensors_v1'
  AND dashboard IS NOT NULL
  AND dashboard::text NOT LIKE '%raw_readings%';

UPDATE core.instance_dashboards d
SET layout = jsonb_set(layout, '{widgets}',
        (layout->'widgets') || '[{"type":"raw_readings","x":0,"y":14,"w":12,"h":4}]'::jsonb)
FROM core.template_instances t
WHERE d.instance_id = t.instance_id
  AND t.template_id = 'side_by_side_sensors_v1'
  AND layout IS NOT NULL
  AND layout::text NOT LIKE '%raw_readings%';
