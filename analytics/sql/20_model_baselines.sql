-- af_out.model_baselines: healthy mean/std per model and metric. Fleet reference (z_fleet) of the
-- health-evaluator, synced to DynamoDB as MODEL#{model} BASELINE (P1, P8).
-- Readings from 72 h before a failure until its repair are excluded, so faults are never learned as normal.

CREATE OR REPLACE TABLE af_out.model_baselines AS
WITH healthy AS (
  SELECT t.model, t.metrics
  FROM af_lake.telemetry AS t
  LEFT JOIN af_lake.incidents AS i
    ON i.machineId = t.machineId
    AND t.timestamp BETWEEN TIMESTAMP_SUB(i.createdAt, INTERVAL 72 HOUR) AND IFNULL(i.resolvedAt, i.createdAt)
  WHERE t.status = 'OPERATING'
    AND t.timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    AND i.incidentId IS NULL
),
long_format AS (
  SELECT h.model, m.metric, m.value
  FROM healthy AS h,
  UNNEST([
    STRUCT('temperatureC' AS metric, h.metrics.temperatureC AS value),
    STRUCT('vibrationMmS' AS metric, h.metrics.vibrationMmS AS value),
    STRUCT('pressureBar' AS metric, h.metrics.pressureBar AS value),
    STRUCT('fuelRateLph' AS metric, h.metrics.fuelRateLph AS value),
    STRUCT('rpm' AS metric, h.metrics.rpm AS value)
  ]) AS m
  WHERE m.value IS NOT NULL
)
SELECT
  model,
  metric,
  ROUND(AVG(value), 3) AS mean,
  ROUND(STDDEV_SAMP(value), 3) AS std,
  COUNT(*) AS n,
  CURRENT_TIMESTAMP() AS computed_at
FROM long_format
GROUP BY model, metric;
