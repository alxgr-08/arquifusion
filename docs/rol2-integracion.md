# Integración del backend en AWS Academy

La base de trabajo incluye el simulador de Sebastián, sus tareas de incidente, las reglas de diagnóstico de Alexis y el protocolo de acuse v1.2 de los PR #47 y #49. El ensayo manual que ya llegó al cierre corresponde a una versión anterior del flujo. La evidencia actual y el estado de cada componente se mantienen en la [arquitectura](arquitectura.md).

## Recursos y contratos de integración

| Componente | Entrada y destino en la demo | Configuración |
|---|---|---|
| Simulador | Un caso sintético manual produce la misma lectura para Health Evaluator y Firehose. | `AF_INGEST_MODE=direct`, `AF_HEALTH_EVALUATOR_FUNCTION`, `AF_FIREHOSE_STREAM`. [Caso congelado y recuperación de envíos parciales](../infra/aws/telemetry/README.md) |
| Flujo de incidentes | EventBridge recibe `source=arquifusion.health`, tipo `AnomalyDetected`, severidad WARNING o CRITICAL. El destino es la entrada Standard, que inicia la hija. La regla está `DISABLED`. | `af-dev-incident-entry` y `af-dev-incident`. Las tareas usan la tabla regional, el catálogo comprimido en SSM Standard y los ajustes de la operación sintética. [Ensayo por operación](ensayo-lab/README.md) |
| Diagnóstico, piezas y asignación | El workflow construye el request contractual de diagnóstico, reserva por componente y almacén de zona, y asigna técnico por especialidad, distancia y carga. | La cuenta usa diagnóstico por reglas y distancia en código. `available >= :n` y la reserva por incidente protegen el stock. [Contratos](../contracts/) · [Tareas](../services/incident_tasks/README.md) |
| Avisos | La Lambda consulta `/af/notify/topics`. Los cinco roles apuntan al tópico regional del proyecto. | El tópico tenía cero suscripciones confirmadas en la inspección. El ensayo preventivo manual conserva un resumen dentro de la ejecución. [Notificaciones](../services/notify/README.md) |
| Preventivo y detector | Corrida manual fijada a una máquina, operación y almacén sintéticos. Comprueba propiedad, última lectura y antigüedad. | `maintenance_trial_operation` vale `null` por defecto. [Módulo y pruebas](../infra/aws/maintenance/README.md) |
| API y web | La primera etapa plantea GET de detalle y POST de acuse. Diez GET optativos sirven al dashboard. Cada ruta usa el JWT de Google y una concesión adecuada a la operación. | Google OAuth web, origen exacto, `AF_TABLE_NAME`, `AF_OPERATION_ID`. Cada permiso Lambda está limitado al origen API, stage, método y ruta. [Contrato](../contracts/api.md) · [Guía de API](../infra/aws/api/README.md) |

## Secuencia para el ensayo

1. Preparar un kit local con `python scripts/ensayo_kit.py --run 7 --out build/ensayo-0007`. Elegir una corrida libre para el ensayo real. Cada miembro accede con su propio `sub`. Sebastián vincula al técnico del kit con su `techId`.
2. La telemetría v1.0 conserva su `messageId` y contexto. El evaluador persiste `PROFILE` y `READING#` y respeta los atributos que usa el flujo de incidentes. El código nuevo incluye una outbox por anomalía recuperable. La deduplicación de la entrada protege contra un reenvío del mismo evento.
3. En la espera adecuada, leer el detalle, revisar la asignación y confirmar ARRIVED. La web envía el ETag fuerte mediante `If-Match`. Repetir el procedimiento para la nueva espera RESOLVED. Un reintento conserva cuerpo y ETag. Un 412 requiere una nueva revisión humana.
4. Verificar un mantenimiento, el consumo correcto de piezas, los recibos de ambas esperas y el cierre certificado por el workflow. El motor puede diagnosticar distintos componentes según medidas y nivel de riesgo. El kit fija datos de entrada para que la expectativa sea comprobable.
5. Comprobar por separado la carga y el retorno del dato analítico. El preventivo usa `operatingHours` y `overuseHours` en el KPI y `forecastQty` en el pronóstico de stock. Conservar el job, identidad de lectura, agregado y clave retornada.

El ZIP regional incluye los módulos de cada servicio, `af_shared` y sus dependencias Linux de Python 3.12. El despliegue y las invocaciones reales se revisan por etapa. Las pruebas locales ejecutan el ASL y los contratos del checkout. [Suite integral](../services/api/tests/test_multicloud_rehearsal.py) · [Runbook de incidentes](ensayo-lab/README.md)
