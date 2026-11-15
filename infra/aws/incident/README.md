# Incidentes de desarrollo en AWS Academy

El 15/11 el código v1.2 se desplegó y el caso ENSAYO-3816 cerró INC-a8c8 con dos recibos ACCEPTED, un mantenimiento y una pieza. La regla permanece deshabilitada. La API del ensayo se ejecutó localmente mediante IAM. Los detalles siguientes conservan el historial del primer despliegue. [Relevo y evidencia actual](../../../docs/revision-nocturna-2026-11-15.md) · [Ejecutor manual reproducible](ensayo-manual.md)

## Primer despliegue de desarrollo

Rama `infra/rol1-aws-dynamodb-plan`, commit `be19d3e`. Tras `git fetch origin`, la diferencia con `origin/main` fue 0/0. Sebastián incorporó el ASL, las tareas, la asignación, las notificaciones y los callbacks en #39. El #40 integró el evaluador y el diagnóstico de Alexis y alineó la entrada de IA con `aiMachine`, `aiContext`, `aiHistory` y `aiZoneCorrelation`. La definición de incidentes del plan conserva todos los estados y comentarios de ese commit, con los cuatro ARN resueltos. [PR #39](https://github.com/alxgr-08/arquifusion/pull/39) · [PR #40](https://github.com/alxgr-08/arquifusion/pull/40)

Plan de desarrollo guardado en `build/terraform-review/incident-integration-standard.tfplan`, perfil `default`, región `us-east-1`. SHA-256 `0A66521CBCFE54E4D3E0DDCDA80E9882F0730483F77F185BC5F40B3DAEB4110C`. Resultado `Plan: 21 to add, 0 to change, 0 to destroy`. El binario anterior, `incident-integration.tfplan`, corresponde al diseño con el catálogo en Advanced y queda como histórico. La comparación de los dos planes limita la diferencia al parámetro del catálogo y a su lector en la nueva Lambda de tareas.

Los seis recursos desplegados figuran como `no-op` en el plan con refresco, incluida la tabla en `aws/terraform.tfstate`. El proveedor normalizó mapas `tags` y la lista de capas vacíos. Las etiquetas efectivas y el código desplegado del evaluador coinciden. La preparación del primer plan dejó versiones temporales de `aws/terraform.tfstate.tflock` en S3. Para esta revisión se usó `plan -lock=false` en una ventana sin despliegues simultáneos. El bootstrap conserva su estado local separado.

| Altas | Uso |
|---:|---|
| 4 Lambda y 4 grupos de logs | `af-dev-incident-tasks`, `af-dev-assign-technician`, `af-dev-ai-diagnosis` y `af-dev-notify`. Python 3.12, Linux x86_64, grupo por función con retención de 14 días. |
| 2 máquinas Standard | `af-dev-incident-entry` identifica reenvíos. `af-dev-incident` ejecuta el ASL de Sebastián y espera los callbacks. |
| 1 regla y 1 destino | Bus existente `af-dev-bus`. Fuente `arquifusion.health`, tipo `AnomalyDetected`, severidad `WARNING` o `CRITICAL`. El plan crea la regla `DISABLED`. |
| 1 cola SQS y 1 política de cola | `af-dev-incident-delivery-dlq`, 14 días. Solo la regla de esta cuenta puede escribir por la concesión al servicio EventBridge. |
| 3 parámetros SSM y 1 tópico SNS | `/af/incident/settings`, `/af/incident/fleet-catalog`, `/af/notify/topics`. Las cinco audiencias usan el tópico de desarrollo `af-dev-incident-notifications` y el atributo SNS `role`. El plan crea cero suscripciones. |
| 3 alarmas CloudWatch | Mensajes visibles en la DLQ y ejecuciones fallidas de cada máquina. Las alarmas quedan visibles en CloudWatch, sin destinatarios externos. |

Las Lambda de Rol 2 reciben `AF_TABLE_NAME`. La de diagnóstico recibe `AF_AI_PROVIDER=rules`. Los cuatro paquetes incluyen `af_shared` y las dependencias con hashes fijados. El paquete de IA incorpora también `anthropic[bedrock]`, aunque el ensayo usa el diagnóstico determinista. Asignación utiliza la distancia geográfica del código con `AF_ROUTE_CALCULATOR` ausente. El flujo consulta los índices GSI1 y GSI2 de la tabla.

## Catálogo de flota

