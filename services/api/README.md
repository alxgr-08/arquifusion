# af-dev-api (deployed for now as `af-dev-api-ack`)

- **Owner:** Rol 2
- **Trigger:** API Gateway HTTP API, payload v2, JWT authorizer for Google (issuer `https://accounts.google.com`,
  audience = OAuth Client ID of #41). The Lambda takes `iss` + `sub` only from `requestContext.authorizer.jwt.claims`.
- **Input -> output:** REST endpoints of `contracts/api.md` (v1.2). Works with explicit routes or `ANY /{proxy+}`.
- **Problems:** P4, P5, P7, P10

## Package (Rol 1)

| | |
|---|---|
| Handler | `handler.handler` |
| ZIP root | `services/api/handler.py`, `services/api/routes.py` and the folder `shared/af_shared/` (as `af_shared/`) |
| Runtime | Python 3.12 · `boto3` from the runtime · `pydantic>=2.7` (layer or vendored: `af_shared` imports it) · Powertools optional |
| Env | `AF_TABLE_NAME` (default `af-dev-arquifusion`), `AF_OPERATION_ID` (for `GET /parts`, default `MINA-NORTE`), `LOG_LEVEL` optional. Never set `AF_AUTH_DISABLED` in AWS (local tests only) |
| Timeout / memory | 15 s / 256 MB · reserved concurrency 2 in the lab is enough (account limit 10) |

Permissions to verify on `LabRole` for table `af-dev-arquifusion`:

| Action | Resource | Why |
|---|---|---|
| `dynamodb:GetItem`, `dynamodb:Query` | table and `index/GSI1` | reads, grants (`GRANT#…`, consistent read), incident and receipt |
| `dynamodb:PutItem`, `dynamodb:ConditionCheckItem` | table | `TransactWriteItems`: receipt `ACK#…` + checks of grant and wait |
| `dynamodb:UpdateItem` | table | receipt status (`DELIVERED` / `ACCEPTED` / `REJECTED`) |
| `states:SendTaskSuccess` | state machine `af-dev-incident` (its executions) | resume the wait |

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
