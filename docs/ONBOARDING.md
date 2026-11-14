# Onboarding del equipo

## 1. Primeros 15 minutos (todos)

1. Aceptar la invitación: https://github.com/alxgr-08/arquifusion/invitations
2. Instalar: Git, Python 3.12, Node 22, AWS CLI v2, Terraform >= 1.10, Google Cloud SDK, GitHub CLI.
3. Clonar y verificar:
   ```bash
   git clone https://github.com/alxgr-08/arquifusion.git
   cd arquifusion
   python -m venv .venv
   .venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
   pip install -r requirements-dev.txt
   python scripts/validate_contracts.py
   python scripts/run_tests.py
   ```
4. Leer `CLAUDE.md` y `contracts/`. Si usan IA para programar, CLAUDE.md ya le da el contexto.
5. Tomar sus issues en GitHub (filtro `label:rol:N`).

## 2. Flujo de trabajo

```bash
git checkout main && git pull
git checkout -b feat/rol2-simulator        # feat/ fix/ infra/ + rolN + tema
# ... código + tests ...
ruff check . && python scripts/run_tests.py
git commit -m "feat: add fleet simulator"   # Conventional Commits en inglés
git push -u origin feat/rol2-simulator
gh pr create --fill                          # 1 aprobación, squash merge
```

- `main` protegida: nadie hace push directo.
- PR pequeños (< 300 líneas). CODEOWNERS asigna revisor automáticamente.
- Cambiar algo en `contracts/` = PR aprobado por los tres + subir `schemaVersion`.
- Bloqueado más de 30 min → avisar en el grupo.

## 3. AWS Academy (Learner Lab)

- Un solo entorno `dev` en **una** cuenta del lab (la de Rol 1). Los demás desarrollan en local con `moto`
  y `contracts/examples`; para probar en la nube, Rol 1 despliega o comparte credenciales temporales.
- No se pueden crear roles IAM: todo usa `LabRole` (`var.lab_role_arn` en Terraform).
- Las credenciales caducan (~4 h): copiar de "AWS Details" a `~/.aws/credentials` (incluye `aws_session_token`).
- Solo us-east-1 y us-west-2.
- Verificar el primer día (Rol 1) y anotar en `docs/decisiones.md`:

| Servicio | Si no está disponible |
|---|---|
| Amazon Bedrock | Gemini en Vertex AI (GCP) desde `ai-diagnosis` |
| AWS IoT Core | API Gateway + Lambda como ingesta |
| Amazon Location | Distancia haversine en Lambda + mapa MapLibre con tiles libres |
| DynamoDB Global Tables | Tabla en 1 región + PITR + backups (explicar en el pitch) |
| KMS clave propia | Clave administrada por AWS |

## 4. Qué hace cada rol primero (fase F0–F1)

### Rol 1 · Leonardo (infra)
1. Verificar servicios del lab (tabla de arriba) y crear el bucket de estado de Terraform.
2. `infra/aws`: tabla DynamoDB (diseño en CLAUDE.md), bus de EventBridge, SSM `/af/*`, Secrets Manager.
3. IoT Core + IoT Rule → Firehose → S3 y → `health-evaluator`.
4. GCP: proyecto, datasets BigQuery, service account → Secrets Manager.
5. Publicar en el grupo los outputs (tabla, bus, endpoint IoT, URL API, Cognito, mapa).

### Rol 2 · Sebastián (backend)
1. Definir la flota de demo en `contracts/examples/fleet-config.json` (máquinas, modelos, zonas, técnicos,
   repuestos, bodegas) y compartirla: Rol 3 la usa para datos sintéticos.
2. `simulator` publicando telemetría válida contra `contracts/telemetry.schema.json`.
3. `statemachines/incident.asl.json`: reemplazar los `Pass` por tareas reales, uno a uno.
4. Ejemplos de respuestas de API en `contracts/examples/api/` para que el frontend trabaje con mocks.
5. Acordar con Rol 3 el contrato de `ai-diagnosis` (`contracts/ai-diagnosis.schema.json`).

### Rol 3 · Alexis (datos, IA, producto)
1. `health-evaluator`: lógica de puntaje con tests (sin AWS).
2. Historial sintético + SQL de BigQuery en `analytics/`.
3. `ai-diagnosis` con Bedrock (o Gemini) + respaldo por reglas.
4. Frontend con mocks → API real.
5. Guion y video.

## 5. Hito F1 (al 25 % del tiempo)

Flujo completo con lógica mínima:
`simulator → IoT → health-evaluator → DynamoDB → EventBridge → Step Function → SNS → web lista máquinas`.
