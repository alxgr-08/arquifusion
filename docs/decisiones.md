# Decisions log

Arquitectura vigente y evidencias por entorno: [ArquiFusion](arquitectura.md).

| Date | Decision | Reason |
|---|---|---|
| 2026-11-14 | Monorepo with contracts-first model | Each role works against `contracts/` without waiting for others |
| 2026-11-14 | Roles: Leonardo Rol 1, Sebastián Rol 2, Alexis Rol 3 | Team agreement |
| 2026-11-14 | AWS Academy Learner Lab: all compute uses `LabRole` | Learner Lab does not allow creating IAM roles |
| 2026-11-14 | Stock keeps `available` + `reservations.{incidentId}`; reserve with `available >= :n` | DynamoDB conditions cannot do `qty - reserved`; the map makes Step Functions retries idempotent |
| 2026-11-14 | One open incident per machine (conditional claim on PROFILE) | health-evaluator emits AnomalyDetected on every bad reading |
| 2026-11-14 | AI diagnosis falls back to rules on error/timeout/invalid answer | The demo must not depend on Bedrock availability in the lab |
| 2026-11-14 | Step Function timeouts read from SSM `/af/incident/settings` | 30 min technician timeout does not fit a 5-minute video |
| 2026-11-14 | Simulator fast loop (10 s) for machines with an injected fault | Health score needs 3 consecutive readings; 1/min is too slow for the demo |
| 2026-11-14 | Rol 2 adopts Rol 3 contracts: fleet-config.json, ai-diagnosis-*, frontend/src/api/types.ts | One source of truth for the web, the history and the backend |
| 2026-11-14 | Incident `timeline` only holds the 5 events the web shows; other events go to `activity` | The web labels only DETECTED, ASSIGNED, ARRIVED, RESOLVED, ESCALATED |
| 2026-11-15 | health-evaluator keeps `pendingAlert` in PROFILE until EventBridge accepts AnomalyDetected | A Lambda retry after a committed reading was skipped as duplicate and the alert was lost |
| 2026-11-15 | Claim stores `openIncidentExecution` (`$$.Execution.Id`); the incident ID derives from it | A retried claim whose response was lost must continue its own incident, not end as a duplicate and leave the machine blocked |
| 2026-11-15 | Setting `partsFromOtherWarehouses` (default true) | Lab rehearsal with an isolated warehouse must never take shared stock |
| 2026-11-15 | Claim keeps `lastIncidentDetectedAt`; an older or equal `detectedAt` with no open incident is ignored | The health-evaluator outbox (#44) may re-publish an alert whose incident was already handled |
| 2026-11-15 | Login with Google (API Gateway JWT authorizer), roles in DynamoDB `GRANT#{iss}#{sub}` / `OP#{op}`; no Cognito | Team decision (#42); a role per operation, revocable and re-checked in the same transaction as the ack |
| 2026-11-15 | `/ack` v1.2: wait version (`waitGeneration`, ETag/If-Match), receipt `ACK#{incId}#{stage}#{gen}`, workflow certifies ARRIVED/RESOLVED (`CertifyArrival`, `CloseIncident`) | Review of #47: a click must confirm the wait the user saw; the workflow is the single writer and its record is the evidence for lost answers |
| 2026-11-15 | `close_incident` resumes after an interrupted close (no early return when already closed) | A crash between `#CLOSED` and releasing the claim left the machine busy |
| 2026-11-15 | Rehearsal settings per operation in DynamoDB (`OP#{op}` / `INCIDENT_SETTINGS`), catalog name from `AF_INCIDENT_CATALOG` | Isolated runs without overwriting shared SSM parameters managed by Terraform |

## Preparación local de Rol 1 para Academy

La preparación original se conserva en `infra/rol1-aws-bootstrap` (`5401e3c`).
El worktree `infra/rol1-aws-dynamodb-plan` parte de `origin/main` en `be19d3e`.

- Bootstrap independiente en `infra/aws/bootstrap`, con estado local. Consulta el bucket
  existente de ArquiFusion y gestiona versionado, cifrado y bloqueo de acceso público.
  El backend de la aplicación quedó inicializado en `aws/terraform.tfstate`, en el
  bucket dedicado de ArquiFusion, con workspace `default` y bloqueo nativo de S3.
- Tabla regional en `us-east-1`, claves del repositorio, índices con proyección `ALL`,
  capacidad bajo demanda, PITR, Streams y TTL `expiresAt`.
- S3 confirmó versionado `Enabled`, cifrado `AES256` y los cuatro bloqueos de
  acceso público. DynamoDB confirmó la tabla `ACTIVE`, ambos GSI activos, TTL,
  Streams, PITR y protección contra borrado después del despliegue autorizado.
- Se conserva el rol existente como dependencia de la etapa posterior de servicios.
  El bucket y la tabla se preparan sin crear ni alterar roles IAM.
- API Gateway, BigQuery ML y archivo alternativo son propuestas de la variante Academy.
  Adaptador HTTP, raw completo, recuperación de publicación y control de expiración
  requieren coordinación con sus productores y consumidores antes de implementar.

La auditoría inicial está en `docs/auditoria-inicial.md`. Se aplicó el plan de la tabla
con SHA-256 `64731BF14F5CF88B8259F645FF2489F77CCB70F5CDDF4A6E8F14C13EFD430B6F`.
Antes de preparar el evaluador, el estado remoto contenía únicamente la tabla y el plan
completo devolvió `No changes`. El primer despliegue del evaluador en Academy creó la Lambda,
un bus dedicado, dos parámetros SSM y un grupo de logs con el `LabRole` existente para
la función. Las pruebas normal y anómala verificaron las escrituras en DynamoDB y la
aceptación de la anomalía por EventBridge. La entrega a un consumidor queda pendiente.
En aquella versión una publicación fallida tras la transacción podía perderse al reintentar.
[Despliegue, permisos y pruebas del evaluador](../infra/aws/health_evaluator/README.md)

## Autenticación de incidentes en este Learner Lab

Leonardo confirmó Google Identity Services y el autorizador JWT nativo de API Gateway HTTP como arquitectura definitiva de esta fase. AWS Academy es el entorno operativo, con perfil `default`, región `us-east-1` y máximo educativo de diez entornos Lambda concurrentes compartidos. La identidad verificada se vincula a concesiones de operación y rol en DynamoDB. TECHNICIAN requiere coincidencia exacta con el técnico asignado. El diseño excluye Cognito.

El plan de nueve altas fue una revisión previa a los PR #47 y #49. El diseño actual publica el GET del incidente y su confirmación por POST. El backend obtiene el permiso desde DynamoDB, exige el `ETag` de la espera en `If-Match` y mantiene recibos para los reintentos. El cliente Google registrado permite `http://localhost:5173`; queda pendiente registrar el origen HTTPS de la web. [Revisión de Sebastián](https://github.com/alxgr-08/arquifusion/pull/49) · [Arquitectura vigente](./arquitectura.md)

## Origen de los datos y evidencia multicloud

La telemetría completa sale del simulador `direct` hacia Firehose. El ensayo temporal de Academy entregó un Parquet válido en S3 usando el esquema de Glue. DynamoDB Streams alimenta el archivo de incidentes y consumo de repuestos. El código del puente usa el archivo de Firehose y conserva recibos para evitar duplicados. El proyecto BigQuery es `arquifusion`, ubicación `US`. La evidencia del equipo con datos sintéticos y una prueba íntegra entre las dos nubes se registran por separado. [Ensayo regional](../infra/aws/telemetry/README.md) · [Resultados de Alexis](https://github.com/alxgr-08/arquifusion/pull/48)

## Actualización local del 15 de noviembre

El flujo usa almacenes por operación, procedencia de lecturas y mantenimiento, ETag fuerte y una transacción DynamoDB que delega la serialización al SDK. El evaluador conserva una fila de outbox por alerta y puede reconciliar una máquina silenciosa. La SPA se prepara para Amplify y Firebase queda como alternativa. El retorno de la demo selecciona una sola máquina, operación y fecha por identidad federada. El inventario de 27 recursos observados precede a estas mejoras. [Arquitectura y clasificación](arquitectura.md) Para un ensayo único, el puente admite también las dos sesiones autorizadas de un operador, con verificación del Parquet y el mensaje antes de escribir. [Procedimiento manual](../infra/gcp/README.md)
