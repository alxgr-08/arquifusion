# CLAUDE.md — ArquiFusion Fleet Intelligence

Context for AI-assisted coding. Read before generating code.

## Goal
Serverless multi-cloud platform (AWS ops + GCP analytics/AI) for heavy-machinery fleets: predictive maintenance,
preventive maintenance by usage, parts forecasting, technician assignment, fuel anomalies, fleet productivity.
Problems are tagged P1–P13 (see README). If a feature is not visible in the 5-minute demo video, it is not a priority.

## Hard rules
- 100% serverless: no EC2, RDS or always-on containers.
- Two clouds must be used with data flowing both ways (AWS <-> GCP BigQuery).
- Keep secrets out of code, logs, Terraform and Vite. Use the existing LabRole and SSM for application configuration. The cross-cloud bridge requires an approved isolated identity.
- Contracts in `contracts/` are the only dependency between roles. Never change a contract without team approval
  and a `schemaVersion` bump. Validate inputs/outputs with the pydantic models in `shared/af_shared`.

## Stack
- Lambdas: Python 3.12, boto3, pydantic v2, pytest, ruff, Lambda Powertools Logger (structured JSON logs with machineId/incidentId).
- Frontend: React + Vite + TypeScript, MapLibre, Recharts.
- IaC: Terraform (AWS + GCP). Step Functions: ASL JSON in `statemachines/`.
- Regions: us-east-1 (regional Academy deployment). BigQuery location US.
- Resource names: `af-{env}-{component}` (env = dev). Tags: project=arquifusion, env, owner.

## Conventions
- JSON keys camelCase; Python snake_case with type hints; enums UPPER_SNAKE.
- IDs: EXC-0042, INC-8f3a, T-07, FLT-HID-01, WH-NORTE.
- Dates: UTC ISO 8601 with `Z`.
- Each service: `services/<name>/handler.py`, `requirements.txt`, `tests/`, `README.md` (what, trigger, how to test).

## DynamoDB single table `af-dev-arquifusion`
| Entity | PK | SK | GSI1PK / GSI1SK | GSI2PK / GSI2SK |
|---|---|---|---|---|
| Machine | MACHINE#{id} | PROFILE | OP#{op}#MACHINE / {status}#{id} | |
| Reading (TTL 48h, expiresAt) | MACHINE#{id} | READING#{ts} | | |
| Incident | MACHINE#{id} | INCIDENT#{createdAt}#{incId} | OP#{op}#INCIDENT#{OPEN\|CLOSED} / {sevRank}#{createdAt} | ZONE#{op}#{zone} / {createdAt}#{id} |
| Durable pending anomaly | MACHINE#{id} | OUTBOX#ANOMALY#{messageId} | OUTBOX#ANOMALY / see the health handler | |
| Maintenance | MACHINE#{id} | MAINT#{date}#{mntId} | | |
| Daily KPI | MACHINE#{id} | KPI#{date} | OP#{op}#KPI#{date} / {id} | |
| Technician | TECH#{id} | PROFILE | OP#{op}#TECH / {availability}#{id} | |
| Stock | PART#{id} | STOCK#{wh} | WH#{wh} / PART#{id} | |
| Forecast | PART#{id} | FORECAST#{wh}#{week} | | |
| Model baseline | MODEL#{model} | BASELINE | | |
sevRank: 1=CRITICAL, 2=WARNING, 3=WATCH.

## Health score (P1, P8)
1. EWMA per metric, alpha 0.2.
2. risk = max(z_own, 0.8 * z_fleet), only in the dangerous direction, clamped [0, 5].
3. health = 100 - 20 * weighted_avg(risks); x1.25 penalty if >= 2 metrics have risk > 2.
4. Severity only rises after 3 consecutive readings (anomalyStreak).
5. Any metric risk >= 4.5 -> CRITICAL immediately.
6. Own baseline (Welford) only updates while severity is OK.
Thresholds: >=80 OK, 60–79 WATCH, 40–59 WARNING, <40 CRITICAL. Weights/thresholds in SSM `/af/health/*`.

## Ownership
- Rol 1 (Leonardo): Terraform, API Gateway, hosting, security, data bridge, CI and diagram.
- Rol 2 (Sebastián): simulator, fault-injector, offline-detector, Step Functions, incident-tasks, assign-technician,
  notify, api-*.
- Rol 3 (Alexis): health-evaluator, ai-diagnosis, analytics/ (BigQuery ML), bigquery-sync-back, frontend, pitch.

## AWS Academy (Learner Lab) constraints
- Use the existing `LabRole` for Lambda, Step Functions and Firehose. Scope API Gateway invocation by account, stage, method and route. The Academy design uses manual rehearsals with the incident rule disabled.
- Session credentials expire (~4 h). Never hardcode them; refresh `~/.aws/credentials` from the lab "AWS Details".
- Only us-east-1 and us-west-2 are available.
- Verify service availability by action in this Learner Lab. See [the current architecture](docs/arquitectura.md).

## Shared helpers
- `af_shared.contracts`: pydantic models of the contracts.
- `af_shared.keys`: DynamoDB key builders. Always use them instead of formatting keys by hand.
