# API de incidentes en AWS Academy

La API queda fuera del plan por defecto. Incluirla prepara dos rutas de incidentes en el stage `dev`, con el autorizador JWT de Google y una integración Lambda proxy de payload 2.0. El endpoint sigue cerrado hasta activar `open_incident_api_endpoint` en un plan de ensayo. [Contrato v1.2](../../../contracts/api.md) · [Servicio y permisos](../../../services/api/README.md)

| Ruta | Acceso y respuesta |
|---|---|
| `GET /machines/{id}/incidents/{incId}` | Requiere un perfil atribuible, su grant y coincidencia con la operación del incidente. Devuelve `Incident` y, cuando existe una espera versionada, su `ETag`. |
| `POST /machines/{id}/incidents/{incId}/ack` | Exige `If-Match` con la versión vista y una concesión TECHNICIAN con el técnico asignado o MAINT_LEAD de esa operación. |

`af-dev-api-ack` ejecuta `handler.handler` en Python 3.12, con 256 MB y 15 s. El ZIP incluye `handler.py`, `routes.py`, `af_shared/` y las dependencias Linux x86_64 fijadas del proyecto. La función recibe `AF_TABLE_NAME` y `AF_OPERATION_ID`. El rol de ejecución se obtiene del `LabRole` existente. Cada ruta tiene un permiso de invocación para su cuenta, API, stage, método y patrón de recurso.

`enable_fleet_read_routes` añade diez GET al mismo autorizador e integración. El stage permite una ráfaga de cuatro solicitudes y mantiene la tasa de una por segundo. El único POST disponible sigue siendo el acuse. Las pruebas locales ya ejercitan el aislamiento de estas lecturas. [Pruebas por operación](../../../services/api/tests/test_operation_scope.py) · [Rutas y roles del servicio](../../../services/api/routes.py) · [Contrato de las pantallas](../../../contracts/api.md)

| Pantalla | Rutas GET adicionales | Autorización en el servicio |
|---|---|---|
| Flota de una operación | `/operations/{op}/machines`, `/operations/{op}/incidents`, `/operations/{op}/technicians`, `/operations/{op}/kpis` | Grant para `{op}` y consulta de su partición del GSI. El KPI requiere MAINT_LEAD, SUPERVISOR o MANAGER. |
| Detalle e historial | `/machines/{id}`, `/machines/{id}/readings`, `/machines/{id}/incidents`, `/machines/{id}/maintenance` | Perfil atribuible para el detalle y procedencia de la operación para cada fila histórica. El historial de otra operación queda fuera del resultado. |
| Piezas | `/parts`, `/parts/{partId}/stock` | Rol de personal autorizado para la operación solicitada y existencias y pronóstico únicamente de los almacenes incluidos en su `OP#… / META`. |

## Cliente y origen

El cliente web público confirmado en #41 es `197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com`. Alexis confirmó el registro de `https://demo.d2ygcm34t8aku9.amplifyapp.com` y mantiene `http://localhost:5173` para desarrollo. La API del ensayo admite únicamente ese origen HTTPS. [Confirmación del propietario](https://github.com/alxgr-08/arquifusion/issues/41#issuecomment-6897046368)

