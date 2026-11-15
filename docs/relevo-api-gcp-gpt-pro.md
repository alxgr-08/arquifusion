# ArquiFusion · Relevo para GPT Pro del 15 de noviembre de 2026

Corte histórico. Para el estado vigente consultar la arquitectura: [ArquiFusion](arquitectura.md).

Este relevo precede a la revisión de los PR #47 y #49. Para continuar desde el código integrado y las verificaciones de Academy usa la [entrega de Rol 1](entrega-rol1.md).

Leonardo es Rol 1, arquitectura e infraestructura. El ambiente operativo es **AWS Academy Learner Lab**, perfil `default`, región `us-east-1`, con otros proyectos en la misma cuenta y un límite educativo de **10 entornos de ejecución Lambda concurrentes**. AWS ejecuta la aplicación y Google Cloud debe aportar BigQuery y BigQuery ML. La decisión para la fase es Google Sign-In con Google Identity Services y el autorizador JWT nativo de API Gateway HTTP. Cognito queda excluido. Las verificaciones de infraestructura de este relevo se limitaron a lecturas de AWS y GCP.

## Inventario de issues del 15 de noviembre de 2026

GitHub muestra 32 issues, con 7 cerrados y 25 abiertos. El #43 es un PR. El estado del trabajo se contrastó con el código y los ensayos registrados. [Issues abiertos](https://github.com/alxgr-08/arquifusion/issues?q=is%3Aissue%20is%3Aopen) · [Issues cerrados](https://github.com/alxgr-08/arquifusion/issues?q=is%3Aissue%20is%3Aclosed)

| Issue | Estado en GitHub | Evidencia y siguiente paso |
| --- | --- | --- |
| #1, #2, #3 | Cerrados | Motor de salud y evaluador probados en Academy. El diagnóstico se ejecutó con reglas. Queda por verificar Bedrock si el equipo lo requiere. |
| #4, #5, #6, #30 | Cerrados | SQL/ML, retorno a DynamoDB, frontend y contratos de diagnóstico en el repositorio. El ensayo de ML y retorno con GCP y el del frontend con login real siguen pendientes. |
| #7 | Abierto | Hay guion. Falta evidencia del video final. |
| #8, #9 | Abiertos | Bootstrap S3 y tabla regional probados. Para #8 documentar las comprobaciones pendientes y las exclusiones del laboratorio. Para #9 acordar el alcance regional frente a la réplica del texto original. |
| #10, #11 | Abiertos | Ingesta IoT/Firehose y replicación sin ensayo. Hay parámetros SSM desplegados. Falta conciliar el resto de los criterios de seguridad y el método de identidad del puente. |
| #12, #13 | Abiertos | Bus, destino de incidentes, dos máquinas de estados y cinco Lambdas desplegados. Quedan el disparo automático, la entrega a suscriptor y los servicios restantes. |
| #14 | Abierto | Falta identificar el proyecto GCP del equipo y verificar los tres datasets y permisos. |
| #15, #16 | Abiertos | Cargador BigQuery y exportador Streams probados en local. Faltan el lote real y el acuerdo sobre transporte y frecuencia. |
| #17, #18, #19 | Abiertos | #17 requiere actualizar el diseño histórico a GIS y JWT de API Gateway y probar hosting y acceso. Faltan CI de Terraform en PR y diagrama final. |
| #20 | Abierto | Existe el JSON de la flota y hay un catálogo SSM para incidentes. Falta verificar el catálogo que el simulador espera en `/af/sim/fleet-config`. |
| #21, #22, #28, #29 | Abiertos | Simulador, inyector de fallos, flujo preventivo y detector offline con código y pruebas locales. Pendiente el ensayo operativo de cada pieza. |
| #23, #24 | Abiertos | Un incidente recorrió las Step Functions y cerró con un mantenimiento en Academy. Revisar con Sebastián la evidencia de reasignación, reservas y correlación que exige el cierre. |
| #25, #26, #27 | Abiertos | Asignación real con distancia local, SNS sin recepción humana demostrada y API de negocio con código local. Quedan el alcance de Location, entrega al suscriptor y rutas autenticadas en AWS. |
| #41 | Abierto | Alexis aún no ha comentado. Falta confirmar el cliente GIS, el origen del frontend y el Project ID de BigQuery. |
| #42 | Abierto | Sebastián acepta el enfoque general e informa de pruebas en su copia local. Falta acordar el ARN de la ejecución que posee la espera y el tratamiento de `If-Match`, aclarar que GIS va directo al autorizador JWT y revisar el PR con evidencia. [Respuesta de Sebastián](https://github.com/alxgr-08/arquifusion/issues/42#issuecomment-6890349300) |

