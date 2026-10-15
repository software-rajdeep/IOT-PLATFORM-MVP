-- =============================================================================
-- 002 — Rubber thickness (single sensor): declare only the inputs the
-- application actually uses. The mapping step reads the template contract, so
-- trimming `optional` (and the field roles that pointed at it) removes the
-- irrelevant "Product / recipe code" and "Line speed" rows. Data, not code.
-- Idempotent.
-- =============================================================================

UPDATE core.application_templates
SET optional    = '[]'::jsonb,
    field_roles = field_roles - 'group' - 'x_speed'
WHERE template_id = 'side_by_side_sensors_v1';