El autorizador comprueba el JWT de Google contra `https://accounts.google.com` y la audiencia registrada. El handler usa `iss` y `sub` del contexto para consultar `GRANT#<iss>#<sub> / OP#<operación>`. La transacción del POST comprueba la concesión y la espera. Las cuentas de prueba y sus concesiones se preparan durante el ensayo autorizado. [Revisión de #49](./revision-ack.md) · [Autorización de API Gateway](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html) · [Código de concesiones](../../../shared/af_shared/grants.py)

El gateway admite `GET` y `POST`, los encabezados de entrada `Authorization`, `Content-Type` e `If-Match`, y expone `ETag` y `Retry-After` al navegador. API Gateway responde al preflight y su configuración CORS prevalece sobre los encabezados CORS del handler, incluido el comodín de `af_shared.http`. [CORS de las HTTP APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-cors.html)

## Preparación y revisión

Desde la raíz del proyecto, con el entorno Python de desarrollo y los paquetes de infraestructura de la base ya compilados:

```powershell
python infra/aws/health_evaluator/build.py --api
python -m pytest -q services/api/tests shared/tests/test_grants.py infra/aws/api
terraform -chdir=infra/aws test -test-directory=api
```

Las pruebas Terraform usan un proveedor AWS simulado. El modo por defecto omite la API y su ZIP. Incluirla requiere el ZIP, el cliente web y su origen registrado. Esta configuración prepara el plan cerrado de desarrollo.

```hcl
enable_incident_api      = true
ack_google_web_client_id = "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
ack_frontend_origin      = "http://localhost:5173"
```

| Configuración para el ensayo | Exposición y alcance del plan |
|---|---|
| Solo `enable_incident_api = true` | Stage reservado y cerrado. Dos rutas y 11 recursos de API. |
| Además `open_incident_api_endpoint = true` | Stage HTTPS accesible, dos rutas con JWT. Se conserva el mismo origen exacto. |
| Además `enable_fleet_read_routes = true` | Diez lecturas adicionales, doce rutas y 31 recursos de API en total. Este interruptor es independiente de la apertura. |

`incident_api_base_url` identifica el stage. Para cerrar un ensayo ya desplegado, preparar un plan con `enable_incident_api = true`, las mismas rutas y `open_incident_api_endpoint = false`. El resumen debe mostrar el cierre del endpoint. La aplicación de cada plan requiere la autorización de Leonardo.

## Ensayo acordado y despliegue del 15/11

El perfil público [ensayo-0001.tfvars.example](ensayo-0001.tfvars.example) conserva Amplify y configura la API con las doce rutas JWT para la operación `ENSAYO-0001`. La autorización de Leonardo permitió aplicar exclusivamente el plan guardado `api-open.tfplan`, con SHA-256 `87F604A64FDBB40E592D04164D2030918F165798E66A60BCA5DFC5E6C79116BB`: **31 altas, 0 cambios y 0 bajas; los 29 recursos anteriores sin cambios**. Su URL real es `https://8wrfp8ncbh.execute-api.us-east-1.amazonaws.com/dev`. La regla de incidentes permanece deshabilitada. [Relevo del cierre y comprobaciones](../../../docs/cierre-rol1-2026-11-15.md)

El endpoint exige JWT incluso mientras faltan los datos del ensayo y las concesiones reales. La API usa `ENSAYO-0001`; los usuarios necesitan una concesión para esa operación, y TECHNICIAN debe corresponder a `T-E0001`. No reutilizar automáticamente permisos de `MINA-NORTE`. La preparación offline usa los scripts de Sebastián y rechaza plantillas de participantes incompletas:

```powershell
python infra/aws/incident/preflight_web_demo.py prepare --kit build/ensayo-0001
python infra/aws/incident/preflight_web_demo.py check --kit build/ensayo-0001
# Solo con una ruta privada indicada expresamente por el propietario:
python infra/aws/incident/preflight_web_demo.py check --kit build/ensayo-0001 --require-real-grants --grants-file <ruta-privada>
```

Estos comandos no consultan AWS ni crean concesiones. Validar la estructura de un `sub` tampoco demuestra que pertenezca a la persona; eso se comprueba mediante su login Google. La consulta remota de colisiones y cualquier siembra son pasos distintos. El seed condicional puede crear filas anteriores antes de encontrar un conflicto posterior: exigir un preflight sin conflictos y conciliar antes de repetir.

Después de esta alta hay **60 recursos administrados**. Conservar las opciones de API, hosting, origen y operación del perfil en todos los planes posteriores. La copia local `api-open.auto.tfvars.json` está excluida de Git. Los planes alternativos guardados contra 29 recursos quedaron obsoletos; `plan_stage.py` los bloquea por inventario hasta que se adapte su revisión a la nueva base. Nunca aplicar un plan que proponga retirar la API por usar los valores por defecto.

`review_plan.py` compara el HCL del plan guardado con el checkout. En el modo histórico controla las 27 direcciones originales. Después de crear Amplify se debe añadir `--existing-hosting`: exige que las 29 direcciones existentes permanezcan en no-op y verifica la propiedad, cuenta, región, relación app/rama y origen del hosting manual. También controla los cinco paquetes de Lambda, el workflow, la regla desactivada y las direcciones exactas de la API. Exige que el estado del endpoint, las rutas JWT y el origen coincidan con la intención del revisor. En un stage ya creado admite cambios de apertura/CORS, de ráfaga y de código de la API dentro de ese alcance. Coteja el ZIP con el hash del plan y los módulos de `services/api` y `shared/af_shared`. El resumen `.review.json` contiene conteos, rutas, direcciones y hashes. El JSON completo de Terraform se procesa en memoria.

```powershell
python infra/aws/api/review_plan.py build/terraform-review/r1-base.tfplan --expect disabled
python infra/aws/api/review_plan.py build/terraform-review/r1-api-revision.tfplan --expect closed
python infra/aws/api/review_plan.py build/terraform-review/ensayo-api.tfplan --expect open
python infra/aws/api/review_plan.py build/terraform-review/ensayo-flota.tfplan --expect open --fleet-reads
```

Los nombres de archivo son ejemplos para los planes recién generados. Para un origen HTTPS registrado, añadir `--origin` con su valor exacto. El cliente live conserva el ETag de la espera mostrada, lo envía en `If-Match` y lee `Retry-After`. [Cliente del frontend](../../../frontend/src/api/client.ts)

### Preparación posterior al hosting

Amplify ya administra la aplicación `af-dev-arquifusion-web` y la rama manual `demo`. El origen reservado es `https://demo.d2ygcm34t8aku9.amplifyapp.com`. Alexis debe confirmar su registro en Google. Mientras responde, el perfil de revisión [hosting-review.tfvars.example](hosting-review.tfvars.example) prepara las 12 rutas del dashboard con el endpoint cerrado. El CORS exacto no demuestra por sí solo que Google haya autorizado el origen.

Después del hosting, los planes anteriores basados en 27 recursos quedaron obsoletos. Conservar `prepare_manual_web_hosting=true` al regenerar cualquier etapa y utilizar `--existing-hosting` para su revisión. El perfil completo debe proponer 31 altas de API, cero cambios/bajas y 29 no-op. El endpoint se abrirá con otro plan revisado cuando se confirme el origen y se autorice el ensayo. [Plan y comprobaciones actuales](plan-hosting-2026-11-15.md)

```powershell
python infra/aws/api/review_plan.py build/api-hosting-20261115/api-dashboard-closed.tfplan --expect closed --fleet-reads --existing-hosting --origin https://demo.d2ygcm34t8aku9.amplifyapp.com
```

Evidencia vigente del 15/11 y ensayos posteriores de Parquet y BigQuery en la [arquitectura](../../../docs/arquitectura.md).

## Evidencia local y siguiente ensayo

Pasaron 33 pruebas del servicio API, 15 pruebas Terraform con proveedor AWS simulado y 8 del verificador de planes. Se comprobó el rechazo del detalle de otra operación después de un traslado (403) y de un perfil ausente (404), el filtrado de históricos mezclados y el de una misma pieza entre dos almacenes de operaciones distintas, incluso con grants de roles diferentes para la misma persona. La prueba local del runtime exige autenticación en Lambda aun si alguien configura por error el interruptor de depuración. [Pruebas por operación](../../../services/api/tests/test_operation_scope.py)

Un mismo lote sintético recorrió el evaluador, el ASL con sus dos acuses, el archivo Parquet y la integración de BigQuery con dobles locales. Quedan pendientes la prueba del JWT en API Gateway por HTTPS, el callback en el laboratorio y la evidencia del mismo lote entre ambas nubes. [Ensayo local conjunto](../../../services/api/tests/test_multicloud_rehearsal.py) · [Ensayo HTTPS pendiente](./ensayo.md)

## Evidencia histórica de la cuenta

Las observaciones siguientes corresponden a la auditoría del 14 y preceden a #41 y #49. Las cifras de recursos, permisos y ejecuciones de esa fecha son antecedentes para el laboratorio.

## Auditoría de partida del 14 de noviembre

**Revisión del 14 de noviembre de 2026, hora de Lima.** Se partió del relevo de las 23:09 y del worktree `infra/rol1-aws-dynamodb-plan` en `be19d3e6ae23`. Después de `git fetch origin`, la diferencia de commits con `origin/main` era 0/0. Se conservaron los cambios locales. El plan corresponde a `default` en `us-east-1`. STS identificó a `voclabs` y se comprobó que la cuenta coincide con el bucket de estado. El bootstrap sigue separado en el worktree original.

## Reglas específicas de este Learner Lab

Leonardo proporcionó el texto del Readme de su curso, actualizado el 24 de junio de 2025. La herramienta de navegador no pudo verificar la URL de la ventana de Academy. Se utilizó el texto del curso junto a las consultas directas con la CLI.

- El curso limita los servicios a `us-east-1` y `us-west-2`, salvo excepciones documentadas. ArquiFusion usa únicamente `us-east-1` en esta fase.
- El límite del curso es **10 entornos de ejecución Lambda concurrentes**. La API `GetAccountSettings` de esta cuenta reportó 400. Para el ensayo rige 10, compartidos con los otros proyectos. Un incidente en vuelo, con observación de concurrencia y throttles. La rama de notificación permite hasta cinco invocaciones en paralelo.
- API Gateway figura en el Readme. Cognito no figura y Leonardo confirmó que no está disponible. El resultado IAM histórico de una simulación de Cognito no cambia esa restricción. La solución excluye el servicio.
- El curso restringe IAM y describe excepciones para roles vinculados a servicios. Leonardo prohíbe crear roles en este proyecto. Se comprobó por lectura que ya existen `LabRole` y `AWSServiceRoleForAPIGateway`. La política de confianza de `LabRole` incluye API Gateway, Lambda, Step Functions y EventBridge. El autorizador JWT nativo no invoca una Lambda.
- Los recursos persisten entre sesiones. El panel de presupuesto puede ir entre 8 y 12 horas por detrás. El Readme advierte de desactivación y pérdida de recursos si se excede el presupuesto. Esta auditoría no determinó el saldo actual. EC2, VPS, cargas periódicas y despliegues multirregión están fuera del encargo.

## Matriz de compatibilidad de ArquiFusion

| Servicio o acción | Evidencia | Consecuencia |
|---|---|---|
| STS y backend S3 | Lectura actual. Identidad y cuenta correctas, SSE-S3 AES256, versionado y cuatro bloqueos públicos activos. Uso real del estado remoto `aws/terraform.tfstate`. | Mantener el bucket dedicado y el estado local de bootstrap separado de los recursos de aplicación. |
| DynamoDB | Uso real. Tabla `af-dev-arquifusion` ACTIVE, PK/SK, GSI1/GSI2, bajo demanda, TTL `expiresAt`, PITR, Streams `NEW_AND_OLD_IMAGES` y protección contra borrado. | El ensayo anterior persistió un incidente y un mantenimiento. La suscripción de Streams para el outbox y el archivo histórico siguen pendientes. |
| Lambda y Logs | Cinco Lambdas con prefijo `af-dev-`: `health-evaluator`, `incident-tasks`, `assign-technician`, `ai-diagnosis` y `notify`. Cinco grupos de logs con retención de 14 días. Health Evaluator procesó lecturas normales/anómalas y las cuatro Lambdas de incidentes participaron en el ensayo. | Se puede reutilizar el empaquetado Linux x86_64 / Python 3.12 y el rol existente. |
| Step Functions Standard | Dos máquinas ACTIVE y cero ejecuciones RUNNING. Entrada e hija del ensayo `INC-1a24` terminaron SUCCEEDED. | El callback directo del operador fue real. La llamada HTTP y el callback desde una futura Lambda API requieren prueba propia. |
| EventBridge | Productor, bus y destino ya creados. Evento aceptado por el bus en el ensayo del evaluador. Regla `af-dev-incident` DISABLED, dirigida a `af-dev-incident-entry`, 185 reintentos máximos durante 24 horas y DLQ. | Falta probar entrega automática para una operación sintética autorizada. La auditoría dejó intacta la regla. |
| SSM | Cinco parámetros del proyecto. Catálogo de 4.248 bytes almacenado en Standard mediante manifiesto comprimido de 1.854 bytes y verificado por la Lambda. | Reutilizar la configuración desplegada. |
| SNS, SQS y CloudWatch | Tópico creado con cero suscripciones. DLQ con cero mensajes visibles y en vuelo, retención de 14 días. Tres alarmas en OK. Cuatro Lambdas sumaron 18 invocaciones y cero errores en la ventana del ensayo anterior. | Recepción humana y entrada efectiva de un fallo en la DLQ pendientes. Las métricas corresponden al ensayo manual. |
| API Gateway HTTP/REST | Lectura confirmada. Sin API ni Lambda API del proyecto según nombres y estado Terraform. El Readme admite el servicio. El simulador permitió GET/POST sobre la colección HTTP API y un recurso prospectivo de autorizadores. | Crear una API y validar un JWT real en esta cuenta siguen sin probar. El plan prepara la opción A cerrada. |
| Cognito | Excluido por Leonardo y ausente del listado entregado. | Google Identity Services aporta la identidad. Los permisos de negocio siguen bajo control de ArquiFusion. |
| IAM y S3, denegación explícita | La CLI recibió `AccessDenied` con denegación explícita al leer `iam:GetRole` sobre `voclabs` y `s3:GetBucketObjectLockConfiguration` sobre el bucket del proyecto. La lectura del rol `LabRole` sí funcionó. | No extrapolar estas denegaciones a todo IAM o S3. La revisión de `voclabs` identificó su rol mediante STS y ListRoles. |
| Permisos del futuro backend | Simulación de `iam:PassRole` de `voclabs` a Lambda, `states:SendTaskSuccess` de `LabRole` y acciones DynamoDB GetItem, Query, PutItem, UpdateItem y ConditionCheckItem sobre la tabla. Todas indicaron `allowed`. | Falta el ejercicio bajo la identidad de ejecución de la nueva Lambda después de autorizar su despliegue. |
| Secrets Manager | El Readme lo incluye. Listado de metadatos accesible, cero secretos del prefijo consultado. | No se consultaron valores ni se probó su escritura. |
| IoT Core, Glue, Kinesis Delivery Streams y KMS | El Readme menciona estos servicios o funciones. Glue ETL permite G.1X/Standard, hasta diez workers y concurrencia 1. | No hay evidencia de uso de esas operaciones por ArquiFusion. Para la demo ya se probó invocación directa de Health Evaluator. La vía de archivo Streams → Lambda → S3 todavía requiere implementación. El estado usa SSE-S3. |
| Bedrock, Amazon Location, CloudFront, Scheduler y Global Tables | El recorrido actual no acredita su uso ni permisos efectivos. | Diagnóstico por reglas, distancia calculada en código y DynamoDB regional con PITR ya funcionaron. Firebase Hosting es una alternativa pendiente. El disparo manual ya se probó. |
| GCP | El repositorio incluye SQL, modelos y código de retorno. No se aportó un proyecto Firebase autorizado ni hay configuración de hosting en el checkout. | AWS conserva la operación y GCP la analítica BigQuery ML. Faltan permisos, facturación y pruebas del proyecto real. |
