# Health Evaluator en AWS Academy

La raíz de aplicación es `infra/aws/`. Antes de añadir la Lambda, Terraform administraba la tabla `af-dev-arquifusion` en el backend S3 de ArquiFusion y un plan completo con refresco devolvió `No changes`. El primer despliegue añadió los cinco recursos siguientes en AWS Academy con el perfil `default`, región `us-east-1`.

| Alta | Configuración |
|---|---|
| Lambda `af-dev-health-evaluator` | Python 3.12, x86_64, `handler.handler`, 256 MB, 10 s, rol existente consultado por nombre `LabRole` |
| Bus `af-dev-bus` | Bus dedicado compatible con el cliente `boto3.client("events").put_events` del handler |
| SSM `/af/health/weights` | `String` JSON, las cinco ponderaciones de `HealthConfig` |
| SSM `/af/health/thresholds` | `String` JSON, cortes 80/60/40, racha 3, crítico directo 4.5, mínimo histórico 30 |
| Logs `/aws/lambda/af-dev-health-evaluator` | Grupo privado de la función, retención elegida de 14 días |

Terraform conecta `AF_TABLE_NAME`, `AF_EVENT_BUS`, `AF_SSM_HEALTH_WEIGHTS`, `AF_SSM_HEALTH_THRESHOLDS` y `POWERTOOLS_SERVICE_NAME`. La tabla tiene acción `no-op`. El refresco representa el atributo local `tags` vacío como `{}` donde antes figuraba `null`; `tags_all` y las tres etiquetas reales coinciden. El alcance de esta fase es la invocación directa con telemetría plana. El simulador ya admite `AF_INGEST_MODE=direct` y `AF_HEALTH_EVALUATOR_FUNCTION`. Un trigger IoT o un adaptador HTTP se coordina con su responsable.

## Primer despliegue de desarrollo

El 15 de noviembre de 2026 (UTC) se aplicó el archivo revisado `health-evaluator.tfplan` tras verificar los SHA-256 del plan y del ZIP que figuran abajo. Terraform confirmó `5 added, 0 changed, 0 destroyed`. La función quedó `Active`, con actualización `Successful`, Python 3.12 y el `LabRole` existente. El hash del código desplegado coincidió con el del ZIP. Se comprobó el contenido de los dos parámetros SSM (versión 1), la retención de logs de 14 días y el bus `af-dev-bus`.

Las lecturas usaron el caso preparado `3f82648e89a04ed8bc6e8c205e3534f9`. Dos consultas consistentes confirmaron que la máquina `HELAW-1769` y su modelo sintético carecían de registros. Se insertó la línea base del modelo con una escritura condicional y se invocó la función una vez por lectura. Ambas invocaciones devolvieron HTTP 200 y `result=OK`.

| Lectura | ID de solicitud de Lambda | Puntuación y severidad | Clave verificada en DynamoDB | `emitted` |
|---|---|---|---|---|
| Normal | `bcef88c8-9e68-48e1-8d5d-87150ecdbbcc` | 100 / OK | `READING#2026-11-15T01:40:25Z` | `false` |
| Anómala | `2330b1bc-dccc-4997-8ec0-3e85639b8983` | 0 / CRITICAL | `READING#2026-11-15T01:40:26Z` | `true` |

Una lectura consistente de `MACHINE#HELAW-1769 / PROFILE` después de cada invocación confirmó ambas transiciones de puntuación y el avance de `lastSeen`. La lectura normal seguía intacta al comprobar la anómala. Las lecturas tienen TTL de 48 horas. El perfil y la línea base de prueba persisten.

CloudWatch registró `START`, `END` y `REPORT` para ambas solicitudes. A las 01:56:08.879 UTC registró `AnomalyDetected published` para la segunda. El handler escribe ese mensaje después de comprobar que `PutEvents` devolvió cero entradas fallidas. Esto acredita la aceptación desde el productor. El bus tenía cero reglas antes y después de las pruebas. La entrega a un consumidor queda sin verificar. Las respuestas, las lecturas de DynamoDB y los registros de CloudWatch están en `build/health-evaluator/cases/3f82648e89a04ed8bc6e8c205e3534f9/`, excluido de Git.

## Preparar y revisar el paquete

Ejecutar desde la raíz del worktree en PowerShell con Python 3.12 y pip.

