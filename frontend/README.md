# frontend — owner: Rol 3

React + Vite + TypeScript · MapLibre · Recharts. Hosted on S3 + CloudFront (Rol 1), sign-in with Google Identity Services.

## Run

```bash
cd frontend
npm install
cp .env.example .env.local   # VITE_API_MODE=mock by default
npm run dev                  # http://localhost:5173
npm test
npm run build                # dist/ -> S3
```

## Modes

| `VITE_API_MODE` | Data |
|---|---|
| `mock` (default) | In-browser simulated fleet (`src/mock/`): 12 machines, technicians, parts, KPIs. Faults ramp up every 2 s and run the full incident flow, so the demo works without backend. |
| `live` | Google sign-in first (`VITE_GOOGLE_CLIENT_ID`), then API Gateway at `VITE_API_URL` (`contracts/api.md`, include the stage, e.g. `/dev`) with the Google ID token in `Authorization`. The token is kept only in memory; the session ends on sign-out, on token expiry or on any 401. |

Demo rehearsal (mock): open `/?fault=EXC-0042:BEARING_WEAR` or `/incidents?fault=TRK-0107:FUEL_BATCH_ZONE`
to inject faults on load. Fault types: `BEARING_WEAR`, `OVERHEATING`, `HYDRAULIC_LEAK`, `FUEL_BATCH_ZONE` (whole zone, P11).

## Screens

| Route | Content | Problems |
|---|---|---|
| `/` | Live map by severity, technicians, fleet table, next maintenance by usage, fault injector | P1, P2, P4 |
| `/machines/:id` | Health and sensor chart, incident history with timeline, maintenance and parts | P1, P8, P10 |
| `/incidents` | Queue by severity, AI diagnosis, message for the selected role, technician + ETA, reserved parts, zone incident, ack buttons | P5, P9, P10, P11 |
| `/kpis` | Productivity vs cost, fuel L/t vs model, per-machine table | P6, P7 |
| `/parts` | Stock per warehouse, 4-week forecast, shortage / overstock / irregular demand | P3 |

The role selector only changes the view (sections and the incident message for that role); it grants no access.
The API authorizes each request with the grants stored in DynamoDB (#42).

## Needed from other roles

- Rol 2: response examples in `contracts/examples/api/` (shapes proposed in `src/api/types.ts`) and a
  `GET /parts` list endpoint (or `/parts/{partId}/stock` for each part).
- Rol 1: Cognito user pool id + client id, API URL, Amazon Location map style URL (`VITE_MAP_STYLE_URL`),
  S3 + CloudFront for hosting.
