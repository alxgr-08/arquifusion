# ArquiFusion · relevo para Leonardo, el equipo y GPT Pro

Corte histórico anterior a la publicación del PR #50 y al despliegue v1.2. El [relevo de la revisión autónoma](revision-nocturna-2026-11-15.md) registra los resultados posteriores.

15 de noviembre de 2026, Lima. Repositorio `alxgr-08/arquifusion`. Leonardo es Rol 1 · arquitectura e infraestructura, Sebastián es Rol 2 · backend y operación, Alexis es Rol 3 · salud, frontend y analítica. Esta entrega se preparó sobre el commit `53f97555acb96848b1145b4e50c137a3ea2fe05a` en la rama local `infra/rol1-entrega-academy`. Los cambios de código están en el worktree `arquifusion-rol1-entrega`. Las issues ya reflejan este corte en GitHub.

## A. Resumen ejecutivo

ArquiFusion usa telemetría de maquinaria pesada para detectar anomalías, asignar técnicos y repuestos y comprobar el retorno al servicio. AWS Academy Learner Lab opera la flota. Google Cloud conserva el histórico y entrena los modelos. Hay dos demostraciones reales independientes: un incidente completado en Academy y el entrenamiento y los resultados sobre el histórico sintético en BigQuery. El puente, la web con identidad real y la nueva versión de los callbacks todavía necesitan su primer ensayo conjunto.

El examen encontró una doble serialización de atributos en las transacciones boto3, consultas que podían atribuir registros a la operación equivocada, un frontend que no retenía debidamente la versión revisada de una espera y la posibilidad de perder una alerta al publicar en EventBridge. La entrega corrige estos puntos, protege los reintentos del simulador y suma una prueba integral desde la telemetría hasta la lectura autorizada de un KPI.

Se consultó AWS con el perfil `default` en `us-east-1`. Se comprobó el propietario del backend y que el objeto de estado conservó la misma versión antes y después de los planes. El estado de la aplicación contiene **27 recursos administrados**, además de la consulta del `LabRole` existente. La regla sigue **DISABLED**. Las únicas escrituras remotas de esta revisión fueron actualizaciones de issues en GitHub. Código, ZIP y planes permanecen locales. Los estados del bootstrap y los recursos de otros proyectos quedaron intactos.

## B. Arquitectura

La referencia de arquitectura describe la madurez de cada conexión. El PNG entregado originalmente contenía píxeles sin un modelo de edición embebido. Se reconstruyó el diagrama en Draw.io con 35 bloques y 26 conectores editables. Las exportaciones diferencian recursos observados, ensayo retirado, código local y conexiones pendientes. Se entregaron también las tres versiones en la carpeta de contexto solicitada. [Arquitectura](arquitectura.md) · [Draw.io](architecture.drawio) · [SVG](architecture.svg) · [PNG](architecture.png)

1. **Operación.** El simulador `direct` conserva el mensaje original y envía la misma lectura al evaluador y a Firehose. El evaluador calcula el estado y escribe en DynamoDB. Ante una anomalía, el código local persiste una outbox por mensaje en la misma transacción. Se puede reintentar para una máquina silenciosa. Un `EventId` acredita aceptación del bus. La ejecución de la hija y el incidente certifican la entrega posterior.
2. **Incidentes.** La regla filtra `AnomalyDetected` WARNING o CRITICAL. En el ensayo se usa entrada manual a Step Functions Standard, dos esperas de confirmación y correlación de reintentos. El tópico SNS tiene cero suscripciones confirmadas. La UI necesita una concesión de la operación, y para TECHNICIAN también la asignación correcta. El backend certifica ARRIVED y RESOLVED contra sus recibos.
3. **Dos nubes.** La lectura completa llega al lago por Firehose, Glue y S3 Parquet/Snappy. DynamoDB Streams alimenta los hechos de incidentes y consumos en NDJSON. El cargador usa `load_table_from_file`, staging con caducidad y recibos por objeto e identidad. El retorno acota operación, máquina y día, verifica el perfil y evita sobrescribir un KPI distinto. La automatización necesita federación aislada. Una alternativa local supervisada reutiliza ese mismo cargador con una cuenta humana autorizada. [Puente GCP](../infra/gcp/README.md)
4. **Acceso web.** React/Vite usa Google Identity Services. API Gateway HTTP valida el ID token y su audiencia. La Lambda aplica permisos de negocio con `iss` y `sub` y registros de DynamoDB. Cognito está excluido. Amplify WEB es el destino HTTPS propuesto. Se prepara con una rama manual y se publica el ZIP aparte. Firebase Hosting es la alternativa si el proyecto GCP lo permite. [API](../infra/aws/api/README.md) · [Hosting](../infra/hosting/README.md)

