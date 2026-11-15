-- Weekly demand forecast per part and warehouse (ARIMA_PLUS) + irregular consumption (P3).
-- Synced to DynamoDB as PART#{partId} FORECAST#{warehouse}#{isoWeek}.

CREATE OR REPLACE MODEL af_ml.parts_demand
OPTIONS (
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'date',
  time_series_data_col = 'qty',
  time_series_id_col = ['part_id', 'warehouse_id'],
  data_frequency = 'DAILY',
  horizon = 28
) AS
WITH days AS (
  SELECT d AS date
  FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY), DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))) AS d
),
series AS (
  SELECT DISTINCT partId AS part_id, warehouseId AS warehouse_id FROM af_lake.parts_movements
),
daily AS (
  SELECT partId AS part_id, warehouseId AS warehouse_id, date, SUM(qty) AS qty
  FROM af_lake.parts_movements
  GROUP BY partId, warehouseId, date
)
SELECT s.part_id, s.warehouse_id, d.date, IFNULL(daily.qty, 0) AS qty
FROM series AS s
CROSS JOIN days AS d
LEFT JOIN daily
  ON daily.part_id = s.part_id AND daily.warehouse_id = s.warehouse_id AND daily.date = d.date;

CREATE OR REPLACE TABLE af_out.parts_forecast AS
SELECT
  part_id,
  warehouse_id,
  FORMAT_DATE('%G-W%V', DATE(forecast_timestamp)) AS iso_week,
  ROUND(SUM(GREATEST(forecast_value, 0)), 1) AS forecast_qty,
  ROUND(SUM(GREATEST(prediction_interval_upper_bound, 0)), 1) AS forecast_qty_upper
FROM ML.FORECAST(MODEL af_ml.parts_demand, STRUCT(28 AS horizon, 0.9 AS confidence_level))
GROUP BY part_id, warehouse_id, iso_week;

-- Irregular consumption: coefficient of variation of weekly demand over the last 26 weeks.
CREATE OR REPLACE TABLE af_out.parts_irregular AS
WITH weekly AS (
  SELECT partId AS part_id, warehouseId AS warehouse_id, DATE_TRUNC(date, ISOWEEK) AS week, SUM(qty) AS qty
  FROM af_lake.parts_movements
  WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 26 WEEK)
  GROUP BY part_id, warehouse_id, week
)
SELECT
  part_id,
  warehouse_id,
  ROUND(AVG(qty), 2) AS avg_weekly_qty,
  ROUND(MAX(qty), 2) AS max_weekly_qty,
  ROUND(SAFE_DIVIDE(STDDEV_SAMP(qty), AVG(qty)), 2) AS coefficient_of_variation,
  SAFE_DIVIDE(STDDEV_SAMP(qty), AVG(qty)) > 1.0 AS irregular
FROM weekly
GROUP BY part_id, warehouse_id;
