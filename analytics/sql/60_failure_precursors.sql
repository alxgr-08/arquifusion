-- Patterns that appear before failures (P12): for each component, how each metric deviates from the model
-- baseline 72-48 h, 48-24 h and 24-0 h before the failure, and how early the first strong signal (|z| > 3) shows up.
-- Requires 20_model_baselines.sql. Zone incidents (common external cause) are excluded.

CREATE OR REPLACE TABLE af_out.failure_precursors AS
WITH failures AS (
  SELECT incidentId, machineId, component, createdAt
  FROM af_lake.incidents
  WHERE zoneIncidentId IS NULL AND createdAt >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 180 DAY)
),
before_failure AS (
  SELECT f.incidentId, f.component, f.createdAt, t.timestamp, t.model, t.metrics
  FROM failures AS f
  JOIN af_lake.telemetry AS t
    ON t.machineId = f.machineId
    AND t.timestamp BETWEEN TIMESTAMP_SUB(f.createdAt, INTERVAL 72 HOUR) AND f.createdAt
  WHERE t.status = 'OPERATING'
),
z_scores AS (
  SELECT
    p.incidentId,
    p.component,
    b.metric,
    TIMESTAMP_DIFF(p.createdAt, p.timestamp, MINUTE) / 60 AS hours_before,
    SAFE_DIVIDE(
      CASE b.metric
        WHEN 'temperatureC' THEN p.metrics.temperatureC
        WHEN 'vibrationMmS' THEN p.metrics.vibrationMmS
        WHEN 'pressureBar' THEN p.metrics.pressureBar
        WHEN 'fuelRateLph' THEN p.metrics.fuelRateLph
        WHEN 'rpm' THEN p.metrics.rpm
      END - b.mean,
      b.std
    ) AS z
  FROM before_failure AS p
  JOIN af_out.model_baselines AS b ON b.model = p.model
),
per_failure AS (
  SELECT
    incidentId,
    component,
    metric,
    AVG(IF(hours_before > 48, z, NULL)) AS z_72_48h,
    AVG(IF(hours_before > 24 AND hours_before <= 48, z, NULL)) AS z_48_24h,
    AVG(IF(hours_before <= 24, z, NULL)) AS z_24_0h,
    MAX(IF(ABS(z) > 3, hours_before, NULL)) AS first_signal_hours_before
  FROM z_scores
  GROUP BY incidentId, component, metric
)
SELECT
  component,
  metric,
  COUNT(*) AS failures,
  ROUND(AVG(z_72_48h), 2) AS avg_z_72_48h,
  ROUND(AVG(z_48_24h), 2) AS avg_z_48_24h,
  ROUND(AVG(z_24_0h), 2) AS avg_z_24_0h,
  ROUND(COUNTIF(ABS(z_24_0h) > 2) / COUNT(*), 2) AS share_with_signal_24h,
  ROUND(APPROX_QUANTILES(first_signal_hours_before, 2)[OFFSET(1)], 1) AS median_lead_hours
FROM per_failure
GROUP BY component, metric
ORDER BY component, metric;
