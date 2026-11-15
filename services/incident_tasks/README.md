# af-dev-incident-tasks

- **Owner:** Rol 2
- **Trigger:** Step Functions (`af-dev-incident`, `af-dev-preventive`)
- **Input -> output:** `{"action": ...}` -> task result (see `handler.py` for the list)
- **Problems:** P2, P3, P10, P11

- `incidents.py`: claim (one open incident per machine), context (builds the ai-diagnosis request blocks), rule
  fallback with the ai-diagnosis response shape, create incident (Incident shape of the web), zone incident, atomic
  part reservation by `diagnosis.component` and zone warehouse, task tokens, close (downtime, parts, recurrence, MAINT#).
- `preventive.py`: usage factor, due machines, preventive orders, restock plan with the BigQuery forecast.
- Reads SSM `/af/sim/fleet-config` (parts, warehouses) and `/af/incident/settings` (timeouts;
  `scripts/seed_demo.py --demo` shortens them).

## How to test

```bash
pytest services/incident_tasks statemachines
```
