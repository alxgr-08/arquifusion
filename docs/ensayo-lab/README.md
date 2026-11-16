# Ensayo en AWS Academy · flujo de incidentes (Rol 2)

Para la grabación de Canvas se usa la corrida **ya preparada** de `ENS-0001`. Los ejemplos de siembra e invocación directa de esta guía pertenecen a corridas independientes. El técnico hace los acuses por HTTPS en la web. La TA excluyó la ingesta con Lambda y advirtió que alcanzar diez ejecuciones simultáneas pone en riesgo la cuenta compartida. La prueba web parte de un solo evento manual de incidente. [Alcance vigente y responsables](../arquitectura.md#cierre-para-canvas)

Un solo caso en vuelo, regla `af-dev-incident` **deshabilitada**, sin tocar parámetros SSM compartidos.

En la grabación, la zona `ZE`, la máquina `ENS-0001` y el técnico `T-E0001` corresponden al ensayo simulado de Cerro de Pasco, Perú. El tajo Raúl Rojas y las calles de Chaupimarca sirven de referencia geográfica.
Cada corrida tiene sus propios identificadores, generados por `scripts/ensayo_kit.py`:

```bash
python scripts/ensayo_kit.py --run 7 --out ensayo-0007
```

| Dato de la corrida 7 | Valor |
|---|---|
| Operación / zona | `ENSAYO-0007` / `ZE` |
| Máquina / técnico | `ENS-0007` / `T-E0007` (especialidades `DRIVETRAIN`, `BEARING`) |
| Almacén (caso 2) | `WH-E0007` |
| Permiso del operador | `GRANT#accounts.google.com#ensayo-operador-0007` / `OP#ENSAYO-0007`, rol `MAINT_LEAD` |
| Ajustes de la corrida | `OP#ENSAYO-0007` / `INCIDENT_SETTINGS`: esperas 600 s / 1800 s, 2 intentos de asignación, `partsFromOtherWarehouses: false`, `zoneWarehouses: {"ZE": ["WH-E0007"]}` |

Los ajustes y el almacén de la zona viven en una fila propia de la corrida (el claim la combina con
`/af/incident/settings`), así que no se sobrescribe `/af/incident/settings` ni el catálogo
(`AF_INCIDENT_CATALOG=/af/incident/fleet-catalog` o `/af/sim/fleet-config`). `run.json` lista las filas de la corrida.
`time`/`detectedAt` se generan al preparar la corrida (UTC, ahora) y se conservan si se reenvía el mismo archivo.

`services/api/tests/test_ensayo_lab.py` corre exactamente estos archivos contra la Step Function, las Lambdas
reales y la API invocada directamente: caso 1 llega a `RESOLVED` sin piezas, el reenvío del mismo evento no abre
un segundo incidente; caso 2 consume 1 unidad solo de `WH-E0007`; stocks compartidos y parámetros SSM intactos.

## Caso 1 (vibración, cero piezas)

```bash
T=af-dev-arquifusion; cd ensayo-0007
# 1. Filas de la corrida, solo si no existen (nunca sobrescriben; sin --apply solo muestra el plan)
cd .. && python scripts/seed_ensayo.py ensayo-0007 && python scripts/seed_ensayo.py ensayo-0007 --apply && cd ensayo-0007
# 2. Ejecución (directa o por af-dev-incident-entry); anotar ambos executionArn por separado
aws stepfunctions start-execution --state-machine-arn <arn> --name ensayo-0007-caso1 \
  --input file://anomaly-caso1-vibracion.json
# 3. ID del incidente (sin token)
aws dynamodb get-item --table-name $T --key '{"PK":{"S":"MACHINE#ENS-0007"},"SK":{"S":"PROFILE"}}' \
  --projection-expression openIncidentId
# 4. Versión de la espera: en api-get-incident.json reemplazar INC-XXXX; la respuesta trae el header ETag
aws lambda invoke --function-name af-dev-api-ack --cli-binary-format raw-in-base64-out \
  --payload file://api-get-incident.json out-get.json
# 5. En api-ack-arrived.json reemplazar INC-XXXX y ETAG-XXXX (sin comillas extra) y confirmar
aws lambda invoke --function-name af-dev-api-ack --cli-binary-format raw-in-base64-out \
  --payload file://api-ack-arrived.json out-arrived.json
# 6. Repetir 4 (nuevo ETag: ahora espera RESOLUTION) y 5 con api-ack-resolved.json
```

Respuestas: 200 = confirmado. 503 `CALLBACK_PENDING` = repetir **el mismo** archivo. 412 `STALE_WAIT` = la espera
cambió: volver al paso 4. 409 `WRONG_STAGE` = la ejecución aún no llegó a esa espera: esperar y volver al paso 4.

La invocación directa prueba la lógica, no el login: en AWS solo API Gateway llena `authorizer.jwt.claims`. El
ensayo de Google Sign-In usa la web en `VITE_API_MODE=live`, un ID token real y API Gateway, con un permiso cargado
con `scripts/seed_grants.py` para el `sub` real.

Evidencia a registrar por separado: estado de la ejecución de entrada y de `af-dev-incident`; fila del incidente
(`timeline` DETECTED, ASSIGNED, ARRIVED, RESOLVED; `certified`); recibos `ACK#…`; una publicación SNS (`MessageId`)
y, aparte, la notificación recibida por una persona.

## Web autenticada (HTTPS + Google)

`seed_ensayo.py` también crea la metadata que la web necesita: `OP#ENSAYO-0007 / META` (zonas y almacenes),
`ZONE#ZE` y `WH#WH-E0007`. La web se construye con `VITE_OPERATION_ID=ENSAYO-0007`. Un 200 con lista vacía significa
que esa operación no tiene datos (no que falló la infraestructura).

Permisos de los participantes: completar el `sub` de Google de cada persona en `grants-participantes.json`
(jefe de mantenimiento, técnico asignado `T-E0007`, supervisor) y cargarlos con
`python scripts/seed_grants.py ensayo-0007/grants-participantes.json`. El archivo no va al repo ni a issues; el
script rechaza las plantillas sin completar. Cada fila debe ser la cuenta real de esa persona.

`scripts/seed_demo.py` ahora también es condicional por defecto (crea solo lo que falta, informa `CONFLICT` y no
toca SSM salvo con `--write-ssm`); para una operación aislada usar este kit.

## Reenvío del mismo evento

Con la regla apagada, volver a iniciar una ejecución con el **mismo** `anomaly-caso1-vibracion.json` (otro `--name`).
Esperado: termina en `DuplicateIgnored` y no hay un segundo `INCIDENT#` para `ENS-0007`.

## Caso 2 (descuento real)

```bash
python scripts/seed_ensayo.py ensayo-0007 --apply --with-stock
aws stepfunctions start-execution --state-machine-arn <arn> --name ensayo-0007-caso2 \
  --input file://anomaly-caso2-rodamiento.json
```

Mismos pasos 3 a 6. Esperado: `partsReserved` = `BRG-SPH-22` de `WH-E0007`; ese stock queda `qty 0`, `available 0`,
`reserved 0`; una sola fila `MAINT#`; los almacenes compartidos sin cambios.

## Interrupciones

- Ejecución colgada en una espera: `aws stepfunctions stop-execution`. La máquina queda con `openIncidentId`; para la
  siguiente prueba usar **otra corrida** (`--run 8`) en vez de editar o borrar filas existentes.
- `CloseIncident` interrumpido: si Step Functions lo reintenta (aunque sea otro día) termina lo que falte (libera
  máquina y técnico, una fila `MAINT#`, un consumo de stock).
- Al terminar no se borra historial: las filas quedan identificadas por `run` / prefijos de la corrida (`run.json`).