**Acceso GCP.** En esta revisión el catálogo de Codex mostró el conector BigQuery deshabilitado por el administrador. El conector de Drive es independiente. No se encontró un MCP BigQuery configurado ni `gcloud` y `bq` en el PATH local. Con la cuenta institucional y el Project ID, comprobar en el explorador de BigQuery los datasets `af_lake`, `af_ml` y `af_out` en `US`. El primer acceso puede limitarse a inspección de metadatos. Para conectar Codex, coordinar con el administrador la modalidad autorizada. [Consola de BigQuery](https://console.cloud.google.com/bigquery) · [MCP oficial de Google](https://docs.cloud.google.com/bigquery/docs/use-bigquery-mcp)

## Ramas y orden de integración al 15 de noviembre de 2026

Tras `git fetch origin`, GitHub anuncia diez ramas. `origin/main` y la rama de trabajo de Leonardo `infra/rol1-aws-dynamodb-plan` apuntan a `be19d3e` (#40). La rama local `main` y la del bootstrap siguen en `5401e3c` (#39), a un commit de distancia. La actualización de referencias no integró código en ningún worktree.

| Rama en GitHub | Qué hacer con ella |
| --- | --- |
| `main` | Base del equipo. Los PR #39 y #40 ya están integrados. |
| `fix/rol3-integration-notes` | Conservar. El PR #43 de Alexis aporta el commit `838117e`, tiene CI verde y sigue abierto para revisión. |
| `prueba-mcp` | Apunta exactamente a `be19d3e`, sin PR asociado ni commits propios hoy. El equipo puede retirarla si terminó la prueba. |
| `feat/rol3-health-score`, `feat/rol3-health-handler`, `feat/rol3-ai-diagnosis`, `feat/rol3-frontend`, `feat/rol3-analytics`, `feat/rol3-bigquery-sync-back`, `docs/rol3-pitch` | Los PR #31–#37 se cerraron individualmente. #40 documenta la incorporación conjunta. Los archivos funcionales principales y el guion están en `main`. Quedan diferencias históricas en CI, documentación y archivos auxiliares. Son candidatas a limpieza con Alexis. Volver a fusionarlas arriesga recuperar versiones antiguas de archivos compartidos. |

**Revisión de #43.** El evaluador conserva el contexto opcional en `READING#`. En un ensayo local con el código exacto del PR, una lectura con fracciones de segundo conservó el lote de combustible y la temperatura ambiente. El exportador la clasificó como incompleta porque todavía faltan `location`, `machineId`, `machineType`, `model`, `operationId`, `schemaVersion` y el `timestamp` original. En otra prueba, tres máquinas vecinas compartían lote y la afectada tenía un lote distinto. Con señales de combustible y temperatura el diagnóstico de la afectada dio `FUEL_SYSTEM / HIGH`. Acordar ese caso con Alexis antes de integrar. La fusión futura tampoco desplegará automáticamente las dos Lambdas. [PR #43](https://github.com/alxgr-08/arquifusion/pull/43) · [CI](https://github.com/alxgr-08/arquifusion/actions/runs/43683526691) · [PR de integración #40](https://github.com/alxgr-08/arquifusion/pull/40)

**Trabajo de Leonardo.** El worktree original conserva el estado separado del bootstrap y 15 archivos pendientes. Las 15 rutas también están en el worktree de integración, que tiene 58 archivos pendientes en total. Doce coinciden al normalizar saltos de línea. Las tres diferencias son `.gitignore`, `docs/decisiones.md` e `infra/aws/README.md`. Ningún archivo estaba en staging. Mantener el original y trabajar en el de integración. Para preparar los PR, abrir worktrees limpios desde `origin/main` y trasladar solo los archivos y los bloques que correspondan a cada entrega. Esto permite revisar los cambios compartidos, como el empaquetador de Lambda, sin mover los originales.

1. Infraestructura AWS ya desplegada y su documentación. Incluir fuentes y archivos de bloqueo de proveedores. Excluir configuración privada, estados, planes y ZIP. Si #43 entra primero, verificar los hashes de los paquetes de las Lambdas y los 27 recursos en el plan antes de cualquier despliegue.
2. Puente S3–BigQuery con sus dos servicios, pruebas y dependencias. Puede revisarse en paralelo al login.
3. API de acuse en un PR propio, con su ruta cerrada durante la revisión. Las decisiones de frontend y callbacks se siguen en los issues de Alexis y Sebastián. [Issue #41](https://github.com/alxgr-08/arquifusion/issues/41) · [Issue #42](https://github.com/alxgr-08/arquifusion/issues/42)

## Actualización del puente AWS–GCP y la coordinación

Leonardo publicó y asignó los dos pedidos del equipo. Alexis tiene la solicitud de cliente GIS, origen y Project ID. Sebastián tiene la revisión del protocolo de autorización y callbacks. [Alexis · issue #41](https://github.com/alxgr-08/arquifusion/issues/41) · [Sebastián · issue #42](https://github.com/alxgr-08/arquifusion/issues/42)

Ambos handlers del Rol 1 ya tienen implementación y pruebas locales. El ensayo con datos sintéticos carga dos lecturas distintas, un incidente cerrado y un consumo correctivo de una unidad en el doble de BigQuery. Repetir el lote por partes y por Parquet conserva esos conteos. La evidencia proviene de S3 simulado, un archivo Parquet real y un cliente BigQuery de prueba. Siguen pendientes la identidad del puente, los permisos del laboratorio y de GCP, el empaquetado Linux y el ensayo en las dos nubes. El estado de los 27 recursos y la regla DISABLED que se cita abajo corresponde a la última consulta del relevo de infraestructura. En esta actualización no se accedió a AWS ni al proyecto de GCP.

La lectura reducida de DynamoDB que escribe hoy Health Evaluator carece de datos obligatorios del contrato. Se archiva con el detalle de lo que falta. Alexis y Leonardo deben identificar la fuente íntegra y acordar con analítica que la tabla de incidentes reciba una fila al cierre, mientras S3 guarda las revisiones. Los permisos, las entradas y el procedimiento de recuperación están en los README de ambos servicios. [Exportación de Streams](../services/stream_exporter/README.md) · [Carga a BigQuery](../services/lake_to_bigquery/README.md)

En esta actualización pasaron 28 pruebas específicas del puente con sockets bloqueados. La suite del repositorio pasó 153 pruebas, incluidas esas 28, y las suites locales de infraestructura pasaron otras 52. Dos pruebas existentes de API y detector offline intentaron abrir un socket. El bloqueo de red funcionó y ambas pasaron. Ruff y los seis ejemplos de contrato pasaron. Las dependencias resueltas para CPython 3.12 Linux x86_64 ocuparon 31,33 MiB descomprimidas para el exportador y 222,69 MiB para el cargador con PyArrow. Falta medir el paquete final y probar su importación en el runtime Linux. [Configuración y empaquetado](../services/lake_to_bigquery/README.md)

## Estado que debe preservar la siguiente sesión

- Worktree `arquifusion-rol1-dynamodb`, rama `infra/rol1-aws-dynamodb-plan`, commit `be19d3e6ae23`. Después de `git fetch origin`, la comparación con `origin/main` dio **0 adelante y 0 atrás**. Los últimos cambios integrados siguen siendo #39 y #40. Las ramas de Alexis incluyen frontend, analytics y sync-back. Los cambios locales previos siguen en el worktree. El bootstrap S3 conserva su estado separado en el worktree original.
- STS y el backend coincidieron en la cuenta del laboratorio. El estado remoto usa `aws/terraform.tfstate` en el bucket exclusivo de ArquiFusion. La lista actual contiene 27 recursos administrados y una lectura de `LabRole`. La tabla `af-dev-arquifusion` sigue ACTIVE con Streams `NEW_AND_OLD_IMAGES`; la regla `af-dev-incident` del bus `af-dev-bus` sigue DISABLED. La consulta regional no encontró la API propuesta `af-dev-api`, su Lambda ni sus dos grupos de logs.
- El relevo anterior documentó una lectura normal y una anómala en Health Evaluator, persistencia en DynamoDB y aceptación de una anomalía en EventBridge. El incidente sintético `INC-1a24` terminó RESOLVED mediante callbacks directos a Step Functions. Se registró un mantenimiento. Su timeline conservó DETECTED y ASSIGNED. **El ensayo de los dos POST autenticados todavía está pendiente.**
- AWS Academy ya ha ejecutado el servicio Lambda, DynamoDB, SSM Standard, EventBridge y Step Functions usados en el flujo. La creación de esta HTTP API, su JWT authorizer, el envío de access logs por Gateway y la ruta protegida quedan pendientes de demostración en Academy. `iam:PassRole` para Lambda y `apigateway:POST` sobre /apis dieron `allowed` para la identidad del laboratorio en simulación. Para el LabRole, GetItem, Query, ConditionCheckItem, PutItem y UpdateItem en la tabla del proyecto y SendTaskSuccess también dieron `allowed`. Existe confianza para Lambda. Un resultado del simulador no acredita creación ni excluye restricciones del laboratorio. No se cambió IAM.
- El perfil de la cuenta puede reportar una cuota de servicio mayor. Para la prueba se respeta el límite educativo, compartido con los otros proyectos. Una ejecución sintética en vuelo. El stage propuesto limita el ritmo y la ráfaga a uno. Ese ajuste también puede devolver HTTP 429 y no reserva capacidad Lambda de la cuenta.

## Qué encontraron GitHub y Google Cloud

El conector de GitHub devolvió 404 al consultar el issue #23 y el PR #40, y 422 al buscar. La sesión autenticada del navegador sí abrió el repositorio. En #23 figura el comentario que Leonardo dejó el 14 de noviembre a las 22:17 de Lima. No figura una respuesta de Sebastián al protocolo de autenticación y recibos. La búsqueda de OAuth, Google Sign-In, googleusercontent.com y revision-ack devolvió cero resultados. `revision-ack.md` sigue local, fuera de la historia de Git. En las ramas remotas consultadas no apareció un cliente GIS ni un origen de ensayo confirmado. La última información de Leonardo es que todavía no existían. Una revisión privada o un cliente existente bajo otra cuenta solo pueden confirmarlos sus responsables. [Comentario del issue #23](https://github.com/alxgr-08/arquifusion/issues/23#issuecomment-6888454551) · [PR #39](https://github.com/alxgr-08/arquifusion/pull/39) · [PR #40](https://github.com/alxgr-08/arquifusion/pull/40)

La consola Google Cloud de Leonardo estaba sin proyecto seleccionado. El selector de recursos, en Todos, devolvió **«No hay recursos para mostrar»** al buscar `arquifusion`. El issue #14 está Open y remite al README, sin Project ID ni evidencia de despliegue. En el worktree hay un `project_id` de ejemplo. No hay CLI `gcloud` ni `bq` accesible en esta máquina. Quedan sin verificar el proyecto del equipo bajo otro nombre, el acceso de Leonardo, los datasets, la facturación y los jobs. [Issue #14](https://github.com/alxgr-08/arquifusion/issues/14) · [Carga S3 a BigQuery #15](https://github.com/alxgr-08/arquifusion/issues/15)

## Plan local revisado

| Dato | Valor verificado |
|---|---|
| Binario guardado, ignorado por Git | `build/terraform-review/api-ack-gis-review.tfplan` |
| SHA-256 del binario | `61A093123BAE4EDBC61BCBEFA9908CF24328F3831F3524808BF8472BCB2DDCF3` |
| Resultado de Terraform | **Plan: 9 to add, 0 to change, 0 to destroy.** Los 27 recursos del estado actual son `no-op`. |
| ZIP asociado | `build/api/api-ack.zip`, SHA-256 `1118BA6E2E2B4E7BC36A6D28A1DBC3B11E9257A1DB1252BB9B49B361402B1394` |
| Evidencia compartible | `build/terraform-review/api-ack-gis-review.review.json`. Para GPT Pro, compartir este relevo o el JSON reducido. El plan completo permanece local. |

Las nueve altas son la **HTTP API** `af-dev-api`, **JWT authorizer**, **integración AWS_PROXY v2.0**, **ruta POST** `/machines/{id}/incidents/{incId}/ack`, **stage dev**, **Lambda** `af-dev-api-ack` (Python 3.12, LabRole), **permiso de invocación** y **dos grupos CloudWatch Logs** de 14 días. Las referencias a API y función que AWS asignará al crearlas siguen calculadas. El permiso se limita a `apigateway.amazonaws.com`, la cuenta esperada y `<execution-arn>/dev/POST/machines/*/incidents/*/ack`. El handler también comprueba API, stage, routeKey y método. Los logs de acceso registran requestId, routeKey y status.

**Este es el plan estructural cerrado.** Contiene `disable_execute_api_endpoint=true`, sin dominio alternativo y sin ruta comodín. La audiencia `urn:arquifusion:dev:ack:unconfigured` no es un cliente emitido por Google. CORS carece de orígenes permitidos. Cuando Alexis confirme ambos valores, Terraform exige el par, acepta únicamente el origen HTTPS exacto y configura POST con Authorization, Content-Type e If-Match. El cliente web real todavía no se ha usado en un plan. Abrir el endpoint exige un cambio separado después del visto bueno del backend y del frontend. El plan de dos altas del 14 se conserva como antecedente.

La lectura de refresco mostró 19 direcciones con normalización de tags, listas de alarmas o capas y, para la DLQ, la misma política representada también en la cola. Se verificaron los valores vacíos y la igualdad de la política con el recurso que la administra. **Cero actualizaciones o reemplazos propuestos**. El plan usó refresh y `-lock=false` para evitar escribir el lock S3 durante esta revisión. Un plan futuro debe volver a comprobar este estado y el hash del ZIP.

## Qué implementó Leonardo y qué necesita revisión

La candidata de Rol 1 vive en `infra/aws/api/`. Recibe únicamente la identidad del contexto JWT que API Gateway valida con `https://accounts.google.com` y el Client ID exacto. Comprueba audiencia, emisor, expiración y, si existe, `azp`. La clave de identidad estable se deriva de `iss + NUL + sub`. Obtiene la operación del PROFILE y lee consistentemente la concesión `PRINCIPAL#<hash> / OP#<operación>` **antes de leer el incidente**. Para TECHNICIAN exige `techId == assignedTech`; para MAINT_LEAD exige la misma operación. Nunca usa email, rol visual, claims Cognito ni un bypass de autorización. [Google GIS](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token) · [Autorizador JWT de AWS](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html)

El candidato propone una generación pública de espera en `If-Match`. Una transacción revalida perfil, concesión y asignación y registra un recibo durable PENDING antes de enviar el callback. Su clave incluye la fila de incidente, generación y acción. El recibo guarda el actor y payload inmutables, la hora, el ARN de ejecución y una huella del token. El backend conserva el token de tarea. Un reintento respeta una reserva de envío de 15 segundos y el primer payload. La API no borra la siguiente espera, no cierra el incidente por su cuenta y solo devuelve éxito cuando ve un recibo CONFIRMED. Una respuesta de red perdida o `InvalidToken` deja el resultado pendiente. El acuse duplicado ya confirmado devuelve el DTO público de incidente. [Semántica de SendTaskSuccess](https://docs.aws.amazon.com/step-functions/latest/apireference/API_SendTaskSuccess.html)

Sebastián todavía debe implementar o acordar un mecanismo equivalente para guardar `pendingGeneration` y `pendingExecutionArn`, confirmar efectos y recibo de forma idempotente, y reconciliar PENDING con el historial de la ejecución hija. Al expirar, debe identificar inequívocamente esa espera. En cierre parcial hay que revisar técnico, reservas, PROFILE y mantenimiento incluso si GSI1 ya está en CLOSED. La implementación de cierre actual sí usa la fecha de resolución original en el SK del mantenimiento. Se necesita una lectura autorizada para entregar la nueva generación a la web o una ficha sintética para la primera prueba. El handler histórico de Rol 2 sigue esperando grupos de Cognito y consume el token antes de enviar el callback. Está fuera del punto de entrada del ZIP candidato. Los contratos, frontend, ASL y código de Rol 2 no se editaron.

**Riesgos pendientes.** El LabRole es compartido. La política que limita API Gateway no restringe por sí sola una identidad del laboratorio que ya posea permiso directo de invocar Lambda o escribir en la tabla. Antes de abrir, Leonardo debe controlar quién puede invocar la función y aprovisionar concesiones, y mantener datos sintéticos. El banco de pruebas simula la confirmación de recibos del workflow; no demuestra entrega de la nueva ruta. Sigue pendiente el transactional outbox de alertas entre DynamoDB y EventBridge, que tiene propietario y etapa distintos.

## Pruebas y archivos

| Verificación | Resultado real |
|---|---|
| `terraform fmt -check -recursive`, `terraform validate`, plan completo con refresco | Correctos. El comparador cotejó los 27 no-op con la lista remota y comprobó el ZIP contra `source_code_hash`. |
| `python scripts/run_tests.py` | 125 pruebas pasaron, incluidas siete de analítica. |
| Pruebas previas de infraestructura Health Evaluator e incidentes | 12 pasaron. El segundo comando también repitió las siete de analítica. |
| `python -m pytest -q infra/aws/api` | 37 pasaron. Incluyen firma RS256 local, contexto ausente y expirado, concesión ausente o de otra operación, técnico incorrecto, revocación en plena transacción, reintentos, vencimiento y respuesta perdida. |
| Cierre completo local | Moto DynamoDB y el CloseIncident real de Rol 2 dieron un mantenimiento, un solo consumo de stock, técnico disponible y PROFILE liberado al repetir después de medianoche. La confirmación y la timeline del nuevo workflow se modelaron con un doble. |
| `python -m pytest -q infra/gcp` | Tres pruebas del verificador de metadatos pasaron con datos locales. |
| Frontend y contratos | TypeScript y Vite compilaron con `VITE_API_MODE=live`. Vitest pasó sus diez pruebas existentes. Se validaron seis ejemplos JSON. Ruff pasó en los archivos intervenidos. No se hicieron peticiones HTTP reales, cargas a BigQuery ni entrenamiento. |

**187 pruebas distintas** entre Python y Vitest. La sesión conservó los cambios previos y no hizo commit, push o merge. Las escrituras fueron locales al worktree y a archivos ignorados de construcción. No se ejecutó apply, destroy, migración de estado, habilitación de regla, invocación Lambda o callback cloud.

| Archivos de esta preparación | Contenido |
|---|---|
| `infra/aws/api.tf`, `infra/aws/health_evaluator/build.py` | Nueve recursos y modo de empaquetado `--api` exclusivo de la candidata. |
| `infra/aws/api/ack_api.py`, `ack_store.py`, `identity_review.py` | Adaptador de la ruta, transacción DynamoDB y política de autorización. |
| `infra/aws/api/test_ack_api.py`, `review_plan.py` | Pruebas locales y verificador del plan con salida reducida. Las 18 pruebas anteriores de identidad se conservaron. |
| `infra/aws/api/revision-ack.md`, `ensayo.md`, `README.md` | Revisión detallada para Sebastián, matriz del ensayo y diferencia respecto del plan antiguo. |
| `infra/gcp/check_bigquery.py`, `test_check_bigquery.py`, `README.md` | Inspección de metadatos, pruebas y situación del puente. |
| `docs/decisiones.md`, `docs/relevo-api-gcp-gpt-pro.md` | Decisión vigente y relevo compartible. |

## Dos nubes, en paralelo al login

1. **Leonardo y Alexis · identificar proyecto.** Confirmar el Project ID y en qué cuenta institucional es visible. Con una CLI autenticada existente ejecutar `python infra/gcp/check_bigquery.py PROJECT_ID`. El comando solo lee proyecto, API habilitada, datasets, tablas y modelos con nombres previstos. Confirmar ubicación `US`, permisos diferenciados para metadata, cargas, jobs y modelos y si el curso permite facturación. El issue #14 y el selector de proyectos son evidencia del bloqueo; no prueban inexistencia global. El sandbox limita, entre otros, DML, streaming y BigQuery Data Transfer Service. El tutorial oficial de BigQuery ML pide verificar facturación. El SQL disponible y un sandbox vacío no acreditan que ARIMA_PLUS o KMEANS se puedan entrenar en el proyecto académico. [Límites de BigQuery Sandbox](https://docs.cloud.google.com/bigquery/docs/sandbox) · [Prerrequisitos de BigQuery ML](https://docs.cloud.google.com/bigquery/docs/create-machine-learning-model)
2. **Leonardo · stream_exporter.** Implementado en el worktree a partir de las imágenes anterior y nueva de DynamoDB. La propuesta para Academy archiva lotes comprimidos en un bucket S3 de datos y separa lecturas incompletas, cambios de incidentes y consumos de stock. Falta acordar el transporte y la fuente de telemetría íntegra con Alexis, y comprobar el bucket, los permisos y el mapping en otro ensayo autorizado. [Exportador y pruebas](../services/stream_exporter/README.md)
3. **Leonardo · lake_to_bigquery.** Implementados el ingreso opcional de Parquet, las cargas batch, los recibos por lote y la reanudación por job ID. Se ensayaron en local fallos antes y después de una confirmación, registros repetidos y lotes repartidos de otra manera. El próximo hito con acceso GCP será verificar las tablas e identidad y pedir autorización para un único lote sintético. [Cargador y pruebas](../services/lake_to_bigquery/README.md)
4. **Alexis · SQL/ML y retorno.** El repositorio contiene DDL de `af_lake`, `af_ml` y `af_out`, seis etapas SQL, generador sintético y el servicio `bigquery_sync_back`. AWS no tiene desplegados los dos handlers de Leonardo ni el sync-back con sus nombres previstos. En paralelo al GIS, acordar un lote, identificar el job/modelo real, comprobar un resultado en `af_out` y verificar su retorno idempotente a DynamoDB. No activar Scheduler ni consultas programadas para la primera prueba. Criterio de aceptación: mismo lote/operación, job ID, filas y resultado trazable en ambas nubes. [Analítica #4](https://github.com/alxgr-08/arquifusion/issues/4) · [Sync-back #5](https://github.com/alxgr-08/arquifusion/issues/5)

## Solicitudes publicadas en GitHub

Los siguientes son los textos publicados y los enlaces a cada issue asignado.

**Alexis · Rol 3 · Cliente web de Google, origen autorizado y Project ID de BigQuery**

@alxgr-08, soy Leonardo (Rol 1). Para preparar el ensayo de ArquiFusion en AWS Academy necesitamos confirmar si ya existe un cliente OAuth 2.0 de tipo Aplicación web para Google Identity Services. Comparte solo el Client ID público (*.apps.googleusercontent.com), el origen HTTPS exacto autorizado (incluye puerto si corresponde, sin ruta) y el Project ID público del equipo para BigQuery. Si aún no existen o necesitas acceso institucional, indica cuál falta y quién puede habilitarlo. El proyecto del login y el de BigQuery pueden ser distintos. No adjuntar client secret, JSON privado, tokens ni variables de entorno.

¿Puedes confirmar la integración GIS en modo VITE_API_MODE=live, el ID token solo en memoria, VITE_API_URL con el stage /dev, limpieza de sesión y reintento del mismo acuse con If-Match? El frontend deberá obtener la versión pública de la espera desde una fuente autorizada y distinguir un 503 pendiente de un cierre confirmado. El selector visual de rol no otorga acceso. La primera API solo tiene la ruta POST de acuse.

En paralelo a la API, para #14 y #15 confirma el proyecto, ubicación US de af_lake/af_ml/af_out, accesos de lectura, disponibilidad y modalidad permitida de BigQuery ML, y formato esperado de telemetry, incidents y parts_movements. Así acordamos un lote sintético trazable y el sync-back.

[Ver issue #41 asignado a Alexis](https://github.com/alxgr-08/arquifusion/issues/41)

**Sebastián · Rol 2 · Revisión de autorización, reintentos y recibos de POST /ack**

@sebastianperez72, soy Leonardo (Rol 1). Dando continuidad a #23, preparé localmente una integración candidata para POST /machines/{id}/incidents/{incId}/ack. El endpoint está cerrado. Necesitamos tu revisión del protocolo antes de abrirlo.

1. JWT nativo de API Gateway con el Client ID de Google de ArquiFusion. Resolver iss+sub contra una concesión por operación en DynamoDB. TECHNICIAN exige asignación exacta, MAINT_LEAD queda limitado a su operación. Volver a comprobar concesión, versión y asignación en la transacción.
2. Guardar pendingExecutionArn de la hija, etapa y generación pública de la espera. Acordar con Alexis cómo se comunica al cliente y el uso de If-Match. Conservar los dos cuerpos y el DTO Incident.
3. Crear un recibo PENDING por incidente, etapa y generación antes de SendTaskSuccess. Payload, actor y hora estables. El workflow certifica ARRIVED o RESOLVED y sus efectos exactamente una vez, preservando el siguiente token. Si se pierde la respuesta, reconciliar por ackId en la misma ejecución. InvalidToken por sí solo no demuestra éxito ni vencimiento.
4. Probar revocación, reasignación, vencimiento, doble clic, falla antes/después del callback, cierre interrumpido y reintento al día siguiente. Debe quedar una fila de mantenimiento y un único consumo/liberación de reservas, técnico y PROFILE.

¿Aceptas este diseño o qué cambio propones? Comparte la revisión o PR con la evidencia. No incluir tokens de tarea, JWT ni salidas completas del historial.

[Ver issue #42 asignado a Sebastián](https://github.com/alxgr-08/arquifusion/issues/42)

## Orden para continuar

Alexis puede responder en #41 y Sebastián en #42. Leonardo puede preparar el paquete Linux y cotejar el contrato de entrada analítica con las respuestas. Con el cliente GIS, el origen y el protocolo acordado, repetir las pruebas de la API y generar un plan nuevo para revisión. Tras autorización, el ensayo de API usaría un incidente sintético en Academy con frontend live y la regla DISABLED. En paralelo, con el Project ID real se puede ejecutar la inspección de GCP en modo lectura. El primer lote real requiere una autorización de escritura propia. [Pruebas HTTP y criterios de aceptación](../infra/aws/api/ensayo.md) · [Revisión técnica del backend](../infra/aws/api/revision-ack.md)
