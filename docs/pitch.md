# Guion del pitch — máximo 4:45 (owner: Rol 3)

> Si el video pasa de 5:00 no se revisa. Meta: **4:40**. Ritmo: ~140 palabras por minuto.
> Hablan los tres: **A** = Alexis (Rol 3), **L** = Leonardo (Rol 1), **S** = Sebastián (Rol 2).

## Guion

### 0:00 – 0:30 · Problema (A) — pantalla: foto/clip de una mina + el título

> "En una mina, un camión detenido no es un problema de un camión: frena la pala que lo carga, la chancadora que lo
> espera y a todo el turno. ArquiFusion fabrica esa maquinaria y sus clientes operan cientos de equipos. Hoy se
> enteran de una falla cuando la máquina ya se detuvo, con datos que nadie alcanza a mirar, mantenimientos por
> calendario y repuestos que faltan justo cuando se necesitan."

### 0:30 – 1:00 · Solución (A) — pantalla: vista Flota en vivo

> "Construimos ArquiFusion Fleet Intelligence: una plataforma que convierte la telemetría de la flota en
> decisiones. Detecta comportamientos anormales antes de la falla, organiza la respuesta automáticamente y aprende
> del historial. Es cien por ciento serverless y multinube: AWS opera, Google Cloud analiza, y solo pagamos cuando
> llegan datos."

### 1:00 – 3:30 · Demo en vivo (S conduce, A comenta la IA)

| Tiempo | Acción en pantalla | Narración |
|---|---|---|
| 1:00 | Vista **Flota**: mapa con 12 máquinas en verde | **S:** "Estas son doce máquinas enviando telemetría cada minuto por IoT Core." |
| 1:10 | Panel **Demo**: inyectar *Desgaste de rodamiento* en EXC-0042 | **S:** "Simulamos un rodamiento que empieza a desgastarse." |
| 1:20 | La barra de salud de EXC-0042 baja: amarillo → naranja → rojo | **A:** "El motor de salud compara cada lectura con el historial de esa máquina y con otras del mismo modelo. Temperatura y vibración suben juntas; solo así, combinadas y sostenidas tres lecturas, la gravedad sube. Así evitamos falsas alarmas." |
| 1:45 | Consola de **Step Functions**: ejecución en curso, pasos en verde | **S:** "Al pasar a crítico se publica un evento y arranca esta Step Function: correlaciona la zona, diagnostica, reserva el repuesto, asigna técnico y avisa por rol." |
| 2:05 | **Incidentes**: tarjeta de EXC-0042 con el diagnóstico de IA | **A:** "Claude, en Amazon Bedrock, lee las señales y el historial y explica: probable desgaste de rodamiento, detener la máquina, dos horas de reparación." |
| 2:20 | Cambiar el selector de **Rol**: Operador → Técnico → Gerencia | **A:** "Cada persona recibe lo que necesita: el operador, qué hacer ya; el técnico, el diagnóstico y el repuesto reservado; gerencia, el impacto." |
| 2:35 | Técnico asignado, ETA, repuesto reservado; botón **Confirmar llegada** | **S:** "El técnico se eligió por distancia, especialidad y carga de trabajo. Confirma su llegada y la Step Function continúa." |
| 2:50 | Inyectar *Combustible contaminado* en la zona Z3 → banner de incidente de zona | **S:** "Ahora fallan varias máquinas en la misma zona…" **A:** "…y la plataforma no ve tres incidentes: ve uno solo. Todas cargaron el mismo lote de combustible." |
| 3:10 | **KPIs**: gráfico productividad vs costo; **Repuestos**: pronóstico | **A:** "En Google Cloud, BigQuery ML calcula todos los días KPIs, el pronóstico de repuestos y detecta consumos anómalos de combustible. Este camión casi no trabaja y es el más caro por hora; esta excavadora consume cada semana un poco más: desgaste." |

### 3:30 – 4:30 · Arquitectura (L) — pantalla: `docs/architecture.png`, luego consolas

> "Así funciona por dentro. La telemetría entra por IoT Core; Firehose la guarda en S3 como Parquet y una Lambda
> evalúa la salud en tiempo real. El estado vive en DynamoDB con tablas globales en dos regiones y recuperación a
> cualquier segundo de los últimos 35 días. EventBridge y Step Functions orquestan cada incidente. Cada quince
> minutos los datos viajan a BigQuery en Google Cloud, y los resultados del aprendizaje automático vuelven a AWS:
> las dos nubes trabajan en ambos sentidos."
>
> *(pantalla: Secrets Manager y Parameter Store)* "Ninguna credencial está en el código: la llave de Google Cloud
> está en Secrets Manager, y los umbrales y pesos del motor de salud, en Parameter Store cifrado con KMS; se ajustan
> sin redesplegar."
>
> "No hay ningún servidor encendido: Lambda, Step Functions, DynamoDB bajo demanda y BigQuery escalan de doce a
> miles de máquinas, y el costo crece con el uso, no con la infraestructura."

### 4:30 – 4:45 · Cierre (A) — pantalla: lista P1–P13 con checks

> "Fallas anticipadas, mantenimiento según el uso real, repuestos, técnicos, combustible, productividad e
> incidentes de zona: los trece problemas de ArquiFusion en una sola plataforma. El siguiente paso: conectar
> máquinas reales. Gracias."

## Dónde se ve cada problema

| Problema | Momento del video |
|---|---|
| P1 fallas / anomalías multivariables | 1:20 barra de salud, 2:05 diagnóstico |
| P2 mantenimiento según uso | 1:00 columna "Próx. mant." en Flota |
| P3 repuestos | 2:35 repuesto reservado, 3:10 pronóstico |
| P4 ubicación y estado | 1:00 mapa |
| P5 asignación de técnicos | 2:35 |
| P6 combustible | 3:10 |
| P7 productividad | 3:10 |
| P8 comportamiento inusual (vs. propio y flota) | 1:20 narración |
| P9 comunicación por rol | 2:20 |
| P10 historial | 2:35 línea de tiempo del incidente |
| P11 incidentes de zona | 2:50 |
| P12 historial para planificar | 3:10 + `af_out.failure_precursors` en la consola de BigQuery si sobra tiempo |
| P13 escala y costo | 4:00 |

## Rúbrica visible en el video

- [ ] 2 nubes funcionando: consola AWS **y** consola BigQuery con tablas `af_out` (3:30–4:00)
- [ ] Step Functions ejecutándose (1:45)
- [ ] Secretos en Secrets Manager / SSM (4:05) — **obligatorio**
- [ ] IA: diagnóstico Bedrock (2:05) + BigQuery ML (3:10)
- [ ] Serverless y alta disponibilidad: dicho y mostrado en el diagrama (3:30)

## Preparación de la grabación

1. Ensayar con el modo simulado: `http://localhost:5173/?fault=EXC-0042:BEARING_WEAR`.
2. El día de la grabación, usar el entorno real; si algo falla, el modo simulado reproduce el mismo flujo.
3. Tener abiertas de antemano las pestañas: frontend, Step Functions (ejecución), BigQuery, Secrets Manager, SSM.
4. Grabar por bloques (OBS) y unir; cronometrar cada bloque contra esta tabla.
5. Revisión final de los tres: duración ≤ 4:45, audio claro, ningún secreto visible en pantalla.
