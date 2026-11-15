# af-dev-health-evaluator

- **Owner:** Rol 3
- **Entrada de Academy:** invocación directa del simulador o ensayo manual. [Arquitectura](../../docs/arquitectura.md)
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
3. La transacción añade `READING#{ts}` con operación, contexto y TTL de 48 h. Actualiza el perfil sobre la última versión leída. Las nuevas claves y `lastSeen` usan UTC con seis decimales de segundo. Se comparan los instantes antes de escribir y se comprueba la igualdad exacta del `lastSeen` leído dentro de la transacción. Así se conservan dos muestras del mismo segundo y un reenvío de una lectura histórica con clave de segundos sigue siendo idempotente. Una transacción rechazada por capacidad o contención exige reintentar la invocación sobre el perfil actualizado.
4. Ante una escalada WARNING/CRITICAL la misma transacción escribe `OUTBOX#ANOMALY#{messageId}` en la partición de máquina y mantiene el puntero compatible `PROFILE.pendingAlert`. El evento conserva su detectedAt. Solo se retira su propia fila tras recibir un EventId sin errores del bus.

La recuperación se intenta después de guardar la muestra nueva. Si EventBridge falla, se conserva cada lectura aceptada en orden y el error de publicación continúa siendo visible para el invocador. Se intentan por separado la alerta anterior y la del mensaje que se está procesando. El reenvío de un mensaje busca su propia fila durable incluso si otra escalada sustituyó el puntero de `PROFILE`. Los diagnósticos de validación omiten los valores de entrada y una fecha sin zona horaria se rechaza antes de consultar DynamoDB.

Si una versión anterior conservaba la alerta únicamente en `PROFILE.pendingAlert`, la transacción que registra una nueva escalada materializa también la alerta anterior en su propia outbox. Comprueba que cualquier fila ya existente tenga la misma identidad y detalle y preserva sus atributos adicionales. El puntero antiguo y el nuevo evento permanecen recuperables durante una actualización de versión y un fallo del bus.

### Recuperar una alerta

Ante una máquina que dejó de reportar, identificar la fila por su clave o por GSI1PK `OUTBOX#ANOMALY`. Una invocación manual autorizada acepta `{"reconcileAlert":{"machineId":"…","operationId":"…","messageId":"…"}}`. Relee la fila consistentemente. Los perfiles anteriores se recuperan por el mismo ID cuando todavía contienen el puntero. Una respuesta ambigua puede producir una repetición. La entrada Standard y el flujo deduplican la misma identidad de negocio.

El opt-in `prepare_anomaly_outbox_consumer` prepara un mapping de Streams de nuevas filas, inicialmente deshabilitado. Los errores parciales informan el número de secuencia y los pendientes se conservan sin TTL. Revisar operativamente eventos fuera de la ventana de 30 días del workflow. La aceptación en el bus y la entrega a un consumidor tienen evidencias independientes. Este protocolo está validado en local y pendiente de despliegue. [Mapping](../../infra/aws/outbox.tf)

The profile update only SETs its own attributes, so fields owned by Rol 2 (`openIncidentSK`, `lastMaintenanceHours`)
are never overwritten.

### Deployment (Rol 1)

| Setting | Value |
|---|---|
| Runtime / handler | Python 3.12 · `handler.handler` (package with `scoring.py` and `shared/af_shared`) |
| Trigger | Modo `direct`; el consumidor de recuperación es optativo y queda deshabilitado al crearse. |
| Env | `AF_TABLE_NAME`, `AF_EVENT_BUS`, `AF_SSM_HEALTH_WEIGHTS`, `AF_SSM_HEALTH_THRESHOLDS`, `POWERTOOLS_SERVICE_NAME` |
| Permissions | DynamoDB GetItem/TransactWriteItems/UpdateItem/DeleteItem y lectura del stream si se aprueba el consumidor, events:PutEvents, ssm:GetParameter (LabRole in Academy) |
| Memory / timeout | 256 MB / 10 s |

### Model baseline item (written by bigquery-sync-back)

```json
{ "PK": "MODEL#EXC-320", "SK": "BASELINE",
  "metrics": { "temperatureC": { "mean": 78.1, "std": 4.6 }, "vibrationMmS": { "mean": 4.2, "std": 1.4 } } }
```

## How to test

```bash
python -m pytest -q --disable-socket services/health_evaluator
```