```powershell
py -3.12 infra/aws/health_evaluator/build.py
terraform -chdir=infra/aws fmt -check
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws show -no-color ../../build/terraform-review/health-evaluator.tfplan
Get-FileHash -Algorithm SHA256 build/terraform-review/health-evaluator.tfplan,build/health-evaluator/health-evaluator.zip
```

El constructor descarga 13 wheels fijados por versión y SHA-256, selecciona CPython 3.12 / manylinux2014 x86_64 e incluye `boto3` y sus dependencias. Empaqueta `handler.py`, `scoring.py` y el paquete `af_shared` en la raíz del ZIP. Fija orden, fechas, permisos y finales de línea de las fuentes. Los dos builds dieron el mismo SHA-256. Terraform conserva el SHA-256 del ZIP en `source_code_hash`; el plan guardado se revisa junto con ese archivo, que Terraform leerá del disco en un despliegue posterior. [Empaquetado de funciones Python en Lambda](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html)

- Plan `build/terraform-review/health-evaluator.tfplan` · `7EBF1D3967389996DD87F1ECBB31F97C12C7B913D6CBE6109F878C6E34650409`.
- ZIP `build/health-evaluator/health-evaluator.zip` · `F86C3A8B88B2DAAAB14C41741C6F23563576760CCC87F5D9757CF3C7B0A366A0`.

## Evidencia de permisos

| Identidad | Comprobación | Resultado |
|---|---|---|
| Sesión de despliegue `voclabs` | STS, tabla, nombre de Lambda, grupo de logs, ambos parámetros y bus | Tabla activa. Función, grupo, parámetros y bus dedicado ausentes antes del plan. El bus `default` existe. |
| Confianza de `LabRole` | `iam:GetRole` | La política permite `sts:AssumeRole` a `lambda.amazonaws.com`. Siete políticas administradas adjuntas, cero inline, sin permissions boundary. |
| Desplegador → rol | `iam:SimulatePrincipalPolicy` sobre el ARN real de `LabRole` con `iam:PassedToService=lambda.amazonaws.com` | `iam:PassRole = allowed`. Este permiso corresponde al desplegador. |
| Desplegador → recursos propios | Simulación de `lambda:CreateFunction/GetFunction/TagResource`, `events:CreateEventBus/DescribeEventBus/TagResource`, `ssm:PutParameter/GetParameter/AddTagsToResource`, `logs:CreateLogGroup/PutRetentionPolicy/TagResource` | `allowed` para los nombres y ARN previstos. Para probar también se simularon `lambda:InvokeFunction`, `dynamodb:GetItem/PutItem` y `logs:FilterLogEvents`. |
| Ejecución de Lambda | Simulación de `dynamodb:GetItem` y de `PutItem/UpdateItem` en la tabla, `ssm:GetParameter` en cada parámetro, `events:PutEvents` en el bus, `logs:CreateLogStream/PutLogEvents` en el grupo | Todas `allowed` sin claves de contexto faltantes. La transacción usa Put y Update con condiciones integradas. En IAM se autorizan las operaciones subyacentes. [Permisos de transacciones DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html) |

`GetRole(voclabs)` y la lectura de tres políticas administradas de Academy devolvieron `AccessDenied` durante la revisión previa. Se identificó el rol del desplegador cruzando el ID del principal STS con `ListRoles`. El simulador respondió para ambos roles. El despliegue y las dos invocaciones posteriores comprobaron los permisos usados en este caso de desarrollo. [Simulador IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_testing-policies.html) · [PassRole](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_passrole.html)

Si Academy deniega crear el bus, el bus existente `default` también obtuvo `allowed` simulado para `PutEvents`. Antes de optar por él, revisar sus consumidores con el equipo y preparar otro plan que solo lo referencie. Si se deniega la publicación en ambos buses, la entrega en AWS queda bloqueada. Si se deniega crear parámetros, el responsable autorizado puede preaprovisionarlos y la infraestructura puede referenciarlos tras verificar su propietario. El handler usa sus valores integrados si recibe `ParameterNotFound`. Ante `AccessDenied` falla y requiere habilitar lectura o acordar un cambio con el autor. Si se deniega `PassRole`, corresponde al administrador del laboratorio autorizar el rol existente para Lambda. Un grupo de logs preaprovisionado también puede referenciarse tras revisar su propietario. El rol necesita escritura en sus streams para producir evidencia.

El análisis estático de operaciones se puede repetir localmente.

```powershell
uvx iam-policy-autopilot@latest generate-policies services/health_evaluator/handler.py --language python --region us-east-1 --service-hints dynamodb ssm events --pretty
```

