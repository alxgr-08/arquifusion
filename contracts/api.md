# API REST (backend -> frontend) · v1.1

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
| GET | `/machines/{id}/maintenance` | `Maintenance[]`, newest first | P2, P10 | all |
| GET | `/operations/{op}/incidents?state=OPEN\|CLOSED` | `Incident[]`, CRITICAL first | P5, P9, P11 | all |
| GET | `/operations/{op}/technicians?availability=` | `Technician[]` | P5 | all |
| GET | `/parts` | `PartStock[]` (stock per warehouse + `forecastNext4Weeks`) | P3 | staff |
| GET | `/parts/{partId}/stock` | `PartStock` | P3 | staff |
| GET | `/operations/{op}/kpis?date=YYYY-MM-DD` | `MachineKpi[]` (default yesterday UTC) | P6, P7 | MAINT_LEAD, SUPERVISOR, MANAGER |
| POST | `/machines/{id}/incidents/{incId}/ack` | `Incident` (updated); resumes the Step Function | P5, P10 | TECHNICIAN, MAINT_LEAD |
| POST | `/simulator/faults` | `{faultId, affectedMachines, ...}` (Lambda `af-dev-fault-injector`) | Demo | MAINT_LEAD, SUPERVISOR, MANAGER |

`all` = OPERATOR, TECHNICIAN, MAINT_LEAD, SUPERVISOR, MANAGER. `staff` = all except OPERATOR.
No token -> 401. No grant -> 403 `NO_GRANT`; other operation -> 403 `OTHER_OPERATION`; wrong role -> 403 `FORBIDDEN`.

## Bodies

`POST /machines/{id}/incidents/{incId}/ack`
```json
{ "action": "ARRIVED" }
{ "action": "RESOLVED", "partsUsed": [{ "partId": "BRG-SPH-22", "qty": 1 }], "notes": "Rodamiento cambiado" }
```
`partsUsed` and `notes` are optional (default: everything reserved was used). 409 `WRONG_STAGE` if the incident is not
waiting for that action, 409 `TASK_EXPIRED` if the wait timed out and the incident was reassigned.
Who: MAINT_LEAD of the incident's operation, or the TECHNICIAN whose grant `techId` is the assigned technician
(403 `NOT_ASSIGNED` / `NO_TECH_ID`). Grant, version and assignment are re-checked in the same transaction that stores
the confirmation (403 `GRANT_CHANGED`). Safe to repeat: a repeated click returns 200 without a second callback, and
503 `CALLBACK_PENDING` means "repeat the same action" (the confirmation is kept and delivered once).

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
