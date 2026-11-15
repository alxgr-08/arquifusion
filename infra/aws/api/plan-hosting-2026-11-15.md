# Plan de API después del hosting · 15/11/2026

## Entorno y alcance

Preparación local de Leonardo, Rol 1, en `infra/rol1-entrega-academy`, commit `9d2994b1d535d0a021e0e7f26874925060d42638`. `git fetch origin` mantiene `origin/main` en `53f9755`; la rama está tres commits por delante y cero por detrás. No se integraron ramas ni se hicieron commits o push en esta etapa.

AWS Academy Learner Lab, cuenta `975050277936`, perfil `default`, región `us-east-1`. La identidad STS corresponde a la sesión `voclabs` de la cuenta esperada. Se reutiliza `LabRole`, cuya confianza para Lambda se comprobó por lectura. El límite educativo es diez entornos Lambda concurrentes para toda la cuenta compartida.

El backend S3 existente es exclusivo de ArquiFusion: bucket `af-dev-tfstate-975050277936`, clave `aws/terraform.tfstate`, workspace `default`, región `us-east-1`, bloqueo nativo S3 configurado. No se inicializó ni migró el backend. El bootstrap conserva su estado separado.

## Resultado

El plan base confirma **0 altas, 0 cambios, 0 bajas y 29 no-op**, incluidos DynamoDB, Health Evaluator y ambas piezas del hosting Amplify. La regla `af-dev-incident` permanece **DISABLED**.

El plan guardado definitivo es `build/api-hosting-20261115/api-dashboard-closed.tfplan`:

```text
Plan: 31 to add, 0 to change, 0 to destroy.
29 recursos existentes en no-op.
SHA-256: 60A2A19F5B8303258F8879DF32D33D1CC44140B3934A23335896B2DF6CC70317
```

El estado remoto conserva el mismo lineage y serial **9** antes y después. Se cotejaron sin cambios tres archivos de estado del bootstrap en los worktrees existentes. La evidencia de solo lectura está en `build/api-hosting-20261115/verification.json`; el resumen del binario, sus direcciones y rutas están en `api-dashboard-closed.review.json` del mismo directorio. No se guardó en Git ningún plan, estado o configuración privada.

Los planes antiguos de `build/night-20261115/` son históricos después de aplicar el hosting. No utilizarlos para esta etapa.

## Recursos de la API

| Recurso nuevo | Cantidad |
|---|---:|
| HTTP API `af-dev-api` | 1 |
| JWT authorizer Google | 1 |
| Lambda `af-dev-api-ack`, Python 3.12 x86_64, `handler.handler`, `LabRole` existente | 1 |
| Integración AWS_PROXY con payload 2.0 | 1 |
| Stage `dev` | 1 |
| Grupos CloudWatch de Lambda y acceso HTTP, retención 14 días | 2 |
| Rutas JWT | 12 |
| Permisos de invocación por cuenta, API, stage, método y ruta | 12 |
| **Total previsto** | **31** |

Las rutas son once GET y un único POST:

```text
GET  /operations/{op}/machines
GET  /operations/{op}/incidents
GET  /operations/{op}/technicians
GET  /operations/{op}/kpis
GET  /machines/{id}
GET  /machines/{id}/readings
GET  /machines/{id}/incidents
GET  /machines/{id}/maintenance
GET  /parts
GET  /parts/{partId}/stock
GET  /machines/{id}/incidents/{incId}
POST /machines/{id}/incidents/{incId}/ack
```

No se publica el inyector de fallos. El perfil incluye las diez lecturas adicionales que necesita el dashboard, además del detalle y acuse de incidentes.

## Autenticación y entrada cerrada

El endpoint se prepara con `disable_execute_api_endpoint=true`; no hay dominio personalizado. Abrirlo será otra etapa revisada. El cliente Google público es el confirmado por Alexis en #41. El emisor es `https://accounts.google.com` y la audiencia es el Client ID web exacto de ArquiFusion. No se crea Cognito ni se configura `AF_AUTH_DISABLED`.

El origen CORS preparado es exclusivamente `https://demo.d2ygcm34t8aku9.amplifyapp.com`. Se permiten GET/POST y las cabeceras Authorization, Content-Type e If-Match; se exponen ETag y Retry-After. El registro del origen en Google está pendiente de confirmación de Alexis. Google y CORS son controles independientes.

