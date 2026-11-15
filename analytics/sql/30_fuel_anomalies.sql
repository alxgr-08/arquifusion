-- Fuel consumption out of the expected range (P6).
-- 1) ARIMA_PLUS per machine on daily liters/ton + ML.DETECT_ANOMALIES -> sudden deviations.
-- 2) Linear trend of liters/ton relative to the model -> progressive increase (wear).
-- Requires 10_daily_kpis.sql.

CREATE OR REPLACE MODEL af_ml.fuel_lpt_arima
OPTIONS (
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'date',
  time_series_data_col = 'fuel_liters_per_ton',
  time_series_id_col = 'machine_id',
  data_frequency = 'DAILY'
) AS
SELECT machine_id, date, fuel_liters_per_ton
FROM af_out.daily_kpis
WHERE fuel_liters_per_ton IS NOT NULL;

CREATE OR REPLACE TABLE af_out.fuel_anomalies AS
SELECT
  machine_id,
  DATE(date) AS date,
  fuel_liters_per_ton,
  lower_bound,
  upper_bound,
  anomaly_probability
FROM ML.DETECT_ANOMALIES(MODEL af_ml.fuel_lpt_arima, STRUCT(0.95 AS anomaly_prob_threshold))
WHERE is_anomaly;

-- Rebuilt instead of UPDATE: the BigQuery sandbox (no billing) does not allow DML.
CREATE OR REPLACE TABLE af_out.daily_kpis
PARTITION BY date
CLUSTER BY machine_id
AS
SELECT k.* REPLACE (k.fuel_anomaly OR a.machine_id IS NOT NULL AS fuel_anomaly)
FROM af_out.daily_kpis AS k
LEFT JOIN af_out.fuel_anomalies AS a
  ON k.machine_id = a.machine_id AND k.date = a.date;

-- Progressive increase: slope of (machine L/t / model L/t) over the last 30 days, in % per week.
CREATE OR REPLACE TABLE af_out.fuel_trends AS
WITH ratio AS (
  SELECT
    machine_id,
    model,
    DATE_DIFF(date, DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY), DAY) AS day_index,
    SAFE_DIVIDE(fuel_liters_per_ton, fuel_liters_per_ton_model) AS ratio
  FROM af_out.daily_kpis
  WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY) AND fuel_liters_per_ton IS NOT NULL
)
SELECT
  machine_id,
  model,
  ROUND(AVG(ratio), 3) AS avg_ratio_vs_model,
  ROUND(100 * 7 * SAFE_DIVIDE(COVAR_SAMP(day_index, ratio), VAR_SAMP(day_index)), 2) AS trend_pct_per_week,
  100 * 7 * SAFE_DIVIDE(COVAR_SAMP(day_index, ratio), VAR_SAMP(day_index)) > 1.5 AS progressive_increase
FROM ratio
GROUP BY machine_id, model;
