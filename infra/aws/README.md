# infra/aws — owner: Rol 1 (Leonardo)

Arquitectura vigente y evidencias por entorno: [ArquiFusion](../../docs/arquitectura.md).

AWS Academy Learner Lab, perfil `default`, región `us-east-1`. El estado de la aplicación, el bootstrap y los archivos de datos tienen ciclos de vida separados.

## Preparación del primer despliegue

La auditoría de solo lectura y el plan de integración están en
[`docs/auditoria-inicial.md`](../../docs/auditoria-inicial.md).

`bootstrap/` conserva el estado local de los tres controles del bucket existente
`af-dev-tfstate-<ACCOUNT_ID>` de ArquiFusion. Consulta el bucket como dato de S3 y
comprueba su región. El laboratorio deniega `s3:GetBucketObjectLockConfiguration`,
una lectura que el recurso general `aws_s3_bucket` necesita para refrescarse.
El bloque `removed` con `destroy = false` protege la transición de la referencia anterior.
El ciclo de vida del bucket y sus etiquetas quedan fuera de la gestión de esta raíz.
El estado local está excluido de Git. El backend de la aplicación usa otra clave y bloqueo nativo S3.

La raíz `infra/aws/` administra la tabla `af-dev-arquifusion`, con PK/SK,
GSI1/GSI2 con proyección `ALL`, TTL `expiresAt`, PITR, Streams y protección contra borrado.
El cifrado de DynamoDB utiliza la clave propiedad de AWS por defecto.
Los proveedores y el backend limitan la cuenta con `expected_account_id` y
`allowed_account_ids`, respectivamente.

Validación estática en un checkout nuevo, desde la raíz del repositorio en PowerShell.

```powershell
terraform -chdir=infra/aws/bootstrap init -backend=false -input=false
terraform -chdir=infra/aws/bootstrap validate
terraform -chdir=infra/aws init -backend=false -input=false
terraform -chdir=infra/aws validate
terraform fmt -check -recursive infra/aws
```

Estas validaciones descargan proveedores y comprueban la configuración local.
El versionado está habilitado y el backend de la aplicación usa `aws/terraform.tfstate`
en el bucket dedicado. El despliegue de la tabla se verificó en `us-east-1`.
Las credenciales temporales se obtienen del perfil AWS CLI `default`.

## Plan local del bootstrap

El plan de versionado aplicado quedó en el worktree original, junto a su estado local.
Para revisar el bootstrap desde ese worktree, el siguiente comando guarda un plan
con fecha en `build/`, excluido de Git. Ejecutarlo desde la raíz del repositorio.

```powershell
$env:AWS_PROFILE = "default"
$env:AWS_REGION = "us-east-1"
$accountId = aws sts get-caller-identity --profile default --region us-east-1 --query Account --output text --no-cli-pager
if ($LASTEXITCODE -ne 0 -or $accountId -notmatch '^\d{12}$') { throw "No se pudo verificar la cuenta" }
$env:TF_VAR_expected_account_id = $accountId.Trim()
New-Item -ItemType Directory -Force -Path build/terraform-review | Out-Null
$planName = "bootstrap-versioning-$(Get-Date -Format 'yyyyMMdd-HHmmss').tfplan"
terraform -chdir=infra/aws/bootstrap plan -input=false "-out=../../../build/terraform-review/$planName"
```

El plan de versionado cambió `aws_s3_bucket_versioning.tfstate` de `Disabled` a `Enabled`,
con cero altas y cero bajas. SSE-S3 y los cuatro bloqueos de acceso público están activos.
El plan original `bootstrap.tfplan` quedó obsoleto tras la creación parcial inicial.

La revisión de Prompt 1 incluye un ensayo de plan de tabla desde una copia de los
`.tf` en `build/terraform-review/table-local/`, con backend local y estado vacío.
Los archivos originales mantienen su backend S3. Ese ensayo permite revisar el esquema
y una alta de tabla. El plan definitivo se generó y se aplicó desde `infra/aws/`
en el worktree `infra/rol1-aws-dynamodb-plan` con el backend S3 y su bloqueo nativo.

Conservar `.terraform.lock.hcl` de ambas raíces para reproducir los proveedores.
Los planes, estados, configuración local y directorios `.terraform/` quedan excluidos.

