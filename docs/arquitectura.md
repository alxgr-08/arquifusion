# Arquitectura y estado de ArquiFusion

Corte del 15 de noviembre de 2026. ArquiFusion ayuda a mantenimiento y operaciones a decidir cuándo detener una máquina, qué pieza reservar, a quién asignarla y cómo comprobar su vuelta al servicio. AWS Academy Learner Lab opera la flota. BigQuery analiza el histórico en Google Cloud. Esta página mantiene la arquitectura vigente. El diario de decisiones y los relevos anteriores conservan su fecha de corte. [Draw.io editable](architecture.drawio) · [SVG](architecture.svg) · [PNG para Canvas](architecture.png)

## Recorridos y evidencia

| Recorrido | Evidencia y estado | Qué falta observar |
|---|---|---|
| Telemetría → evaluación → DynamoDB / EventBridge | **IMPLEMENTADO Y VERIFICADO** en Academy para dos lecturas sintéticas. Se conservan lectura y perfil. El evaluador actualizado conserva microsegundos, protege la pertenencia y publica WARNING/CRITICAL. El 15/11 se comprobaron salud 100/0, dos lecturas y un Event ID en CloudWatch. | La outbox por alerta ya está desplegada. Se probaron el replay y la ruta manual de un evento aceptado en la Lambda real. La caída del bus y la recuperación de varios pendientes están verificadas localmente. Un evento aceptado no acredita que alguien lo haya recibido. |
| Regla → entrada Standard → hija de incidente | **PARCIAL**. Regla y destino desplegados, regla `DISABLED`. El caso manual v1.2 INC-a8c8 llegó a RESOLVED con dos recibos ACCEPTED, un mantenimiento y una pieza. Su replay terminó en DuplicateIgnored. La hija coordina correlación, reserva, diagnóstico por reglas, asignación, avisos y dos esperas. | Los paquetes y ASL ya están alineados y desplegados. El handler de acuse del ensayo se ejecutó localmente mediante IAM. Faltan HTTP/JWT real, entrega automática y recepción humana de SNS. |
| Flota direct → Firehose con Glue → S3 Parquet | **IMPLEMENTADO Y VERIFICADO en ensayo temporal**. Se recuperó un Parquet Snappy real. Se retiraron los recursos temporales. | Aprobar un lago privado propio, distinto del bucket de estado. Enviar una sola lectura identificable y conservar su objeto. |
| Streams → exportador → S3 NDJSON | **IMPLEMENTADO Y VERIFICADO en local** para incidentes y consumo de stock. El stream de DynamoDB está activo. | Crear el consumidor previsto y probar sus prefijos por separado. El mapping parte deshabilitado. |
| S3 → staging → af_lake | **IMPLEMENTADO Y VERIFICADO en local** con Parquet del ensayo y dobles de S3/BigQuery. Se cargan los bytes originales y se deduplica por identidad. Alexis confirmó `af_bridge_stage` en US, con TTL de tablas de 24 h. | Falta una identidad autorizada en ambas nubes para el relay actual. Verificar job, fila y repetición en GCP. No recrear staging ni presentarlo como inspección propia de Leonardo. [Acceso y procedimiento](../infra/gcp/cierre-puente-2026-11-15.md) |
| Histórico → BigQuery ML → af_out | **IMPLEMENTADO Y VERIFICADO en GCP** según la inspección del paquete de contexto del 15/11. En US se observaron 69 120 lecturas, 25 incidentes, 108 movimientos, 3 modelos y 9 tablas de salida. | Ligar una lectura reciente de AWS por máquina y periodo a un resultado. Vigilar expiración de datasets/tablas del Sandbox. |
| af_out → DynamoDB | **PARCIAL**. La Lambda optativa retorna un KPI de máquina/operación/día con identidad WIF y escritura condicional. El ensayo manual puede usar una identidad humana autorizada y los mismos recibos. El retorno general de referencias y pronósticos es una ruta histórica explícita. | Demostrar una selección sintética acotada, su escritura y su lectura desde la web. |
| Google → navegador → HTTP API → Lambda | **API DESPLEGADA, ENSAYO AUTENTICADO PENDIENTE**. La aplicación autorizada añadió 31 recursos y conservó los 29 anteriores. Doce rutas JWT, Lambda Python 3.12/LabRole, stage `dev`, logs y permisos limitados. Usa `ENSAYO-0001`, cuyo kit y dos grants MAINT_LEAD/TECHNICIAN ya están cargados. La SPA conserva el ETag revisado; el backend consulta permisos por identidad estable y operación. | Ensayar login Google, 403 y callbacks por HTTPS con el técnico asignado. El login real de cada participante no se sustituye por cargar su grant. [Relevo y evidencia actual](cierre-rol1-2026-11-15.md) |
| Amplify Hosting | **FRONTEND LIVE PUBLICADO**. Aplicación WEB `af-dev-arquifusion-web`, app ID `d2ygcm34t8aku9`, rama manual `demo`, sin builds automáticos. Job 1 SUCCEED. `https://demo.d2ygcm34t8aku9.amplifyapp.com` responde 200; index y entrada JavaScript coinciden con el ZIP revisado. Se observó el botón Google renderizado. | Los participantes deben iniciar sesión y revisar datos/acuse. Firebase conserva su condición de alternativa. |
| Preventivo, offline y vista de la flota en vivo | **IMPLEMENTADOS SIN VALIDAR en cloud**. El ASL original, detector y vistas cuentan con pruebas locales. El módulo opcional añade una Lambda, su log y un workflow Standard para una operación ENSAYO. [Guía del preventivo y el detector](../infra/aws/maintenance/README.md) | Ensayar cada función por separado. Una vista simulada debe rotularse como tal. |
| Réplica interregional, Cognito, IoT como ingreso obligatorio y diagnóstico obligatorio en Bedrock | **OBSOLETOS para esta demo**. El diseño usa tabla regional con PITR, GIS, modo direct y diagnóstico por reglas. | Las alternativas históricas no forman parte del recorrido evaluado. |

