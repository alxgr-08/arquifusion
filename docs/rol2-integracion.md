# Rol 2 · Qué necesita cada uno para integrar el backend

Las pruebas del Rol 2 corren las dos Step Functions de punta a punta (intérprete de ASL + DynamoDB en memoria),
con la flota de `contracts/examples/fleet-config.json` y las reglas reales de `ai-diagnosis` del Rol 3.

## Para Rol 1 (Terraform, Learner Lab)

### Lambdas (handler `handler.handler`, Python 3.12, rol `LabRole`)

| Función | Trigger | Timeout | Env |
|---|---|---|---|
| `af-dev-simulator` | Scheduler `rate(1 minute)` | 60 s | `AF_INGEST_MODE` (`iot`/`direct`), `AF_IOT_ENDPOINT` opcional, `AF_SIM_RAMP_SECONDS` (300), `AF_SIM_FAST_INTERVAL_SECONDS` (15), plan B: `AF_HEALTH_EVALUATOR_FUNCTION`, `AF_FIREHOSE_STREAM` |
| `af-dev-fault-injector` | API `POST /simulator/faults` | 10 s | `AF_SIMULATOR_FUNCTION`, `AF_FAULT_GROUPS` opcional |
| `af-dev-offline-detector` | Scheduler `rate(5 minutes)` | 30 s | `AF_TABLE_NAME`, `AF_OPERATIONS`, `AF_OFFLINE_AFTER_MINUTES` |
| `af-dev-incident-tasks` | Step Functions | 30 s | `AF_TABLE_NAME` |
| `af-dev-assign-technician` | Step Functions | 30 s | `AF_TABLE_NAME`, `AF_ROUTE_CALCULATOR` opcional |
| `af-dev-notify` | Step Functions | 30 s | `AF_TABLE_NAME` |
| `af-dev-api` | API Gateway (todas las rutas menos `/simulator/faults`) | 15 s | `AF_TABLE_NAME`, `AF_OPERATION_ID` (MINA-NORTE, para `GET /parts`) |

Empaquetado: cada `services/<x>/` + `shared/af_shared/` en el mismo zip, más una capa con `pydantic>=2.7`.
boto3 viene en el runtime. Powertools es opcional (si la capa está, el logger la usa).

### Step Functions
- `statemachines/incident.asl.json` y `preventive.asl.json` con `templatefile()`; variables
  `incident_tasks_arn`, `assign_technician_arn`, `notify_arn`, `ai_diagnosis_arn`.
- Regla de EventBridge (bus `af-dev-bus`) -> `af-dev-incident`:
  ```json
  { "source": ["arquifusion.health"], "detail-type": ["AnomalyDetected"], "detail": { "severity": ["WARNING", "CRITICAL"] } }
  ```
- Scheduler diario -> `af-dev-preventive` con input `{"operationId": "MINA-NORTE"}`.

### Otros recursos
- DynamoDB: GSI1 y GSI2 con proyección `ALL` (la API y los flujos leen atributos completos del índice).
- 5 topics SNS (`af-dev-notify-operator`, `-technician`, `-maint-lead`, `-supervisor`, `-manager`) y sus ARN en SSM
  `/af/notify/topics` como JSON `{"OPERATOR": "arn...", ...}`. En el lab, suscripciones por email para el video.
- Amazon Location: un route calculator (`AF_ROUTE_CALCULATOR`) si el lab lo permite; si no, se usa haversine solo.
- Parámetros SSM: los escribe `python scripts/seed_demo.py --demo` (`/af/sim/fleet-config` = el
  `fleet-config.json` completo; tier `Intelligent-Tiering` por si pasa de 4 KB).

## Para Rol 3

1. **PROFILE:** tu health-evaluator usa UpdateItem y no toca `openIncidentId`, `openIncidentSK`,
   `openIncidentSeverity` ni `lastMaintenanceHours`, así que no nos pisamos. El seed agrega `lastSeen`,
   `maintenanceIntervalHours` y `fuelBatchId` (de la zona).
2. **AnomalyDetected:** con un evento por escalamiento basta. Si llegan dos para la misma máquina, la segunda
   ejecución termina en `DuplicateIgnored` (y si sube a CRITICAL, escala el incidente y avisa).
3. **READING# sin `context`:** el health-evaluator no guarda `context` de la telemetría. Con `FUEL_BATCH_ZONE` el
   simulador manda `fuelBatchId` = `<lote de la zona>-X`, pero el backend solo ve el lote de la zona. Si guardas
   `context` en READING#, `get_profile` ya lo usa.
4. **ai-diagnosis:** uso tu contrato tal cual (los tests corren tus `rules.py` con el request que arma la Step
   Function, así que si cambias el contrato lo vemos al instante). Si la Lambda falla o tarda más de 45 s, la Step
   Function usa un respaldo por reglas con la misma forma de respuesta.
5. **`classify` en `rules.py`:** combustible + temperatura da `ENGINE`, pero el mock del frontend dice `FUEL_SYSTEM`
   para `FUEL_BATCH_ZONE`. Con +5 °C la temperatura puede o no pasar z=2, así que el componente (y el repuesto
   reservado) puede salir distinto según la lectura. ¿Lo dejamos en `FUEL_SYSTEM` cuando hay incidente de zona?
6. **KPI# (bigquery-sync-back):** el preventivo usa `operatingHours` y `overuseHours`. **FORECAST#**: `forecastQty`.
7. **Frontend:** la API responde con las formas de `frontend/src/api/types.ts` (hay un test que compara campo por
   campo con ese archivo). Ejemplos en `contracts/examples/api/`.

## Cambios respecto de la guía
- La condición `qty - reserved >= :n` no es válida en DynamoDB (no hay aritmética en condiciones). El stock guarda
  `available` y la reserva usa `available >= :n`; además `reservations.{incidentId}` la hace idempotente.
- Los técnicos ocupados siguen siendo candidatos con penalización por cola (+15 por incidente), como dice la fórmula.

## Demo (orden sugerido)
```bash
python scripts/seed_demo.py --demo                      # flota, stock, técnicos, timeouts cortos
# inyectar falla (token de un usuario SUPERVISOR)
curl -X POST "$API/simulator/faults" -H "Authorization: Bearer $TOKEN" \
     -d '{"machineId":"EXC-0042","faultType":"BEARING_WEAR"}'
# P11: todas las máquinas de la zona de TRK-0103 (Z3) con combustible contaminado
curl -X POST "$API/simulator/faults" -H "Authorization: Bearer $TOKEN" -d '{"machineId":"TRK-0103","faultType":"FUEL_BATCH_ZONE"}'
# técnico confirma llegada y cierre (o desde la web)
curl -X POST "$API/machines/EXC-0042/incidents/$INC/ack" -H "Authorization: Bearer $TECH_TOKEN" -d '{"action":"ARRIVED"}'
curl -X POST "$API/machines/EXC-0042/incidents/$INC/ack" -H "Authorization: Bearer $TECH_TOKEN" -d '{"action":"RESOLVED"}'
```
