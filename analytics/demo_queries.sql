-- Pitch scene 2:50-3:45 (docs/pitch.md): read-only queries over the 60-day synthetic history in `arquifusion`.
-- Run each one in the BigQuery console (project arquifusion, location US). Data is synthetic: label the scene as such.

-- 1. Fuel anomalies detected by ARIMA_PLUS (af_ml.fuel_lpt_arima): liters per ton outside the expected band.
SELECT machine_id, date, ROUND(fuel_liters_per_ton, 3) AS l_per_ton,
       ROUND(lower_bound, 3) AS expected_min, ROUND(upper_bound, 3) AS expected_max,
       ROUND(anomaly_probability, 3) AS probability
FROM `arquifusion.af_out.fuel_anomalies`
ORDER BY anomaly_probability DESC, date DESC
LIMIT 10;

-- 2. Machines whose fuel use per ton rises week after week (progressive wear, not a one-off spike).
SELECT machine_id, model, ROUND(avg_ratio_vs_model, 2) AS ratio_vs_model,
       ROUND(trend_pct_per_week, 2) AS trend_pct_per_week
FROM `arquifusion.af_out.fuel_trends`
WHERE progressive_increase
ORDER BY trend_pct_per_week DESC;

-- 3. Fleet segments from K-means (af_ml.machine_clusters): utilization, cost and fuel vs. model.
SELECT cluster_label, COUNT(*) AS machines,
       ROUND(AVG(avg_utilization_pct), 1) AS utilization_pct,
       ROUND(AVG(avg_cost_per_hour), 1) AS cost_per_hour,
       ROUND(AVG(fuel_ratio_vs_model), 2) AS fuel_vs_model,
       STRING_AGG(machine_id, ', ' ORDER BY machine_id) AS machine_ids
FROM `arquifusion.af_out.machine_clusters`
GROUP BY cluster_label
ORDER BY machines DESC;

-- 4. Parts demand forecast (af_ml.parts_demand, ARIMA_PLUS): next weeks per part and warehouse.
SELECT part_id, warehouse_id, iso_week, ROUND(forecast_qty, 1) AS forecast_qty,
       ROUND(forecast_qty_upper, 1) AS upper_90
FROM `arquifusion.af_out.parts_forecast`
WHERE forecast_qty_upper > 0
ORDER BY forecast_qty_upper DESC, iso_week
LIMIT 12;
