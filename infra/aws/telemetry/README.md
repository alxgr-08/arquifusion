# Telemetría regional en AWS Academy

Para preparar una sola lectura sintética, ejecutar `python infra/aws/telemetry/prepare_case.py` desde la raíz con las dependencias de desarrollo. Invoca el simulador de Sebastián en modo `direct` con dobles locales para Lambda, Firehose y SSM. Verifica el contrato y la igualdad del mensaje en ambas salidas. Guarda el catálogo, el payload y el registro NDJSON bajo `build/telemetry-cases/`. El primer catálogo sintético ocupó 589 bytes.

La lectura en Academy del 15 de noviembre devolvió cero parámetros para `/af/sim/fleet-config` y `ResourceNotFoundException` para `af-dev-simulator`. La flota completa de demostración ocupa 4.248 bytes compactada como JSON. El simulador espera flota directa en SSM Standard, cuyo límite es 4.096 bytes. Para el ensayo con el productor real, acordar con Sebastián una flota aislada o su lectura del manifiesto comprimido. El manifiesto `/af/incident/fleet-catalog` lo interpreta el adaptador de incidentes. No sustituir el parámetro compartido durante la prueba.

## Una máquina y una lectura en modo direct

`prepare_case.py` ejecuta el productor real localmente con una flota sintética de una máquina, cero fallas y el bucle rápido desactivado. `run_case.py` revisa los dos payloads congelados y la pertenencia de la máquina al caso. Por defecto opera sin red y conserva `reading.json` y `firehose-record.ndjson` para conciliar su `messageId`. Usar el ID hexadecimal de 32 caracteres que imprimió el generador.

```powershell
$case = python infra/aws/telemetry/prepare_case.py | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) { throw 'No se pudo generar la lectura sintética' }
$caseId = Split-Path -Leaf $case
python infra/aws/telemetry/run_case.py $caseId
```

Cuando el lago permanente esté desplegado y la etapa de escritura esté autorizada, `python infra/aws/telemetry/run_case.py $caseId --send --account-id $accountId` entrega esos mismos bytes al evaluador y a Firehose. Usa el perfil `default`, la región `us-east-1`, solo una invocación asíncrona a `af-dev-health-evaluator` y un `PutRecordBatch` con un registro en `af-dev-telemetry`. El flujo manual no requiere desplegar una Lambda del simulador, editar SSM ni crear un horario. Antes de enviar comprueba STS, etiquetas del evaluador, destino/formato del stream y que la partición de la máquina sintética esté vacía. Requiere lectura de esas tres dependencias y permisos de envío a los dos destinos. El evaluador puede escribir en DynamoDB y, si detecta una anomalía, en EventBridge.

`dispatch.jsonl` reserva el único intento local del caso antes de enviar y anota aceptación por destino. Un 202 de Lambda y un RecordId acreditan recepción. Verificar después la lectura en DynamoDB, el registro en `records/` y, cuando corresponda, la fila de BigQuery. Ante un timeout o aceptación parcial, conservar el diario, conciliar ambos destinos por `messageId` y usar el archivo exacto solo para el destino pendiente después de revisar el resultado. La herramienta no repite automáticamente un caso iniciado ni regenera la muestra para una recuperación.

## Catálogo, destino y controles

El módulo opcional recibe telemetría v1.0 del simulador y la convierte en Parquet con Snappy en S3. `enable_telemetry_lake` vale `false` por defecto. La raíz solo consulta los recursos del lago cuando se habilita. En `dev` usa el `LabRole` que la raíz lee de IAM y el proveedor primario de `us-east-1`. [Contrato de telemetría](../../../contracts/telemetry.schema.json) · [Simulador](../../../services/simulator/handler.py)

| Recurso | Nombre o configuración |
| --- | --- |
| Bucket privado dedicado | `af-dev-arquifusion-lake-<cuenta>-us-east-1-an`, versionado habilitado, SSE-S3 AES256 y los cuatro controles de Block Public Access |
| Glue Data Catalog | Base `af_dev_telemetry`, tabla externa `telemetry`, 11 columnas y tres estructuras anidadas (`location`, `metrics`, `context`) |
| Amazon Data Firehose | `af-dev-telemetry`, DirectPut, OpenX JSON insensible a mayúsculas, Parquet/Snappy, búfer de entrada de 64 MiB o 60 s |
| S3 | Datos en `records/YYYY/MM/DD/HH/`, por hora de llegada. Errores en `errors/!{firehose:error-output-type}/`. El cargador escanea solo `records/` para Parquet. |