## C. Entrega de Leonardo

| Área | Archivos que revisará el equipo | Resultado |
|---|---|---|
| Estado, tabla y base de incidentes | [Raíz AWS](../infra/aws/README.md), [DynamoDB](../infra/aws/dynamodb.tf), [bootstrap](../infra/aws/bootstrap/), [incident.tf](../infra/aws/incident.tf) | Entrega acumulada desde los worktrees de planificación. Tabla regional protegida, estado S3 exclusivo y bootstrap independiente. |
| API y hosting | [api.tf](../infra/aws/api.tf), [revisor de planes](../infra/aws/api/review_plan.py), [hosting.tf](../infra/aws/hosting.tf), [preparación del ZIP](../infra/hosting/prepare.py) | Dos rutas JWT de incidente y diez lecturas opcionales, integración Lambda proxy, permisos de invocación por cuenta/stage/método/ruta, logs y CORS por origen. Hosting manual sin tokens Git ni build automático. |
| Datos y recuperación | [telemetría](../infra/aws/telemetry/README.md), [bridge.tf](../infra/aws/bridge.tf), [outbox.tf](../infra/aws/outbox.tf), [cargador](../services/lake_to_bigquery/), [exportador](../services/stream_exporter/) | Envío manual acotado, archivo de la muestra, exportación operativa y deduplicación. Mappings optativos inicialmente apagados. |
| BigQuery y retorno | [staging](../infra/gcp/bridge_staging.tf), [ensayo humano](../infra/gcp/rehearse_object.py), [retorno](../services/bigquery_sync_back/README.md) | Dataset temporal optativo en US, ZIP Linux del puente con versiones fijadas, prueba de los bytes Parquet y retorno condicional. El operador humano verifica exactamente un objeto original antes de transmitirlo. |
| Preventivo y observabilidad | [módulo](../infra/aws/maintenance/README.md), [ASL](../statemachines/preventive.asl.json) | Workflow Standard, Lambda y grupo de logs opcionales, acotados a una corrida. El resumen queda dentro de la ejecución. |
| CI y documentación | [CI](../.github/workflows/ci.yml), [Terraform](../.github/workflows/terraform.yml), [guía de entrada](ONBOARDING.md), [decisiones](decisiones.md), [ensayo del video](pitch.md) | Suites con red bloqueada, validación de contratos, planes simulados e importación del ZIP en Linux en el workflow preparado. El equipo puede repetir estos controles tras publicar un PR. |

**Inventario del worktree.** El listado local `build/mission/archivos-de-la-rama.txt` conserva el estado Git por archivo y el resumen inicial de rutas pendientes. Incluye trabajo heredado de la planificación y las modificaciones de esta entrega. El índice de Git está vacío de cambios preparados.

### Capacidad efectiva de AWS Academy

