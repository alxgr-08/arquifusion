# af-dev-lake-to-bigquery

El simulador publica su muestra original en Firehose en modo `direct`. Glue define el esquema que Firehose usa para escribir Parquet Snappy en S3. Este cargador recibe esos archivos y los hechos de incidentes y repuestos que archiva DynamoDB Streams. Los destinos del equipo son `arquifusion.af_lake.telemetry`, `incidents` y `parts_movements` en `US`. [Simulador](../simulator/README.md) · [Exportador](../stream_exporter/README.md) · [DDL analítico](../../analytics/sql/00_setup.sql)

## De Parquet a la tabla de Alexis

1. Para cada `.parquet` del prefijo configurado, lee de S3 con el ETag listado y limita el archivo a 32 MiB. Envía los **bytes originales** con `load_table_from_file` y `SourceFormat.PARQUET`. Un job con ID determinista carga una tabla exclusiva del objeto en un dataset de staging autorizado. Usa `CREATE_IF_NEEDED` y `WRITE_EMPTY` solo allí. BigQuery interpreta INT96 como TIMESTAMP. [Mapeo de tipos Parquet de BigQuery](https://docs.cloud.google.com/bigquery/docs/loading-data-cloud-storage-parquet#parquet_conversions)
2. Lee las filas tipadas mediante la API REST paginada de BigQuery. Mapea los nombres físicos al camelCase del contrato, comprueba el instante UTC y valida los campos y estructuras. Conserva sensores nulos y valores cero. Dos versiones distintas del mismo `messageId` en un archivo detienen su publicación. El límite local es de 50 000 filas y 32 MiB de filas JSON canónicas por archivo. Ordena las filas por identidad y publica lotes durables de hasta 250 bajo `arquifusion/bridge/v1/data/telemetry/` y sus avisos `pending/`. El recibo `parquet/` acredita la preparación de todos los lotes.
3. Cada lote reserva sus `messageId` con escrituras condicionales en S3. Una consulta SELECT parametrizada busca en la tabla final las identidades de ese lote, incluidas las que ya estaban allí antes del puente. Un mensaje existente con el mismo contenido se cuenta como duplicado. Una discrepancia o varias filas preexistentes para un mismo ID dejan el lote pendiente de conciliación. El puente fija la selección en `plans/telemetry/` **antes** de enviar el job final. Esta lectura puede escanear la tabla para detectar el mismo ID en cualquier fecha. [Parámetros de consultas](https://docs.cloud.google.com/bigquery/docs/parameterized-queries)
4. Carga únicamente los hechos seleccionados mediante un batch NDJSON con `CREATE_NEVER`, `WRITE_APPEND`, cero registros inválidos y el esquema existente. Este segundo job permite cargar un subconjunto de un Parquet con IDs repetidos y conservar las columnas REQUIRED, la partición y el clustering de `af_lake.telemetry`. El archivo S3 original permanece disponible. La Lambda usa el cliente BigQuery sin PyArrow ni pandas. El sandbox admite cargas batch y consultas SELECT. [Cargas batch](https://docs.cloud.google.com/bigquery/docs/batch-loading-data) · [Límites del sandbox](https://docs.cloud.google.com/bigquery/docs/sandbox)

## Recuperación

- Se guardan bajo `arquifusion/bridge/v1/control/<proyecto>/` los propietarios por identidad (`facts/`), planes, intentos (`jobs/`), recibos finales (`done/`), recepciones de Parquet y cursores. Cada aviso NDJSON verifica grupo, SHA-256 y conteo. Un recibo final registra `loadedRows`, `duplicateOrDelegatedRows` y, cuando corresponde, `alreadyPresentRows` como parte del segundo conteo.
- Si se pierde una respuesta o se interrumpe el checkpoint, el cargador recupera el job por su ID en `US` y comprueba tabla, formato, disposiciones y conteo. Otro ID solo se usa tras un error transitorio confirmado del job atómico. Un intento antiguo de append sin metadatos se concilia antes de reanudar. Se conservan los IDs de los jobs NDJSON ya iniciados por la versión anterior.
- Con archivos o invocaciones simultáneos, la primera reserva condicional determina qué lote puede cargar cada hecho. El aviso del propietario existe antes de la reserva. Los demás lotes pueden cerrar como delegados aunque el propietario aún esté pendiente. Un conflicto detiene al propietario hasta su revisión. Conservar controles y archivos fuente durante todo el período de repetición. El puente debe ser el único escritor concurrente de esos IDs en la tabla final. Un escritor externo que inserte entre el SELECT y el append no participa en la reserva.
- Cada tabla de staging hereda la expiración predeterminada del dataset. La generación de su nombre cambia por ventanas de hasta un día, acotadas por esa expiración. Si caduca la tabla entre invocaciones se puede cargar de nuevo desde el archivo S3 retenido. Al completar la preparación, las repeticiones del mismo objeto y ETag usan el recibo y evitan otra carga de staging.
- Un escaneo limitado por fuente vuelve periódicamente al principio para recoger archivos tardíos o fallidos. Para recuperar un aviso NDJSON existente, el evento manual es `{"replay":{"kind":"incidents","batch":"<sha256 del NDJSON>"}}`. También se aceptan los lotes de telemetría ya archivados por versiones anteriores. El recibo `done/` se escribe tras comprobar el append y precede al borrado del aviso.

## Configuración para el ensayo autorizado

| Variable | Valor |
| --- | --- |
| `AF_LAKE_BUCKET`, `AF_LAKE_ACCOUNT_ID` | Bucket privado de datos del proyecto en `us-east-1` y cuenta propietaria. Se comprueban región, etiquetas `project=arquifusion`, `env=dev`, `purpose=telemetry-lake` y `owner=rol1` y `ExpectedBucketOwner`. |
| `GCP_PROJECT_ID` | `arquifusion`. La entrada de Lambda rechaza otro proyecto antes de crear el cliente federado. Los tres destinos finales de `af_lake` deben existir en `US`. |
| `AF_PARQUET_PREFIX` | Prefijo de telemetría Firehose terminado en `/`, separado del archivo del puente y de los errores de conversión. Omitirlo habilita únicamente el drenaje de manifiestos. |
| `AF_BQ_STAGING_DATASET` | Obligatorio al activar Parquet. Dataset previo, privado y exclusivo del puente, en `US`, con expiración predeterminada de tablas. Ejemplo propuesto `af_bridge_stage`. |
| `GOOGLE_APPLICATION_CREDENTIALS` | En Lambda, `/opt/arquifusion/gcp-wif.json` en una capa de configuración aprobada y de procedencia controlada. Se carga explícitamente como `google.auth.aws.Credentials`. |
| `AF_LAKE_PAGE_SIZE` | De 1 a 100 objetos por fuente e invocación. Predeterminado 20. |

**Identidad.** El descriptor WIF de la capa corresponde al proveedor y, si aplica, a la cuenta de servicio autorizados para ArquiFusion. Antes del ensayo, verificar audiencia, endpoints STS oficiales y condición de atributos del proveedor. Restringir la concesión a la identidad AWS que pueda distinguirse realmente para este proyecto. El `LabRole` compartido con otras aplicaciones del laboratorio por sí solo no demuestra ese aislamiento. La ruta WIF sigue condicionada a que Alexis y Leonardo dispongan de esta confianza efectiva y del descriptor aprobado. Las pruebas locales usan dobles y credenciales sintéticas de Moto. [Federación de AWS a Google Cloud](https://cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds)

**Acceso mínimo que hay que comprobar.** En S3, consultar región y etiquetas, listar el prefijo Firehose y los pendientes, leer los objetos originales, archivo y controles, escribir lotes, avisos y controles y acotar `DeleteObject` a `pending/`. En BigQuery, crear y consultar jobs propios, leer metadatos del dataset de staging, crear sus tablas, cargar datos y leerlos. Para los destinos finales se usan lectura de metadatos, lectura de filas de telemetría y append sobre las tres tablas existentes. Revisar las comprobaciones de permisos de jobs de carga con la identidad acordada. Todas las escrituras de preparación y destino cuentan para las cuotas del proyecto. [Permisos de carga](https://docs.cloud.google.com/bigquery/docs/batch-loading-data#permissions) · [Expiración de tablas](https://docs.cloud.google.com/bigquery/docs/managing-tables#update-table-expiration)

## Reproducir en local

Desde la raíz del checkout con Python 3.12 y `requirements-dev.txt` instalados, ejecutar

```powershell
python -m pytest -q --disable-socket services/stream_exporter/tests services/lake_to_bigquery/tests
```

La muestra de 3576 bytes en `tests/firehose-syn-9001.parquet` proviene del ensayo sintético de Firehose/Glue de Academy del 15 de noviembre de 2026. SHA-256 `5933d192461941b94e72477d50e775caa965c71bcd4f7cd33c0a75356170e9c2`. Tiene una lectura de `SYN-9001`, timestamp `2026-11-15T06:58:05.139Z`, `pressureBar=null` y `loadTons=0`. El doble BigQuery decodifica el Parquet solo en las pruebas y comprueba la igualdad byte a byte del archivo recibido. Se cubren reintentos, caída tras el append, expiración de staging, archivos con mezcla de IDs repetidos y nuevos, errores de esquema y datos preexistentes.

## Empaquetado Linux x86_64

El constructor compartido genera `build/bridge/lake-to-bigquery.zip` y `build/bridge/stream-exporter.zip` con las ruedas fijadas por SHA-256 en `requirements-linux.lock` de ambos servicios. Acepta `--wheelhouse DIRECTORIO` para construir sin red con las ruedas ya descargadas. El smoke extrae cada ZIP e importa los handlers y el contrato con Python 3.12 Linux x86_64, con el intérprete aislado de los paquetes del host.

```sh
python -m services.lake_to_bigquery.build
python -m services.lake_to_bigquery.build --smoke
```

Para comprobar las fuentes de ambos ZIP en Windows se puede ejecutar `python -m services.lake_to_bigquery.build --smoke-sources` con el entorno de pruebas. Importa el código del artefacto y usa las dependencias compiladas para el host. El smoke completo de las ruedas Linux se ejecuta con `--smoke` en la plataforma destino.

El handler del cargador es `services.lake_to_bigquery.handler.handler`. La capa WIF se configura por separado. El smoke de importación se ejecuta sin credenciales.

**Validación del 15 de noviembre de 2026**, sobre la base `53f9755`. Pasaron 46 pruebas del cargador y exportador con sockets deshabilitados. Una de ellas intercepta el transporte HTTP del cliente oficial BigQuery y comprueba los bytes y las opciones del multipart de carga. La interpretación de los valores por el backend BigQuery todavía se ensaya con un doble. La prueba de identidad rechaza un proyecto vacío o ajeno antes de acceder al descriptor federado.

El constructor imprime el SHA-256 y los tamaños comprimido y expandido de cada ZIP. Para la revisión de despliegue, conservar esa salida junto al plan que referencia el paquete. Una modificación del código o de sus dependencias requiere reconstruir el ZIP y volver a revisar su huella.

Se comprobaron los CRC, el formato ELF x86_64 de cada biblioteca nativa y el smoke de fuentes con las dependencias de Windows. Quedan por ejecutar el smoke nativo de Linux y el ensayo autorizado de staging, append y recibo en GCP. La entrada del cargador está acotada a los tamaños descritos arriba. La configuración de Firehose amortigua hasta 64 MiB o 60 s. Si el tráfico crece, medir el tamaño real de los archivos y el número de filas antes de ampliar el volumen de la demo.
