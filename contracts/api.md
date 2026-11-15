# API REST (backend -> frontend) · v1.2

Base: API Gateway (HTTP API). Auth (v1.1, #42): Google ID token (`Authorization: Bearer <token>`), verified by the
API Gateway JWT authorizer (issuer `https://accounts.google.com`, audience = OAuth Client ID). The role is not in the
token: it comes from the user's grant in DynamoDB (`GRANT#{iss}#{sub}` / `OP#{operationId}`, see `af_shared/grants.py`),
one row per operation. The `Groups` column below is that role.
JSON responses, camelCase fields. Errors: `{ "error": { "code": "...", "message": "..." } }`.
**Response shapes: `frontend/src/api/types.ts`** (the API tests check every field against that file).

| Method | Route | Returns | Problem | Groups |
|---|---|---|---|---|
| GET | `/operations/{op}/machines?status=` | `Machine[]` | P4 | all |
| GET | `/machines/{id}` | `Machine` | P1, P4 | all |
| GET | `/machines/{id}/readings?from=&to=` | `Reading[]` (default last 24 h) | P1, P8 | all |
| GET | `/machines/{id}/incidents` | `Incident[]`, newest first | P10 | all |
| GET | `/machines/{id}/incidents/{incId}` | `Incident` + header `ETag` = version of the wait it is in (v1.2) | P5, P10 | all |
| GET | `/machines/{id}/maintenance` | `Maintenance[]`, newest first | P2, P10 | all |
| GET | `/operations/{op}/incidents?state=OPEN\|CLOSED` | `Incident[]`, CRITICAL first | P5, P9, P11 | all |
| GET | `/operations/{op}/technicians?availability=` | `Technician[]` | P5 | all |
| GET | `/parts` | `PartStock[]` (stock per warehouse + `forecastNext4Weeks`) | P3 | staff |
| GET | `/parts/{partId}/stock` | `PartStock` | P3 | staff |
| GET | `/operations/{op}/kpis?date=YYYY-MM-DD` | `MachineKpi[]` (default yesterday UTC) | P6, P7 | MAINT_LEAD, SUPERVISOR, MANAGER |
| POST | `/machines/{id}/incidents/{incId}/ack` | `Incident` (updated); resumes the Step Function. Requires `If-Match` (v1.2) | P5, P10 | TECHNICIAN, MAINT_LEAD |
| POST | `/simulator/faults` | `{faultId, affectedMachines, ...}` (Lambda `af-dev-fault-injector`) | Demo | MAINT_LEAD, SUPERVISOR, MANAGER |

`all` = OPERATOR, TECHNICIAN, MAINT_LEAD, SUPERVISOR, MANAGER. `staff` = all except OPERATOR.
No token -> 401. No grant -> 403 `NO_GRANT`; other operation -> 403 `OTHER_OPERATION`; wrong role -> 403 `FORBIDDEN`.

## Bodies

`POST /machines/{id}/incidents/{incId}/ack`
```json
{ "action": "ARRIVED" }
{ "action": "RESOLVED", "partsUsed": [{ "partId": "BRG-SPH-22", "qty": 1 }], "notes": "Rodamiento cambiado" }
```
`partsUsed` and `notes` are optional (default: everything reserved was used). `{ "event": "ARRIVED" }` is still accepted
as an alias of `action`.

**Which wait (v1.2).** Every wait of the workflow (arrival, resolution, a re-armed resolution, a reassignment, even to
the same technician) has an opaque version. `GET /machines/{id}/incidents/{incId}` returns it as `ETag: "<version>"`
(no ETag = not waiting for a confirmation). The web sends it back in `If-Match: "<version>"` with the POST, so the
click confirms exactly the wait the user saw. `Access-Control-Expose-Headers: ETag, Retry-After`.

**Who.** MAINT_LEAD of the incident's operation, or the TECHNICIAN whose grant `techId` is the assigned technician.
The operation comes from the stored incident, never from the browser. Grant (role, techId, version), wait and
assignment are re-checked in the same transaction that stores the confirmation receipt. The receipt keeps the real
actor (also when a lead confirms for the technician).

| Status | Code | Meaning | What the web does |
|---|---|---|---|
| 200 | — | Confirmed: this ack was delivered/accepted (also for a repeated identical request) | Show the returned `Incident` |
| 401 | `UNAUTHORIZED` | No/invalid identity (API Gateway answers 401 itself for a bad or expired token) | Sign in again |
| 403 | `NO_GRANT`, `OTHER_OPERATION`, `FORBIDDEN`, `NO_TECH_ID`, `NOT_ASSIGNED`, `GRANT_CHANGED` | Not allowed (now) | Show the message; reload |
| 428 | `PRECONDITION_REQUIRED` | Missing `If-Match` | Read the incident first (GET) |
| 412 | `STALE_WAIT` | The wait changed since the user saw it (response carries the current `ETag`) | Reload and ask the user again |
| 409 | `WRONG_STAGE`, `TASK_EXPIRED`, `ACK_CONFLICT`, `ACK_BY_OTHER`, `WAIT_NEEDS_RECONCILIATION` | Not waiting for that action / the wait ended without this ack / same wait already confirmed with other parts-notes or by another user / a wait stored before v1.2 | Reload; do not retry |
| 503 | `CALLBACK_PENDING` (+ `Retry-After`) | Stored, effect not confirmed yet | Repeat **the same** request (same `If-Match` and body) |

A 200 always corresponds to a confirmed effect of that ack. ARRIVED/RESOLVED are written to `timeline` by the workflow
(once per stage); the 200 already shows the confirmed state.

`POST /simulator/faults`
```json
{ "machineId": "EXC-0042", "faultType": "BEARING_WEAR" }
{ "machineId": "EXC-0042", "faultType": "FUEL_BATCH_ZONE" }
{ "action": "CLEAR" }
```
faultType: BEARING_WEAR, OVERHEATING, HYDRAULIC_LEAK, FUEL_BATCH_ZONE (every machine of that machine's zone), plus
SENSOR_FAILURE and STOPPED. Optional `durationMinutes` (default 60) and `intensity` (multiplier, default 1.0).
Faults grow over `AF_SIM_RAMP_SECONDS` (default 5 min) with the same magnitudes as the frontend mock.

## Response examples
Generated from a real run of the incident flow: `contracts/examples/api/*.json`.

## Incident states
`OPEN -> ASSIGNED -> IN_PROGRESS -> RESOLVED`. `timeline[]` only has DETECTED, ASSIGNED, ARRIVED, RESOLVED, ESCALATED;
backend-only events (DIAGNOSED, PARTS_RESERVED, NOTIFIED, ACK_TIMEOUT, CLOSED...) are kept in `activity` and not returned.

## Alcance en AWS Academy

Se mantiene la interfaz v1.2. La variante de ensayo publica únicamente las rutas seleccionadas en Terraform. Las rutas de piezas respetan el `operationId` opcional de la consulta y el conjunto de almacenes autorizados; sin parámetro usan la operación configurada. Las rutas históricas solo devuelven filas cuya pertenencia a la operación esté acreditada. Para el acuse se admite un único ETag fuerte entre comillas. Se conserva la versión de la espera durante los reintentos. [Preparación local de la API](../infra/aws/api/README.md)
