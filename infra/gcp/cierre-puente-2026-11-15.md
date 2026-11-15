# Cierre de preparación del puente después de main · 15/11/2026

## Fuente y paquete actual

La referencia de esta comprobación es `main` en `09e4eff4238822a6519886202afd37941da6ab32`, después de #51–54. El PR #53 corrigió la consulta seleccionada de retorno: las columnas de telemetría están calificadas con `tel.` para impedir que `model` del HAVING resuelva al alias agregado `ANY_VALUE(model) AS model`. Alexis identificó el fallo con BigQuery real y reportó la validación y una lectura tras corregirlo. [Diagnóstico del propietario](https://github.com/alxgr-08/arquifusion/pull/50#issuecomment-6896935605) · [Corrección #53](https://github.com/alxgr-08/arquifusion/pull/53)

El ZIP anterior de `bigquery-sync-back` no contenía esa corrección. Se reconstruyó **solo ese paquete**, sin modificar código del servicio, con el builder Linux CPython 3.12 existente y sus requisitos fijados por hashes. Dos builds consecutivos dieron los mismos bytes:

| Artefacto | SHA-256 | Estado |
|---|---|---|
| `build/bridge/bigquery-sync-back.zip` | `2496bad1b0125316218f7f6864a659750b58a2ea674a31c5089c6d0ad0f1c0a3` | Actual, contiene la consulta `tel.*` de #53 |
| ZIP anterior | `7ec3436961aeb38ebbb95c49a567aa5cda1676a2efd972ca2c4a277cf4304e1f` | Histórico, no utilizar para el siguiente despliegue |
| `build/bridge/lake-to-bigquery.zip` | `2106db46bbcc08604e42cc0601316cecadacc4ccce03e0f8abebefca6b107ddf` | Handler ya coincide con main; no necesitó reconstrucción |
| `build/bridge/stream-exporter.zip` | `b8a9db040d2ed00d4e6cd377672646c99cd634deceec92ef48da46a05486858a` | Handler ya coincide con main; no necesitó reconstrucción |

