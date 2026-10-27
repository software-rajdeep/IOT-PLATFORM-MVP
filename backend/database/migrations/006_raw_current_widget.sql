-- 006 — Compact live card for the latest raw sensor readings.
-- Idempotent: do not duplicate it in saved layouts.

INSERT INTO core.widget_types (widget_type, name, description, category, requires, default_w, default_h, sort_order)
VALUES ('raw_current', 'Current raw readings', 'Latest reading from each mapped sensor.',
        'value', '["input"]', 6, 2, 165)
ON CONFLICT (widget_type) DO NOTHING;

UPDATE core.application_templates
SET dashboard = jsonb_set(dashboard, '{widgets}',
        (dashboard->'widgets') || '[{"type":"raw_current","x":0,"y":12,"w":6,"h":2}]'::jsonb)
WHERE template_id = 'side_by_side_sensors_v1'
  AND dashboard IS NOT NULL
  AND dashboard::text NOT LIKE '%raw_current%';

UPDATE core.instance_dashboards d
SET layout = jsonb_set(layout, '{widgets}',
        (layout->'widgets') || '[{"type":"raw_current","x":0,"y":12,"w":6,"h":2}]'::jsonb)
FROM core.template_instances t
WHERE d.instance_id = t.instance_id
  AND t.template_id = 'side_by_side_sensors_v1'
  AND layout IS NOT NULL
  AND layout::text NOT LIKE '%raw_current%';
