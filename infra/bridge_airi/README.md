# Puente de telemetría de ArquiFusion

El productor de la operación `AF-BRIDGE-PERU` envía las lecturas con Python y Boto3 a `af-dev-telemetry` en AWS Academy. Glue aporta el esquema a Firehose, que escribe Parquet Snappy en el prefijo privado `records/`. S3 notifica cada objeto a SQS. La lectura completa se conserva en el archivo.

En Google Cloud, Cloud Scheduler invoca cada hora al trabajo privado de Cloud Run `airi-erp/us-east1/af-bridge`. Una sola tarea recoge hasta diez entregas por ejecución. Si la cola está vacía, termina. El trabajo usa su propia cuenta de servicio para BigQuery y presenta un ID token de Google a la API de máquina a máquina de Academy. La Lambda autorizadora valida firma, emisor, audiencia, vigencia y el identificador numérico de esa cuenta. La API conecta directamente con SQS, S3 y DynamoDB. Ninguna lectura ni fila de KPI pasa por la Lambda autorizadora.

El trabajo descarga el objeto por rangos sujetos al ETag y envía sus bytes a BigQuery con `load_table_from_file`. El identificador del trabajo de carga incluye el SHA-256 del archivo. El dataset `airi-erp.af_bridge` está en `US`. La tabla `telemetry_raw` conserva las particiones 31 días. La vista `telemetry` identifica las lecturas por operación, máquina y `messageId`. La vista `daily_kpis` calcula los últimos 30 días con las fórmulas del equipo. El trabajo devuelve el KPI por máquina y día UTC a `af-dev-arquifusion`, y elimina el mensaje de SQS después de comprobar el resultado mediante una lectura consistente. La transacción verifica la operación del perfil y la cantidad de lecturas ya representadas. Tras cinco recepciones fallidas, SQS conserva el mensaje en `af-dev-airi-dead`.

La operación inscrita tiene perfiles con identificadores `BRG-####`. Para incorporar otra operación hay que coordinar la selección de flota en la vista, las reglas de autorización de la integración y los perfiles en DynamoDB. El caso de vídeo `ENS-0001`, los incidentes y consumos procedentes de Streams, y los modelos históricos de Alexis en `arquifusion` tienen ciclos de vida independientes. La regla de incidentes no interviene en el puente.

## Operar y revisar

- Para ver los siguientes disparos y los resultados del trabajo, ejecutar `python infra/bridge_airi/observe.py status` y `python infra/bridge_airi/observe.py logs` con una sesión de operador autorizada de `airi-erp`. La transferencia programada utiliza la identidad del trabajo en la nube.
- Para consultar un KPI, buscar `BRG-8115` en `airi-erp.af_bridge.daily_kpis` y leer `MACHINE#BRG-8115 / KPI#AAAA-MM-DD` en la tabla de Academy. `sourceMessageId` y `sourceReadings` permiten contrastar la fila con BigQuery.
- Un ensayo acotado usa `smoke.py` para preparar dos lecturas, enviar cada una a Firehose y verificar ambos objetos. La acción `replay` reenvía exclusivamente la notificación del primer objeto. Los marcadores locales en `build/bridge-permanent/smoke/` evitan repetir un envío por error.
- Ante una acumulación de mensajes, revisar primero el historial de Cloud Run y la cola de fallos. Los trabajos de carga recuperables y la vista por `messageId` permiten reprocesar el archivo original. La pausa del programador conserva los mensajes de origen hasta el límite de retención de SQS, que es de 14 días.

## Infraestructura y paquetes

Terraform usa la clave independiente `bridge/airi-erp/terraform.tfstate` en el bucket privado de estado del equipo. El bucket del lago se prepara con `infra/aws/telemetry/prepare_bucket.py`. La raíz del puente reutiliza el módulo regional de telemetría. Para revisar un despliegue existente desde la raíz del repositorio se generan los archivos derivados antes de ejecutar Terraform.

```powershell
python infra/bridge_airi/package_auth.py
python infra/bridge_airi/gateway.py
python infra/bridge_airi/warehouse.py
terraform -chdir=infra/bridge_airi init
terraform -chdir=infra/bridge_airi validate
```

La raíz utiliza de forma predeterminada el digest de la imagen desplegada. Para preparar una actualización, `build_image.py` envía a Cloud Build el Dockerfile, las dependencias y el archivo del trabajador. Usar el digest devuelto en `TF_VAR_image_digest` para revisar el plan y registrar el digest publicado en `main.tf`. La primera instalación utilizó una fase de creación del repositorio privado y otra de despliegue de la imagen. Un valor vacío solicita retirar el trabajo y el programador de un estado activo. El servicio de construcción solo puede publicar imágenes en el repositorio del puente.

[Programar trabajos privados en Cloud Run](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule) · [Identidad del servicio](https://docs.cloud.google.com/run/docs/securing/service-identity) · [Ensayo previo con un operador](../../docs/evidencias/2026-11-15-puente-airi.json)
