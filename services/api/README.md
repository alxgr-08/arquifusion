# af-dev-api

- **Owner:** Rol 2
- **Trigger:** API Gateway HTTP API (JWT authorizer, Cognito)
- **Input -> output:** REST endpoints of `contracts/api.md`
- **Problems:** P4, P5, P7, P10

- One Lambda for every route except `/simulator/faults`. Works with explicit routes or `ANY /{proxy+}`.
- `POST .../ack` consumes the stored task token and calls `SendTaskSuccess` (needs `states:SendTaskSuccess`).
- Response examples: `contracts/examples/api/`.

## How to test

```bash
pytest services/api
```
