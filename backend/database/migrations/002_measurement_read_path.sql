-- Speed up the dashboard's latest/history/statistics reads, which filter by
-- both instance and device before ordering by the newest measurement.
CREATE INDEX IF NOT EXISTS idx_measurements_instance_device_time
    ON metrics.measurements(instance_id, device_id, ts DESC);
