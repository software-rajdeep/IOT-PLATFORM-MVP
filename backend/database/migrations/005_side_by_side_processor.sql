-- 005 — Enable the two-sensor side-by-side thickness processor.
-- The processor averages all mapped displacement.value_mm readings before
-- applying the configured calibration and quality limits. Existing instances
-- use the same template processor after this idempotent update.

UPDATE core.application_templates
SET processor_key = 'sbs_thickness'
WHERE template_id = 'side_by_side_sensors_v1';