| Servicio o acción | Evidencia disponible | Límite del ensayo |
|---|---|---|
| S3 de estado / DynamoDB | CLI confirmó AES256, las cuatro protecciones de acceso público y versionado. Tabla ACTIVE, GSI1/GSI2 ACTIVE, on-demand, TTL en `expiresAt`, Streams `NEW_AND_OLD_IMAGES`, PITR y protección contra borrado. | Clave de aplicación `aws/terraform.tfstate` en un bucket de propietario verificado. Crear un lago dedicado antes de archivar telemetría. |
| Lambda / SSM / Logs / Step Functions | Cinco Lambdas Python 3.12, cinco parámetros, cinco grupos de logs, tres alarmas y dos workflows Standard en el inventario. El flujo histórico llegó a cierre. | Máximo educativo de **10 entornos Lambda concurrentes para toda la cuenta**. Un caso en vuelo, sin simuladores periódicos. El catálogo compacto de 4.248 bytes supera SSM Standard de 4.096 bytes. Las tareas usan su adaptador comprimido y el ensayo del productor puede usar el kit de una máquina. |
| EventBridge / SQS / SNS | Bus, regla, destino, DLQ con política y tópico existen. Se aceptó una anomalía. Regla `DISABLED`, cero suscripciones SNS confirmadas. | La entrega automática y la recepción humana necesitan evidencia independiente. |
| Firehose / Glue / S3 de datos | Un ensayo temporal real produjo un Parquet Snappy de 3.576 bytes con once columnas y fue retirado. SHA-256 `5933D192461941B94E72477D50E775CAA965C71BCD4F7CD33C0A75356170E9C2`. En la nueva consulta faltan stream y catálogo permanentes del proyecto. | Se comprobó el rechazo del plan permanente al faltar la identidad etiquetada del lago. Requiere alta autorizada del bucket de datos. [Evidencia y runbook](../infra/aws/telemetry/README.md) |
| IAM | Se pudo leer `LabRole`. En el relevo constan denegaciones explícitas para `iam:GetRole` sobre `voclabs` y `s3:GetBucketObjectLockConfiguration` sobre el bucket de estado. | El proyecto usa el rol existente. Una simulación de `PassRole` o de callbacks no acredita una operación real. `states:SendTaskSuccess` emplea task tokens y carece de tipo de recurso IAM específico. [Referencia AWS](https://docs.aws.amazon.com/service-authorization/latest/reference/list_stepfunctions.html) |
| API Gateway / Amplify | Se pudieron listar APIs y apps en la región. No apareció una de ArquiFusion en esas consultas. | Faltan alta de la HTTP API, validación JWT real y permiso efectivo de alta y publicación Amplify. El alta estática no conecta cuentas Git. [Hosting manual de AWS](https://docs.aws.amazon.com/amplify/latest/userguide/manual-deploys.html) |
| Otros servicios | El Readme del curso limita por lo general a `us-east-1` y `us-west-2`. La ejecución del proyecto se acota a Virginia. | El recorrido no depende de CloudFront, IoT, Bedrock, Amazon Location, Scheduler, roles nuevos, EC2 ni Global Tables. GCP aloja BigQuery ML. En el Sandbox, controlar cuota y caducidad y usar jobs por lotes. [Límites de BigQuery Sandbox](https://docs.cloud.google.com/bigquery/docs/sandbox) |

### Planes guardados

Los cinco planes son **alternativas contra el mismo estado**. Los conteos incluyen la reconciliación de la base. Una vez desplegada una etapa habrá que volver a planificar conservando las opciones de los recursos que deban seguir administrados. Los binarios están en `build/mission/`, fuera de Git. Los JSON `*.review.json` contienen acciones, direcciones y hashes para su revisión. Se usó `-lock=false` para no escribir un lock en S3 durante estas lecturas.

| Archivo y alcance | Altas | Cambios | Bajas | No-op | SHA-256 del binario |
|---|---:|---:|---:|---:|---|
| `base.tfplan` · los cinco paquetes Lambda y la definición de la hija | 0 | 6 | 0 | 21 | `F3FB6FEB265B4A7237FEC1A3A7C4499B7ED7AB957B635D1454E8F046DAC33743` |
| `hosting-manual.tfplan` · aplicación Amplify y rama estática | 2 | 6 | 0 | 21 | `7365BFF8EFAD612210901E91382605CD9DBB64A2186141B333A9DC3E77755DC5` |
| `api-incidente.tfplan` · GET detalle y POST acuse, endpoint cerrado | 11 | 6 | 0 | 21 | `B6BB21FE2C0F3DC735CD3387BD158936C6D98CDF63E85B10B6DCBE4C713E33D0` |
| `api-dashboard.tfplan` · dos rutas de incidente y diez lecturas, endpoint cerrado | 31 | 6 | 0 | 21 | `F023B7A1AAEEDABCFCE6533CEBC83FF76FA1E4B2C88479C17EA397D4035F0C7D` |
| `mantenimiento-outbox.tfplan` · preventivo y detector manuales y mapping de outbox deshabilitado | 4 | 6 | 0 | 21 | `A6E042078C9B7A5633B9B2722AC2835A357574E98344C5CB1774E6E97BCB2520` |

Los seis cambios son actualizaciones in situ de `health-evaluator`, `incident-tasks`, `assign-technician`, `ai-diagnosis`, `notify` y el ASL de `af-dev-incident`. El revisor comprobó el contenido de los ZIP desplegables contra el checkout y que la tabla y la regla tienen acción no-op. El refresco mostró normalización de campos vacíos y la política de DLQ que ya administraba su recurso. Los planes API contienen el Client ID web real y `http://localhost:5173`. Hace falta un plan posterior con el origen HTTPS y la apertura explícita para ensayar el sitio público. El nombre final del dominio solo lo asigna Amplify tras el alta.

### Validaciones reproducibles

| Verificación | Resultado alcanzado aquí |
|---|---|
| Python de negocio, servicios, analítica y ASL | **227 pruebas pasaron**, con sockets bloqueados. Incluye 35 de salud y 33 de API. La suite Windows del detector emitió un aviso de socket bloqueado. El caso también pasó al ejecutarlo por separado. |
| Adaptadores Python de infraestructura | **39 pruebas pasaron** con sockets bloqueados, incluidas las diez del ensayo preventivo/offline y la ida manual de un Parquet con repetición y filtro del retorno. |
| TypeScript, frontend y contratos | **18 pruebas del frontend**, typecheck y build Vite satisfactorios (418 módulos). Se validaron seis ejemplos JSON. Ruff pasó para el repositorio. |
| Terraform | `fmt -check -recursive` y validación de las raíces de aplicación, bootstrap por copia local, telemetría y GCP. **34 escenarios simulados**: 4 base/hosting, 15 API, 6 puente, 3 lago, 4 preventivo y 2 GCP. Se verificaron por separado cinco planes contra el estado real de la aplicación. |
| Recorrido conjunto local | Telemetría sintética, evaluación, evento, ASL real, llegada, resolución, recibos, mantenimiento, consumo, exportación, Parquet, carga por el SDK y lectura autorizada del KPI. El replay dejó un solo mantenimiento, un solo consumo positivo y un KPI. boto3 habló con Moto, los claims fueron sintéticos y BigQuery fue un doble. El valor de salida del modelo en esta prueba es un fixture. El entrenamiento con resultados reales del equipo consta aparte. [Prueba](../services/api/tests/test_multicloud_rehearsal.py) |
| Paquetes | Se importaron las fuentes de los tres ZIP del puente en Windows. La importación de las extensiones nativas Linux x86_64 con CPython 3.12 queda en el CI preparado. No hay WSL ni contenedor Linux funcionando en este equipo. El ZIP web comprobado es **mock**, con 15 archivos. El build live debe usar la URL final. |
| Acceso GCP actual | Este host carece de `gcloud`, `bq` y de sesión ADC de usuario configurada. El inventario de GCP procede de la evidencia técnica del equipo del 15/11 y del PR #48. No se abrió la clave privada descargada. |

**Repetición local desde la raíz con las dependencias de desarrollo instaladas.**

```sh
python scripts/validate_contracts.py
python scripts/run_tests.py
python -m pytest -q --disable-socket services/api/tests/test_multicloud_rehearsal.py
python -m pytest -q --disable-socket infra/aws/api infra/aws/health_evaluator infra/aws/incident infra/aws/telemetry infra/aws/maintenance infra/gcp infra/hosting
python -m ruff check .
npm --prefix frontend test
npm --prefix frontend run build
terraform fmt -check -recursive infra
```

## D. Aportes de Sebastián y Alexis

**Sebastián.** Los PR #39, #40, #47 y #49 aportan la orquestación, integración y las esperas versionadas. La entrega conserva su handler y el ASL. Se corrigieron localmente la serialización de transacciones, las consultas por operación y almacén, los históricos tras un traslado, el ETag fuerte y la verificación del dueño del recibo. El simulador reenvía a Firehose únicamente los registros explícitamente fallidos, con idénticos bytes y sin repetir la invocación al evaluador. Las respuestas ambiguas se conservan para conciliación. El preventivo y el detector acotan sus efectos a la máquina y la bodega del kit. El consumidor real debe probar la asignación y los dos recibos en Academy. [Ensayo de Sebastián](https://github.com/alxgr-08/arquifusion/issues/23)

**Alexis.** Los PR #43 y #44 mejoraron contexto y `pendingAlert`, #45 y #48 corrigieron el pipeline en Sandbox y #46 integró GIS. Esta entrega añade outbox recuperable por evento, una vista directa del incidente, revisión humana de la espera, reintento del mismo ETag y cuerpo, y retorno condicional del KPI. La outbox conserva una alerta mientras falle el bus. Puede haber publicaciones repetidas si el proceso cae después de `PutEvents`; el `messageId` y la entrada idempotente permiten conciliarlas. Una interrupción que supere la retención del stream requiere recuperación por la fila durable. [GCP](https://github.com/alxgr-08/arquifusion/issues/14) · [Web y Google](https://github.com/alxgr-08/arquifusion/issues/41)

## E. Git y GitHub

- `git fetch origin` confirmó `HEAD` y `origin/main` en `53f9755`, divergencia 0/0 de commits. Los worktrees históricos `arquifusion` (`5401e3c`) y `arquifusion-rol1-dynamodb` (`be19d3e`) se conservaron. Los archivos del nuevo worktree incluyen trabajo local anterior. No se hizo commit, push, merge, rebase ni apertura de PR de código.
- Nueve PR fusionados, #39, #40 y #43 a #49, y cero PR abiertos en el corte. El remoto anunció nueve ramas. `prueba-mcp` está siete commits detrás de main y sin commits exclusivos. Las seis `feat/rol3-*` publicadas y `docs/rol3-pitch` conservan historia de funciones o documentación ya presentes en main y entre uno y tres commits exclusivos por ascendencia. El squash y la integración posterior hacen insuficiente ese contador para decidir una eliminación. No se retiró ninguna referencia. [Historial de PR](https://github.com/alxgr-08/arquifusion/pulls?q=is%3Apr+is%3Amerged)
- Se actualizaron las quince issues que estaban abiertas. #8 (bucket) y #9 (DynamoDB regional) quedaron cerradas con evidencia. #10 y #17 se conservaron con el alcance vigente. Se actualizaron #7, #10–19, #23 y #41 con propietarios, bloqueos y criterios de aceptación. Estado final **13 issues abiertas y 19 cerradas**. Se reutilizaron las existentes. [Tablero abierto](https://github.com/alxgr-08/arquifusion/issues?q=is%3Aissue+is%3Aopen) · [S3 #8](https://github.com/alxgr-08/arquifusion/issues/8) · [DynamoDB #9](https://github.com/alxgr-08/arquifusion/issues/9)

## F. Estado por componente

| Componente | Responsable | Estado | Evidencia | Pendiente | Issue/PR |
|---|---|---|---|---|---|
| Estado S3 y tabla de operación | Leonardo | IMPLEMENTADO Y VERIFICADO · AWS | CLI y estado remoto de 27 recursos | Incorporar IaC al PR | [#8](https://github.com/alxgr-08/arquifusion/issues/8), [#9](https://github.com/alxgr-08/arquifusion/issues/9) |
| Evaluación y reenvío de alertas | Alexis / Leonardo | PARCIAL | Health y bus probados en AWS. Nueva outbox y recuperación probadas localmente | Desplegar paquetes actuales y demostrar reconciliación | [#44](https://github.com/alxgr-08/arquifusion/pull/44), [#13](https://github.com/alxgr-08/arquifusion/issues/13) |
| Workflow, inventario y recibos v1.2 | Sebastián / Leonardo | PARCIAL | Cierre AWS histórico con cero piezas. Nuevo cierre local con un consumo y un mantenimiento | Repetir ambos ACK autenticados con stock en Academy | [#23](https://github.com/alxgr-08/arquifusion/issues/23), [#49](https://github.com/alxgr-08/arquifusion/pull/49) |
| Regla automática / entrega SNS | Leonardo / Sebastián | PENDIENTE de ensayo real | Regla y DLQ desplegadas. Regla DISABLED, cero suscripciones | Evento único y destinatario que acepte participar | [#12](https://github.com/alxgr-08/arquifusion/issues/12) |
| API protegida y hosting | Leonardo / Alexis | IMPLEMENTADO SIN VALIDAR en Academy | Cinco planes revisados, dos modos API cerrados y alta estática separada | HTTPS, ID token real, permisos efectivos de creación y publicación | [#17](https://github.com/alxgr-08/arquifusion/issues/17), [#41](https://github.com/alxgr-08/arquifusion/issues/41) |
| Preventivo y detector | Sebastián / Leonardo | IMPLEMENTADO Y VERIFICADO · local | Repetición de ASL con una orden y bodega aislada. El detector respeta lecturas recientes y cambios de titularidad | Invocación manual en corrida autorizada | [#12](https://github.com/alxgr-08/arquifusion/issues/12), [#13](https://github.com/alxgr-08/arquifusion/issues/13) |
| Archivo de telemetría | Leonardo / Sebastián | PARCIAL | Parquet real de ensayo retirado y simulador `direct` probado localmente | Bucket privado permanente y lectura identificable | [#10](https://github.com/alxgr-08/arquifusion/issues/10) |
| Streams → S3 y carga a BigQuery | Leonardo | IMPLEMENTADO Y VERIFICADO · local | NDJSON de incidentes y repuestos, Parquet, recibos y replays | Probar consumidor y carga en los datasets autorizados | [#15](https://github.com/alxgr-08/arquifusion/issues/15), [#16](https://github.com/alxgr-08/arquifusion/issues/16) |
| BigQuery ML e histórico | Alexis | IMPLEMENTADO Y VERIFICADO · evidencia GCP del equipo | 69.120 lecturas, 25 incidentes, 108 movimientos, dos ARIMA_PLUS, un KMEANS y nueve salidas | Seleccionar el resultado trazable al caso nuevo y revisar expiración | [#48](https://github.com/alxgr-08/arquifusion/pull/48) |
| Identidad y retorno entre nubes | Alexis / Leonardo | BLOQUEADO para ensayo cloud | WIF con identidad aislada o alternativa humana preparada. Retorno idempotente local | Acceso personal autorizado o federación aislada, staging y job real | [#11](https://github.com/alxgr-08/arquifusion/issues/11), [#14](https://github.com/alxgr-08/arquifusion/issues/14) |
| Entrega, CI y Canvas | Equipo | PARCIAL | Fuentes Draw.io y PNG, paquete web y guion preparados | PR revisado, CI Linux, video, entrega de enlace y diagrama | [#18](https://github.com/alxgr-08/arquifusion/issues/18), [#19](https://github.com/alxgr-08/arquifusion/issues/19), [#7](https://github.com/alxgr-08/arquifusion/issues/7) |
| Cognito, IoT obligatorio y réplica entre regiones | Equipo | OBSOLETO para esta entrega | La arquitectura utiliza GIS, envío direct y DynamoDB regional | Conservar el diagrama y planes alineados | [#10](https://github.com/alxgr-08/arquifusion/issues/10), [#17](https://github.com/alxgr-08/arquifusion/issues/17) |

## G. Siguiente ventana de trabajo

1. **Leonardo, ahora.** Revisar el diff del worktree y los cinco planes. Priorizar la base. Preparar el PR de código cuando esté autorizado, excluyendo secretos, `.terraform`, estados, ZIP y planes. Ejecutar el smoke Linux del puente en el CI; tras cualquier cambio, comparar los paquetes y volver a planificar con bloqueo antes de aprobar un despliegue. Los planes actuales son revisiones contra una única fotografía del estado.
2. **Leonardo y Alexis, en paralelo.** Revisar la app y rama manuales para obtener la URL HTTPS. Tras alta autorizada, Leonardo pasa el origen exacto a Alexis. Alexis lo registra como origen JavaScript autorizado del cliente web existente. Regenerar el plan API para ese origen. Para el video, usar `VITE_API_MODE=live`, URL de API con stage, Client ID público y operación del ensayo. No subir el ZIP rotulado mock como evidencia de funcionamiento live.
3. **Sebastián y cada participante.** Acordar una corrida sintética libre, operación, roles y técnico. Cada persona obtiene su propio `sub` de Google. Sebastián registra la concesión sobre esa operación, con `techId` para el técnico, dentro de una ventana de escritura aprobada. La URL corresponde a Alexis. El `sub` personal para la concesión corresponde a Sebastián. Un authorization code o un token no se comparte para este fin.
4. **Leonardo y Alexis, sin esperar al frontend.** Aprobar un lago de datos privado distinto del estado, confirmar acceso de lectura/escritura pertinente y staging US con caducidad, y elegir un operador autorizado o WIF aislado. Si se usa un operador, seguir `inspect`, `load`, actualización acotada del KPI por Alexis y `return-kpi`. Conservar mensaje, hash del objeto, jobs, fila de origen y clave retornada. Revisar el alcance de los SQL de reconstrucción antes de usarlos sobre datasets compartidos.
5. **Equipo, ensayo.** Registrar una ejecución madre y su hija, el incidente, las dos versiones de espera, ambos recibos y el consumo positivo. Comprobar 401 sin token, 403 para otra operación o técnico, 412 para espera obsoleta y recuperación de una respuesta perdida. Para una prueba automática posterior, acordar el alcance exacto y registrar el estado final de la regla. Para el video basta documentar con precisión el recorrido que se haya hecho. [Ensayo de incidentes](ensayo-lab/README.md) · [Ensayo de API](../infra/aws/api/ensayo.md)
6. **Alexis y el equipo, entrega.** Grabar el guion de hasta 4:45 cuando haya evidencia real suficiente, revisar el diagrama final y subir a Canvas video, enlace del repositorio y PNG. Mantener separados el histórico sintético, la regresión local y el caso de ambas nubes. [Guion](pitch.md)
