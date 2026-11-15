# statemachines — owner: Rol 2

| File | Name | Trigger |
|---|---|---|
| `incident.asl.json` | `af-dev-incident` | EventBridge rule: `source = arquifusion.health`, `detail-type = AnomalyDetected`, `detail.severity = [WARNING, CRITICAL]` |
| `preventive.asl.json` | `af-dev-preventive` | EventBridge Scheduler daily, input `{"operationId": "MINA-NORTE"}` |

Placeholders filled by Terraform `templatefile()`: `${incident_tasks_arn}`, `${assign_technician_arn}`,
`${notify_arn}`, `${ai_diagnosis_arn}`. Execution role: `LabRole` (needs lambda:InvokeFunction).

`tests/asl_runner.py` is a small ASL interpreter: the tests run these JSON files end to end against the real
Lambdas and an in-memory DynamoDB (`af_shared.fakes`). Change a state -> run `pytest statemachines`.

`incident.asl.json` waits (`WaitForTechnician`, `WaitForResolution`) pass `$$.Execution.Id`, `$$.State.EnteredTime`
and the assigned technician to `store_task_token`, which derives the public wait version used by `POST /ack`
(ETag / If-Match). `CertifyArrival` (after the arrival wait) and `CloseIncident` are the only writers of ARRIVED /
RESOLVED in the timeline and record the accepted `ackId` in `certified.{stage}` (see `services/api/README.md`).