La conversión de formato requiere un búfer de al menos 64 MiB. El cargador limita sus archivos a 32 MiB y 50 000 filas. El intervalo de 60 s permite ensayar con pocas máquinas. Para evaluar mayor caudal hay que medir tamaño físico, tamaño descomprimido y filas por objeto. Firehose factura la ingesta y la conversión. S3 factura las solicitudes y el almacenamiento, incluidas las versiones retenidas. [Conversión en Firehose](https://docs.aws.amazon.com/firehose/latest/dev/enable-record-format-conversion.html) · [Precios de Firehose](https://aws.amazon.com/firehose/pricing/) · [Precios de S3](https://aws.amazon.com/s3/pricing/)

## Preparar el bucket tras autorizar su alta

El laboratorio denegó `s3:GetBucketObjectLockConfiguration` al recurso Terraform general `aws_s3_bucket` durante el bootstrap del estado. Para datos se habilita fuera de Terraform un bucket de propósito general dedicado. El módulo comprueba el ARN exacto, las cuatro etiquetas y la región antes de administrar sus controles. El nombre pertenece al espacio regional de la cuenta. S3 devuelve 409 si ya existe, también en Virginia. [Antecedente del laboratorio](../README.md) · [Espacios de nombres de S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/gpbucketnamespaces.html)

Este bloque de PowerShell **crea el bucket y le asigna etiquetas**. Requiere la cuenta de Academy ya comprobada en `TF_VAR_expected_account_id`, AWS CLI con soporte de `--bucket-namespace` y autorización para estas dos escrituras. Ante una colisión o respuesta de creación ambigua, el bloque se detiene antes del etiquetado.

```powershell
$accountId = $env:TF_VAR_expected_account_id
if ($accountId -notmatch '^\d{12}$') { throw 'Falta el ID de cuenta aprobado' }
$awsOptions = @('--profile', 'default', '--region', 'us-east-1', '--no-cli-pager')
$activeId = aws sts get-caller-identity @awsOptions --query Account --output text
if ($LASTEXITCODE -ne 0 -or $activeId -ne $accountId) { throw 'Cuenta de Academy incorrecta' }
$dataBucket = "af-dev-arquifusion-lake-${accountId}-us-east-1-an"
aws s3api create-bucket @awsOptions --bucket $dataBucket --bucket-namespace account-regional
if ($LASTEXITCODE -ne 0) { throw 'Revisar el resultado de CreateBucket antes de continuar' }
$lakeTags = 'TagSet=[{Key=project,Value=arquifusion},{Key=env,Value=dev},{Key=owner,Value=rol1},{Key=purpose,Value=telemetry-lake}]'
aws s3api put-bucket-tagging @awsOptions --bucket $dataBucket --expected-bucket-owner $accountId --tagging $lakeTags
if ($LASTEXITCODE -ne 0) { throw 'Bucket recién creado, etiquetado pendiente de conciliación' }
```

Si la sesión se interrumpe tras el alta, conciliar el `CreateBucket` de esta operación y comprobar propietario, región, etiquetas y uso antes de completar la fase. En un bucket preaprovisionado, exigir la misma identidad y dedicación. El bucket de estado tiene otro nombre y queda fuera del módulo.

## Revisar el plan

Usar la configuración, el backend y los paquetes de Lambda del despliegue actual. Comprobar con `glue get-database` y `firehose describe-delivery-stream` el inventario de los dos nombres antes de la primera alta. Una colisión fuera del estado requiere identificar al propietario. La lectura de etiquetas del módulo utiliza `tag:GetResources` acotada al ARN dedicado. [API de etiquetas](https://docs.aws.amazon.com/cli/latest/reference/resourcegroupstaggingapi/get-resources.html)

```powershell
terraform -chdir=infra/aws init -input=false -backend-config=backend.hcl
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws plan -input=false -lock=false -var=enable_telemetry_lake=true
```

Este plan de revisión usa el estado remoto sin escribir un objeto de bloqueo. Antes de aplicar en otra fase, regenerar el plan con el bloqueo normal y revisar todos los cambios. Para un lago nuevo esperar seis altas del módulo, tres controles S3, la base y tabla Glue y el stream Firehose. El bucket debe existir cuando se planifica. El plan habilitado se detiene si faltan el bucket, el acceso a sus etiquetas o su identidad correcta. Deshabilitar el módulo después de desplegarlo implica retirar infraestructura. El versionado y el cifrado tienen protección contra destrucción y el bloqueo público se conserva al retirar su recurso.

Los tests usan un proveedor simulado, verifican el catálogo contra el contrato, comprueban el destino y rechazan un bucket ausente o con otro propósito. Se ejecutan desde la raíz del repositorio.

```powershell
terraform -chdir=infra/aws/telemetry init -backend=false -input=false
terraform -chdir=infra/aws/telemetry test
```

## Entrega a Sebastián y Alexis

La salida `telemetry_lake` expone `AF_FIREHOSE_STREAM` para el simulador en modo `AF_INGEST_MODE=direct`, y `AF_LAKE_BUCKET`, `AF_LAKE_ACCOUNT_ID` y `AF_PARQUET_PREFIX` para el cargador. En modo directo, el simulador también requiere `AF_HEALTH_EVALUATOR_FUNCTION`. Publica un JSON terminado en salto de línea por elemento de `PutRecordBatch`. Ante fallos individuales comprueba `FailedPutCount` y las respuestas en orden, y reenvía solo los registros rechazados, conservando sus bytes y su `messageId`. Los reintentos de Firehose se producen después de la entrega al evaluador. Una respuesta ambigua requiere conciliar los identificadores enviados y archivados. Un error de conversión se investiga bajo `errors/`. [Simulador](../../../services/simulator/handler.py) · [Carga a BigQuery](../../../services/lake_to_bigquery/README.md) · [API de PutRecordBatch](https://docs.aws.amazon.com/firehose/latest/APIReference/API_PutRecordBatch.html)

El 15 de noviembre de 2026, el ensayo temporal `20261115t065444z-aa776cd1f0` en Academy pasó una lectura sintética por `PutRecord`. Se descargó un Parquet de 3 576 bytes con SHA-256 `5933d192461941b94e72477d50e775caa965c71bcd4f7cd33c0a75356170e9c2`. Las once columnas y sus valores coincidieron al comparar nombres sin distinguir mayúsculas, incluido `pressureBar=null` y `loadTons=0`. El archivo tenía nombres superiores en minúscula, campos internos camelCase y timestamp físico INT96 sin zona. El contrato de origen exige UTC con `Z`. Se verificó la retirada del stream, catálogo, objeto y bucket del ensayo.

La infraestructura permanente requiere su propio plan y despliegue autorizado. El alta en el espacio regional, su etiquetado y la lectura de esas etiquetas se comprueban en ese bucket. El ensayo temporal usó un bucket con nombre global. Quedan pendientes la prueba desde el simulador con `PutRecordBatch` y la carga del mismo `messageId` en la tabla real de BigQuery.