## Operación y responsables

El estado remoto de la aplicación contiene **60 recursos administrados** y una fuente de datos del LabRole: 27 originales, app/rama Amplify y 31 de API. Los 29 anteriores quedaron sin cambios en esta última aplicación autorizada. La API añade la sexta Lambda; no configura concurrencia reservada ni activa simuladores. Se conservan la tabla bajo demanda con dos GSI, TTL, Streams y PITR, cinco parámetros SSM, dos máquinas Standard, bus/regla/destino deshabilitado, DLQ, SNS y observabilidad. El bootstrap usa estado local separado. El perfil es `default`, región `us-east-1`. El laboratorio comparte cuenta con otras aplicaciones y limita a 10 entornos Lambda concurrentes. Cada plan posterior debe conservar API y hosting y revisar cuenta, propiedad, acciones y permisos efectivos. No incluir estados, planes ni credenciales en Git.

| Responsable | Entregable | Referencia de ejecución |
|---|---|---|
| Leonardo | IaC, paquetes, API Gateway, lago, exportación y carga, evidencias | [AWS](../infra/aws/README.md), [GCP](../infra/gcp/README.md), [exportador](../services/stream_exporter/README.md), [cargador](../services/lake_to_bigquery/README.md) |
| Sebastián | Simulador, incidentes, API, técnicos, reservas, avisos y callbacks | [Backend](rol2-integracion.md), [ensayo](ensayo-lab/README.md), [contrato HTTP](../contracts/api.md) |
| Alexis | Salud, diagnóstico, web, SQL/ML y retorno | [Motor](../services/health_evaluator/README.md), [frontend](../frontend/README.md), [analítica](../analytics/README.md), [retorno](../services/bigquery_sync_back/README.md) |

## Ensayo conjunto

1. Generar artefactos locales y contrastar sus hashes con el plan sin bloqueo remoto. Identificar exactamente qué paquetes y ASL se actualizarían.
2. Usar máquina y operación sintéticas. Registrar messageId, objeto Parquet, ejecución del workflow y recibos de ACK. Usar token en memoria y concesión por rol y técnico.
3. En US buscar la fila de ese messageId, repetir el objeto y verificar que el conteo sigue en uno. Vincular por máquina y fecha el KPI de salida, el ítem retornado y la lectura autorizada de la aplicación.
4. Registrar qué parte se ejecutó realmente en cada nube. El caso de incidentes y el histórico sintético son dos evidencias independientes hasta completar este ensayo.

[Historial de los PR](https://github.com/alxgr-08/arquifusion/pulls?q=is%3Apr+is%3Amerged) · [Alcance del Sandbox](https://docs.cloud.google.com/bigquery/docs/sandbox) · [JWT de HTTP API](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html)

## Prueba integral reproducible en local

El ensayo de regresión usa clientes reales boto3 con DynamoDB y S3 simulados, el intérprete del ASL de Sebastián, el motor de diagnóstico por reglas, claims de prueba y el cliente simulado de BigQuery. Sigue la misma telemetría, alerta, llegada, resolución, reserva, mantenimiento, fila de lago y KPI. La repetición comprueba que quedan dos recibos, una pieza consumida, un mantenimiento y un KPI. La respuesta analítica del test es un fixture controlado. El entrenamiento real de BigQuery ML se acreditó por separado en GCP. [Ensayo de extremo a extremo](../services/api/tests/test_multicloud_rehearsal.py)

[Relevo actual, despliegues, evidencias y siguientes planes](revision-nocturna-2026-11-15.md) · [Corte anterior](informe-final-2026-11-15.md)