## Pruebas en local

Se validaron seis ejemplos de contratos y pasaron los 125 tests del runner del repositorio más las dos pruebas específicas de infraestructura. Dentro de ese total, 32 tests son del evaluador. Moto reprodujo tanto la llegada al destino de prueba como la pérdida al fallar la publicación. En Windows se verificaron el ZIP y la extensión ELF64 x86_64 para CPython 3.12. Las dos invocaciones del primer despliegue ejecutaron ese ZIP en el runtime administrado de Lambda.

```powershell
$env:PYTHONPATH = (Resolve-Path shared).Path
.\.venv\Scripts\python.exe scripts/validate_contracts.py
.\.venv\Scripts\python.exe -m pytest -q services/health_evaluator infra/aws/health_evaluator/test_smoke.py
$case = (.\.venv\Scripts\python.exe infra/aws/health_evaluator/prepare_case.py).Trim()
```

El generador solo escribe en `build/`. Parte de `telemetry.ok.json` y `telemetry.warning.json`, valida el esquema, usa UUID nuevos, máquina y modelo sintéticos exclusivos, y timestamps UTC distintos y crecientes. Cada lectura expira 48 horas después de su timestamp. Un reenvío de la misma fecha para esa máquina se omite aun cambiando el UUID.

El caso usa una línea base sintética de modelo con vibración media 4.1 y desviación 0.1. Con los pesos y umbrales del plan, la primera lectura de 4.1 da 100/OK. La segunda de 7.8 da EWMA 4.84 y riesgo de flota superior a 4.5, por lo que escala directamente a CRITICAL y emite. Sin línea base de modelo, el historial propio necesita al menos 30 muestras y las escaladas ordinarias requieren tres lecturas consecutivas. El handler guarda la configuración SSM en caché durante 300 segundos.

## Prueba futura de entrega a un consumidor en Academy

Ejecutar desde PowerShell en la raíz. Preparar un caso fresco con el comando anterior. Para observar la recepción de forma independiente, preparar antes una regla de evidencia en `af-dev-bus` con el patrón de `$case/event-pattern.json` y un destino de prueba exclusivo, por ejemplo SQS. El destino debe permitir `sqs:SendMessage` al principal `events.amazonaws.com` condicionado al ARN de esa regla. Esta instrumentación y sus permisos requieren revisión aparte. El plan principal comprende las cinco altas descritas al inicio.

```powershell
function Invoke-AfCli {
    $result = & aws @args --profile default --region us-east-1 --no-cli-pager --output json
    if ($LASTEXITCODE -ne 0) { throw "Falló aws $($args[0]) $($args[1])" }
    if ($result) { ($result -join [Environment]::NewLine) | ConvertFrom-Json }
}
$table = 'af-dev-arquifusion'
$fn = 'af-dev-health-evaluator'
$expected = terraform -chdir=infra/aws output -raw table_arn
if ($LASTEXITCODE -ne 0) { throw 'No se pudo leer el backend del proyecto' }
$sts = Invoke-AfCli sts get-caller-identity
if ($expected -ne "arn:aws:dynamodb:us-east-1:$($sts.Account):table/$table") { throw 'Cuenta inesperada' }
$active = Invoke-AfCli dynamodb describe-table --table-name $table --query Table.TableStatus
if ($active -ne 'ACTIVE') { throw 'Tabla fuera de servicio' }
$bus = Invoke-AfCli events describe-event-bus --name af-dev-bus --query Name
$binding = Invoke-AfCli lambda get-function-configuration --function-name $fn --query '{State:State,Table:Environment.Variables.AF_TABLE_NAME,Bus:Environment.Variables.AF_EVENT_BUS}'
if ($binding.State -ne 'Active' -or $binding.Table -ne $table -or $binding.Bus -ne $bus) { throw 'Lambda o bus inesperados' }
```

Para comprobar la entrega de AWS, usar una cola de evidencia autorizada y exclusiva del proyecto. Antes de la invocación, su responsable puede instalar la siguiente regla efímera y el destino. El ARN de la regla, utilizado en la política de la cola, sigue el formato `arn:aws:events:us-east-1:<ACCOUNT_ID>:rule/af-dev-bus/<nombre>`. Este bloque requiere permisos de pruebas para `events:ListRules/PutRule/TagResource/PutTargets` y `sqs:GetQueueAttributes`, además del permiso del servicio para entregar a la cola.

