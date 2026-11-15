# af-dev-stream-exporter

Este exportador del Rol 1 consume `NEW_AND_OLD_IMAGES` de `af-dev-arquifusion` en `us-east-1`. Escribe hechos analíticos de incidentes y movimientos de repuestos en el bucket S3 privado del lago. La telemetría original del simulador viaja por Firehose, Glue y Parquet. Cualquier registro `READING#` queda excluido antes de acceder a sus imágenes, incluso cuando contiene una copia íntegra de la muestra. [Cargador de BigQuery](../lake_to_bigquery/README.md)

| Claves en el stream | Destino | Cuándo se publica |
| --- | --- | --- |
| `MACHINE#… / INCIDENT#…` | Archivo `incident_history` | Cambio de estado o de campos analíticos. Se proyectan atributos permitidos. |
| `MACHINE#… / INCIDENT#…` | Tabla `af_lake.incidents` | Cierre `RESOLVED`, GSI1 de esa misma operación en `CLOSED`, fecha de cierre y flujo sin fallo. Una identidad por clave de máquina e incidente. |
| `PART#… / STOCK#…` | Tabla `af_lake.parts_movements` | La caída de `qty` corresponde a la liquidación de una reserva de incidente y concuerdan los saldos físicos, reservados y disponibles. Una identidad por ARN del stream e ID del evento. |
| `PART#… / STOCK#…` | Archivo `stock_review` | La cantidad física disminuye sin evidencia suficiente para clasificarla como consumo correctivo. |

Reservar, liberar o reponer stock produce cero filas de demanda. El exportador ignora escrituras de concesiones, perfiles, ACK, pronósticos, lecturas, borrados TTL y actualizaciones exclusivas del token de espera. El historial de incidentes omite tokens y atributos privados del flujo.

## Archivo y recuperación

En `arquifusion/bridge/v1/data/<grupo>/<sha256>.ndjson.gz` se escribe un lote de hasta 250 entradas ordenadas. La huella corresponde al NDJSON descomprimido. Un PUT condicional reserva la clave del archivo. Para los dos grupos cargables se escribe después `pending/<grupo>/<sha256>.json`. Una interrupción entre ambas operaciones devuelve la secuencia afectada al mapping. Una repetición o la reconstrucción del manifiesto desde el archivo conservado recupera el lote.

El mapping necesita `ReportBatchItemFailures`. Ante un fallo se conservan el número de secuencia y la clase de error en los logs. Verificar antigüedad del iterador antes de que expiren las imágenes del stream, y presupuestar las ejecuciones dentro del límite compartido de diez entornos Lambda en Academy. [Fallos parciales de Streams](https://docs.aws.amazon.com/lambda/latest/dg/services-ddb-batchfailurereporting.html) · [Escrituras condicionales en S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)

## Configuración

| Variable | Valor |
| --- | --- |
| `AF_SOURCE_STREAM_ARN` | ARN exacto del stream de `af-dev-arquifusion` en la cuenta de Academy y `us-east-1`. |
| `AF_LAKE_BUCKET` | Bucket privado de datos del proyecto con etiquetas `project=arquifusion`, `env=dev`, `purpose=telemetry-lake` y `owner=rol1`. |
| `AF_LAKE_ACCOUNT_ID` | ID de la cuenta propietaria para `ExpectedBucketOwner`. |

El rol de ejecución requiere lectura del stream, escritura en sus logs, consulta de región y etiquetas del bucket y `PutObject` bajo los prefijos de archivo y manifiestos. Comprobar estos permisos cuando se autorice el mapping. El handler es `services.stream_exporter.handler.handler`. El constructor del puente genera `build/bridge/stream-exporter.zip` para Python 3.12 Linux x86_64. [Instrucciones de construcción y smoke](../lake_to_bigquery/README.md#empaquetado-linux-x86_64)

Las pruebas offline reproducen los estados de incidentes, liquidación y reposición de stock, repetición y fallos parciales. Desde la raíz del checkout ejecutar

```powershell
python -m pytest -q --disable-socket services/stream_exporter/tests services/lake_to_bigquery/tests
```