El paquete nuevo ocupa 35.771.190 bytes, 77.970.602 expandido. CRC y vinculación de la fuente con el checkout pasaron; no incluye PyArrow ni pandas. Pasaron **12 pruebas del servicio de retorno** y **10 del conjunto seleccionado KPI/relay**, con sockets bloqueados. Los conjuntos se solapan y no suman 22 distintas. El smoke de fuentes pasó en Windows. La [CI Terraform del PR #55](https://github.com/alxgr-08/arquifusion/actions/runs/43755932793) reconstruyó e importó los tres ZIP en **Linux x86_64 CPython 3.12**, con hashes iguales a los locales.

La evidencia detallada está en `build/cierre-demo-20261115/bridge/validation.json`, excluida de Git. No se desplegó el ZIP ni se ejecutó el retorno en ninguna nube.

## Acceso y staging: qué se sabe realmente

Alexis, propietario de `arquifusion`, confirmó que **`af_bridge_stage` ya existe en US con vencimiento predeterminado de tablas de 24 horas**. También reportó el preflight de metadatos completo como correcto. Es una comprobación de su sesión, no de la sesión de Leonardo. [Confirmación del propietario](https://github.com/alxgr-08/arquifusion/pull/50#issuecomment-6897355061)

La instalación portátil local de `gcloud` funciona. Una consulta local `auth list` mostró **cero cuentas configuradas y ninguna activa**. Tampoco existe un ADC en la ubicación configurada del SDK ni está presente `GOOGLE_APPLICATION_CREDENTIALS`. Solo se comprobaron esos metadatos; no se abrieron contenidos de credenciales, no se llamó a `google.auth.default`, no se inició sesión y no se consultaron recursos GCP en esta preparación.

Por tanto:

- **La instalación de la CLI está resuelta.** [Activación de la copia portátil](cli-portable.md)
- **La existencia del staging está reportada y confirmada por Alexis.** No volver a proponer su creación con un estado Terraform vacío.
- **El acceso personal de Leonardo a GCP sigue pendiente.** La respuesta de Alexis identifica a Alexis como operador de su propia sesión Google.
- Mantener la gestión de staging deshabilitada en Terraform hasta acordar una eventual adopción autorizada; no ejecutar el ejemplo de alta como si el dataset siguiera ausente. Su ciclo de vida y datos pertenecen al proyecto de Alexis.

## Camino ejecutable con las herramientas existentes

El ensayo manual actual requiere que **el mismo operador** tenga sesiones autorizadas para las dos nubes. No comparte credenciales entre personas:

1. El propietario permite al operador usar `arquifusion`. Se necesitan jobs en el proyecto, acceso de tablas en staging, lectura/append a las tablas previstas de `af_lake` y lectura de las salidas del retorno. Ese permiso debe concederse por la vía institucional autorizada.
2. El operador inicia personalmente su sesión CLI y ADC de usuario Google. Las herramientas del relay rechazan una ruta explícita de llave privada y una identidad que no sea de usuario. El perfil AWS debe apuntar a la cuenta Academy de ArquiFusion en `us-east-1`; una sesión de otra cuenta no sirve.
3. Ejecutar el preflight de metadatos desde la CLI autenticada:

   ```powershell
   python infra/gcp/check_bigquery.py arquifusion --bridge
   ```

4. Con el lago desplegado mediante su etapa aprobada, enviar una sola lectura congelada del simulador. Verificar la lectura y el perfil en DynamoDB y descargar el objeto Parquet exacto de `records/`. Conservar el `reading.json` original, key, ETag, SHA-256 y messageId. Una aceptación parcial necesita conciliación por destino antes de reintentar.
5. Inspeccionar offline la igualdad del Parquet con esa lectura:

   ```powershell
   python -m infra.gcp.rehearse_object inspect --parquet <objeto.parquet> --reading <reading-original.json>
   ```

6. Tras aprobar la carga concreta, el comando `load` añade la cuenta Academy aprobada y la key exacta del objeto. Reutiliza los jobs deterministas, reservas y recibos de los componentes del puente. Repetir los mismos bytes debe recuperar el resultado y conservar una sola fila final:

   ```powershell
   python -m infra.gcp.rehearse_object load --parquet <objeto.parquet> --reading <reading-original.json> --account 975050277936 --object <records/key-exacta.parquet>
   ```

7. Alexis genera el KPI del mismo caso, máquina, modelo y día UTC, y conserva el job analítico. El retorno seleccionado comprueba la procedencia en `af_lake.telemetry`; un histórico de otra máquina no sustituye esa evidencia.
8. Tras aprobar el retorno, ejecutar `return-kpi` con los mismos argumentos. La primera escritura debe informar `written`; una repetición idéntica, `already_present`. Una fila incompatible o perfil ajeno se conserva y detiene el proceso. Leer después el KPI por la operación y fecha correctas en la API.

Los pasos de envío, carga, cálculo y retorno escriben recursos o datos y **no se ejecutaron**. Los ejemplos son la receta de la próxima etapa aprobada. [Runbook y recuperación](ensayo-preparado-2026-11-15.md)

## Alternativa con dos operadores

Si únicamente Alexis tiene acceso a Google y Leonardo a la cuenta Academy, puede coordinarse una demostración manual entre dos operadores, pero requiere acordar explícitamente el traspaso de artefactos y su conciliación:

1. Leonardo produce y verifica el objeto AWS y entrega únicamente la lectura sintética original, el Parquet exacto y su manifiesto de hashes/identificadores. No entrega su perfil AWS, claves o tokens.
2. Alexis valida los archivos con el inspector offline y realiza las operaciones BigQuery con su propia sesión. Conserva los job IDs y las comprobaciones de identidad, conteo y procedencia del caso.
3. Alexis devuelve evidencia del resultado del mismo caso, sin credenciales. Leonardo verifica el alcance y prepara una escritura condicional aprobada en su cuenta Academy.

**Esto no es una ruta automática ya implementada.** `rehearse_object load` y `return-kpi` acceden a ambas nubes y a los recibos S3 desde un mismo proceso. Ejecutarlos con una sesión Google que carezca de acceso al S3 del caso no convierte el flujo en un ensayo de dos operadores. Una carga manual que omita esos recibos tampoco demuestra la deduplicación del puente existente. Cualquier adaptación para el traspaso necesita conservar validación, jobs/recibos, aislamiento y escritura condicional, y revisarse antes de ejecutarla.

La vía más directa con el código actual es autorizar a un operador común para ambas nubes. La otra opción queda documentada como coordinación manual pendiente, con sus límites, sin crear scripts que simulen una consulta BigQuery o aceptar un JSON sin verificarlo como si proviniera del cliente autenticado.

## Responsables y siguiente acción

- **Leonardo:** conserva los paquetes y prepara el lago y componentes que se autoricen sobre los **60 recursos actuales**, incluida la API y el hosting. Mantiene EventBridge DISABLED. Concilia y regenera los planes antiguos después del despliegue.
- **Alexis:** confirma cómo obtiene acceso Google el operador común o acuerda el protocolo de dos operadores; genera el KPI del caso nuevo. Staging y corrección #53 ya están reportados como resueltos.
- **Sebastián:** prepara/revisa la operación de demo y sus grants para la lectura web; su simulador direct ya puede producir la lectura completa sin desplegar un proceso periódico.

El bloqueo del acceso GCP no impide completar la infraestructura AWS que tenga su propia autorización. No se crearon roles IAM, WIF, credenciales, datasets, jobs o recursos adicionales en esta preparación.