```powershell
$ruleName = 'af-dev-he-test-' + (Split-Path -Leaf $case)
$existingRules = Invoke-AfCli events list-rules --event-bus-name $bus --name-prefix $ruleName
if ($existingRules.Rules.Count) { throw 'Ya existe la regla de ese caso' }
$queueArn = (Invoke-AfCli sqs get-queue-attributes --queue-url $evidenceQueueUrl --attribute-names QueueArn).Attributes.QueueArn
if ($queueArn -notlike "arn:aws:sqs:us-east-1:$($sts.Account):af-dev-health-evidence*") { throw 'Cola de evidencia inesperada' }
$rule = Invoke-AfCli events put-rule --event-bus-name $bus --name $ruleName --event-pattern "file://$case/event-pattern.json" --tags 'Key=project,Value=arquifusion' 'Key=env,Value=dev' 'Key=owner,Value=rol1'
$rule.RuleArn
$target = Invoke-AfCli events put-targets --event-bus-name $bus --rule $ruleName --targets "Id=Evidence,Arn=$queueArn"
if ($target.FailedEntryCount) { throw 'El destino de evidencia no se pudo asociar' }
```

Con la regla y su política de entrega listas, usar el caso generado. El primer control comprueba que el handler rechace telemetría inválida. Las llamadas siguientes escriben únicamente la línea base sintética y los datos de las dos invocaciones.

```powershell
[IO.File]::WriteAllText("$case/invalid.json", '{"schemaVersion":"invalid"}')
$bad = Invoke-AfCli lambda invoke --function-name $fn --payload "fileb://$case/invalid.json" "$case/invalid-response.json"
if ($bad.FunctionError -or (Get-Content "$case/invalid-response.json" -Raw | ConvertFrom-Json).result -ne 'INVALID') { throw 'La validación de Lambda no respondió INVALID' }
$old = Invoke-AfCli dynamodb get-item --table-name $table --consistent-read --key "file://$case/profile-key.json"
if ($old.Item) { throw 'La máquina ya existe. Genera otro caso y actualiza el observador.' }
Invoke-AfCli dynamodb put-item --table-name $table --item "file://$case/baseline.json" --condition-expression 'attribute_not_exists(PK)'

$startMs = [DateTimeOffset]::UtcNow.AddSeconds(-10).ToUnixTimeMilliseconds()
$requestIds = @()
foreach ($sample in 'normal','anomaly') {
    $file = "$case/$sample-response.json"
    $meta = Invoke-AfCli lambda invoke --function-name $fn --invocation-type RequestResponse --log-type Tail --payload "fileb://$case/$sample.json" $file
    if ($meta.FunctionError) { throw "Lambda devolvió $($meta.FunctionError). Revisa $file" }
    $msg = Get-Content "$case/$sample.json" -Raw | ConvertFrom-Json
    $reply = Get-Content $file -Raw | ConvertFrom-Json
    $reading = (Invoke-AfCli dynamodb get-item --table-name $table --consistent-read --key "file://$case/$sample-key.json").Item
    $machineProfile = (Invoke-AfCli dynamodb get-item --table-name $table --consistent-read --key "file://$case/profile-key.json").Item
    $alarm = $sample -eq 'anomaly'
    $severity = if ($alarm) { 'CRITICAL' } else { 'OK' }
    $score = if ($alarm) { 0 } else { 100 }
    if ($reply.result -ne 'OK' -or $reply.severity -ne $severity -or $reply.healthScore -ne $score -or $reply.emitted -ne $alarm) { throw "Resultado inesperado en $sample" }
    if ($reading.messageId.S -ne $msg.messageId -or $machineProfile.lastSeen.S -ne $msg.timestamp -or $reading.severity.S -ne $severity -or $machineProfile.severity.S -ne $severity -or [double]$reading.healthScore.N -ne $score -or [double]$machineProfile.healthScore.N -ne $score) { throw "Persistencia incompleta en $sample" }
    $reply
    $tail = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($meta.LogResult))
    $id = [regex]::Match($tail, 'REPORT RequestId:\s*([a-fA-F0-9-]+)').Groups[1].Value
    if (-not $id) { throw 'No se pudo correlacionar el request ID de Lambda' }
    $requestIds += $id
}
$machine = (Get-Content "$case/normal.json" -Raw | ConvertFrom-Json).machineId
$deadline = [DateTime]::UtcNow.AddMinutes(3)
do {
    $logs = (Invoke-AfCli logs filter-log-events --log-group-name "/aws/lambda/$fn" --start-time $startMs).events.message -join "`n"
    $missing = @($requestIds | Where-Object { -not $logs.Contains("REPORT RequestId: $_") })
    $published = $logs -split "`n" | Where-Object { $_.Contains($machine) -and $_.Contains('AnomalyDetected published') }
    if (-not $missing.Count -and $published) { break }
    Start-Sleep -Seconds 5
} while ([DateTime]::UtcNow -lt $deadline)
if ($missing.Count -or -not $published) { throw 'CloudWatch aún no confirma ambos REPORT y la publicación' }
$published
```

La lectura normal exitosa puede producir solo los registros de plataforma. Para comprobar la recepción, definir `$evidenceQueueUrl` con la URL de la cola autorizada para ese caso y validar el cuerpo original recibido.

```powershell
$batch = Invoke-AfCli sqs receive-message --queue-url $evidenceQueueUrl --wait-time-seconds 20 --max-number-of-messages 10
$msg = Get-Content "$case/anomaly.json" -Raw | ConvertFrom-Json
$entry = $batch.Messages | Where-Object {
    $receivedEvent = $_.Body | ConvertFrom-Json
    $receivedEvent.source -eq 'arquifusion.health' -and $receivedEvent.'detail-type' -eq 'AnomalyDetected' -and
    $receivedEvent.detail.machineId -eq $msg.machineId -and $receivedEvent.detail.operationId -eq $msg.operationId -and $receivedEvent.detail.severity -eq 'CRITICAL'
} | Select-Object -First 1
if (-not $entry) { throw 'Recepción pendiente de confirmar en el observador' }
[IO.File]::WriteAllText("$case/received.json", $entry.Body)
.\.venv\Scripts\python.exe -c "import json,sys; from jsonschema import Draft202012Validator as V; from pathlib import Path; V(json.loads(Path('contracts/anomaly-detected.schema.json').read_text(encoding='utf-8')),format_checker=V.FORMAT_CHECKER).validate(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig')))" "$case/received.json"
if ($LASTEXITCODE -ne 0) { throw 'El evento recibido incumple el contrato' }
```

Conservar respuestas, request IDs, claves e informe del observador. Los registros `PROFILE` y `MODEL#…/BASELINE` de prueba persisten. La TTL automática afecta a los `READING#`. Para retirar la instrumentación autorizada, quitar el target `Evidence` de `$ruleName` con `events remove-targets`, comprobar `FailedEntryCount=0` y retirar únicamente esa regla con `events delete-rule`, ambos con `--event-bus-name $bus`. La cola queda a cargo de su propietario.

