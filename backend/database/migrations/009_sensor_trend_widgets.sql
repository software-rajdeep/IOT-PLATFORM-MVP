-- 009 — Per-sensor thickness cards and one trend graph per mapped sensor.
-- The data bundle already exposes every mapped sensor (sensors[]); the dashboard
-- assembly now also ships a per-sensor thickness series for baseline calibration.
-- Idempotent: skip layouts/widget rows that already contain the new types.

INSERT INTO core.widget_types
  (widget_type, name, description, category, requires, default_w, default_h, sort_order)
VALUES
  ('sensor_thickness', 'Per-sensor thickness', 'Latest thickness for each mapped sensor.', 'value', '["input"]', 12, 2, 166),
  ('sensor_trends',    'Per-sensor graphs',    'One thickness trend chart per mapped sensor.', 'chart', '["input"]', 12, 4, 240)
ON CONFLICT (widget_type) DO NOTHING;

-- Template default layout.
WITH bottom AS (
  SELECT coalesce(max(coalesce((w->>'y')::int, 0) + coalesce((w->>'h')::int, 0)), 0) AS y
  FROM jsonb_array_elements((
    SELECT dashboard->'widgets' FROM core.application_templates
    WHERE template_id = 'side_by_side_sensors_v1'
  )) w
)
UPDATE core.application_templates t
SET dashboard = jsonb_set(t.dashboard, '{widgets}', (t.dashboard->'widgets') || jsonb_build_array(
      jsonb_build_object('type', 'sensor_thickness', 'x', 0, 'y', (SELECT y FROM bottom), 'w', 12, 'h', 2),
      jsonb_build_object('type', 'sensor_trends', 'x', 0, 'y', (SELECT y FROM bottom) + 2, 'w', 12, 'h', 4)))
WHERE t.template_id = 'side_by_side_sensors_v1'
  AND t.dashboard IS NOT NULL
  AND t.dashboard::text NOT LIKE '%"sensor_trends"%';

-- Saved per-instance layouts.
UPDATE core.instance_dashboards d
SET layout = jsonb_set(d.layout, '{widgets}', (d.layout->'widgets') || jsonb_build_array(
      jsonb_build_object('type', 'sensor_thickness', 'x', 0,
        'y', (SELECT coalesce(max(coalesce((w->>'y')::int, 0) + coalesce((w->>'h')::int, 0)), 0)
              FROM jsonb_array_elements(d.layout->'widgets') w),
        'w', 12, 'h', 2),
      jsonb_build_object('type', 'sensor_trends', 'x', 0,
        'y', (SELECT coalesce(max(coalesce((w->>'y')::int, 0) + coalesce((w->>'h')::int, 0)), 0) + 2
              FROM jsonb_array_elements(d.layout->'widgets') w),
        'w', 12, 'h', 4)))
FROM core.template_instances t
WHERE d.instance_id = t.instance_id
  AND t.template_id = 'side_by_side_sensors_v1'
  AND d.layout IS NOT NULL
  AND d.layout::text NOT LIKE '%"sensor_trends"%';
