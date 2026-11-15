# Ensayo en AWS Academy · flujo de incidentes (Rol 2)

Un solo caso en vuelo, con una operación, una zona, una máquina y un técnico exclusivos de prueba
(`MINA-ENSAYO`, `ZE`, `EXC-E001`, `T-E1`). Nada de esto aparece en el simulador ni en el offline-detector
(que solo recorre `MINA-NORTE`). `services/api/tests/test_ensayo_lab.py` ejecuta estos mismos archivos contra
la Step Function, las Lambdas reales y la API invocada directamente.

| Archivo | Para qué |
|---|---|
| `item-machine.json`, `item-technician.json` | Perfiles exclusivos (formato DynamoDB JSON) |
| `item-stock-caso2.json` | Solo caso 2: 1 rodamiento en un almacén exclusivo `WH-ENSAYO` |
| `incident-settings-lab.json` | `/af/incident/settings` con esperas cortas y `partsFromOtherWarehouses: false` |
| `anomaly-caso1-vibracion.json` | Caso 1: `WARNING` solo por vibración → `DRIVETRAIN`, sin repuesto: reserva con cero piezas |
| `anomaly-caso2-rodamiento.json` | Caso 2: vibración + temperatura → `BEARING` → `BRG-SPH-22` de `WH-ENSAYO` |
| `item-grant-operador.json` | Permiso `MAINT_LEAD` en `MINA-ENSAYO` para la identidad del operador del ensayo |
| `api-ack-arrived.json`, `api-ack-resolved.json` | Evento para invocar `af-dev-api` directo (sin API Gateway), con esa identidad |

El token de tarea nunca sale de DynamoDB: la API lo lee y llama a `SendTaskSuccess`. No usar
`aws stepfunctions send-task-success` a mano (habría que copiar el token y se saltaría el timeline).

## Pasos (caso 1)

```bash
T=af-dev-arquifusion
# 0. Respaldo de la configuración que se va a tocar
aws ssm get-parameter --name /af/incident/settings --query Parameter.Value --output text > settings-backup.json
# 1. Perfiles y configuración del ensayo
aws dynamodb put-item --table-name $T --item file://item-machine.json
aws dynamodb put-item --table-name $T --item file://item-technician.json
aws dynamodb put-item --table-name $T --item file://item-grant-operador.json
aws ssm put-parameter --name /af/incident/settings --type String --overwrite --value file://incident-settings-lab.json
# 2. Ejecución directa (sin la regla de EventBridge)
aws stepfunctions start-execution --state-machine-arn <arn af-dev-incident> --name ensayo-caso1-01 \
  --input file://anomaly-caso1-vibracion.json
# 3. ID del incidente (sin token)
aws dynamodb get-item --table-name $T --key '{"PK":{"S":"MACHINE#EXC-E001"},"SK":{"S":"PROFILE"}}' \
  --projection-expression openIncidentId
# 4. Reemplazar INC-XXXX por ese ID en los dos api-ack-*.json y confirmar llegada y resolución
aws lambda invoke --function-name af-dev-api --cli-binary-format raw-in-base64-out \
  --payload file://api-ack-arrived.json out-arrived.json
aws lambda invoke --function-name af-dev-api --cli-binary-format raw-in-base64-out \
  --payload file://api-ack-resolved.json out-resolved.json
# 5. Verificar: ejecución SUCCEEDED, incidente RESOLVED, máquina sin openIncidentId, técnico AVAILABLE con queue 0
aws stepfunctions describe-execution --execution-arn <arn de la ejecución> --query status
```

`out-*.json` debe tener `statusCode` 200 y `state` `IN_PROGRESS` / `RESOLVED`. Si da 409 `WRONG_STAGE`, la
ejecución todavía no llegó a esa espera: esperar unos segundos y repetir.

## Caso 2 (descuento real de stock)

Agregar a `/af/sim/fleet-config` (respaldar antes) el almacén `"WH-ENSAYO": {"zones": ["ZE"]}` dentro de
`warehouses`, cargar `item-stock-caso2.json` y repetir con `anomaly-caso2-rodamiento.json` y otro `--name`.
Resultado esperado: `partsReserved` = `BRG-SPH-22` de `WH-ENSAYO`, stock de ese ítem en 0, una fila `MAINT#`.
Con `partsFromOtherWarehouses: false` el flujo nunca toma piezas de `WH-NORTE` ni `WH-SUR`.

## Limpieza

Restaurar `/af/incident/settings` (y `/af/sim/fleet-config` si se usó el caso 2) con los respaldos y borrar las
filas `MACHINE#EXC-E001` (PROFILE, INCIDENT#…, MAINT#…), `TECH#T-E1`, `GRANT#accounts.google.com#ensayo-operador` y `PART#BRG-SPH-22` / `STOCK#WH-ENSAYO`.
Si una ejecución queda colgada en una espera, detenerla con `aws stepfunctions stop-execution` y borrar
`openIncidentId`/`openIncidentExecution` del perfil de la máquina (o volver a cargar `item-machine.json` e
`item-technician.json`, que también deja al técnico libre).
