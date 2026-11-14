# API REST (backend -> frontend) · v1.0

Base: API Gateway. Auth: Cognito JWT (`Authorization: Bearer <token>`); the Cognito group defines what each user sees.
JSON responses, camelCase fields. Errors: `{ "error": { "code": "...", "message": "..." } }`.

| Method | Route | Returns / does | Problem |
|---|---|---|---|
| GET | `/operations/{op}/machines?status=` | Machines with status, health and position | P4 |
| GET | `/machines/{id}` | Profile and current state | P1, P4 |
| GET | `/machines/{id}/readings?from=&to=` | Recent readings for charts | P1, P8 |
| GET | `/machines/{id}/incidents` | Incident history | P10 |
| GET | `/machines/{id}/maintenance` | Maintenance and replaced parts | P2, P10 |
| GET | `/operations/{op}/incidents?state=OPEN` | Incident queue sorted by severity | P5, P9 |
| GET | `/operations/{op}/technicians` | Technicians and availability | P5 |
| GET | `/parts/{partId}/stock` | Stock and forecast per warehouse | P3 |
| GET | `/operations/{op}/kpis?date=` | Utilization, fuel, cost per machine | P6, P7 |
| POST | `/machines/{id}/incidents/{incId}/ack` | Technician confirms arrival/close (resumes Step Function) | P5, P10 |
| POST | `/simulator/faults` | Injects a fault for the demo | Demo |

Response examples go in `contracts/examples/api/` (Rol 2, before F1 ends).
