# ArquiFusion Fleet Intelligence

Plataforma serverless y multinube para flotas de maquinaria pesada. Convierte la telemetría en decisiones de mantenimiento: detecta anomalías, abre el incidente, asigna técnico y repuesto, confirma la llegada y la resolución, y analiza el histórico para anticipar consumo anómalo y demanda de piezas.

![Arquitectura](docs/architecture.png)

[Diagrama editable (Draw.io)](docs/architecture.drawio) · [SVG](docs/architecture.svg) · [Arquitectura por componente](docs/arquitectura.md) · [Web de la demo](https://demo.d2ygcm34t8aku9.amplifyapp.com)

## Dos nubes

| Nube | Qué hace | Servicios |
|---|---|---|
| **AWS** (Academy Learner Lab, `us-east-1`) | Opera la flota en tiempo real: evaluación de salud, incidentes, técnicos, stock y web | Lambda (Python 3.12), Step Functions, DynamoDB (PITR, Streams, TTL), EventBridge, SQS (DLQ), SNS, API Gateway HTTP con JWT de Google, Amplify Hosting, SSM Parameter Store, CloudWatch |
| **Google Cloud** (proyecto `arquifusion`) | Analiza el histórico y entrena los modelos | BigQuery, BigQuery ML (`ARIMA_PLUS`, `KMEANS`), Google Identity Services para el login |

El histórico de BigQuery es sintético (12 máquinas) y está rotulado como tal. El puente AWS ↔ BigQuery (Firehose + Glue → S3 Parquet → BigQuery → KPI de vuelta a DynamoDB) está diseñado e implementado en código ([`lake_to_bigquery`](services/lake_to_bigquery/), [`bigquery_sync_back`](services/bigquery_sync_back/)) y Firehose/Glue se probó en un ensayo temporal, pero el recorrido completo no está desplegado en esta entrega.

En el Learner Lab, alcanzar diez ejecuciones Lambda simultáneas pone en riesgo la cuenta compartida. La TA indicó ejecutar la ingesta con Python/Boto3 desde una máquina. La demo utiliza un evento manual de incidente, la regla EventBridge deshabilitada y los datos históricos ya cargados en BigQuery. [Estado, restricciones y recorrido del ensayo](docs/arquitectura.md)

## Cómo responde a la rúbrica

| Criterio | Dónde está |
|---|---|
| Persistencia y alta disponibilidad | DynamoDB bajo demanda con PITR, Streams y TTL; outbox de alertas; recibos idempotentes con ETag/If-Match ([`infra/aws/dynamodb.tf`](infra/aws/dynamodb.tf)) |
| Step Functions | Flujo de incidente: correlación, reserva de pieza, diagnóstico, asignación, avisos y dos esperas con callback (llegada y resolución) ([`statemachines/`](statemachines/)) |
| Dos nubes | AWS para la operación, GCP BigQuery/BigQuery ML para la analítica |
| Serverless | Solo Lambda, Step Functions, DynamoDB, API Gateway, Amplify y BigQuery; sin servidores ni VPC |
| IA | BigQuery ML: pronóstico de consumo de combustible y de demanda de repuestos (`ARIMA_PLUS`) y segmentación de la flota (`KMEANS`) ([`analytics/`](analytics/)). Diagnóstico del incidente con Claude en Amazon Bedrock y respaldo por reglas; la demo usa el de reglas ([`services/ai_diagnosis/`](services/ai_diagnosis/)) |
| Seguridad de variables globales | SSM Parameter Store para la configuración de las Lambdas; sin secretos en código, Terraform ni Vite. Acceso con Google Sign-In, autorizador JWT y permisos por operación y rol en DynamoDB |

## Equipo

| Rol | Integrante | Responsable de |
|---|---|---|
| Rol 1 · Arquitectura cloud e infraestructura | Leonardo (`leonardopanduro-rgb`) | `infra/`, seguridad, CI/CD, lago de datos, diagrama |
| Rol 2 · Backend y orquestación | Sebastián (`sebastianperez72`) | simulador, inyector de fallas, `statemachines/`, incidentes, técnicos, avisos, API |
| Rol 3 · Datos, IA y producto | Alexis (`alxgr-08`) | evaluador de salud, diagnóstico, `analytics/`, retorno de KPIs, `frontend/`, pitch |

## Estructura

| Ruta | Contenido |
|---|---|
| `contracts/` | JSON Schemas, ejemplos y contrato HTTP |
| `services/` | Una carpeta por Lambda |
| `statemachines/` | ASL de Step Functions |
| `shared/` | Modelos Pydantic y utilidades comunes |
| `analytics/` | SQL de BigQuery y BigQuery ML, histórico sintético y consultas de la demo |
| `frontend/` | React + Vite + TypeScript |
| `infra/aws`, `infra/gcp`, `infra/hosting` | Terraform y scripts de despliegue |
| `docs/` | Arquitectura, decisiones y guion de la demo |

## Ejecutar en local

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/Mac: source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/validate_contracts.py
python scripts/run_tests.py

cd frontend && npm ci && npm run dev   # modo mock por defecto
```

## Flujo de trabajo

`main` está protegida: cambios por PR con CI verde (`python`, `frontend`), Conventional Commits en inglés y squash merge. Más detalle en [`docs/ONBOARDING.md`](docs/ONBOARDING.md).
