# af-dev-bigquery-sync-back

El retorno de Alexis incorpora un KPI diario de BigQuery a la tabla operacional de Academy para que la API lo lea en GSI1. La Lambda preparada en Terraform se invoca manualmente y queda desactivada por defecto. El historial de modelos y repuestos conserva su entrada de compatibilidad.

## Ensayo de una máquina

1. Preparar la lectura sintética con su `machineId`, `operationId` y fecha UTC, cargarla en `arquifusion.af_lake.telemetry` y acordar con Alexis la actualización de `af_out.daily_kpis`. Conservar el `messageId` y el job analítico. El KPI aporta el agregado por máquina y día. Comprobar la ejecución del pipeline permite relacionarlo con la lectura reciente. [SQL del KPI](../../analytics/sql/10_daily_kpis.sql)
2. Acordar los identificadores públicos WIF de ArquiFusion y el permiso de lectura del proyecto. Configurar `bridge_wif` y `sync_back_trial` en la raíz AWS para un plan de revisión. Se admiten el par `SYN-9001` / `AF-PARQUET-SMOKE`, el kit `ENS-####` / `ENSAYO-####` con igual sufijo, y las máquinas `TLxxx-####` de la operación `AF-LAKE-TEST-<32 hex mayúsculas>` del generador. La tabla esperada es `af-dev-arquifusion` en la cuenta y región de la función. El perfil de la máquina debe contener la misma operación y modelo. [Terraform del puente](../../infra/aws/bridge.tf)
3. Después de autorizar esa etapa, el evento debe coincidir exactamente con los tres valores configurados para la función. Ejemplo `{"machineId":"ENS-0007","operationId":"ENSAYO-0007","date":"2026-11-15"}`. La invocación ejecuta una sola consulta SELECT parametrizada en US. Dado que `daily_kpis` carece de columna de operación, se comprueba en `af_lake.telemetry` que todas las lecturas de esa máquina y fecha pertenecen a una sola operación y modelo. Se incorporan los campos de clúster y tendencia disponibles. Un resultado vacío devuelve `{"kpis":0,"status":"not_found"}`, y dos resultados se rechazan.
4. La transacción de DynamoDB comprueba el `PROFILE` y exige que la clave `MACHINE#<id> / KPI#<día>` esté libre. Un primer retorno devuelve `{"kpis":1,"status":"written"}`. Al repetir la invocación se leen perfil y KPI en una instantánea transaccional. Si el ítem completo coincide, devuelve `{"kpis":0,"status":"already_present"}`. Un KPI distinto o un perfil ajeno requieren revisión y se conservan intactos.
5. Leer por GSI1 `OP#<operación>#KPI#<día>`, comprobando máquina y valores. La verificación en la web requiere sesión y concesión para esa operación. [Contrato HTTP](../../contracts/api.md)

Ejemplo de alcance Terraform, con la identidad federada ya aprobada configurada en `bridge_wif`:

```hcl
sync_back_trial = {
  machine_id   = "ENS-0007"
  operation_id = "ENSAYO-0007"
  kpi_date     = "2026-11-15"
}
```

El handler desplegable es `services.bigquery_sync_back.handler.selected_handler`. Terraform configura `AF_TABLE_ARN`, `AF_SYNC_MACHINE_ID`, `AF_SYNC_OPERATION_ID`, `AF_SYNC_DATE`, `GCP_PROJECT_ID=arquifusion` y `GOOGLE_APPLICATION_CREDENTIALS=/opt/arquifusion/gcp-wif.json`. La capa contiene el descriptor público del proveedor aprobado. El administrador de identidad debe comprobar que el sujeto federado queda aislado del resto de aplicaciones que comparten LabRole.

**Permisos a comprobar**. En GCP, jobs de consulta en `arquifusion` y lectura de `af_lake.telemetry`, `af_out.daily_kpis`, `af_out.machine_clusters` y `af_out.fuel_trends`. En DynamoDB, `ConditionCheckItem`, `PutItem` y `GetItem` para las claves del ensayo, además de logs de la función. El plan reutiliza el rol existente y la capa WIF. [Permisos transaccionales de AWS](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html) · [Identidad entre nubes](../../infra/gcp/README.md)

## Compatibilidad con la proyección histórica

La entrada original `services.bigquery_sync_back.handler.handler` permite el modo masivo únicamente al configurar `AF_SYNC_MODE=legacy`, `AF_TABLE_NAME=af-dev-arquifusion` y `GCP_PROJECT_ID=arquifusion`. La ventana usa `days` del evento o `AF_SYNC_DAYS` (7 por defecto), y el índice de KPI usa `AF_OPERATION_ID` (originalmente `MINA-NORTE`). Esta ruta sobrescribe por PK/SK con `batch_writer` y requiere autorización propia para el conjunto de datos. El ensayo Terraform apunta exclusivamente al handler de una fila.

| Salida en BigQuery | Destino histórico en DynamoDB |
| --- | --- |
| `daily_kpis`, `machine_clusters`, `fuel_trends` | `MACHINE#<id> / KPI#<día>` |
| `model_baselines` | `MODEL#<modelo> / BASELINE` |
| `parts_forecast`, `parts_irregular` | `PART#<id> / FORECAST#<almacén>#<semana>` |

La identidad predeterminada también es WIF en este modo. Para recuperar explícitamente la autenticación anterior de una instalación aprobada se usa `AF_SYNC_AUTH_MODE=legacy_secret` con `AF_GCP_SECRET_ID` indicado por el operador y una cuenta de servicio del proyecto `arquifusion`. El runtime del ensayo omite esa configuración.

## Validación local

```sh
python -m pytest -q --disable-socket services/bigquery_sync_back
python -m services.lake_to_bigquery.build --service bigquery_sync_back
python -m services.lake_to_bigquery.build --service bigquery_sync_back --smoke
```

La última prueba requiere CPython 3.12 Linux x86_64. En Windows `--smoke-sources` comprueba la importación del código del ZIP con las dependencias locales. Los ensayos de Moto ejercitan el SDK, las transacciones, las condiciones y GSI1. La consulta de BigQuery se verifica con dobles. El resultado en ambas nubes requiere el ensayo autorizado.
