-- af_out.daily_kpis: utilization, idle/overuse hours, fuel per ton vs model and cost per productive hour (P6, P7).
DECLARE fuel_price_per_liter FLOAT64 DEFAULT 1.10;
DECLARE fixed_cost_per_day FLOAT64 DEFAULT 2400;  -- ownership + operator per machine and day
DECLARE fuel_ratio_alert FLOAT64 DEFAULT 1.20;    -- 20 % above the model average on the same day
DECLARE lookback_days INT64 DEFAULT 60;

CREATE OR REPLACE TABLE af_out.daily_kpis
PARTITION BY date
CLUSTER BY machine_id
AS
WITH readings AS (
  SELECT
    machineId AS machine_id,
    model,
    DATE(timestamp) AS date,
    status,
    metrics.loadTons AS load_tons,
    metrics.fuelRateLph AS fuel_rate_lph,
    -- hours represented by each reading: gap to the next one, capped at 30 min
    LEAST(
      IFNULL(TIMESTAMP_DIFF(LEAD(timestamp) OVER (PARTITION BY machineId ORDER BY timestamp), timestamp, SECOND), 900),
      1800
    ) / 3600 AS hours
  FROM af_lake.telemetry
  WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL lookback_days DAY)
),
nominal AS (
  SELECT model, APPROX_QUANTILES(load_tons, 100)[OFFSET(50)] AS median_load
  FROM readings
  WHERE status = 'OPERATING' AND load_tons > 0
  GROUP BY model
),
daily AS (
  SELECT
    r.machine_id,
    r.model,
    r.date,
    SUM(IF(r.status = 'OPERATING', r.hours, 0)) AS operating_hours,
    SUM(IF(r.status = 'IDLE', r.hours, 0)) AS idle_hours,
    SUM(IF(r.status IN ('STOPPED', 'MAINTENANCE'), r.hours, 0)) AS down_hours,
    SUM(IF(r.status = 'OPERATING' AND r.load_tons > 1.1 * n.median_load, r.hours, 0)) AS overuse_hours,
    SUM(IF(r.status = 'OPERATING', r.load_tons, 0)) AS tons_moved,  -- one load cycle per reading
    SUM(r.fuel_rate_lph * r.hours) AS fuel_liters
  FROM readings AS r
  LEFT JOIN nominal AS n USING (model)
  GROUP BY r.machine_id, r.model, r.date
),
kpis AS (
  SELECT
    *,
    SAFE_DIVIDE(fuel_liters, NULLIF(tons_moved, 0)) AS fuel_liters_per_ton,
    (fuel_liters * fuel_price_per_liter + fixed_cost_per_day) / GREATEST(operating_hours, 1) AS cost_per_hour
  FROM daily
)
SELECT
  machine_id,
  model,
  date,
  ROUND(100 * operating_hours / 24, 1) AS utilization_pct,
  ROUND(operating_hours, 2) AS operating_hours,
  ROUND(idle_hours, 2) AS idle_hours,
  ROUND(down_hours, 2) AS down_hours,
  ROUND(overuse_hours, 2) AS overuse_hours,
  ROUND(tons_moved, 1) AS tons_moved,
  ROUND(fuel_liters, 1) AS fuel_liters,
  ROUND(fuel_liters_per_ton, 3) AS fuel_liters_per_ton,
  ROUND(AVG(fuel_liters_per_ton) OVER (PARTITION BY model, date), 3) AS fuel_liters_per_ton_model,
  ROUND(cost_per_hour, 1) AS cost_per_hour,
  -- Compared with the other machines of the same model that day (the machine itself excluded: with 2 machines
  -- per model, including it hides a +28 % jump).
  IFNULL(
    fuel_liters_per_ton > fuel_ratio_alert * SAFE_DIVIDE(
      SUM(fuel_liters_per_ton) OVER (PARTITION BY model, date) - fuel_liters_per_ton,
      COUNT(fuel_liters_per_ton) OVER (PARTITION BY model, date) - 1
    ),
    FALSE
  ) AS fuel_anomaly
FROM kpis;
