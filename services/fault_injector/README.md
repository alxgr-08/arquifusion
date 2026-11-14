# af-dev-fault-injector

- **Owner:** Rol 2
- **Trigger:** API Gateway `POST /simulator/faults`
- **Input -> output:** Fault request -> updates SSM `/af/sim/faults`, kicks the simulator (`AF_SIMULATOR_FUNCTION`, async, fastOnly)
- **Problems:** Demo (P11 with `zoneId`)

- Body and fault types: see `contracts/api.md`. `{"action": "CLEAR"}` removes all faults.
- Faults expire (`durationMinutes`, default 60) and are cleared automatically when the incident closes.
- Groups allowed: `AF_FAULT_GROUPS` (default MAINT_LEAD, SUPERVISOR, MANAGER).

## How to test

```bash
pytest services/fault_injector
```
