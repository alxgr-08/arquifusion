"""BigQuery tables and views for the private Academy bridge. The dashboard's KPI definitions live in analytics/."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/bridge-permanent"
DATASET = "airi-erp.af_bridge"
OPERATION = "AF-BRIDGE-PERU"


def field(name, kind="STRING", children=()):
    value = {"name": name, "type": kind, "mode": "NULLABLE"}
    if children:
        value["fields"] = list(children)
    return value


def prepare():
    fields = [
        field(name) for name in ("schemaVersion", "messageId", "machineId", "operationId", "model", "machineType")
    ]
    fields += [
        field("timestamp", "TIMESTAMP"),
        field("location", "RECORD", [field("lat", "FLOAT"), field("lon", "FLOAT"), field("zoneId")]),
        field("status"),
    ]
    metrics = "engineHours temperatureC vibrationMmS pressureBar fuelRateLph fuelLevelPct loadTons speedKmh rpm".split()
    fields += [
        field("metrics", "RECORD", [field(m, "FLOAT") for m in metrics]),
        field("context", "RECORD", [field("fuelBatchId"), field("ambientTempC", "FLOAT")]),
    ]
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "raw-schema.json").write_text(json.dumps(fields, indent=2), encoding="utf-8", newline="\r\n")
    canonical = f"""SELECT r.*
FROM \x60{DATASET}.telemetry_raw\x60 AS r
WHERE operationId = '{OPERATION}' AND REGEXP_CONTAINS(machineId, r'^BRG-[0-9]{{4}}$')
  AND schemaVersion = '1.0' AND REGEXP_CONTAINS(messageId, r'^[0-9a-fA-F-]{{36}}$')
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY operationId, machineId, messageId
  ORDER BY timestamp, TO_JSON_STRING(r)
) = 1"""
    (BUILD / "telemetry.sql").write_text(canonical, encoding="utf-8", newline="\r\n")
    analytics = (ROOT / "analytics/sql/10_daily_kpis.sql").read_text(encoding="utf-8")
    select = analytics[analytics.index("WITH readings AS (") :].strip().removesuffix(";")
    replacements = {
        "FROM af_lake.telemetry": f"FROM \x60{DATASET}.telemetry\x60",
        "INTERVAL lookback_days DAY": "INTERVAL 30 DAY",
        "fuel_price_per_liter": "1.10",
        "fixed_cost_per_day": "2400",
        "fuel_ratio_alert": "1.20",
        "PARTITION BY machineId ORDER BY timestamp": "PARTITION BY machineId ORDER BY timestamp, messageId",
    }
    for before, after in replacements.items():
        if before not in select:
            raise ValueError("Review the changed daily KPI contract: " + before)
        select = select.replace(before, after)
    daily = f"""WITH calculated AS ({select}),
proof AS (
  SELECT machineId, DATE(timestamp) AS date, COUNT(*) AS source_readings,
         ARRAY_AGG(messageId ORDER BY timestamp DESC, messageId DESC LIMIT 1)[OFFSET(0)] AS last_message_id
  FROM \x60{DATASET}.telemetry\x60
  WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  GROUP BY machineId, date
)
SELECT k.*, proof.source_readings, proof.last_message_id,
       SUM(proof.source_readings) OVER (PARTITION BY k.date) AS day_readings
FROM calculated AS k
JOIN proof ON k.machine_id = proof.machineId AND k.date = proof.date"""
    (BUILD / "daily.sql").write_text(daily, encoding="utf-8", newline="\r\n")


if __name__ == "__main__":
    prepare()