El backend existente usa `iss/sub` verificados por el Gateway para buscar concesiones confiables en DynamoDB. Revalida operación, rol y pertenencia de la máquina/incidente; TECHNICIAN requiere el técnico asignado. El acuse exige el ETag fuerte de la espera y conserva un recibo transaccional para reintentos y reconciliación. Se mantienen intactos los contratos, el backend de Sebastián y el frontend de Alexis en esta etapa.

La autorización JWT y CORS siguen la documentación de [AWS JWT](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html) y [AWS HTTP API CORS](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-cors.html). El preflight lo atiende HTTP API sin añadir una ruta de escritura anónima.

## Validaciones y límites

El ZIP de API se coteja con todos los módulos actuales del servicio y `af_shared`, su hash y las dependencias Linux CPython 3.12. Su SHA-256 es `CA75D68A9055488CBE12DD6CC805685187E912CDE354714027748448BDD53B62`. Se comprobaron por lectura ausencia de colisiones de la API, Lambda y grupos de logs previstos, y propiedad del hosting existente.

El revisor se amplió con `--existing-hosting`: exige exactamente las 29 direcciones base en no-op y relaciona app/rama/origen. El refresh de Amplify normaliza atributos vacíos de `null` a `[]` o `{}`; se admiten únicamente esas equivalencias. Una variable, patrón, automatismo o cambio real sigue siendo rechazado.

`terraform fmt -check -recursive` y `terraform validate` pasaron. Las pruebas del backend, grants y revisor completaron **78 passed**; Ruff y su comprobación de formato pasaron. Los **15 escenarios Terraform de API con proveedor AWS simulado** pasaron. Estas pruebas no demuestran permiso de creación del servicio ni un login real en Academy.

La tasa del stage sigue en una solicitud por segundo y ráfaga de cuatro. La pantalla de máquina hace polling cerca de esa tasa; el ensayo con un usuario debe medir 429 y reintentos antes de ampliar tráfico. El throttling no impone por sí solo el límite educativo de diez entornos Lambda.

## Archivos y reproducción

- `review_plan.py` y `test_review_plan.py`: preservación de hosting y pruebas de rechazo de cambios reales.
- `hosting-review.tfvars.example`: perfil público de revisión con hosting conservado, dashboard completo y endpoint cerrado.
- `README.md` y este informe: comandos, alcance y estado de preparación.
- `build/api-hosting-20261115/`: planes, variables locales, logs y evidencia saneada, excluidos de Git.

Desde la raíz del worktree, con Terraform en PATH, el revisor del plan definitivo se ejecuta así:

```powershell
python infra/aws/api/review_plan.py build/api-hosting-20261115/api-dashboard-closed.tfplan --expect closed --fleet-reads --existing-hosting --origin https://demo.d2ygcm34t8aku9.amplifyapp.com
```

Para regenerar el plan, conservar el backend ya inicializado y proporcionar la cuenta esperada y `-var-file=api/hosting-review.tfvars.example` a `terraform -chdir=infra/aws plan`. Esta revisión utiliza `-lock=false` para mantener las llamadas cloud en lectura, y compara el estado remoto antes y después. No sobrescribir un binario revisado: generar otro nombre y recalcular SHA-256. Una futura aplicación debe tomar el bloqueo normal y comprobar nuevamente estado y alcance.

## Próximo paso seguro

1. Leonardo revisa el plan cerrado y su SHA; su aplicación requerirá autorización específica.
2. Alexis confirma el origen HTTPS registrado en Google. La web permanece sin publicar.
3. Sebastián revisa #50 y prepara, mediante una operación autorizada, las concesiones reales de la operación y técnico de demo. No se sembraron grants en esta etapa.
4. Tras desplegar la API cerrada y confirmar Google, preparar un plan explícito de apertura que preserve los demás recursos. Obtener entonces la URL real del stage para el build `VITE_API_MODE=live`.
5. Ejecutar el ensayo HTTPS autorizado: token válido/ausente/inválido/vencido, sin grant, otra operación, técnico ajeno, ETag obsoleto, acuse repetido, respuesta perdida y cierre con un único mantenimiento. [Runbook](ensayo.md)

No se ejecutaron apply, destroy, escrituras de datos, invocaciones Lambda, publicaciones de eventos, cargas web, cambios IAM, commit, push o merge. La única actualización de Git fue `git fetch origin`.
