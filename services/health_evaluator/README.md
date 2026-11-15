# af-dev-health-evaluator

- **Owner:** Rol 3
- **Trigger:** IoT Rule
- **Input -> output:** Telemetry -> PROFILE, READING#, AnomalyDetected
- **Problems:** P1, P8

## Health score (`scoring.py`, pure logic)

1. EWMA per metric (alpha 0.2).
2. z-score of the EWMA against the machine's own history (Welford, needs >= 30 samples) and against the model fleet
   baseline (`MODEL#{model} BASELINE`, from BigQuery). `risk = max(z_own, 0.8 * z_fleet)` in the dangerous
   direction, clamped to [0, 5].
3. `health = 100 - 20 * weighted_avg(risk)`, x1.25 when 2+ metrics have risk > 2.
4. Severity rises only after 3 consecutive readings; any risk >= 4.5 goes straight to CRITICAL; recovery is immediate.
5. The own baseline learns only from healthy OPERATING readings whose metric is not an outlier (|z| <= 3), so a
   developing fault is never learned as normal.
6. `should_emit` is true only when severity rises into WARNING or CRITICAL (one event per escalation).

| Metric | Dangerous direction | Default weight |
|---|---|---|
| temperatureC | HIGH | 0.30 |
| vibrationMmS | HIGH | 0.30 |
| pressureBar | LOW | 0.15 |
| fuelRateLph | HIGH | 0.15 |
| rpm | BOTH | 0.10 |

Weights and thresholds are overridable from SSM `/af/health/weights` and `/af/health/thresholds`
(`HealthConfig.from_params`).

## Handler (`handler.py`)

1. Validate the payload against `contracts/telemetry` (invalid -> logged and dropped, no retry).
2. Read `PROFILE` and `MODEL#{model} BASELINE`, run `scoring.evaluate`.
3. One transaction: put `READING#{ts}` (TTL `expiresAt` = ts + 48 h, `attribute_not_exists(SK)` drops MQTT duplicates;
   keeps the telemetry `context`, e.g. `fuelBatchId`) and update `PROFILE` (condition `lastSeen < ts` drops out-of-order readings; `GSI1SK` only rewritten on status change).
4. If severity rose into WARNING/CRITICAL, publish `AnomalyDetected` (validated with `af_shared`) to the event bus.

The profile update only SETs its own attributes, so fields owned by Rol 2 (`openIncidentSK`, `lastMaintenanceHours`)
are never overwritten.

### Deployment (Rol 1)

| Setting | Value |
|---|---|
| Runtime / handler | Python 3.12 · `handler.handler` (package with `scoring.py` and `shared/af_shared`) |
| Trigger | IoT Rule `SELECT * FROM 'arquifusion/+/+/telemetry'` -> Lambda action |
| Env | `AF_TABLE_NAME`, `AF_EVENT_BUS`, `AF_SSM_HEALTH_WEIGHTS`, `AF_SSM_HEALTH_THRESHOLDS`, `POWERTOOLS_SERVICE_NAME` |
| Permissions | DynamoDB GetItem/TransactWriteItems, events:PutEvents, ssm:GetParameter (LabRole in Academy) |
| Memory / timeout | 256 MB / 10 s |

### Model baseline item (written by bigquery-sync-back)

```json
{ "PK": "MODEL#EXC-320", "SK": "BASELINE",
  "metrics": { "temperatureC": { "mean": 78.1, "std": 4.6 }, "vibrationMmS": { "mean": 4.2, "std": 1.4 } } }
```

## How to test

```bash
python -m pytest -q services/health_evaluator
```
