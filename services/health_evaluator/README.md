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

## How to test

```bash
python -m pytest -q services/health_evaluator
```
