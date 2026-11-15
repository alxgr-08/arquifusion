# Ensayo del acuse por HTTP

## Pruebas locales

`python -m pytest -q services/api/tests shared/tests/test_grants.py` ejercita el handler, las concesiones, las generaciones de espera y los reintentos con una tabla y Step Functions simuladas. `terraform -chdir=infra/aws test -test-directory=api` comprueba con un proveedor simulado los modos cerrado y abierto, la audiencia, el origen y los permisos de invocación de cada ruta. El registro real del origen se contrasta con el cliente de Google. [Infraestructura](./README.md) · [Kit sintético de Rol 2](../../../docs/ensayo-lab/README.md)

La suite del servicio (`python -m pytest -q services/api/tests`) pasó 33 pruebas. Incluye aislamiento de máquina e históricos, pertenencia de almacenes, grants con distintos roles y protección del runtime. Un lote del caso sintético llega al cierre con dos recibos y un solo mantenimiento y recorre el puente analítico con BigQuery simulado. El ensayo HTTPS siguiente comprobará autenticación, permisos y respuestas en la infraestructura real. [Cobertura de acceso](../../../services/api/tests/test_operation_scope.py) · [Caso compartido entre servicios](../../../services/api/tests/test_multicloud_rehearsal.py)

## Ensayo pendiente en AWS Academy

1. Verificar en la versión empaquetada que el GET comprueba la operación del incidente, incluidos el perfil ausente y la máquina trasladada. Generar un plan con `enable_incident_api = true` y `open_incident_api_endpoint = true`, el cliente de #41 y su único origen autorizado para el ensayo. Revisar con `--expect open`. Para ensayar la flota añadir `enable_fleet_read_routes = true` y revisar con `--fleet-reads`. El origen registrado de desarrollo es `http://localhost:5173`. Para Amplify se registra primero el origen HTTPS público en el cliente de Google y se usa el mismo en CORS y en `--origin`. La aplicación del plan y la preparación del caso requieren autorización específica.
2. Usar una operación sintética aislada, un único incidente y la concesión mínima del técnico de prueba. La fila del grant la crea el responsable autorizado. Confirmar que la operación está en la espera ARRIVAL y que el técnico está asignado. Registrar únicamente códigos HTTP, estados y conteos del caso. Mantener fuera de los registros los ID tokens, identificadores personales, tokens de tarea y payloads privados.
3. En la web live de Alexis, obtener un ID token, leer el detalle y conservar el ETag que vio el usuario. Enviar el POST con ese mismo ETag entre comillas y el cuerpo acordado. Repetir para RESOLUTION usando la nueva versión de la espera. Las pruebas del dashboard completo necesitan también las rutas de consulta de ese dashboard.

| Prueba | Evidencia esperada |
|---|---|
| Preflight con origen aprobado | Devuelve ese origen, métodos GET y POST y las cabeceras solicitadas. Otro origen carece de permiso CORS. La respuesta real expone ETag y Retry-After. |
| Token ausente, caducado o de otra audiencia | API Gateway responde 401. No se escribe un recibo. |
| Sin concesión, otra operación o técnico ajeno al POST | Con un JWT válido se obtiene 403. Las filas del ensayo y la espera se conservan. |
| Lecturas por operación | Usuarios con el grant permitido leen máquinas, incidentes, técnicos o KPI de su operación. Otra operación recibe 403. El rol OPERATOR también recibe 403 en KPI. |
| Máquina e históricos | Con dos operaciones del caso sintético y una máquina trasladada, verificar que cada historial solo entrega filas atribuibles a la operación autorizada. Incluir el caso de procedencia antigua no verificable. |
| Piezas | En `/parts` y `/parts/{partId}/stock`, usar una pieza presente en dos almacenes de distintas operaciones. Verificar que el listado, cantidades y pronóstico solo incluyan los almacenes de la operación autorizada, incluso al cambiar `operationId`. |
| GET del incidente en una espera versionada | 200 con `Incident` y `ETag`. El DTO contiene los datos visibles del incidente. |
| POST sin versión, con versión vieja o en etapa distinta | 428 `PRECONDITION_REQUIRED`, 412 `STALE_WAIT` con el ETag actual, o 409 `WRONG_STAGE`, respectivamente. El 412 requiere volver a mostrar el detalle. |
| ARRIVED y RESOLVED | Un recibo por etapa/generación. El flujo certifica ARRIVED y RESOLVED. Tras cerrar hay un registro de mantenimiento y se libera la máquina. |
| Respuesta pendiente del callback | 503 `CALLBACK_PENDING` con `Retry-After`. Repetir exactamente el cuerpo y el `If-Match` originales. Al confirmar, 200. |
| Repetición o conflicto | Un reintento idéntico conserva el efecto. Un cuerpo o actor incompatibles o una espera vencida devuelven el conflicto del contrato. |

El cierre de esta prueba requiere observar el `If-Match` de la versión vista por el usuario y la respuesta por HTTPS del autorizador y del callback. Para cerrar de nuevo la entrada, conservar las rutas del ensayo en Terraform y revisar un plan con `open_incident_api_endpoint = false` y `--expect closed`. [Contrato y códigos](../../../contracts/api.md) · [Cliente live](../../../frontend/src/api/client.ts)

## Petición de ensayo con PowerShell 7

Estos comandos corresponden al futuro **plan de apertura** y a un ensayo autorizado. Requieren la URL operativa del stage de Terraform y los identificadores del caso sintético. Introducir el ID token de una cuenta de prueba consentida en la entrada protegida. La variable de token permanece en memoria.

```powershell
$base = (terraform -chdir=infra/aws output -raw incident_api_base_url).TrimEnd('/')
$machine = [uri]::EscapeDataString((Read-Host 'ID de la máquina sintética'))
$incident = [uri]::EscapeDataString((Read-Host 'ID del incidente sintético'))
$detailUrl = "$base/machines/$machine/incidents/$incident"
$token = Read-Host 'ID token de Google del usuario de ensayo' -AsSecureString
$detail = Invoke-WebRequest -Uri $detailUrl -Authentication Bearer -Token $token
$etag = $detail.Headers['ETag'] | Select-Object -First 1
if (-not $etag) { throw 'Este incidente no tiene una espera versionada.' }

$payload = @{ action = 'ARRIVED' } | ConvertTo-Json -Compress
$ack = @{
    Uri = "$detailUrl/ack"
    Method = 'Post'
    Authentication = 'Bearer'
    Token = $token
    Headers = @{ 'If-Match' = $etag }
    ContentType = 'application/json'
    Body = $payload
    SkipHttpErrorCheck = $true
}
$result = Invoke-WebRequest @ack
[pscustomobject]@{ Status = $result.StatusCode; RetryAfter = $result.Headers['Retry-After'] }
```

Para 503 `CALLBACK_PENDING`, esperar el intervalo de `Retry-After` antes de repetir `Invoke-WebRequest @ack` con el mismo cuerpo y ETag. Ante 412, volver a leer el detalle y solicitar confirmación humana de la nueva espera. Para RESOLVED, repetir el GET cuando el flujo alcance RESOLUTION y preparar el POST con el nuevo ETag y la acción `RESOLVED`.
