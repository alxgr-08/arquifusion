# Arquitectura y estado de ArquiFusion

ArquiFusion ayuda a decidir cuándo detener una máquina, qué pieza reservar y a quién asignar la reparación. AWS Academy Learner Lab opera incidentes en `us-east-1`. Google Cloud analiza un histórico sintético de la flota en BigQuery. La lámina del 15 de noviembre de 2026 muestra el estado previo al ensayo web conjunto. [PNG para Canvas](architecture.png) · [JPG original](architecture.jpg)

El diagrama técnico complementario organiza los mismos recorridos por componente y conserva su fuente editable. [Draw.io](architecture.drawio) · [SVG](architecture.svg)

## Qué se puede demostrar

| Problema | Recorrido | Evidencia disponible |
|---|---|---|
| Una avería se detecta tarde | Health Evaluator evalúa la salud por reglas, conserva lectura y perfil en DynamoDB y publica anomalías en EventBridge | Dos lecturas sintéticas de un ensayo anterior produjeron salud 100 y 0. El bus aceptó la anomalía. La regla `af-dev-incident` está `DISABLED`. La evidencia se puede mostrar sin enviar telemetría nueva. |
| Taller, almacén y técnico pierden el hilo de la atención | Una ejecución manual de `af-dev-incident-entry` despacha la hija `af-dev-incident`. La hija reserva, asigna, espera llegada y resolución y registra mantenimiento y consumo | El ensayo anterior `INC-a8c8` cerró con dos recibos, un mantenimiento y una pieza consumida. El reenvío se deduplicó. El tema SNS tiene cero suscriptores. [Evidencia del ensayo anterior](evidencias/2026-11-15-academy.json) |
| Una persona podría confirmar un incidente ajeno o repetir un acuse | Amplify sirve React por HTTPS. Google identifica a la persona. API Gateway valida el JWT en doce rutas. La API consulta operación, rol y técnico asignado en DynamoDB y vincula cada acuse a la espera revisada con ETag | Amplify `demo`, publicación 2 exitosa. Sebastián confirmó el login, la operación y la vista de `T-E0001`. En el corte quedan pendientes los dos acuses por HTTPS para `ENS-0001`. [Publicación](https://github.com/alxgr-08/arquifusion/pull/55#issuecomment-6901640824) · [Confirmación del técnico](https://github.com/alxgr-08/arquifusion/issues/23#issuecomment-6902549983) |
| Se planifican combustible y repuestos sin histórico | BigQuery `af_lake` alimenta SQL y BigQuery ML en `af_ml`. Los resultados de consumo, segmentos y demanda de piezas llegan a `af_out` | En el proyecto `arquifusion`, ubicación US, el equipo documentó 12 máquinas, 60 días, 69 120 lecturas sintéticas, 25 incidentes y 108 movimientos. Hay dos modelos ARIMA_PLUS, un KMEANS y nueve tablas de resultados. [Analítica](../analytics/README.md) · [Consultas de demostración](../analytics/demo_queries.sql) |

## Qué queda fuera del despliegue de la entrega

| Componente en el repositorio | Alcance comprobado |
|---|---|
| Firehose con esquema Glue → S3 privado | Un ensayo aislado produjo Parquet Snappy válido. Sus recursos temporales se retiraron. El bucket del estado Terraform tiene uso exclusivo de infraestructura. [Ensayo de telemetría](../infra/aws/telemetry/README.md) |
| DynamoDB Streams → `stream_exporter` → S3 | El stream de la tabla está activo. La proyección de incidentes y consumos a NDJSON se probó localmente. Al corte había cero mapeos a Lambdas del proyecto. |
| `lake_to_bigquery` y `bigquery_sync_back` | El código y la regresión local cubren carga, deduplicación y retorno del KPI. Sigue pendiente demostrar un mismo registro a través de las dos nubes. El equipo acordó presentar por separado AWS operativo y BigQuery ML con histórico sintético. [Decisión de alcance](https://github.com/alxgr-08/arquifusion/issues/14#issuecomment-6901536999) · [Prueba local](../services/api/tests/test_multicloud_rehearsal.py) |
| Simulador periódico, mantenimiento preventivo y detector offline | Son capacidades de código. La sesión de demostración usa un evento manual de incidente. El diagnóstico que aparece en AWS utiliza reglas. |

## Límite del Learner Lab y operación del ensayo

La TA indicó que **alcanzar diez ejecuciones Lambda simultáneas puede desactivar la cuenta** y que la ingesta de datos debe ejecutarse con Python/Boto3 desde una máquina. La cuenta aloja otros proyectos. Seis funciones desplegadas pueden producir más de seis ejecuciones si reciben llamadas simultáneas. Por ello, para esta entrega se excluyen el simulador de flota en AWS, los productores de telemetría que invocan Lambda y los consumidores Lambda de ingesta. El puente permanente y la creación de máquinas virtuales quedan fuera del ensayo.

Para una transferencia futura, el operador puede ejecutar Python desde una máquina autorizada. El comando `inspect` del relay compara localmente el Parquet con el mensaje original. Los comandos de transferencia usan directamente las API de S3, BigQuery y DynamoDB y requieren alcance y permisos propios. El histórico que se muestra en el video ya existe en BigQuery. [Relay del operador](../infra/gcp/rehearse_object.py)

El ensayo del incidente utiliza pasos distintos de la ingesta. Leonardo graba Step Functions. Sebastián abre una sola pestaña de la vista del técnico y hace los dos acuses humanos. El ASL contiene un bloque de tres ramas y un mapa de notificaciones con hasta cinco iteraciones concurrentes. La espera del callback reside en Step Functions. El límite del stage, una solicitud por segundo y ráfaga de cuatro, controla solicitudes HTTP. La métrica regional `AWS/Lambda · ConcurrentExecutions · Maximum` incluye la carga de los demás proyectos y llega con retraso. Si la actividad compartida impide mantener margen, el equipo presenta la evidencia anterior o reprograma la prueba. [Definición del flujo](../statemachines/incident.asl.json) · [Métrica de concurrencia](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-concurrency.html)

## Inventario de la operación

El estado remoto de la aplicación contiene **60 recursos administrados** y una consulta del LabRole existente. La tabla `af-dev-arquifusion` está activa, con dos índices, Streams, TTL, PITR y protección contra borrado. El bucket privado de estado tiene versionado, cifrado y bloqueo público. El bootstrap conserva su estado separado. En AWS también están los dos flujos Standard, seis Lambdas, el bus, la regla deshabilitada y su cola de fallos, el tema SNS, los parámetros SSM, logs y alarmas, API Gateway y Amplify.

## Cierre para Canvas

1. **Leonardo** conserva el único evento preparado de `ENS-0001`, comprueba disponibilidad de la cuenta y graba la ejecución hija cuando ambos operadores estén listos y el ensayo esté autorizado. Conserva el ID del incidente y la evidencia final.
2. **Sebastián** confirma llegada y resolución como `T-E0001`, revisa el ETag de cada espera y graba la web. El cierre se acredita con dos recibos certificados, un mantenimiento, una unidad de `BRG-SPH-22` consumida en `WH-E0001` y la liberación del técnico y de la máquina. El éxito del flujo padre solo acredita el despacho.
3. **Alexis y el equipo** completan el clip real de BigQuery ML y ensamblan el video de hasta cinco minutos. Tras revisar las evidencias, actualizan el estado del ensayo en el diagrama y entregan video, enlace al repositorio accesible al jurado y PNG. [Coordinación del ensayo](https://github.com/alxgr-08/arquifusion/issues/23) · [Video](https://github.com/alxgr-08/arquifusion/issues/7) · [Diagrama](https://github.com/alxgr-08/arquifusion/issues/19)
