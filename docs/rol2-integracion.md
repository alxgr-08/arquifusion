# Rol 2 · Qué necesita cada uno para integrar el backend

Todo lo de esta carpeta pasa `ruff check .` y `pytest -q` (53 tests, incluidas las dos Step Functions corridas de
punta a punta con un intérprete de ASL y una DynamoDB en memoria).

## Para Rol 1 (Terraform, Learner Lab)

### Lambdas (handler `handler.handler`, Python 3.12, rol `LabRole`)

| Función | Trigger | Timeout | Env |
|---|---|---|---|
| `af-dev-simulator` | Scheduler `rate(1 minute)` | 60 s | `AF_INGEST_MODE` (`iot`/`direct`), `AF_IOT_ENDPOINT` opcional, plan B: `AF_HEALTH_EVALUATOR_FUNCTION`, `AF_FIREHOSE_STREAM` |
| `af-dev-fault-injector` | API `POST /simulator/faults` | 10 s | `AF_SIMULATOR_FUNCTION`, `AF_FAULT_GROUPS` opcional |
| `af-dev-offline-detector` | Scheduler `rate(5 minutes)` | 30 s | `AF_TABLE_NAME`, `AF_OPERATIONS`, `AF_OFFLINE_AFTER_MINUTES` |
| `af-dev-incident-tasks` | Step Functions | 30 s | `AF_TABLE_NAME` |
| `af-dev-assign-technician` | Step Functions | 30 s | `AF_TABLE_NAME`, `AF_ROUTE_CALCULATOR` opcional |
| `af-dev-notify` | Step Functions | 30 s | `AF_TABLE_NAME` |
| `af-dev-api` | API Gateway (todas las rutas menos `/simulator/faults`) | 15 s | `AF_TABLE_NAME` |

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
- Parámetros SSM: los escribe `python scripts/seed_demo.py --demo` (usa tier `Intelligent-Tiering` porque
  `/af/sim/fleet-config` pasa de 4 KB).

## Para Rol 3

1. **PROFILE con UpdateItem, no PutItem.** La Step Function guarda en `MACHINE#id / PROFILE` los atributos
   `openIncidentId`, `openIncidentSK`, `openIncidentSeverity` y `lastMaintenanceHours`. Un PutItem del
   health-evaluator los borraría y se abrirían incidentes duplicados.
2. **Emitir AnomalyDetected en cada lectura WARNING/CRITICAL está bien:** la Step Function deduplica (una sola
   ejecución abre el incidente, el resto termina en `DuplicateIgnored`; si sube a CRITICAL se escala y se avisa).
3. **READING# con `context`** (al menos `fuelBatchId`): lo uso para la hipótesis de causa común por zona (P11).
4. **ai-diagnosis:** propuesta de contrato en `contracts/ai-diagnosis.request.schema.json` y
   `.response.schema.json`, con ejemplos. Si la Lambda falla, tarda más de 45 s o responde algo inválido, el flujo
   sigue con diagnóstico por reglas (`source: RULES`), así que la demo no depende de Bedrock.
5. **KPI# (bigquery-sync-back):** el preventivo lee `operatingHours`, `overuseHours`, `watchHours` y `loadRatio`
   (carga promedio / nominal) de los últimos 7 días. **FORECAST#** con atributo `demand` (unidades de la semana).
6. **Frontend:** respuestas de ejemplo en `contracts/examples/api/`. La cola de incidentes trae `kind: ZONE` para
   los incidentes de zona (tienen `machineIds` e `hypothesis`).
7. **Ojo con el puntaje:** si `promedio_ponderado` promedia las 9 métricas, dos métricas con riesgo 3,5 dan salud
   ~80 (OK). Con la falla WARNING del simulador (3,5 sigma) conviene ponderar más las métricas en riesgo.

## Cambios respecto de la guía
- La condición `qty - reserved >= :n` no es válida en DynamoDB (no hay aritmética en condiciones). El stock guarda
  `available` y la reserva usa `available >= :n`; además `reservations.{incidentId}` la hace idempotente.
- Los técnicos ocupados siguen siendo candidatos con penalización por cola (+15 por incidente), como dice la fórmula.

## Demo (orden sugerido)
```bash
python scripts/seed_demo.py --demo                      # flota, stock, técnicos, timeouts cortos
# inyectar falla (token de un usuario SUPERVISOR)
curl -X POST "$API/simulator/faults" -H "Authorization: Bearer $TOKEN" \
     -d '{"machineId":"EXC-0042","faultType":"BEARING_WEAR","intensity":"WARNING"}'
# P11: tres máquinas de Z3 con combustible contaminado
curl -X POST "$API/simulator/faults" -H "Authorization: Bearer $TOKEN" -d '{"zoneId":"Z3","faultType":"FUEL_CONTAMINATION"}'
# técnico confirma llegada y cierre (o desde la web)
curl -X POST "$API/machines/EXC-0042/incidents/$INC/ack" -H "Authorization: Bearer $TECH_TOKEN" -d '{"event":"ARRIVED"}'
curl -X POST "$API/machines/EXC-0042/incidents/$INC/ack" -H "Authorization: Bearer $TECH_TOKEN" -d '{"event":"RESOLVED"}'
```
