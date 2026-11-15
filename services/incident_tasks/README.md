# af-dev-incident-tasks

- **Owner:** Rol 2
- **Trigger:** Step Functions (`af-dev-incident`, `af-dev-preventive`)
- **Input -> output:** `{"action": ...}` -> task result (see `handler.py` for the list)
- **Problems:** P2, P3, P10, P11

- `incidents.py`: claim (one open incident per machine), context (builds the ai-diagnosis request blocks), rule
  fallback with the ai-diagnosis response shape, create incident (Incident shape of the web), zone incident, atomic
  part reservation by `diagnosis.component` and zone warehouse, task tokens, close (downtime, parts, recurrence, MAINT#).
- `preventive.py`: usage factor, due machines, preventive orders, restock plan with the BigQuery forecast.
- In Academy the incident task package loads the compressed SSM catalog selected by `AF_INCIDENT_CATALOG`. Each synthetic operation can override settings and its warehouse in `OP#… / INCIDENT_SETTINGS`. The manual preventive trial scopes reads and writes to its kit. [Incident configuration](../../infra/aws/incident/README.md) · [Preventive trial](../../infra/aws/maintenance/README.md)

## How to test

```bash
pytest services/incident_tasks statemachines
```
