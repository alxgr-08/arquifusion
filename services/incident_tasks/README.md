# af-dev-incident-tasks

- **Owner:** Rol 2
- **Trigger:** Step Functions (`af-dev-incident`, `af-dev-preventive`)
- **Input -> output:** `{"action": ...}` -> task result (see `handler.py` for the list)
- **Problems:** P2, P3, P10, P11

- `incidents.py`: claim (one open incident per machine), context, rule-based diagnosis fallback, create incident,
  zone incident, atomic part reservation, task tokens, close (downtime, parts used, recurrence, MAINT#).
- `preventive.py`: usage factor, due machines, preventive orders, restock plan with the BigQuery forecast.
- Reads SSM `/af/incident/catalog` and `/af/incident/settings` (timeouts; `scripts/seed_demo.py --demo` shortens them).

## How to test

```bash
pytest services/incident_tasks statemachines
```
