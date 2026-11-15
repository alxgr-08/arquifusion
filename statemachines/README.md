# statemachines — owner: Rol 2

| File | Name | Trigger |
|---|---|---|
| `incident.asl.json` | `af-dev-incident` | La entrada Standard inicia esta hija. La regla de anomalías WARNING/CRITICAL está DISABLED. En Academy se probó el inicio manual. |
| `preventive.asl.json` | `af-dev-preventive` | Definición del equipo. La prueba de Academy se prepara para una operación sintética y una ejecución manual. |

Placeholders filled by Terraform `templatefile()`: `${incident_tasks_arn}`, `${assign_technician_arn}`,
`${notify_arn}`, `${ai_diagnosis_arn}`. Execution role: `LabRole` (needs lambda:InvokeFunction).

`tests/asl_runner.py` is a small ASL interpreter: the tests run these JSON files end to end against the real
Lambdas and an in-memory DynamoDB (`af_shared.fakes`). Change a state -> run `pytest statemachines`.

`incident.asl.json` waits (`WaitForTechnician`, `WaitForResolution`) pass `$$.Execution.Id`, `$$.State.EnteredTime`
and the assigned technician to `store_task_token`, which derives the public wait version used by `POST /ack`
(ETag / If-Match). `CertifyArrival` (after the arrival wait) and `CloseIncident` are the only writers of ARRIVED /
RESOLVED in the timeline and record the accepted `ackId` in `certified.{stage}` (see `services/api/README.md`).
