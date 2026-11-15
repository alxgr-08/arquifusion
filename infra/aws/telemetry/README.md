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

El puente permanente queda fuera de la demo de Academy. Para un ensayo futuro con el lago instalado y el envío autorizado, `python infra/aws/telemetry/run_case.py $caseId --send-firehose-only --account-id 975050277936` emite **un `firehose:PutRecord` desde la VM autorizada del operador en el curso, mediante Python y Boto3**. La muestra viaja directamente a `af-dev-telemetry` para convertirla a Parquet. `--send` se retiró y finaliza con error antes de establecer una sesión AWS. La comprobación offline sigue disponible sin opciones. Las pruebas de este repositorio se ejecutan localmente con sockets deshabilitados.

El perfil `default` en `us-east-1` necesita `sts:GetCallerIdentity`, `firehose:DescribeDeliveryStream`, `firehose:ListTagsForDeliveryStream`, `s3:GetBucketTagging`, `s3:GetBucketNotification` y `dynamodb:Query` de la máquina sintética. El único permiso de escritura del comando es `firehose:PutRecord`. Comprueba la sesión Academy, propietario, etiquetas, ARN exacto del bucket dedicado, esquema Glue y formato, ausencia de procesamiento previo o respaldo activo en Firehose, ausencia de notificaciones del bucket y partición de máquina libre. Antes del envío, `dispatch.jsonl` reserva el intento y registra el `messageId` y la huella de los bytes enviados. Un `RecordId` acredita aceptación. Para demostrar entrega, verificar el Parquet en `records/` con el mismo mensaje. Ante un timeout, conservar el diario y revisar `records/` y `errors/`. El comando bloquea una segunda ejecución del caso, incluidos los diarios de la versión anterior. La ingesta directa no crea `READING#` ni `PROFILE` en DynamoDB.

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