El simulador y el inyector de fallas de Sebastián validan el JSON completo de `/af/sim/fleet-config` como `FleetConfig`. La acción `reserve_parts` de incidentes hace la misma lectura. El script `scripts/seed_demo.py` publica el catálogo completo en esa ruta y también escribe otros datos. Cambiar el valor histórico por una URI rompería los lectores actuales. El ensayo usa su propia semilla sintética. [Simulador](../../../services/simulator/handler.py) · [Inyector](../../../services/fault_injector/handler.py) · [Tareas de incidentes](../../../services/incident_tasks/incidents.py) · [Semilla de demostración](../../../scripts/seed_demo.py)

El catálogo de `contracts/examples/fleet-config.json`, serializado por Terraform, ocupa **4.248 bytes UTF-8**. El plan guarda el catálogo completo en gzip y Base64 junto a su SHA-256 en `/af/incident/fleet-catalog`. El valor completo del parámetro **Standard ocupa 1.854 de 4.096 bytes**. Terraform comprueba ese límite al planificar. El adaptador del Rol 1 obtiene el parámetro indicado en `AF_INCIDENT_CATALOG`, verifica el hash y entrega el documento a la función `dispatch` de Sebastián. En la prueba, las 12 máquinas produjeron la misma telemetría que con el JSON original. El simulador y el inyector conservan su configuración histórica. [Catálogo canónico](../../../contracts/examples/fleet-config.json) · [Límites de Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/parameter-store-advanced-parameters.html)

**Opción S3 para cuando crezca el catálogo.** El nombre reservado para datos, `af-dev-catalog-<cuenta>`, devolvió HTTP 404 en Academy. El único bucket encontrado de ArquiFusion es el de estado. Sobre ese bucket, la lectura `s3:GetBucketObjectLockConfiguration` que necesita el proveedor al administrar un bucket devolvió una denegación explícita de la política de control del laboratorio. La simulación IAM permitió a `LabRole` leer la versión del objeto previsto, pero la lectura real del catálogo en S3 queda pendiente de disponer del objeto y del bucket.

Para usar esta opción, Leonardo debe revisar por separado el aprovisionamiento de un bucket privado de datos en `us-east-1` con versionado, cifrado y Block Public Access. Publicar el JSON completo como `application/json` en `catalogs/fleet-config.json` mediante esa operación autorizada y guardar el ID de la versión. Otro plan con `-var='incident_catalog_s3_version=<versión>'` comprobará región y contenido, y calculará la referencia SSM con bucket, clave, versión y SHA-256. En ejecución, la Lambda exige la cuenta propietaria y lee esa versión exacta. El plan actual usa únicamente SSM para el catálogo. Una migración de la ruta compartida requiere acordar con Sebastián la actualización conjunta del simulador, el inyector y la semilla. [Lectura de una versión de S3](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)

## Permisos y disponibilidad

La sesión de despliegue es `voclabs`. AWS MCP ejecutó `GetCallerIdentity`, `GetRole`, `ListRoles`, `DescribeEventBus` y cinco llamadas `SimulatePrincipalPolicy`. La CLI contrastó los ARN de esta cuenta y región. La política de confianza leída de IAM incluye `events.amazonaws.com`, `states.amazonaws.com` y `lambda.amazonaws.com` para `sts:AssumeRole`.

| Identidad | Resultado de la simulación IAM |
|---|---|
| Sesión `voclabs` | `allowed` para `states:CreateStateMachine`, `events:PutRule/PutTargets`, aprovisionamiento de los recursos del plan e `iam:PassRole` del `LabRole` a los tres servicios. La simulación de `ssm:PutParameter` en la nueva ruta con nivel Standard devolvió `allowed`. |
| `LabRole` asumido por EventBridge | `states:StartExecution` permitido para la entrada. La confianza y el permiso del rol de destino se comprobaron por separado. |
| `LabRole` asumido por Step Functions | `states:CreateStateMachine` apareció permitido para el propio rol. En el despliegue crea `voclabs`. En ejecución, el rol puede iniciar la máquina de incidentes, consultar su ejecución por ARN e invocar las cuatro funciones. |
| `LabRole` asumido por Lambda | Lecturas y escrituras de DynamoDB para incidente, técnico y stock, consultas GSI, `ssm:GetParameter`, publicación al tópico y escritura de logs permitidas. El cierre puede escribir `/af/sim/faults` si ya contiene una falla de esa máquina. |
| Operador del ensayo | `voclabs` obtuvo `allowed` para `events:PutEvents`, las operaciones subyacentes de la semilla en DynamoDB y `states:SendTaskSuccess`. El callback requiere una identidad de la misma cuenta. |