## Health Evaluator

`health_evaluator.tf` administra la Lambda del commit `be19d3e`, sus dos parámetros SSM,
el bus del proyecto y el grupo de CloudWatch Logs. El primer despliegue en Academy creó los
cinco recursos. La guía registra las dos invocaciones reales, sus escrituras en DynamoDB y
la aceptación de la anomalía por EventBridge. La entrega a un consumidor queda por verificar.
[Despliegue y pruebas de Health Evaluator](health_evaluator/README.md)

## Flujo de incidentes

El plan de desarrollo conecta `af-dev-bus` con el ASL de Sebastián mediante una entrada Standard que deduplica reenvíos. La regla queda deshabilitada hasta revisar una operación sintética. La Lambda de tareas obtiene el catálogo completo de un parámetro SSM Standard propio del flujo. [Plan, catálogo, permisos y prueba controlada](incident/README.md)

## AWS Academy rules

- La Lambda consulta el `LabRole` existente en la cuenta esperada. Si se proporciona `var.lab_role_arn`, debe coincidir con el ARN devuelto por IAM.
- Credentials expire every ~4 h: refresh from "AWS Details" in the lab.

## Lambda packaging

Para el evaluador, `health_evaluator/build.py` reúne las fuentes de `services/health_evaluator/`,
`shared/af_shared` y las dependencias Linux fijadas en `health_evaluator/requirements.lock`.

## API, telemetría y puente

El Terraform de la API empaqueta el handler de Sebastián y publica, bajo activación explícita, dos rutas de incidentes con JWT, un Lambda y el stage `dev`. Alexis registró el cliente de Google y el origen `http://localhost:5173`. Para hosting hace falta registrar su origen HTTPS exacto. [API y ensayo](api/README.md)

El módulo `telemetry` espera un bucket privado de datos exclusivo. Su activación prepara Glue, los controles S3 y un Firehose para el simulador en modo `direct`. El diagrama distingue la prueba temporal en Academy del despliegue permanente. [Configuración de telemetría](telemetry/README.md) · [Arquitectura](../../docs/architecture.png)

`enable_stream_exporter` prepara una Lambda, su grupo de logs y un mapeo del Stream inicialmente deshabilitado, limitado a las claves de incidentes y stock. El mapeo utiliza `ReportBatchItemFailures` y comenzaría en el horizonte de retención al activarlo. Revisar el alcance de las filas y la recuperación antes de habilitarlo. `bridge_wif` activa de forma independiente el cargador manual, su grupo de logs y una capa que contiene la configuración pública del proveedor de identidad. Ambas opciones requieren el lago verificado y ZIPs de Linux en `build/bridge`. El cargador usa `af_bridge_stage` en `US`. El proyecto de Google debe aceptar la identidad de la función antes del ensayo. [Dataset e identidad](../gcp/README.md) · [Exportador](../../services/stream_exporter/README.md) · [Cargador](../../services/lake_to_bigquery/README.md)

La tabla es `af-dev-arquifusion`, el bus es `af-dev-bus` y el tópico de desarrollo es `af-dev-incident-notifications`. La regla de incidentes permanece `DISABLED`. El curso fija diez entornos Lambda simultáneos para la cuenta compartida. [Entrega y planes de Leonardo](../../docs/entrega-rol1.md)

## Controles de esta entrega local

La API está ausente por defecto. El perfil cerrado de incidente contiene 2 rutas, y el conjunto de dashboard añade 10 GET autenticados. La apertura requiere otra opción explícita y un origen autorizado. La tabla es regional y los 27 recursos del estado de aplicación se conservan. La outbox añade datos al procesar futuras alertas y cuenta con mapping optativo inicialmente deshabilitado. El retorno de BigQuery se limita a una operación, máquina y fecha de ensayo. [API](api/README.md) · [Outbox](../../services/health_evaluator/README.md) · [Hosting](../hosting/README.md) · [Retorno](../../services/bigquery_sync_back/README.md)

El alojamiento manual optativo agrega una aplicación Amplify y su rama de demo. La generación del ZIP funciona sin subir archivos. El origen HTTPS que devuelva el servicio sirve para el registro en Google y CORS. [Hosting](../hosting/README.md) · [Planes de este corte](../../docs/informe-final-2026-11-15.md)