El laboratorio denegó `s3:GetBucketObjectLockConfiguration` al recurso Terraform general `aws_s3_bucket` durante el bootstrap del estado. La lectura de ese recurso en el proveedor fijado `6.73.0` todavía consulta Object Lock y propaga AccessDenied en la partición AWS. Para datos se prepara fuera de Terraform un bucket de propósito general dedicado. El módulo comprueba el ARN exacto, las cuatro etiquetas y la región antes de administrar sus controles. El nombre pertenece al espacio regional de la cuenta. S3 devuelve 409 si ya existe, también en Virginia. [Antecedente del laboratorio](../README.md) · [Código del proveedor](https://github.com/hashicorp/terraform-provider-aws/blob/v6.73.0/internal/service/s3/bucket.go#L1117) · [Espacios de nombres de S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/gpbucketnamespaces.html)

`prepare_bucket.py` muestra por defecto una propuesta **offline**, sin construir un cliente ni consultar credenciales. `--check` consulta STS y HeadBucket y comprueba, mediante un skeleton local, que AWS CLI soporta `BucketNamespace`. La cuenta está fijada a la Academy verificada y el nombre jamás coincide con el bucket de estado. Un bucket existente, un 403 o una respuesta ambigua detienen el proceso antes de cualquier escritura. El SHA siguiente identifica la propuesta de dos escrituras; no es un plan Terraform ni demuestra permiso de creación.

```powershell
python infra/aws/telemetry/prepare_bucket.py --account-id 975050277936
python infra/aws/telemetry/prepare_bucket.py --account-id 975050277936 --check
```

El 15/11 la comprobación de lectura pasó: sesión Academy correcta, CLI `2.40.2` con la opción regional y bucket ausente. El SHA de la propuesta es `FAD523CA53AD959C9003E17213F6A94E233A09AAB6260452BF7D253056927894`. La creación en ese namespace dentro de Academy queda **NO_PROBADA**; el ensayo temporal anterior usó un bucket global. No se hizo ninguna escritura en esta preparación.

El siguiente comando **escribe en AWS** y queda pendiente de aprobación expresa del alcance: crear exactamente ese bucket y asignarle sus cuatro etiquetas. No ejecutarlo durante una revisión. El script vuelve a comprobar la ausencia, usa un intento por operación y conserva un diario local exclusivo antes de empezar. Después del alta exige región, los cuatro bloqueos públicos, AES256 y ACLs deshabilitadas antes de etiquetar; luego verifica las etiquetas y su consulta por Terraform. No activa ABAC ni crea políticas, roles o recursos multirregión.

```powershell
# SOLO después de aprobar estas dos escrituras y el SHA de la propuesta:
python infra/aws/telemetry/prepare_bucket.py --account-id 975050277936 --create --approved-sha256 FAD523CA53AD959C9003E17213F6A94E233A09AAB6260452BF7D253056927894 --journal build/lake-bootstrap/create-bucket.jsonl
```

Si se pierde una respuesta o falla una verificación, el diario impide repetir la creación automáticamente. Conciliar CloudTrail, propietario, región, controles, etiquetas y uso antes de continuar; el script no adopta, retaggea ni elimina recursos preexistentes. Una demora en la propagación de etiquetas requiere repetir solamente su lectura y regenerar el plan. Si Academy deniega el namespace o una lectura necesaria, detenerse y revisar la alternativa; no cambiar a un bucket ajeno o al de estado.

## Revisar el plan

Usar la configuración, el backend y los paquetes de Lambda del despliegue actual. Comprobar con `glue get-database` y `firehose describe-delivery-stream` el inventario de los dos nombres antes de la primera alta. Una colisión fuera del estado requiere identificar al propietario. La lectura de etiquetas del módulo utiliza `tag:GetResources` acotada al ARN dedicado. [API de etiquetas](https://docs.aws.amazon.com/cli/latest/reference/resourcegroupstaggingapi/get-resources.html)

```powershell
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws plan -input=false -lock=false -var-file=telemetry/lake-review.tfvars.example -out=../../build/lake-review.tfplan
```

El backend de la aplicación ya está inicializado; no reinicializarlo ni migrarlo para este paso. El [perfil de revisión](lake-review.tfvars.example) conserva el hosting y parte de los **29 recursos existentes**, antes de desplegar la API. Si se aplica antes otra etapa, reconciliar también sus variables y regenerar todos los planes posteriores. Este plan de revisión usa el estado remoto sin escribir un objeto de bloqueo. Antes de aplicar en otra fase, revisar un plan recién generado con todos los recursos previos en no-op y utilizar el bloqueo normal para la aplicación. Para un lago nuevo se esperan seis altas del módulo: tres controles S3, la base y tabla Glue y el stream Firehose. El bucket debe existir cuando se planifica. **Todavía no existe un plan real aplicable del lago**: el bucket ausente bloquea su precondición. Los seis recursos se validan con el proveedor simulado; ese resultado no acredita un despliegue.

Deshabilitar el módulo después de desplegarlo implica retirar infraestructura. El versionado y el cifrado tienen protección contra destrucción y el bloqueo público se conserva al retirar su recurso.

Los tests usan un proveedor simulado, verifican el catálogo contra el contrato, comprueban el destino y rechazan un bucket ausente o con otro propósito. Se ejecutan desde la raíz del repositorio.

```powershell
terraform -chdir=infra/aws/telemetry init -backend=false -input=false
terraform -chdir=infra/aws/telemetry test
```

## Entrega a Sebastián y Alexis

`AF_FIREHOSE_STREAM` identifica el stream del módulo y `AF_LAKE_BUCKET`, `AF_LAKE_ACCOUNT_ID` y `AF_PARQUET_PREFIX` identifican el destino del cargador. El generador local usa el simulador con clientes de prueba para congelar una muestra. El helper de Academy envía únicamente su NDJSON directamente a Firehose. El simulador en modo `direct` con clientes AWS reales también invocaría al evaluador y queda excluido de este ensayo. [Simulador](../../../services/simulator/handler.py) · [Carga a BigQuery](../../../services/lake_to_bigquery/README.md)

El 15 de noviembre de 2026, el ensayo temporal `20261115t065444z-aa776cd1f0` en Academy pasó una lectura sintética por `PutRecord`. Se descargó un Parquet de 3 576 bytes con SHA-256 `5933d192461941b94e72477d50e775caa965c71bcd4f7cd33c0a75356170e9c2`. Las once columnas y sus valores coincidieron al comparar nombres sin distinguir mayúsculas, incluido `pressureBar=null` y `loadTons=0`. El archivo tenía nombres superiores en minúscula, campos internos camelCase y timestamp físico INT96 sin zona. El contrato de origen exige UTC con `Z`. Se verificó la retirada del stream, catálogo, objeto y bucket del ensayo.

La infraestructura permanente requiere su propio plan y despliegue autorizado. El alta en el espacio regional, su etiquetado y la lectura de esas etiquetas se comprueban en ese bucket. El ensayo temporal usó un bucket con nombre global. Quedan pendientes la entrega al lago permanente desde el equipo del operador y la carga del mismo `messageId` en la tabla real de BigQuery.
