-- Datasets and lake tables. Rol 1 manages these with Terraform in infra/gcp; this script is the reference DDL
-- and lets Rol 3 work in a BigQuery sandbox before the infra exists.

CREATE SCHEMA IF NOT EXISTS af_lake OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS af_ml OPTIONS (location = 'US');
CREATE SCHEMA IF NOT EXISTS af_out OPTIONS (location = 'US');

-- contracts/telemetry.schema.json (nested, same shape as the Firehose Parquet files)
CREATE TABLE IF NOT EXISTS af_lake.telemetry (
  schemaVersion STRING,
  messageId STRING,
  machineId STRING NOT NULL,
  operationId STRING,
  model STRING,
  machineType STRING,
  timestamp TIMESTAMP NOT NULL,
  location STRUCT<lat FLOAT64, lon FLOAT64, zoneId STRING>,
  status STRING,
  metrics STRUCT<
    engineHours FLOAT64, temperatureC FLOAT64, vibrationMmS FLOAT64, pressureBar FLOAT64, fuelRateLph FLOAT64,
    fuelLevelPct FLOAT64, loadTons FLOAT64, speedKmh FLOAT64, rpm FLOAT64>,
  context STRUCT<fuelBatchId STRING, ambientTempC FLOAT64>
)
PARTITION BY DATE(timestamp)
CLUSTER BY machineId;

-- Incident history (stream-exporter flattens DynamoDB INCIDENT# items to this shape)
CREATE TABLE IF NOT EXISTS af_lake.incidents (
  incidentId STRING NOT NULL,
  machineId STRING NOT NULL,
  model STRING,
  operationId STRING,
  zoneId STRING,
  component STRING,
  severity STRING,
  createdAt TIMESTAMP NOT NULL,
  resolvedAt TIMESTAMP,
  downtimeMinutes INT64,
  partsReplaced ARRAY<STRUCT<partId STRING, warehouse STRING, qty INT64>>,
  zoneIncidentId STRING,
  fuelBatchId STRING
)
PARTITION BY DATE(createdAt);

-- Part consumption per warehouse (stream-exporter, from STOCK# changes)
CREATE TABLE IF NOT EXISTS af_lake.parts_movements (
  date DATE NOT NULL,
  partId STRING NOT NULL,
  warehouseId STRING NOT NULL,
  qty INT64 NOT NULL,
  reason STRING
)
PARTITION BY date;