**Lectura confirmada.** Bus presente y tabla `ACTIVE` con `NEW_AND_OLD_IMAGES`. Los nombres de las dos máquinas, las cuatro funciones, sus grupos, los tres parámetros, la regla, la cola, el tópico y las alarmas estaban libres en la revisión. La API `af-dev-api` tampoco estaba desplegada. **Escritura pendiente de probar.** Las decisiones del simulador dejan por confirmar la creación y el encadenamiento de roles por los servicios bajo las restricciones efectivas de la sesión Academy. [Rol de EventBridge para Step Functions](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-events-iam-roles.html) · [Alcance del simulador IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_testing-policies.html)

## Entrega, reintentos e idempotencia

EventBridge reintenta la entrega a la entrada hasta 185 veces durante 24 horas con backoff y jitter. Los fallos de entrega agotados o no reintentables van a la DLQ. Una vez que Step Functions acepta la ejecución, los errores de negocio se investigan en su historial y en `ExecutionsFailed`. Revisar también `ExecutionsTimedOut`, `ExecutionsAborted` e `InvocationsFailedToBeSentToDLQ` al reconciliar un ensayo. [Reintentos de EventBridge](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-rule-retry-policy.html) · [Entrega a la DLQ](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-rule-dlq.html)

El destino nativo de la regla inicia `af-dev-incident-entry`. Esta máquina calcula un SHA-256 de los campos del contrato v1 con orden explícito y usa ese valor como nombre de la ejecución Standard de `af-dev-incident`. Conserva `source`, `detail-type` y `detail`. Un nuevo ID de transporte y un cambio de orden en las claves JSON producen el mismo nombre. Un cambio de severidad produce otro nombre y permite que el `claim` de Sebastián escale el incidente abierto. Si el inicio devuelve un error, la entrada comprueba ARN, estado y contenido del intento previo. Solo da el reenvío por atendido con contenido igual y estado `RUNNING` o `SUCCEEDED`. Un fallo, entrada distinta o ejecución ausente queda visible para revisión. Un arranque aceptado todavía puede fallar dentro del ASL.