## Pérdida de alertas

La Lambda desplegada conserva el comportamiento del primer despliegue. El código de Alexis en #44 persiste `pendingAlert` en la transacción y lo retira tras la aceptación de EventBridge. El ensayo local de Rol 1 rechaza la primera publicación, reproduce la misma lectura y comprueba que se reenvía la misma anomalía una vez y se limpia el pendiente. Falta probarlo en Academy tras actualizar el paquete. Una máquina que deja de transmitir puede no disparar la recuperación. [PR #44](https://github.com/alxgr-08/arquifusion/pull/44)

Coordinar con el propietario del handler un **outbox transaccional** que persista la intención de alerta junto con lectura y perfil, con ID estable derivado de la máquina y el mensaje. Un publicador independiente puede reintentar fallos por entrada, marcar el envío tras la aceptación y alarmar por pendientes. Una caída después del envío puede duplicar el evento. El consumidor debe deduplicar el ID. El stream de la tabla ya está activo. Para superar su ventana de retención, mantener las intenciones pendientes hasta confirmarlas. [Patrón transactional outbox de AWS](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)

## Contenido del PR de Rol 1

Incluir `infra/aws/{versions,providers,variables,dynamodb,outputs,health_evaluator}.tf`, `infra/aws/.terraform.lock.hcl`, las plantillas `backend.hcl.example` y `terraform.tfvars.example`. Para bootstrap, incluir solo `infra/aws/bootstrap/main.tf`, `terraform.tfvars.example` y `.terraform.lock.hcl`. Añadir los scripts, lock de wheels y esta guía en `infra/aws/health_evaluator/`, las reglas de `.gitignore`, el README de infraestructura y la documentación del proyecto revisada. Mantener `build/`, ZIP, planes, `*.tfstate*`, `.terraform/`, `backend.hcl`, `terraform.tfvars` y credenciales fuera del PR.
