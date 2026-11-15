# Guion de demostración · hasta 4 min 45 s

| Tiempo | Quién | Qué mostrar y qué decir |
|---|---|---|
| 0:00–0:30 | Alexis | Una excavadora detenida retrasa al turno. Mostrar cómo se relacionan la lectura anómala, la decisión de mantenimiento y la pieza necesaria. |
| 0:30–1:10 | Sebastián | Identificar el modo de la pantalla. Mostrar lectura sintética, señales, severidad y perfil en DynamoDB. En modo mock, etiquetar la escena como simulación. |
| 1:10–2:05 | Sebastián | Mostrar la ejecución hija del incidente y su inicio manual. Enseñar asignación, diagnóstico por reglas, dos esperas y un solo mantenimiento. Solo afirmar entrega de SNS si existe recibo humano. |
| 2:05–2:50 | Leonardo | Mostrar el diagrama y la tabla regional con PITR. Explicar los dos caminos de archivo: telemetría completa por Firehose con Glue a Parquet y hechos operativos por Streams a NDJSON. Enseñar el objeto del ensayo temporal o su evidencia conservada. |
| 2:50–3:45 | Alexis | Mostrar en el proyecto arquifusion los datasets, un modelo y su resultado. El histórico sintético de 12 máquinas sirve para predicción de piezas y detección de consumo anómalo. Indicar qué datos alimentaron el modelo que se muestra. |
| 3:45–4:25 | Leonardo | Si hay ensayo trazable, correlacionar messageId, job de BigQuery, KPI retornado y vista autorizada. En su ausencia, mostrar qué se verificó por separado en cada nube y el alcance local de la prueba conjunta. |
| 4:25–4:45 | Equipo | Resumir el beneficio para mantenimiento y entregar diagrama y repositorio. |

La demo usa Google Sign-In y concesiones en DynamoDB para el acceso real. La grabación debe ocultar tokens y datos personales. La recepción humana, las cadencias automáticas, las garantías de escala y el cierre desde web requieren evidencia propia. [Estado y arquitectura](arquitectura.md)
