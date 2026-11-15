-- Machines with similar usage (KMEANS) to compare each machine against its peers (P7, P8).
-- Features are relative (vs model) so trucks and excavators can share clusters. Requires 10 and 30.

CREATE OR REPLACE TABLE af_out.machine_features AS
SELECT
  k.machine_id,
  ANY_VALUE(k.model) AS model,
  AVG(k.utilization_pct) AS avg_utilization_pct,
  AVG(k.idle_hours) AS avg_idle_hours,
  AVG(k.overuse_hours) AS avg_overuse_hours,
  AVG(k.cost_per_hour) AS avg_cost_per_hour,
  IFNULL(AVG(SAFE_DIVIDE(k.fuel_liters_per_ton, k.fuel_liters_per_ton_model)), 1) AS fuel_ratio_vs_model,
  COUNTIF(k.fuel_anomaly) AS fuel_anomaly_days
FROM af_out.daily_kpis AS k
WHERE k.date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY k.machine_id;

CREATE OR REPLACE MODEL af_ml.machine_clusters
OPTIONS (model_type = 'KMEANS', num_clusters = 3, standardize_features = TRUE) AS
SELECT * EXCEPT (machine_id, model)
FROM af_out.machine_features;

CREATE OR REPLACE TABLE af_out.machine_clusters AS
WITH predicted AS (
  SELECT * FROM ML.PREDICT(MODEL af_ml.machine_clusters, (SELECT * FROM af_out.machine_features))
),
profile AS (
  SELECT
    CENTROID_ID,
    AVG(avg_utilization_pct) AS c_util,
    AVG(avg_cost_per_hour) AS c_cost,
    AVG(avg_overuse_hours) AS c_overuse
  FROM predicted
  GROUP BY CENTROID_ID
)
SELECT
  p.machine_id,
  p.model,
  p.CENTROID_ID AS cluster_id,
  -- The machine's own KPIs win over its centroid: with few machines one outlier can share a cluster with the
  -- balanced ones (e.g. a single overloaded truck).
  CASE
    WHEN p.avg_utilization_pct < 40 OR pr.c_util < 40 THEN 'LOW_UTILIZATION_HIGH_COST'
    WHEN p.avg_overuse_hours >= 1 OR p.avg_utilization_pct >= 85 OR pr.c_overuse >= 1 OR pr.c_util >= 85 THEN 'OVERWORKED'
    ELSE 'BALANCED'
  END AS cluster_label,
  ROUND(p.avg_utilization_pct, 1) AS avg_utilization_pct,
  ROUND(p.avg_cost_per_hour, 1) AS avg_cost_per_hour,
  ROUND(p.fuel_ratio_vs_model, 3) AS fuel_ratio_vs_model
FROM predicted AS p
JOIN profile AS pr USING (CENTROID_ID);
