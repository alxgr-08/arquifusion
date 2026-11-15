# af-dev-assign-technician

- **Owner:** Rol 2
- **Trigger:** Step Functions (`AssignTechnician`, `ReleaseTechnician`)
- **Input -> output:** Incident + diagnosis -> chosen technician, ETA, top 3 candidates (stored in the incident for the web)
- **Problems:** P5

- cost = ETA_min + 60 x (lacks specialty) + 15 x (queue). Specialty = `diagnosis.component` of ai-diagnosis
  (technician `specialties` in fleet-config use the same values). Cheapest wins; claimed with a conditional write.
- Technician fields as in the web: `availability` AVAILABLE | BUSY | OFF, `queue`.
- ETA with Amazon Location `CalculateRouteMatrix` if `AF_ROUTE_CALCULATOR` is set, else haversine x 1.3 at 25 km/h.
- `action: release` after a confirmation timeout: frees the technician and excludes them from the next try.

## How to test

```bash
pytest services/incident_tasks -k assign
```
