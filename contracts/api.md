# API REST (backend -> frontend) · v1.0

Base: API Gateway (HTTP API). Auth: Cognito JWT (`Authorization: Bearer <token>`); the Cognito group defines what each user sees.
JSON responses, camelCase fields. Errors: `{ "error": { "code": "...", "message": "..." } }`.

| Method | Route | Returns / does | Problem | Groups |
|---|---|---|---|---|
| GET | `/operations/{op}/machines?status=&limit=&nextToken=` | Machines with status, health and position | P4 | all |
| GET | `/machines/{id}` | Profile and current state | P1, P4 | all |
| GET | `/machines/{id}/readings?from=&to=&limit=&nextToken=` | Recent readings for charts (default last 24 h) | P1, P8 | all |
| GET | `/machines/{id}/incidents` | Incident history, newest first | P10 | all |
| GET | `/machines/{id}/maintenance` | Maintenance and replaced parts, newest first | P2, P10 | staff |
| GET | `/operations/{op}/incidents?state=OPEN` | Incident queue, CRITICAL first (`kind`: MACHINE or ZONE) | P5, P9, P11 | all |
| GET | `/operations/{op}/technicians?availability=` | Technicians and availability | P5 | staff |
| GET | `/parts/{partId}/stock` | Stock and forecast per warehouse | P3 | staff |
| GET | `/operations/{op}/kpis?date=YYYY-MM-DD` | Utilization, fuel, cost per machine (default yesterday UTC) | P6, P7 | MAINT_LEAD, SUPERVISOR, MANAGER |
| POST | `/machines/{id}/incidents/{incId}/ack` | Technician confirms arrival/close (resumes Step Function) | P5, P10 | TECHNICIAN, MAINT_LEAD |
| POST | `/simulator/faults` | Injects a fault for the demo (Lambda `af-dev-fault-injector`) | Demo | MAINT_LEAD, SUPERVISOR, MANAGER |

`all` = OPERATOR, TECHNICIAN, MAINT_LEAD, SUPERVISOR, MANAGER. `staff` = all except OPERATOR.
No token/groups -> 401; wrong group -> 403.

## Pagination
List endpoints return `{ "items": [...], "count": n, "nextToken": "..." | null }`. Send `nextToken` back to get the next page.

## Bodies

`POST /machines/{id}/incidents/{incId}/ack`
```json
{ "event": "ARRIVED" }
{ "event": "RESOLVED", "partsUsed": [{ "partId": "BRG-KIT-02", "qty": 1 }], "notes": "Rodamiento cambiado" }
```
`partsUsed` is optional (default: everything reserved was used). 409 `WRONG_STAGE` if the incident is not waiting for that
event, 409 `TASK_EXPIRED` if the wait timed out and the incident was reassigned.

`POST /simulator/faults`
```json
{ "machineId": "EXC-0042", "faultType": "BEARING_WEAR", "intensity": "WARNING" }
{ "zoneId": "Z3", "faultType": "FUEL_CONTAMINATION" }
{ "action": "CLEAR" }
```
faultType: BEARING_WEAR, OVERHEAT, HYDRAULIC_LEAK, INJECTOR_WEAR, FUEL_CONTAMINATION, SENSOR_FAILURE, STOPPED.
intensity: WARNING | CRITICAL. Optional `durationMinutes` (default 60) and `sigma`.

## Response examples
Generated from a real run of the incident flow: `contracts/examples/api/*.json`.

## Incident states
`OPEN -> ASSIGNED -> IN_PROGRESS -> RESOLVED -> CLOSED` (also `UNASSIGNED` after a technician timeout, `FAILED`).
`timeline[]` events: DETECTED, DIAGNOSED, PARTS_RESERVED, PARTS_MISSING, ASSIGNED, NOTIFIED, ACK_TIMEOUT, ARRIVED,
RESOLVED, ESCALATED, CLOSED.