La entrada admite eventos de hasta 30 días y una desviación futura de 5 minutos. Así, el período de reenvío admitido cabe dentro de la reserva de nombres de Standard durante 90 días tras el cierre. Los reenvíos más antiguos requieren reconciliación manual. El contrato v1 carece de ID estable de lectura y el productor expresa `detectedAt` en segundos. Dos entradas con todos los campos idénticos son indistinguibles. [Idempotencia de StartExecution](https://docs.aws.amazon.com/step-functions/latest/apireference/API_StartExecution.html)

El ASL conserva los reintentos transitorios, el diagnóstico alternativo y la liberación condicional de la máquina al fallar. `PROFILE.openIncidentId` limita a un incidente abierto. Sebastián debe revisar la pérdida de la respuesta de `claim` después de escribir el perfil. Repetir esa tarea puede tratar a su propia ejecución como duplicada y dejar el incidente sin materializar. En recuperación, contrastar perfil, fila de incidente, técnico, reservas y ejecución antes de redirigir o reintentar. El endpoint `/ack` también consume el token en DynamoDB antes de llamar a `SendTaskSuccess`. Un error entre ambos pasos requiere reconciliación. [Recuperación de ejecuciones Standard](https://docs.aws.amazon.com/step-functions/latest/dg/redrive-executions.html)

## Revisión del laboratorio del 14 de noviembre de 2026

El issue abierto del flujo está asignado a Sebastián. Leonardo preguntó allí por el evento, la semilla, los callbacks, las reservas, la idempotencia, los límites y la disposición del backend para el ensayo. Alexis está mencionado por la publicación fiable de anomalías. Al cierre de la revisión, el único comentario era el de Leonardo. La aprobación de los PR #39 y #40 corresponde al código fusionado. [Comentario de coordinación en #23](https://github.com/alxgr-08/arquifusion/issues/23#issuecomment-6888454551)

Un segundo plan con refresco y `-lock=false` coincidió con el binario revisado en todas las acciones y atributos, las variables y los outputs. El estado S3 tenía el mismo linaje y serial 2. El bucket conserva el cifrado SSE-S3, el versionado y los cuatro controles de Block Public Access. La regla todavía no existe. AWS MCP y CLI consultaron el bus. La CLI encontró cero reglas en el bus, cero reglas del bus predeterminado dirigidas a las máquinas, cero programaciones identificadas por nombre del proyecto o destino directo, y cero mapeos de origen de eventos en el evaluador. En el ASL los destinos son Lambda, SNS mediante la función de notificación y una máquina hija. Ninguno publica de vuelta al bus como `arquifusion.health / AnomalyDetected`.

La simulación permitió a `voclabs` crear la máquina, pasar `LabRole` a Lambda, EventBridge y Step Functions, arrancar el ensayo y detener la ejecución de incidentes. El rol de ejecución puede iniciar la máquina, invocar las cuatro Lambdas y utilizar DynamoDB, sus índices, SSM, SNS y Logs. La confianza del rol para los tres servicios es amplia y carece de condición de origen en esa declaración. Se conserva el rol provisto por Academy. El ensayo real verificará la asunción por los servicios. Los ZIP coincidieron con los hashes del plan y contienen `af_shared` y extensiones ELF64 para Linux x86_64 con CPython 3.12. La cuenta usa SSM Standard por defecto. Los valores del catálogo, los ajustes y el mapa de cinco audiencias ocupan respectivamente 1.854, 210 y 396 bytes. El último tamaño se calculó con el ARN determinista del tópico que se crearía.

| Riesgo | Evidencia y control del ensayo |
|---|---|
| Espera sin límite práctico | El ASL carece de `TimeoutSeconds` global. Sin técnico, espera 120 s y tras cinco intentos notifica, reinicia el contador y vuelve a intentar. Pasados 30 min sin llegada, libera y reasigna. Cada 4 h sin resolución, notifica y vuelve a esperar. Standard puede mantener abierta una ejecución hasta un año. Acordar con Sebastián un límite terminal y una compensación antes de automatizar. [Cuotas de Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html) |
| Concurrencia de la cuenta compartida | El Readme de este Learner Lab fija un máximo de 10 entornos Lambda concurrentes. La cuota técnica que devolvió GetAccountSettings fue 400. Para el ensayo rige el límite del curso y se considera el uso de los demás proyectos. El ASL enriquece contexto en tres ramas y permite hasta cinco notificaciones en paralelo. Mantener un solo incidente en vuelo y un operador con ambos callbacks disponibles. |
| Fallos que necesitan observación | Cuatro grupos Lambda retienen logs 14 días. La cola de fallos de entrega conserva mensajes 14 días y solo permite a la regla de la cuenta escribir por su política de servicio. El plan crea tres alarmas con periodo de 60 s: dos para `ExecutionsFailed` y una por mensajes visibles en la DLQ. Sin acciones de alarma, el operador debe observarlas. Un `StopExecution` produce `ABORTED`; comprobar además ese estado y `TIMED_OUT`. |
| Gasto | AWS Budgets devolvió cero presupuestos visibles. Cost Explorer estimó **USD 0,99** para toda la cuenta del 1 al 14 de noviembre. La captura de Academy mostraba USD 1 usados de USD 50; el saldo de créditos vigente no se obtuvo por API. A tarifa pública, tres alarmas estándar cuestan aproximadamente USD 0,30 por mes completo antes de franquicias. El caso local recorrió 26 estados internos contando ramas y mapa; a USD 0,000025 por transición, su equivalente de Step Functions sería USD 0,00065 antes de franquicias, reintentos, entrada, llamadas a otros servicios y logs. No es una factura del ensayo. [Precios de Step Functions](https://aws.amazon.com/step-functions/pricing/) · [Precios de CloudWatch](https://aws.amazon.com/cloudwatch/pricing/) |

## Ensayo controlado

Generar un caso nuevo para cada fase con `prepare_case.py`. La semilla local contiene solo dos perfiles nuevos, de máquina y técnico, en una transacción condicional. La vibración en una alerta `WARNING` da `DRIVETRAIN`. El generador comprueba que el catálogo carece de repuestos de ese componente, porque las claves de stock se comparten entre operaciones. El caso llegó a `Done / RESOLVED` en memoria y los 44 registros previos conservaron sus valores.

Pasaron seis ejemplos de contrato, 125 pruebas del repositorio, diez pruebas de infraestructura de incidentes y dos del evaluador. El recorrido local ejercita el adaptador y el ASL de Sebastián, la notificación al técnico, ambos callbacks y la liberación de máquina y técnico. Una segunda variante en memoria añadió un repuesto y un almacén exclusivos del UUID del caso. De dos unidades disponibles, reservó una, consumió una al resolver y terminó con una disponible, cero reservadas y cero datos previos alterados. El plan contiene el catálogo canónico. Para acreditar una reserva positiva en AWS, acordar primero un catálogo y stock aislados en otro plan. El ensayo con este binario acredita el camino de cero piezas sin exponer inventario compartido.

Se descomprimió el valor extraído del plan binario y se cotejó con el JSON del repositorio. Dos compilaciones independientes del paquete de tareas generaron el mismo SHA-256. El ZIP incluye las fuentes originales de Rol 2 y el adaptador del Rol 1. El SHA del ZIP local de Health Evaluator coincide con el código desplegado que informa Lambda.

Durante la preparación anterior, AWS validó las dos definiciones renderizadas. `TestEventPattern` acertó los ocho casos, incluidos origen/tipo incorrectos, las cuatro severidades y la operación aislada. `TestState` verificó la identidad estable, la escalada diferenciada y los límites de antigüedad en estados puros. Las respuestas de integraciones simuladas comprobaron duplicado, contenido distinto y ejecución fallida. Ambas definiciones y el filtro del plan actual coinciden con los del plan anterior. El ensayo en AWS queda pendiente de aprobación.

La preparación se puede reproducir desde la raíz del worktree en PowerShell.

```powershell
$env:AWS_PROFILE = 'default'
$env:AWS_REGION = 'us-east-1'
$env:PYTHONPATH = (Resolve-Path shared).Path
py -3.12 infra/aws/health_evaluator/build.py --incident
.\.venv\Scripts\python.exe -m pytest -q infra/aws/incident infra/aws/health_evaluator/test_smoke.py
$case = (.\.venv\Scripts\python.exe infra/aws/incident/prepare_case.py).Trim()
terraform -chdir=infra/aws fmt -check
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws show -no-color ../../build/terraform-review/incident-integration-standard.tfplan
Get-FileHash -Algorithm SHA256 build/terraform-review/incident-integration-standard.tfplan
```

### Ensayo después del PR #49

La prueba histórica del despliegue de noviembre usó un callback directo con un token de Step Functions. El ASL actual certifica cada confirmación contra un recibo de DynamoDB. Para la próxima corrida usa el kit de Sebastián y la API que registra el recibo. El adaptador del catálogo reconoce la ruta `AF_INCIDENT_CATALOG` y verifica el contenido antes de entregar la flota al flujo. [Kit de ensayo y evidencia esperada](../../../docs/ensayo-lab/README.md) · [API de desarrollo](../api/README.md)

1. Genera los archivos locales con `python scripts/ensayo_kit.py --run 17 --out build/ensayo-0017`. Elige un número libre para cualquier prueba en AWS. Revisa que las filas correspondan solo a esa operación.
2. Tras una autorización de despliegue y otra para el ensayo, usa una sola ejecución manual. Conserva por separado el ARN de entrada y el de la máquina hija. La regla queda deshabilitada.
3. En el ensayo con identidad real, usa Google Sign-In, el GET autenticado y su `ETag`. Envía `ARRIVED` con ese `If-Match` y el JWT de la web. Si se pierde la respuesta o llega `503 CALLBACK_PENDING`, conserva el mismo cuerpo y versión. Para la nueva espera de resolución vuelve a leer el incidente y confirma `RESOLVED` con su propio ETag. Ante `412 STALE_WAIT` revisa quién está asignado y pide confirmación para la nueva espera.
4. Comprueba un único incidente, un único mantenimiento, consumo exacto de repuestos de la corrida, liberación de máquina y técnico, los dos recibos y la ejecución final. La publicación SNS y la recepción por una persona tienen evidencias separadas. Una invocación directa de prueba con claims sintéticos solo comprueba la lógica del backend.

## Recuperación de alertas

La versión desplegada de Health Evaluator es anterior a #44. El código de Alexis en `main` persiste `pendingAlert` en el perfil y reintenta la publicación si vuelve a llegar una lectura. Una máquina silenciosa puede dejar un pendiente sin procesar. La concurrencia y una respuesta perdida de EventBridge requieren deduplicación y reconciliación. Para recuperación independiente del tráfico, el equipo puede acordar una fila `OUTBOX#<messageId>` por alerta en la misma transacción, con publicador del Stream y recuperación de pendientes. El exportador analítico filtra únicamente incidentes y movimientos de stock. [PR #44](https://github.com/alxgr-08/arquifusion/pull/44) · [Diseño de outbox de AWS](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)
