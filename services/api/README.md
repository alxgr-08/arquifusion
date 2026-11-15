# API de ArquiFusion · nombre de despliegue previsto af-dev-api-ack

- **Owner:** Rol 2
- **Trigger:** API Gateway HTTP API, payload v2, JWT authorizer for Google (issuer `https://accounts.google.com`,
  audience = OAuth Client ID of #41). The Lambda takes `iss` + `sub` only from `requestContext.authorizer.jwt.claims`.
- **Input -> output:** REST endpoints of `contracts/api.md` (v1.2). The lab gateway publishes only explicitly configured JWT routes.
- **Problems:** P4, P5, P7, P10

## Package (Rol 1)

| | |
|---|---|
| Handler | `handler.handler` |
| ZIP root | `services/api/handler.py`, `services/api/routes.py` and the folder `shared/af_shared/` (as `af_shared/`) |
| Runtime | Python 3.12 · the reproducible ZIP vendors pinned boto3, Pydantic and Powertools, including Linux x86_64 dependencies |
| Env | `AF_TABLE_NAME` (default `af-dev-arquifusion`), `AF_OPERATION_ID` (for `GET /parts`, default `MINA-NORTE`), `LOG_LEVEL` optional. Never set `AF_AUTH_DISABLED` in AWS (local tests only) |
| Timeout / memory | 15 s / 256 MB · shares the Academy limit of 10 environments with other functions; the stage has throttling |

Permissions to verify on `LabRole` for table `af-dev-arquifusion`:

| Action | Resource | Why |
|---|---|---|
| `dynamodb:GetItem`, `dynamodb:Query` | table and `index/GSI1` | reads, grants (`GRANT#…`, consistent read), incident and receipt |
| `dynamodb:PutItem`, `dynamodb:ConditionCheckItem` | table | `TransactWriteItems`: receipt `ACK#…` + checks of grant and wait |
| `dynamodb:UpdateItem` | table | receipt status (`DELIVERED` / `ACCEPTED` / `REJECTED`) |
| `states:SendTaskSuccess` | IAM resource `*` for this action. The service takes its task token from the authorized incident. | Resume that wait. [AWS action and resource matrix](https://docs.aws.amazon.com/service-authorization/latest/reference/list_stepfunctions.html) |

Not needed: `DeleteItem`, `Scan`, `states:GetExecutionHistory` (evidence is the workflow's `certified` record).
`af-dev-incident-tasks` additionally runs `certify_ack` (state `CertifyArrival` and inside `CloseIncident`):
`GetItem`/`UpdateItem` on the incident and on `ACK#…`.

## How /ack works

1. The workflow registers each wait (`store_task_token`) with execution, stage, assigned technician and an opaque
   `waitGeneration` derived from `$$.Execution.Id` + stage + `$$.State.EnteredTime` (a retry of the same wait keeps it;
   a new or re-armed wait, or a reassignment, gets another; an older late registration is ignored).
2. `GET …/incidents/{incId}` returns it as `ETag`; `POST …/ack` needs it in `If-Match`.
3. The POST stores a receipt `ACK#{incId}#{stage}#{generation}` (ackId, actor, time, body fingerprint, payload) in one
   transaction with the grant check and the wait check, then calls `SendTaskSuccess` with that payload.
4. The workflow (`CertifyArrival`, `CloseIncident`) is the only writer of ARRIVED/RESOLVED and records the accepted
   ackId in `certified.{stage}`. If `SendTaskSuccess` fails or its answer is lost, that record decides: accepted ->
   200; another ack certified, flow failed or a newer wait without ours -> 409 `TASK_EXPIRED`; otherwise 503
   `CALLBACK_PENDING` (repeat). `InvalidToken` alone never counts as success.

## How to test

```bash
python scripts/run_tests.py   # or: pytest services/api
```

La entrega local verifica la operación del perfil y del incidente antes de devolver el detalle o su ETag. La transacción del primer recibo comprueba también que el perfil siga existiendo en esa operación. Un traslado o borrado concurrente devuelve 403 y deja intactos el recibo y el callback. Los históricos filtran por procedencia. Un mantenimiento antiguo puede acreditar su operación mediante el incidente asociado. Los registros sin pertenencia verificable se omiten. Para stock y pronóstico los almacenes provienen de `OP#{operationId}/META`. La Lambda exige autenticación incluso si se configura el bypass local por error. [Arquitectura](../../docs/arquitectura.md)

Los listados de flota, técnicos, incidentes y KPI usan GSI1 para descubrir candidatos y vuelven a leer cada fila primaria de forma consistente. Se omiten filas eliminadas, trasladadas o cuyo índice ya no pertenece a la operación solicitada. Los KPI anteriores sin `operationId` conservan su pertenencia mediante el índice de la fila primaria. Este control añade un GetItem por candidato, adecuado para la flota de ensayo. Antes de ampliar a colecciones grandes, revisar paginación y duración de Lambda.

GET de lecturas acepta filas históricas con segundos y nuevas con microsegundos. Consulta segundos completos y después filtra y ordena por el instante UTC. Los límites `from` y `to` son inclusivos y conservan su precisión. El campo `timestamp` de respuesta mantiene el valor de la fila.

La revisión nocturna pasó 45 pruebas locales de API, incluida una transacción real del SDK sobre DynamoDB simulado y el ensayo conjunto de incidentes y analítica. La validación del JWT en el stage publicado requiere la prueba real con GIS.
