# af-dev-simulator

- **Owner:** Rol 2
- **Trigger:** EventBridge Scheduler, `rate(1 minute)`; Lambda timeout 60 s
- **Input -> output:** SSM `/af/sim/fleet-config` + `/af/sim/faults` -> telemetry on `arquifusion/{op}/{machineId}/telemetry`
- **Problems:** Demo (feeds P1, P4, P6, P7, P8)

- Stateless and deterministic: same machine + timestamp -> same message and `messageId` (retries deduplicate).
- Fleet: `contracts/examples/fleet-config.json` (Rol 3), stored whole in SSM `/af/sim/fleet-config` by
  `scripts/seed_demo.py`. Values jitter around `models.<model>.normal` like the frontend mock.
- Faults (`BEARING_WEAR`, `OVERHEATING`, `HYDRAULIC_LEAK`, `FUEL_BATCH_ZONE`, `SENSOR_FAILURE`, `STOPPED`) use the same
  magnitudes as `frontend/src/mock/mockApi.ts` and grow over `AF_SIM_RAMP_SECONDS` (default 300). `FUEL_BATCH_ZONE`
  also changes `context.fuelBatchId` to `<zone batch>-X`.
- Every machine once per minute; machines with an active fault every `AF_SIM_FAST_INTERVAL_SECONDS` (default 15,
  0 = off) inside the same invocation, so the health score reacts within the video.
- Env: `AF_INGEST_MODE` = `iot` (default) or `direct` (plan B without IoT Core: invokes `AF_HEALTH_EVALUATOR_FUNCTION`
  async and writes to `AF_FIREHOSE_STREAM`). Optional `AF_IOT_ENDPOINT` (else `describe_endpoint`).

## How to test

```bash
pytest services/simulator
```
