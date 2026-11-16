# frontend — owner: Rol 3

El cliente del acuse conserva el cuerpo y el ETag de la espera revisada al reintentar un 503, un fallo de red o un JSON de respuesta truncado después de un HTTP 200. Respeta `Retry-After`, limita los intentos y cancela el reintento si cambia la sesión. Los tests locales cubren esos casos y el build de producción pasó la comprobación de TypeScript.

React + Vite + TypeScript · MapLibre · Recharts. La demo usa Google Identity Services y se publica mediante un ZIP en la rama manual `demo` de Amplify. [Web de la demo](https://demo.d2ygcm34t8aku9.amplifyapp.com) · [Publicación](../infra/hosting/README.md)

El ensayo `ENSAYO-0001` sitúa una máquina y un técnico simulados en Chaupimarca, **Cerro de Pasco, Perú**, cerca del tajo Raúl Rojas. Los marcadores usan las coordenadas de la API. OpenStreetMap aporta las calles y los nombres de lugares. El relieve local se incluye en el ZIP para conservar una referencia del terreno si las teselas externas no cargan. La flota simulada `MINA-NORTE` conserva su escenario de Atacama. Un estilo configurado mediante `VITE_MAP_STYLE_URL` sustituye el mapa base. [Mapa turístico de Pasco](https://cdn.www.gob.pe/uploads/document/file/5567372/4951683-pasco-gppv-espanol_web_2023.pdf) · [Datos de elevación](https://registry.opendata.aws/terrain-tiles/)

## Run

```bash
cd frontend
npm install
cp .env.example .env.local   # VITE_API_MODE=mock by default
npm run dev                  # http://localhost:5173
npm test
npm run build                # dist/ para alojar la SPA
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

## Integración del ensayo

Leonardo entrega el endpoint con stage y el origen HTTPS. Alexis registra ese origen en el cliente web de Google. Sebastián verifica las concesiones por operación, rol y asignación del técnico. Verificar por separado qué rutas de lectura están publicadas para cada pantalla. [Contrato](../contracts/api.md)

La ruta de la SPA `/machines/{id}/incidents/{incId}` permite ensayar con solo las dos rutas HTTP de incidente. **Revisar espera y asignación** obtiene el detalle y su ETag antes de habilitar llegada o resolución. Un 503 conserva cuerpo y versión. Un 412 exige revisar la nueva espera. Para el dashboard se requieren las diez lecturas optativas. La inyección de fallas desde la web corresponde al modo simulado.
