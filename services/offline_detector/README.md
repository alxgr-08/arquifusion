# af-dev-offline-detector

- **Owner:** Rol 2
- **Trigger:** EventBridge Scheduler, `rate(5 minutes)`
- **Input -> output:** Machines whose `lastSeen` is older than `AF_OFFLINE_AFTER_MINUTES` (5) -> `status=OFFLINE`, `GSI1SK=OFFLINE#id`
- **Problems:** P4

- Conditional on `lastSeen` unchanged, so a reading that arrives meanwhile wins.
- Env: `AF_OPERATIONS` (default `MINA-NORTE`).

## How to test

```bash
pytest services/offline_detector
```
