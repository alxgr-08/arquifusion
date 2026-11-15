# ArquiFusion · Entrega de Leonardo para el equipo y GPT Pro

Corte histórico. Para el estado vigente consultar la arquitectura: [ArquiFusion](arquitectura.md).

Corte del 15 de noviembre de 2026, 03:25 de Lima. Leonardo es Rol 1, Sebastián es Rol 2 y Alexis es Rol 3. La aplicación opera en AWS Academy Learner Lab, perfil `default`, región `us-east-1`. La cuenta aloja otros proyectos y el curso limita a diez los entornos Lambda concurrentes para toda la cuenta. BigQuery y BigQuery ML están en el proyecto de Google `arquifusion`, ubicación `US`, según Alexis.

**Decisión inmediata.** Revisar el Terraform de la base ya desplegada y publicar el trabajo del Rol 1 por PR cuando Leonardo lo autorice. La infraestructura de la API se puede revisar completa con el endpoint cerrado. Antes de abrir la web, Sebastián debe corregir la autorización del GET y Alexis debe integrar `ETag/If-Match`. El trabajo del lago y BigQuery puede avanzar por separado.

## GitHub y copia local

La rama local `infra/rol1-entrega-academy` y `origin/main` apuntan a `53f97555acb9` después de `git fetch origin`, con 0 commits de diferencia. Los cambios de entrega están en el worktree `arquifusion-rol1-entrega`. La rama original `infra/rol1-aws-dynamodb-plan` sigue en `be19d3e`, siete commits por detrás. Conserva sus 37 entradas de estado Git, 58 rutas al expandir archivos nuevos. El bootstrap S3 mantiene su estado local en esa copia original. El trabajo de esta noche quedó local. [Commit de referencia](https://github.com/alxgr-08/arquifusion/commit/53f97555acb9)

| Responsable | Integraciones en main | Qué queda |
|---|---|---|
| Sebastián | #39 y #40 integraron el flujo inicial. #47 incorporó concesiones por identidad y operación. #49 añadió GET con ETag, recibo por generación de espera, certificación y cierre recuperable. Sebastián respondió en #23. | Autorizar la lectura con la operación de la fila de incidente y rechazar perfiles ausentes o inconsistentes según la política acordada. Revisar ETags débiles. Comprobar en el simulador los fallos parciales de `PutRecordBatch`. [PR #49](https://github.com/alxgr-08/arquifusion/pull/49) · [Respuesta en #23](https://github.com/alxgr-08/arquifusion/issues/23#issuecomment-6890847852) |
| Alexis | #43 retiene contexto de telemetría, #44 añade `pendingAlert`, #45 y #48 corrigen el pipeline de BigQuery Sandbox y reportan sus resultados con datos sintéticos. #46 incorpora GIS en la web. #41 proporciona cliente web, origen de desarrollo y proyecto de datos. | GET de la espera antes del POST, misma versión y cuerpo en reintentos y confirmación humana ante una espera nueva. Registrar la URL HTTPS efectiva y acordar identidad/permisos de GCP. [PR #46](https://github.com/alxgr-08/arquifusion/pull/46) · [PR #48](https://github.com/alxgr-08/arquifusion/pull/48) · [Respuesta en #41](https://github.com/alxgr-08/arquifusion/issues/41#issuecomment-6890758659) |

Se encontraron **0 PR abiertos, 15 issues abiertos y 17 cerrados**. Siguen abiertos los doce de Rol 1 (#8–19), #7 (video), #23 (ensayo de incidentes) y #41 (origen final). Están cerrados #1–6, #20–22, #24–30 y #42. Estos estados pertenecen a GitHub. El despliegue y las pruebas reales se registran por separado. [Issues abiertos](https://github.com/alxgr-08/arquifusion/issues?q=is%3Aissue%20is%3Aopen) · [PR fusionados](https://github.com/alxgr-08/arquifusion/pulls?q=is%3Apr%20is%3Amerged)

## Los doce issues de Rol 1

| Issue | Evidencia actual | Falta para darlo por entregado |
|---|---|---|
| #8 · Bootstrap | Bucket de estado exclusivo con versionado Enabled, SSE-S3 AES256 y los cuatro bloqueos públicos activos. | Incorporar al repositorio la configuración y la evidencia permitida. |
| #9 · DynamoDB | `af-dev-arquifusion` ACTIVE con GSI1/GSI2, Streams NEW_AND_OLD_IMAGES, TTL `expiresAt`, PITR y protección contra borrado. | Registrar el alcance regional de Academy y revisión por PR. |
| #10 · Ingesta | Ensayo temporal real de Firehose/Glue/S3, un Parquet válido, 11 columnas. Módulo permanente optativo preparado. | Crear el bucket de datos aprobado, probar la configuración permanente y el envío del simulador con fallos por registro. |
| #11 · Seguridad | LabRole existente, SSM y cifrados verificados para los recursos desplegados. | Identidad multicloud de mínimo privilegio. Una clave KMS propia y cualquier secreto requieren alcance y permisos específicos. |
| #12 · Incidentes | Bus, regla, destino, DLQ, dos máquinas Standard, SNS y alarmas desplegados. El flujo anterior completó un ensayo manual. | Probar el nuevo ASL y los recibos tras revisión. La regla sigue DISABLED y SNS tiene cero suscripciones confirmadas. |
| #13 · Lambda | Paquetes Python 3.12 de Health, tareas, API y puente preparados. | Actualizar la base con un plan aprobado, probar importación Linux del puente y desplegar las demás funciones solo según la demo acordada. |
| #14 · GCP | Proyecto y datasets de equipo reportados por Alexis. La plantilla crea únicamente el staging temporal opcional. | Verificación GCP por Leonardo, autorización del puente y staging. En este equipo faltan los ejecutables `gcloud` y `bq`. |
| #15 · lake_to_bigquery | Carga original en PARQUET a staging, validación del contrato y consolidación deduplicada al esquema existente. | Un job real en BigQuery con el objeto del ensayo, repetir y verificar exactamente una fila por ID. |
| #16 · stream_exporter | Proyecta incidentes y consumo de repuestos. Filtros y respuestas de fallo parcial preparados. | Ensayo controlado con Stream y archivo permanente. Documentar en el issue el transporte S3 de este diseño. |
| #17 · API y hosting | JWT Google nativo, dos rutas, empaquetado del handler de Rol 2, Firebase Hosting preparado localmente. | Corregir GET y frontend, comprobar permisos de hosting, registrar origen y habilitar únicamente las rutas necesarias. El dashboard consume más de dos rutas. |
| #18 · CI | Workflow local con fmt, validate y planes de proveedores simulados sin credenciales de Academy. | Publicar PR y ver la ejecución real del runner Linux. |
| #19 · Diagrama | PNG de 2300 × 1470 con fuente SVG editable y leyenda de despliegue, ensayo y diseño. | Revisión del equipo y entrega en Canvas junto con repo y video. [PNG](architecture.png) · [SVG](architecture.svg) |

## Estado remoto y planes locales

Se leyó el backend propio en `aws/terraform.tfstate`. Contiene 27 recursos administrados y un dato de lectura del LabRole. Se consultaron STS, S3, DynamoDB, Lambda, EventBridge, API Gateway, SNS, Glue, Firehose, etiquetas e IAM. El bucket permanente del lago devolvió HTTP 404, el stream y la tabla Glue propuestos tampoco existen. La lectura del catálogo de APIs del proyecto devolvió cero. Los planes se ejecutaron con `-lock=false` para mantener la revisión de solo lectura. El bootstrap de estado está separado.

| Archivo en build/terraform-review | Altas | Cambios | Bajas | SHA-256 |
|---|---:|---:|---:|---|
| `r1-base.tfplan` | 0 | 6 | 0 | `A5C0C09BC029E7351E0435D1AD2F9305A74E682EB80AB728EB542C52F4F1D890` |
| `r1-api-revision.tfplan`, endpoint cerrado | 11 | 6 | 0 | `9F600F3FD8DB9963CA3776DDE9DAC593A5016172BCB569C2EB4831D38FB98036` |
| `r1-health.tfplan`, dirigido solo al evaluador | 0 | 1 | 0 | `A40AAB72B4A41F2CFB2278CDD087806CD733D3A4FD4A9A495A2BBDC432962B54` |

Los dos planes completos dejan 21 recursos en `no-op`, incluida DynamoDB. Actualizan en sitio cinco ZIP y la definición de `af-dev-incident`. Los ZIP contienen cambios de Health, diagnóstico, asignación y tareas. En Notify solo cambian archivos de `af_shared`. El ASL de entrada y la regla conservan su configuración. El refresco muestra 19 direcciones con colecciones vacías normalizadas o la política de DLQ que ya administra su recurso separado. La comparación de políticas y campos confirmó su equivalencia. El plan dirigido cubre solo el evaluador y sus dependencias. Los planes son alternativas calculadas sobre el mismo estado; después de cualquier despliegue hay que regenerar la siguiente etapa.

Las once altas de la revisión de API son una HTTP API, un autorizador JWT, una Lambda, dos grupos de logs, una integración, dos rutas, dos permisos de invocación y un stage. Las rutas son `GET /machines/{id}/incidents/{incId}` y `POST /machines/{id}/incidents/{incId}/ack`. El endpoint execute-api queda deshabilitado y no hay dominio alterno. El POST restringe TECHNICIAN al asignado, exige el grant y escribe un recibo por espera. Para abrir el GET, el backend debe comprobar el grant de la operación del propio incidente. CORS permitirá el origen exacto, `If-Match` y la exposición de `ETag` y `Retry-After`. [Revisión para Sebastián](../infra/aws/api/revision-ack.md) · [Ensayo para la etapa siguiente](../infra/aws/api/ensayo.md)

## Datos y pruebas

El archivo del ensayo temporal de Firehose tiene 3576 bytes y SHA-256 `5933D192461941B94E72477D50E775CAA965C71BCD4F7CD33C0A75356170E9C2`. El cargador sube sus bytes originales a una tabla temporal de BigQuery con `load_table_from_file(..., PARQUET)`. Una segunda carga NDJSON inserta solo filas validadas y pendientes en el esquema final de Alexis. El ZIP de Lambda prescinde de PyArrow y pandas. Los recibos y jobs deterministas contemplan el reenvío, las respuestas perdidas y lotes parcialmente solapados. La deduplicación presupone que este puente coordina todos sus escritores para esos IDs. El bucket, la tabla, la base Glue y el Firehose temporales se retiraron al terminar aquella prueba. [Evidencia y módulo regional](../infra/aws/telemetry/README.md) · [Cargador](../services/lake_to_bigquery/README.md)

El mapeo de DynamoDB Streams solo considera claves de incidentes y de stock, y se prepara deshabilitado. La lectura de sensores sale por Firehose. Staging tiene vencimiento para tablas de 24 h y el proyecto BQ permanece en US. En el plan AWS con el lago activo, la precondición detuvo la preparación al comprobar que aún falta el bucket dedicado. En el entorno simulado se verificaron las seis altas del módulo. El cargador, su capa WIF y el exportador también son optativos. Falta comprobar la identidad federada restringida con el administrador de GCP y la ruta de retorno de `bigquery_sync_back` con Alexis. [Plan del puente](../infra/aws/bridge.tf) · [Configuración de GCP](../infra/gcp/README.md)

- Contratos, seis ejemplos válidos. Python, 204 pruebas del runner del repositorio y otras 18 de infraestructura con sockets bloqueados. El test de `offline_detector` emitió una advertencia por un intento de socket bloqueado y pasó. Ruff pasó.
- El simulador `direct` generó en local una sola lectura de una máquina sintética. Health y Firehose recibieron en los dobles el mismo mensaje válido. El catálogo aislado ocupó 589 bytes. Las lecturas de Academy confirmaron que faltan `af-dev-simulator` y `/af/sim/fleet-config`; la flota completa supera SSM Standard. Coordinar con Sebastián el catálogo y los reintentos por registro antes de probar el productor desplegado. [Generador de la prueba](../infra/aws/telemetry/prepare_case.py)
- Frontend, 15 pruebas y build Vite satisfactorios. El ensayo de empaquetado Firebase usó un host sintético. El sitio, el login real y el botón contra API Gateway siguen pendientes. [Preparación de hosting](../infra/hosting/README.md)
- Terraform 1.17.1, fmt y validate satisfactorios. Pasaron 17 pruebas de planes con proveedores simulados repartidas entre la configuración base, gateway, telemetría, puente y staging GCP. Los planes AWS completos se verificaron además contra el estado real y los archivos del worktree.
- El puente pasó 46 pruebas, incluidas en las 204. Se comprobó el envío del cliente oficial de BigQuery a un transporte local y la identidad de los ZIP en dos construcciones. Se importaron sus fuentes en Windows. El import completo de las ruedas Linux está preparado en CI y falta ejecutarlo en Linux x86_64.
- Los archivos de ensayo del hosting están en `infra/hosting/build/`, ignorado por Git. La revisión automática detuvo su limpieza recursiva. Estos archivos se conservan localmente para inspección.
- Los ZIP del puente tienen SHA-256 `D2D8562B476021D9925F0F631175474A66CC169A78D4DF31CB84BA59FEAA2187` para el cargador y `4DCE0BFD28AAD3B5B63078F94B2DCB56ADE27E2A9FAB7B8D3822A5BB81948154` para el exportador. El paquete de la API tiene SHA-256 `9448E987E13258E889C7A9DC0AEBA08AC6DF2FE266EED8EDD2179D98DBACC24B`.

## Orden de trabajo al retomar

1. **Leonardo** revisa por PR la base regional y el diagrama. Excluir `build/`, planes, ZIP, estados, `.terraform/`, configuración local y credenciales. Los locks de proveedores, los locks de wheels y la muestra Parquet sintética forman parte de la revisión de código. Revisar el plan base y las versiones de paquetes antes de autorizar una etapa independiente de despliegue.
2. **Sebastián** confirma el arreglo del GET y el aislamiento del historial entre operaciones. **Alexis** incorpora el GET y conserva el ETag fuerte en reintentos del mismo POST. La cuenta UTEC de una persona necesita un grant aprobado por operación y rol para el ensayo real; el desarrollo y las pruebas locales usan identidades sintéticas.
3. **Leonardo y el propietario de GCP** comprueban permisos de datos y la condición de confianza del principal WIF de este proyecto. Autorizar por separado el bucket privado y la infraestructura del lago. Ejecutar un registro identificable, su reenvío y un único retorno a DynamoDB. El límite educativo de Lambda y las cuotas de BigQuery Sandbox rigen el ensayo. Mantener la regla de incidentes deshabilitada.
4. **El equipo** verifica el flujo web en `VITE_API_MODE=live`, el origen HTTPS, el recorrido completo y los efectos de los reintentos. Tras demostrarlo, actualizar el diagrama, los issues y el video. La toma de evidencia y los despliegues de esta lista requieren su propia autorización.
