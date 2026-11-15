# Decisions log

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
