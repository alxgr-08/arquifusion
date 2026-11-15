# af-dev-api

- **Owner:** Rol 2
- **Trigger:** API Gateway HTTP API (JWT authorizer for Google: issuer `https://accounts.google.com`, audience = OAuth Client ID)
- **Input -> output:** REST endpoints of `contracts/api.md`
- **Problems:** P4, P5, P7, P10

- One Lambda for every route except `/simulator/faults`. Works with explicit routes or `ANY /{proxy+}`.
- Roles come from grant rows in DynamoDB (`af_shared/grants.py`), loaded with `iss` + `sub` of the verified token.
  Load them with `scripts/seed_grants.py`.
- `POST .../ack` stores the confirmation (`pendingAck`) in a transaction that re-checks the grant and the assignment,
  then calls `SendTaskSuccess` (needs `states:SendTaskSuccess`); repeating it is safe.
- Response examples: `contracts/examples/api/`.

## How to test

```bash
pytest services/api
```
