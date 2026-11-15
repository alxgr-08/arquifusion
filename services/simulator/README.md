# af-dev-simulator

- **Owner:** Rol 2
- **Entrada en Academy:** un caso manual con una máquina sintética, en modo `direct`. La cadencia del diseño original permanece desactivada.
- **Salidas del ensayo:** el mismo mensaje completo al evaluador y a Firehose para su conversión a Parquet.
- **Problems:** Demo (feeds P1, P4, P6, P7, P8)

- Stateless and deterministic: same machine + timestamp -> same message and `messageId` (retries deduplicate).
- Fleet: `contracts/examples/fleet-config.json` (Rol 3), the legacy full-fleet initializer attempts to store it in SSM via
  `scripts/seed_demo.py`. Values jitter around `models.<model>.normal` like the frontend mock.
- Faults (`BEARING_WEAR`, `OVERHEATING`, `HYDRAULIC_LEAK`, `FUEL_BATCH_ZONE`, `SENSOR_FAILURE`, `STOPPED`) use the same
  magnitudes as `frontend/src/mock/mockApi.ts` and grow over `AF_SIM_RAMP_SECONDS` (default 300). `FUEL_BATCH_ZONE`
  also changes `context.fuelBatchId` to `<zone batch>-X`.
- Every machine once per minute; machines with an active fault every `AF_SIM_FAST_INTERVAL_SECONDS` (default 15,
  0 = off) inside the same invocation, so the health score reacts within the video.
- Env: `AF_INGEST_MODE` = `iot` (default) or `direct` (opción vigente de Academy: invokes `AF_HEALTH_EVALUATOR_FUNCTION`
  async and writes to `AF_FIREHOSE_STREAM`). Optional `AF_IOT_ENDPOINT` (else `describe_endpoint`).

In direct mode each reading goes to the evaluator once per call to `publish`. Firehose receives up to 500 newline-terminated JSON records per batch. The producer checks `FailedPutCount` and each ordered acknowledgement. It retries the exact bytes of explicitly failed records after 0.25 and 0.5 seconds, for at most three submissions per record. The SDK makes one request per submission. A missing or contradictory acknowledgement, a timeout, or exhaustion raises an error with the message IDs to reconcile. Any later, unsent batch is identified separately. [Firehose batch API](https://docs.aws.amazon.com/firehose/latest/APIReference/API_PutRecordBatch.html)

For the one-machine Academy rehearsal, keep the case's `reading.json`, `firehose-record.ndjson` and `dispatch.jsonl`. Check the evaluator reading in DynamoDB and the same message ID in S3 before replaying a missed destination. Reuse the frozen payload and timestamp. Calling the simulation again with a new clock generates another message ID. A Lambda 202 or a Firehose record ID confirms acceptance. Object delivery and BigQuery ingestion require their own observations. [One-machine rehearsal](../../infra/aws/telemetry/README.md)

## How to test

```bash
pytest services/simulator
```

En Academy, la flota completa supera 4096 bytes en SSM Standard. El ensayo local congela un catálogo sintético de una máquina y no sobrescribe el parámetro común. [Procedimiento del ensayo](../../infra/aws/telemetry/README.md)
