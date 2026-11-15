# BigQuery de ArquiFusion

Alexis confirmó el proyecto `arquifusion` en BigQuery Sandbox, ubicación `US`, y reportó que ejecutó los seis scripts analíticos con datos sintéticos. El DDL y la analítica le pertenecen a Rol 3. El inventario del 15/11 documenta lecturas de los tres datasets, tres modelos y nueve tablas de salida. Para repetirlas se necesita una identidad lectora propia del proyecto. [Arquitectura](../../docs/arquitectura.md) El ensayo Firehose de Academy produjo un Parquet Snappy válido. La carga de ese objeto y el retorno de resultados a DynamoDB requieren su propio ensayo. [Respuesta de Alexis](https://github.com/alxgr-08/arquifusion/issues/41#issuecomment-6890758659) · [PR de resultados](https://github.com/alxgr-08/arquifusion/pull/48)

## Comprobar los recursos del equipo

`python infra/gcp/check_bigquery.py arquifusion` usa una instalación autenticada de `gcloud` y `bq` para consultar metadatos del proyecto, API, datasets, tablas y modelos conocidos. Al iniciar la preparación faltaban ambos comandos en PATH. Se preparó una copia portátil oficial de Google Cloud CLI 593.0.0 en el equipo de Leonardo, sin modificar PATH global ni iniciar sesión. Activar su carpeta bin solo en la sesión de ensayo y autenticar personalmente al operador autorizado antes de comprobar el proyecto. [Uso de la CLI portátil](cli-portable.md) Si ya existen `af_lake`, `af_ml` o `af_out`, coordinar cualquier gestión de su ciclo de vida con Alexis. La raíz Terraform solo define el dataset temporal optativo del puente.

Para comprobar también las precondiciones del puente, usar `python infra/gcp/check_bigquery.py arquifusion --bridge`. Incluye `af_bridge_stage` en US con vencimiento positivo de tablas y las tablas de clúster/tendencia usadas por el retorno seleccionado. Esta comprobación solo lee metadatos; no demuestra permisos para cargar ni ejecutar jobs. Alexis [confirmó que staging ya existe en US con TTL de 24 horas](https://github.com/alxgr-08/arquifusion/pull/50#issuecomment-6897355061). El [perfil de revisión](staging-review.tfvars.example) mantiene su creación deshabilitada para un estado que no lo gestiona. No importarlo ni adoptar su ciclo de vida sin coordinación y autorización; si otro estado ya lo administra, reconciliarlo antes de cambiar sus opciones. [Cierre y paquete actual del puente](cierre-puente-2026-11-15.md)

## Tablas temporales del cargador

El productor es el simulador en modo `direct`. Firehose usa Glue para convertir la lectura en Parquet. El cargador envía el archivo original a BigQuery y valida el contrato antes de añadir filas a `af_lake.telemetry`. `af_bridge_stage` en US aloja las tablas intermedias con vencimiento predeterminado de 24 horas, según la confirmación actual de Alexis. El archivo de AWS y los recibos permiten recuperar reintentos. La raíz GCP conserva una opción explícita de alta `enable_bridge_staging=true` para un destino nuevo previamente acordado; no se debe activar sobre el staging existente sin reconciliar su administración. Nunca se deben recrear los datasets de Alexis como parte del ensayo. [Cargador](../../services/lake_to_bigquery/README.md) · [Configuración de staging](bridge_staging.tf)

## Identidad entre nubes

Para el cargador se propone federación temporal desde AWS. El proyecto GCP debe autorizar un principal que distinga a ArquiFusion de los demás proyectos que también usan `LabRole`. Revisar con el administrador cómo se restringe la confianza y quién puede asumir ese rol. El nombre genérico de rol por sí solo es insuficiente. Confirmar los cuatro identificadores públicos del proveedor y la cuenta de servicio, permiso de jobs en el proyecto y acceso a los datasets concretos. La capa del cargador contiene solo el descriptor público. El retorno manual de un KPI reutiliza este descriptor y consulta con parámetros las lecturas de la máquina y fecha en `af_lake.telemetry` y las tablas concretas `af_out.daily_kpis`, `af_out.machine_clusters` y `af_out.fuel_trends`. Verificar estos permisos de lectura con Alexis. La autenticación por Secrets Manager requiere habilitar expresamente la entrada de compatibilidad del proceso masivo. [Federación con AWS](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds) · [Consumidor de resultados](../../services/bigquery_sync_back/README.md)

## Evidencia de una ida y vuelta

1. Usar un lote sintético nuevo. Registrar los `messageId`, objeto Parquet, SHA-256, ubicación y conteo de filas.
2. Confirmar job de carga en `US`, IDs de las filas en la tabla final y repetición del lote sin duplicados.
3. Ejecutar con Alexis una consulta o modelo acotado y verificar el registro resultante en la tabla de salida.
4. Ejecutar un único retorno sobre datos acordados y verificar la fila esperada de DynamoDB sin sobrescribir perfiles o líneas base existentes.

Para validaciones offline, usar `terraform -chdir=infra/gcp init -backend=false`, `terraform -chdir=infra/gcp validate` y `terraform -chdir=infra/gcp test`. La clave de un futuro backend GCP será `gcp/terraform.tfstate`, separada de `aws/terraform.tfstate`.

## Ensayo manual con una identidad humana

Si la institución no puede distinguir una carga de ArquiFusion dentro del `LabRole` compartido, el operador puede mantener dos sesiones independientes y autorizadas en su equipo. La herramienta usa el perfil de Academy `default` y las credenciales de aplicación de su propia cuenta Google. El administrador concede al operador únicamente los permisos del ensayo en `arquifusion`. El script rechaza una ruta explícita de credenciales de Google y verifica que el tipo de identidad sea de usuario. El proyecto que recibe los jobs y la cuota es `arquifusion`. Las Lambdas conservan su configuración de federación. [Autenticación local oficial](https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment) · [Herramienta](rehearse_object.py)

1. Después de una recepción real, guardar el archivo de `records/YYYY/MM/DD/HH/` y el `reading.json` original del simulador. El perfil que opera en ambas nubes debe estar autorizado para ese caso. En el equipo de desarrollo instalar las dependencias de `requirements-dev.txt`. PyArrow se utiliza únicamente para inspección local.
2. Ejecutar la comprobación sin red. Exige un Parquet de una fila, la igualdad de todos los campos con la lectura y una máquina de ensayo reconocida.

   ```sh
   python -m infra.gcp.rehearse_object inspect --parquet muestra.parquet --reading reading.json
   ```

3. Para una ventana autorizada de escritura, el operador habilita personalmente su ADC de Google con `gcloud auth application-default login`. Se necesitan permisos de jobs en el proyecto, edición en staging, acceso acotado al lago, lectura de las salidas y las operaciones S3/DynamoDB del ensayo. La acción `load` lleva los mismos argumentos y añade `--account <cuenta-Academy> --object <clave-S3>`. Comprueba propietario, etiquetas, región y bytes del objeto. Carga el archivo original con los mismos jobs y recibos de las Lambdas. Repetirlo debe devolver los mismos identificadores y una sola fila en el lago.
4. Alexis actualiza el KPI del día acordado y conserva el ID del job que generó el resultado. El operador ejecuta `return-kpi` con los mismos argumentos. El programa comprueba primero que exista la lectura esperada en BigQuery y usa la transacción condicional del retorno. Si el KPI ya existía y coincide, informa `already_present`. `not_found` indica que falta la salida analítica. Si la web está publicada, comprobar desde su operación que el KPI aparece en la fecha correcta.

El ensayo local de esta vía usa el Parquet real conservado y servicios simulados. La primera ejecución de carga/retorno con acceso humano en ambas nubes está pendiente. El método demuestra un recorrido manual. La automatización requiere la identidad federada aislada.
