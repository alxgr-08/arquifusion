# Contracts

The only dependency between team members. Any change requires a PR approved by all three and a `schemaVersion` bump.

| File | Producer | Consumer |
|---|---|---|
| `telemetry.schema.json` | simulator (Rol 2) | health-evaluator (Rol 3), Firehose/BigQuery (Rol 1) |
| `anomaly-detected.schema.json` | health-evaluator (Rol 3) | incident Step Function (Rol 2) |
| `api.md` | api-* (Rol 2) | frontend (Rol 3) |

Validate locally: `python scripts/validate_contracts.py`
