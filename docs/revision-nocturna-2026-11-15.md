# ArquiFusion · revisión autónoma y despliegue del 15 de noviembre de 2026

Este relevo sucede al informe de las 03:25 y al paquete de arquitectura del mismo día. Leonardo es Rol 1, Sebastián es Rol 2 y Alexis es Rol 3. La aplicación opera en **AWS Academy Learner Lab**, perfil `default`, región `us-east-1`. El requisito de dos nubes usa BigQuery/BigQuery ML en `arquifusion`, ubicación `US`. El corte de código está publicado en el [PR #50 en borrador](https://github.com/alxgr-08/arquifusion/pull/50).

## Resultado comprobado

Se publicaron la infraestructura acumulada y las correcciones de operación, se actualizaron los cinco paquetes existentes y la definición de incidentes, y se completaron pruebas reales sobre datos sintéticos propios. La revisión final encontró una incompatibilidad entre claves de lectura antiguas y nuevas. Una segunda actualización corrigió exclusivamente el paquete de tareas. El estado de Terraform mantiene **27 recursos administrados**. El plan posterior devuelve **0 altas, 0 cambios y 0 bajas**. La regla `af-dev-incident` permanece **DISABLED**. Se conservaron el estado separado del bootstrap y los otros worktrees. [Evidencia saneada de Academy](evidencias/2026-11-15-academy.json)

### Health Evaluator en AWS

| Comprobación | Resultado observado |
|---|---|
| Máquina y operación sintéticas | `HEAHM-4107`, operación `AF-TEST-82A3A8F288AE` |
| Lectura normal | Salud 100, severidad OK, `emitted=false`. Request ID `85a21418-b2c4-4764-a613-91e0ed6fa935`. |
| Anomalía | Salud 0, severidad CRITICAL, `emitted=true`. Request ID `9625bd41-5344-4b66-868f-5c2a28a5ac55`. |
| EventBridge | CloudWatch registra aceptación con Event ID `bc29d7fa-a755-3da7-c115-ab0393dd9f41`. La regla deshabilitada conserva pendiente la entrega automática. |
| Repetición de la anomalía | `SKIPPED`. Request ID `e30e63f3-7528-4ecd-b661-5914317f7291`. |
| Recuperación de un evento ya entregado | `ALREADY_DELIVERED_OR_ABSENT`. Request ID `d1318f81-2c40-4188-a9b9-bf6f553ef454`. |
| Persistencia | Dos `READING#` y perfil CRITICAL, sin outbox pendiente tras aceptación. Línea base con nombre exclusivo del caso. |

La búsqueda filtrada de CloudWatch devolvió cero coincidencias durante el primer minuto. La lectura directa del stream recuperó la aceptación y su Request ID. Los datos sintéticos y los logs se conservaron. La caída del bus, las carreras y la migración de un `pendingAlert` antiguo se ensayaron con Moto y sockets bloqueados.

### Incidente real y acuses

El caso `ENSAYO-3816` usa máquina `ENS-3816`, técnico `T-E3816` y almacén `WH-E3816`. La entrada comenzó a las **05:24:39 de Lima**. Se generó **INC-a8c8**, que terminó `RESOLVED`. La madre, hija y repetición terminaron `SUCCEEDED`.

- Llegada y resolución obtuvieron dos recibos `ACCEPTED`. Los callbacks respondieron 200 con Request IDs `a7858f82-0941-422b-95d8-8d21259b6ba1` y `024c43a0-8d63-41d7-a781-47bbfd74f6e8`.
- Se registró un mantenimiento `MAINT#2026-11-15#MNT-INC-a8c8`.
- Se consumió una unidad de `BRG-SPH-22` en `WH-E3816`. Su stock final tiene `qty=0`, `available=0` y `reserved=0`.
- Una nueva ejecución con el evento repetido tomó `DuplicateIgnored`. Se conservaron un incidente, un mantenimiento y un único consumo.
- Los ARN de ejecución, recibos, cronología y metadatos están en la evidencia JSON. El ejecutor verificó ownership, ausencia de colisiones, cinco hashes, ASL, roles, regla y cero destinatarios SNS antes de sembrar cinco filas condicionalmente.

Los callbacks se enviaron mediante el handler API del checkout, ejecutado localmente con una concesión sintética y el acceso IAM del operador. Step Functions, las tareas y DynamoDB fueron reales. El ensayo por API Gateway con un ID token Google y un técnico humano conserva su verificación pendiente. [Ejecutor reproducible y conciliación](../infra/aws/incident/ensayo-manual.md)

### Compatibilidad histórica comprobada

El primer paquete de esta revisión necesitó un ajuste adicional en `_latest_reading`. En DynamoDB una clave antigua terminada en `00Z` puede ordenar después de `00.100000Z`. El lector ahora compara los instantes de los dos candidatos superiores.

Tras el ajuste se sembraron cuatro filas nuevas propias de `MGIIW-1102`, con una lectura antigua, una fraccional y otra anterior. La Lambda desplegada eligió `fuelBatchId=NEW`. Request ID `f9ca42c4-8da5-4e41-8c00-d4058632b806`. Esta prueba queda registrada junto a las demás evidencias.

## Código corregido y publicado

| Área | Cambios comprobados | Referencias |
|---|---|---|
| Telemetría y alertas | Se guarda la muestra antes de intentar publicar. El replay encuentra su propia outbox, se conserva atómicamente el pendiente legado al escalar y se protegen la línea base y operación frente a concurrencia. Microsegundos y registros antiguos compatibles. Logs de validación sin input y aceptación con Event ID. | [Health](../services/health_evaluator/handler.py), 47 pruebas. |
| Modelos compartidos | Validación de operación, nueve tipos de máquina, números estrictos finitos y timestamps UTC conforme al schema. Contexto adicional preservado. El JSON Schema conserva su contenido. | [Modelos](../shared/af_shared/contracts.py), 71 pruebas compartidas. |
| API y frontend | Perfil, concesión y espera comprobados dentro de la transacción del acuse. Las listas revalidan filas primarias ante GSI obsoletos. Históricos con ambos formatos de timestamp. ETag y cuerpo conservados ante respuesta perdida o truncada. | [API](../services/api/README.md), 45 pruebas. [Web](../frontend/README.md), 20 pruebas. |
| Incidentes | Claim limitado a la operación propietaria, escalada protegida de repeticiones y de perfiles liberados, contador de mantenimiento basado en las horas y fecha persistidas. Selección de lectura compatible con el historial. | [Tareas](../services/incident_tasks/incidents.py), 18 pruebas y 16 del ASL. |
| Puente y Terraform | Guardia del proyecto BigQuery antes de obtener identidad. Revisor de planes vincula también los módulos locales. Nuevas altas optativas con sus planes y controles iniciales deshabilitados. | [Puente](../services/lake_to_bigquery/README.md), [revisor](../infra/aws/api/review_plan.py). |

La outbox reduce el riesgo de perder alertas al fallar EventBridge. Un proceso puede caer después de que el bus acepte el evento y antes de borrar el pendiente, por lo que la identidad estable y la deduplicación del flujo siguen siendo necesarias. Una recuperación que exceda la retención del stream debe usar las filas durables y la ruta manual. La recepción humana de avisos requiere un destinatario acordado.

## Despliegues y planes

Se usó bloqueo normal de S3 para aplicar exclusivamente los archivos guardados y revisados. No se crearon roles ni se habilitaron horarios o reglas.

| Etapa aplicada | Resultado | SHA-256 del plan |
|---|---|---|
| Cinco paquetes Lambda y ASL | 0 altas, 6 cambios, 0 bajas. Los otros 21 quedaron no-op. | `26DA4B982B28460A4B616DB462A9F0416A9538C0854945C37F76A8AD3BBF0C4B` |
| Ajuste histórico de incident-tasks | 0 altas, 1 cambio, 0 bajas. Los otros 26 quedaron no-op. | `A8E2E3DB6422272E5C43DA4536132351352DB1B987E24253146406F9C15B3791` |

Los binarios siguientes están en `build/night-20261115/` del worktree, excluidos de Git. Se generaron después de ambos despliegues. Son alternativas sobre el mismo estado. Una etapa aplicada requiere volver a planificar las restantes.

| Plan preparado | Altas/cambios/bajas | Existentes no-op | SHA-256 |
|---|---|---:|---|
| `api-incidente.tfplan` | 11/0/0 | 27 | `B8B8161851AFD34272C1298D11D170B022862D18D825F8D241581075A3B7EFC1` |
| `api-dashboard.tfplan` | 31/0/0 | 27 | `0554725FBE17AE2CAD915BFAC3F0C7F93F331929E24A6B72E6ACD96C2CDF9E57` |
| `hosting-manual.tfplan` | 2/0/0 | 27 | `4760F724A554BA84C760320449651FCD954334E66DE60F6100F85262E5C79575` |
| `mantenimiento-outbox.tfplan` | 4/0/0 | 27 | `7531312F7A778A2839A73BE3E8654B32DA7A7B69C78632A564E64E0325AF6E37` |

Las API permanecen cerradas y usan el Client ID web real y el origen autorizado `http://localhost:5173`. Para el sitio público, obtener el origen que genere Amplify, registrarlo en Google y preparar otro plan con el CORS exacto y apertura explícita.

El lago permanente necesita un bucket privado dedicado y etiquetado. Su módulo detiene el plan cuando ese requisito falta. Se conservan la implementación y la prueba temporal Parquet de Academy. La publicación permanente y las nuevas altas de GCP quedan pendientes de autorización. El GCP de este equipo sigue sin CLI y sesión personal autorizada.

## Validación y Git

- GitHub confirmó `origin/main` en `53f9755` al comienzo y después del ensayo. Se preservaron las ramas y worktrees previos.
- `d914fe2257ca2a6150892b6518fbb48835c449a1` publica la entrega acumulada.
- `e1e3828aae579841206027fe2b38fae691cf95fd` corrige la selección entre timestamps antiguos y nuevos.
- Git no tenía autor configurado. Se usaron Leonardo Panduro y el correo noreply de la cuenta GitHub verificada `leonardopanduro-rgb` para estos commits, sin cambiar la configuración global.
- **311 pruebas de negocio/modelos/ASL, 51 de infraestructura y 20 de frontend** pasaron en Linux. Pasaron Ruff, seis ejemplos de contratos, TypeScript, Vite, validación de raíces y **34 escenarios Terraform simulados**.
- El workflow importó nativamente los tres paquetes del puente en Ubuntu CPython 3.12. Los hashes del build local coinciden con los del build Linux para esos paquetes.
- Se conserva un aviso del test offline-detector por un intento de socket bloqueado. La prueba termina satisfactoriamente.
- Se actualizaron las trece issues que estaban abiertas y se cerró #18 tras comprobar su criterio de publicación e importación Linux. Quedan **12 abiertas y 20 cerradas**. #8 y #9 ya estaban cerradas. El PR #50 sigue en borrador.

[CI del código](https://github.com/alxgr-08/arquifusion/actions/runs/43704586778) · [Terraform y smoke nativo](https://github.com/alxgr-08/arquifusion/actions/runs/43704586774) · [PR #50](https://github.com/alxgr-08/arquifusion/pull/50)

## Qué falta a cada integrante

| Integrante | Completado | Pendiente exacto | Dependencia | Próxima acción | Issue/PR |
|---|---|---|---|---|---|
| Leonardo | IaC publicada, cinco Lambdas y ASL actualizados, pruebas reales, estado 27/0-drift, cuatro planes y evidencias | Revisar las altas de hosting/API/lago/consumidores y completar sus despliegues autorizados. Confirmar permiso real de alta de Amplify. | Aprobación de los planes, origen Google, acceso GCP | Priorizar hosting para obtener URL y el lago/acceso GCP en paralelo. Tras cada alta regenerar los planes. | #10–17, #50 |
| Sebastián | Núcleo del flujo verificado en Academy con dos recibos, pieza y mantenimiento únicos. Correcciones publicadas | Revisar #50 y preparar las concesiones reales de la operación de demo con el técnico asignado. Validar por HTTP/JWT el caso final. Acordar destinatario SNS. | API accesible, sub personal de participantes, techId y consentimiento del destinatario | Revisar los recibos y el kit, registrar la concesión TECHNICIAN del caso aprobado y ensayar ARRIVED/RESOLVED desde la web. | #23, #12, #50 |
| Alexis | GIS y analítica histórica del equipo integrados; Health actualizado y probado; frontend y retorno comprobados localmente | Registrar origen HTTPS, confirmar permisos e identidad de GCP y producir el KPI de la lectura nueva. Validar vista live y preparar video. | URL de Leonardo, operador GCP autorizado y staging US | Resolver acceso BigQuery en paralelo al origen. Luego registrar URL, activar build live y seguir el mismo messageId hasta el KPI. | #14, #41, #7, #50 |

## Orden para terminar la demostración

1. Revisar el PR #50 por los tres roles. Su amplitud incluye Terraform y los puentes que todavía estaban únicamente en los worktrees locales. Conservar CI verde y revisar los cambios compartidos antes de integrar.
2. Leonardo revisa el alta de hosting y la API. Tras la autorización de la app y rama, obtiene el origen HTTPS y se lo pasa a Alexis. Alexis lo registra en Google. Regenerar el plan de API para ese origen y la apertura acordada. El frontend de ensayo debe utilizar `VITE_API_MODE=live`.
3. En paralelo, Alexis y el administrador conceden al ejecutor los accesos apropiados de BigQuery. Leonardo prepara el bucket/lago y staging para la aprobación de escritura. El ejecutor humano del puente permite avanzar aunque la federación aislada necesite más coordinación. [Procedimiento](../infra/gcp/README.md)
4. Sebastián configura las concesiones de las personas participantes sobre una corrida nueva. Para un técnico se requiere su propio `sub` y el `techId` asignado. Cada persona comparte solo su identificador estable. El caso debe probar 401/403/412, respuesta perdida y dos confirmaciones desde la web.
5. Seguir una lectura del simulador hasta su objeto Parquet, job BigQuery y fila final. Alexis genera el agregado del mismo caso. Retornar el KPI acotado a DynamoDB y leerlo desde la operación de la web.
6. Completar la recepción de avisos con un destinatario voluntario si forma parte del video, revisar el diagrama y grabar el guion. El repositorio sigue siendo privado. El equipo debe comprobar el acceso de los profesores antes de entregar en Canvas el video, enlace de repositorio y PNG.

AWS Academy comparte cuenta con otras aplicaciones y fija un máximo educativo de **10 entornos Lambda concurrentes**. Mantener el ensayo manual acotado. La disponibilidad de listados de API Gateway o Amplify conserva pendiente la prueba de creación. El login Google, la recepción SNS y el recorrido real completo de ambas nubes necesitan sus propias evidencias.
