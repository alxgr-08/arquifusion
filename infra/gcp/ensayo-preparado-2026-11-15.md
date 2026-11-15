# Puente preparado para una lectura · 15/11/2026

## Alcance comprobado en local

Se conservó la rama `infra/rol1-entrega-academy`, sobre el commit `9d2994b1d535d0a021e0e7f26874925060d42638`, con los cambios locales de API intactos. No se modificaron el código de Sebastián, el SQL/frontend de Alexis, contratos ni `bigquery_sync_back`.

La entrada prevista para un futuro ensayo de Academy es **simulador local con clientes de prueba → equipo del operador → Firehose DirectPut → S3 Parquet → BigQuery**. El puente permanente queda fuera de la demo actual. DynamoDB Streams se reserva para incidentes y consumos. El cargador usa `load_table_from_file` con los bytes originales para staging y append NDJSON de los hechos nuevos sobre las tablas finales.

La vía manual ya existente en `rehearse_object.py` permite una primera demostración con dos sesiones propias del operador: AWS Academy `default`, `us-east-1`, y acceso humano Google autorizado para `arquifusion`. No requiere inventar un proveedor WIF ni importar la llave privada descargada. La automatización Lambda sigue dependiendo de una federación aislada, pues el `LabRole` compartido no distingue por sí solo a ArquiFusion de otras aplicaciones.

## Preparado

- Tres paquetes Linux CPython 3.12 x86_64 en `build/bridge/`: exportador, cargador y retorno seleccionado de un KPI.
- `staging-review.tfvars.example`: preparación optativa de un único dataset `arquifusion.af_bridge_stage`, ubicación US, tablas con vencimiento de 24 horas. No gestiona `af_lake`, `af_ml` o `af_out`.
- `check_bigquery.py --bridge`: lectura de metadatos que incluye staging, su ubicación/TTL y las tablas `af_out.machine_clusters` y `af_out.fuel_trends` necesarias para el retorno. No envía consultas, cargas ni jobs.
- Kit offline bajo `build/readiness-20261115/bridge/`: el Parquet histórico del ensayo Academy, JSON de expectativa derivado de él e inspección verificada. Está identificado como histórico; no acredita una nueva escritura o carga cloud.
- `infra/aws/telemetry/prepare_case.py` y `run_case.py` preparan y revisan una lectura local sin red. `--send-firehose-only` habilita un intento directo desde el host después de aprobar la prueba del lago. El diario impide repetir un intento iniciado.

## Validación de esta preparación

Pasaron **68 pruebas offline** de exportador, cargador, retorno y herramientas GCP con sockets deshabilitados. Ruff y formato pasaron. `terraform fmt -check`, `validate` y los dos escenarios de staging con proveedor Google simulado pasaron. Esto no acredita una carga ni un modelo nuevo en el proyecto real.

Se construyeron dos veces los tres paquetes y se obtuvieron exactamente los mismos hashes. Se comprobaron CRC, límites de tamaño y ausencia de PyArrow/pandas:

| Paquete | SHA-256 | ZIP / expandido (bytes) |
|---|---|---:|
| `stream-exporter.zip` | `b8a9db040d2ed00d4e6cd377672646c99cd634deceec92ef48da46a05486858a` | 19 769 522 / 30 939 316 |
| `lake-to-bigquery.zip` | `2106db46bbcc08604e42cc0601316cecadacc4ccce03e0f8abebefca6b107ddf` | 35 329 764 / 76 330 291 |
| `bigquery-sync-back.zip` | `7ec3436961aeb38ebbb95c49a567aa5cda1676a2efd972ca2c4a277cf4304e1f` | 35 771 120 / 77 970 446 |

