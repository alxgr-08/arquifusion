# af-dev-notify

- **Owner:** Rol 2
- **Trigger:** Step Functions
- **Input -> output:** `{kind, role?, incident, diagnosis, ...}` -> SNS publish per role + `NOTIFIED` events in the incident timeline
- **Problems:** P9

- Role x severity matrix in `notifier.py` (guide section 5.6). PANEL/DAILY are not pushed.
- Topics: SSM `/af/notify/topics` = `{"OPERATOR": "arn...", "TECHNICIAN": ..., "MAINT_LEAD": ..., "SUPERVISOR": ..., "MANAGER": ...}`.
- Message attributes for subscription filters: `severity`, `role`, `channel`, `kind`, `machineId`, `techId`.

## How to test

```bash
pytest statemachines -k notif
```

En el laboratorio, la configuración apunta al tópico de desarrollo y aplica atributos de rol/severidad. El inventario encontró cero suscriptores. Para afirmar recepción humana se necesita un destinatario que confirme su suscripción y recepción en un ensayo autorizado. [Arquitectura](../../docs/arquitectura.md)