El constructor usa CPython 3.12 y ruedas Linux fijadas por SHA-256. El informe de paquetes y el resultado de importación están en el directorio local de evidencia. El smoke de fuentes Windows pasó; después la [CI publicada](https://github.com/alxgr-08/arquifusion/actions/runs/43710823184) importó nativamente los tres ZIP en Linux x86_64 / CPython 3.12 y confirmó hashes idénticos a los locales.

## Estado de las dependencias

| Dependencia | Resultado en este equipo | Efecto |
|---|---|---|
| Python 3.12 del host | Constructor disponible con pip | Puede reconstruir paquetes fijados por hashes. |
| Entorno de pruebas del worktree anterior | Dependencias disponibles; no incluye pip | Se usa para pruebas y smoke de fuentes; el build usa Python del host. |
| `gcloud` y `bq` | Al inicio faltaban en PATH; después se preparó CLI portátil oficial 593.0.0, con bq incluido, sin PATH global | Falta habilitar una sesión personal autorizada; no se consultó el proyecto real. [Activación local](cli-portable.md) |
| Sesión Google autorizada | Pendiente de habilitación y comprobación por el operador | No se leyó una llave privada ni se hicieron cargas o consultas. |
| Docker/WSL Linux | No disponibles como entorno Linux operativo local | La CI publicada comprobó importación nativa Linux de los tres ZIP; el smoke de fuentes Windows es una comprobación separada. |
| Lago permanente AWS | Requiere revisión y despliegue de su propia etapa | El bucket de estado Terraform nunca es destino de datos. |
| Staging BigQuery | Existencia, US y TTL no comprobados en cloud | El flag `--bridge` hará la comprobación con una instalación autenticada. |
| Salida del mismo caso en `af_out.daily_kpis` | Pendiente de cálculo por Alexis | Un resultado histórico de otra máquina no sirve como evidencia del retorno. |

## Comandos de preparación sin escrituras cloud

Desde la raíz del checkout, con el entorno de desarrollo:

```powershell
python -m pytest -q --disable-socket services/stream_exporter/tests services/lake_to_bigquery/tests services/bigquery_sync_back/tests infra/gcp/test_check_bigquery.py infra/gcp/test_rehearse_object.py
python -m services.lake_to_bigquery.build
python -m services.lake_to_bigquery.build --smoke-sources
python infra/gcp/check_bigquery.py arquifusion --bridge
```

El último comando es solo lectura y requiere `gcloud`/`bq` ya instalados y autenticados. `MISSING_CLI` o `ACCESS_DENIED` se registran como bloqueo, no como evidencia de inexistencia del proyecto. El build descarga ruedas desde PyPI y escribe únicamente archivos locales.

La inspección del kit histórico se puede repetir sin sesión cloud:

```powershell
python -m infra.gcp.rehearse_object inspect --parquet build/readiness-20261115/bridge/historical-fixture.parquet --reading build/readiness-20261115/bridge/historical-fixture.reading.json
```

Ese JSON se derivó del Parquet para comprobar el inspector. En el ensayo nuevo se usará obligatoriamente el **`reading.json` original que congeló el simulador antes del envío**.

## Ensayo nuevo, después de aprobar las escrituras

1. Comprobar la cuenta Academy `975050277936`, `us-east-1`, propiedad del lago, stream `af-dev-telemetry`, tabla operacional y regla de EventBridge deshabilitada. Revisar los planes contra el estado remoto vigente. El productor de esta prueba se ejecuta en el equipo del operador.
2. Preparar un caso nuevo con `prepare_case.py`, conservar su ID y verificarlo con `run_case.py <caseId>` en modo offline. Guardar máquina, operación, fecha UTC, `messageId` y huellas de `reading.json` y `firehose-record.ndjson`.
3. Tras autorizar el envío único a Firehose, ejecutar `run_case.py <caseId> --send-firehose-only --account-id 975050277936`. Conservar `dispatch.jsonl`. El `RecordId` acredita aceptación. Comprobar el objeto Parquet y la identidad de la lectura. Si se pierde la respuesta, conciliar por `messageId` antes de considerar otra acción. Esta entrada no genera `READING#` ni `PROFILE` en la tabla operacional.
4. Descargar el objeto exacto `records/YYYY/MM/DD/HH/…parquet` del bucket de datos y guardar su key/ETag/SHA-256. Ejecutar `rehearse_object inspect` con ese objeto y el `reading.json` original; debe demostrar igualdad completa, una fila y alcance sintético permitido.
5. El operador autoriza personalmente una sesión ADC de usuario Google. Verificar `check_bigquery.py arquifusion --bridge` y los permisos de proyecto/dataset. No usar el token del navegador como credencial del puente ni compartir tokens o llaves en issues.
6. Tras aprobar la carga del caso, ejecutar `python -m infra.gcp.rehearse_object load --parquet <archivo> --reading <original> --account 975050277936 --object <key-exacta>`. Conservar `stageJob`, `loadJob`, batch y recibos. Repetir con los mismos bytes y key debe recuperar los mismos jobs y una sola fila final.
7. Alexis genera el KPI del día y la máquina de la lectura y conserva el job analítico. El retorno requiere además un `PROFILE` sintético del mismo caso, con máquina, operación y modelo coincidentes. Si todavía falta, detenerse antes del retorno y coordinar una siembra independiente con su propia autorización. No iniciar ingesta Lambda para generar ese perfil. Verificar las dependencias de `af_out` y la ausencia de ambigüedad de operación o modelo en la telemetría diaria.
8. Tras aprobar el retorno único, ejecutar `python -m infra.gcp.rehearse_object return-kpi` con los mismos cuatro argumentos. Debe devolver `written` y una fila `KPI#<día>` del caso. Una repetición idéntica devuelve `already_present`. Un KPI distinto o perfil ajeno detiene la operación sin sobrescribirlo.
9. Leer el KPI por la API live con una concesión de esa operación y conservar evidencia saneada. El cierre de la prueba requiere la misma lectura en AWS, objeto S3, fila de BigQuery, job analítico y KPI operacional.

La carga y el retorno escriben controles en S3, jobs/tablas en BigQuery y el KPI de DynamoDB; esos comandos no se ejecutaron en esta preparación. Las tablas finales conservan su esquema actual. El cargador debe ser el único escritor concurrente de los mismos `messageId`; un escritor externo fuera de sus reservas S3 requiere coordinación.

## Qué debe confirmar Alexis

1. Quién es el operador Google autorizado para `arquifusion` y cómo habilita personalmente su sesión. No hace falta compartir una contraseña, refresh token o llave privada.
2. Permiso `bigquery.jobs.create` en el proyecto, metadatos/creación/carga/lectura de tablas en staging, metadatos/lectura/append sobre las tres tablas de `af_lake`, y lectura de `af_out.daily_kpis`, `machine_clusters` y `fuel_trends` para el retorno.
3. Si `af_bridge_stage` ya existe: ubicación US, TTL positivo y dedicación al puente. Si falta, revisar la alta optativa separada; la lectura de metadatos no prueba permiso de creación.
4. Cómo ejecutar el cálculo acotado al mismo caso y quién conserva el job analítico. No se modificó su SQL ni se ejecutó entrenamiento en esta preparación.

Sebastián no necesita desarrollar otro productor para esta prueba: se reutiliza su simulador en modo `direct`, ejecutado una sola vez localmente con una flota aislada. Las concesiones de la web se coordinan por separado.
