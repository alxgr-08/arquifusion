# Ensayo manual de preventivo y detector offline

El módulo opcional del estado de aplicación de `infra/aws` prepara tres recursos en `us-east-1` para una corrida del kit. Crea la máquina Standard `af-dev-preventive-trial-nnnn`, la Lambda `af-dev-maintenance-trial-nnnn` y su grupo de logs con retención de catorce días. La Step Function usa el ASL preventivo original. La Lambda también acepta una invocación manual del detector. La opción `maintenance_trial_operation` vale `null` por defecto. El módulo carece de Scheduler, reglas de EventBridge, permisos IAM nuevos y suscripciones. [ASL del equipo](../../../statemachines/preventive.asl.json) · [Kit y propiedad de sus identificadores](../../../docs/ensayo-lab/README.md)

## Contrato de la operación

1. El operador fija **una operación previamente acordada**, por ejemplo `ENSAYO-0007`, en la configuración revisada de Terraform. El nombre de la función, la cuenta y el ARN de la tabla `af-dev-arquifusion` también deben coincidir al invocarla.
2. La fila `OP#ENSAYO-0007 / INCIDENT_SETTINGS` debe identificar la corrida `0007`, contener `partsFromOtherWarehouses=false` y mapear exclusivamente `ZE` a `WH-E0007`. El perfil `MACHINE#ENS-0007 / PROFILE` debe tener la misma corrida, operación, zona e índice de pertenencia. La Lambda obtiene ambas filas por clave, con lectura consistente. El evento no determina los permisos.
3. El preventivo carga ese perfil y lee sus KPI de los últimos días. Comprueba la operación de cada KPI. Para cada filtro del kit lee por clave únicamente el stock de ese almacén y su pronóstico de la próxima semana ISO. Un stock ausente cuenta como cero. Un mapa ausente o ampliado interrumpe la ejecución. La ruta general de Sebastián consulta particiones de piezas compartidas y queda fuera del ensayo.
4. Para crear una orden, una transacción comprueba nuevamente el perfil y el mapeo de almacén. El identificador depende de máquina y día UTC. Repetir el flujo el mismo día puede devolver `created=false`. El plan de reposición es informativo. El estado `SendDailySummary` conserva el resumen en `plan.summary` y devuelve cero entregas externas. El ensayo no verifica distribución SNS.
5. El detector revisa el mismo perfil, con umbral de cinco minutos. La actualización condicional comprueba la última lectura y la pertenencia de máquina y corrida. Un perfil reciente o ya marcado devuelve una lista vacía en `offline[operationId]`. La validación inicial rechaza un perfil eliminado o reasignado y la escritura condicional protege frente a cambios que ocurran después de esa validación.

## Verificación local

Desde la raíz del repositorio, con las dependencias de desarrollo instaladas, primero prepara el ZIP compartido de incidentes si aún falta. El empaquetador del ensayo reutiliza sus dependencias Linux x86_64 y toma el código ejecutable del checkout actual. El ZIP y el manifiesto se escriben en `build/maintenance/`. Terraform compara el hash del ZIP, el de las dependencias y el de todas las fuentes con el manifiesto durante el plan.

```powershell
python infra/aws/maintenance/build.py
python -m pytest --disable-socket -q infra/aws/maintenance/test_maintenance.py
terraform -chdir=infra/aws test -test-directory=maintenance/root-tests
```

Para inicializar estas pruebas de Terraform, ejecutar `terraform -chdir=infra/aws init -backend=false -test-directory=maintenance/root-tests` con un directorio de datos de Terraform separado y el espejo local de providers. Los tests usan proveedores simulados y el lockfile de la aplicación. El ensayo Python ejecuta el ASL original, boto3 y DynamoDB en memoria (Moto). Verifica el resultado con dos operaciones sintéticas, un almacén compartido con existencias y un pronóstico ajeno. La fixture añade un KPI sintético de sobreuso y existencias de los filtros únicamente en Moto.

## Próxima corrida en Academy

El plan se prepara desde **la raíz `infra/aws`**, usando el backend y la cuenta aprobados para la aplicación. Elegir una corrida nueva del kit y comprobar primero las dos filas de propiedad. En caso de adoptar una corrida de incidentes, coordinar el momento del detector con su propietario, pues cambia su perfil a `OFFLINE`. Inventariar también ambos nombres de servicio y el log group antes de aprobar las tres altas. Revisar el plan con `maintenance_trial_operation="ENSAYO-nnnn"` y guardar los ARN devueltos por `maintenance_trial`. La creación en Academy, los permisos efectivos de LabRole y la invocación están pendientes.

Con esos recursos aprobados y preparados, iniciar la Step Function manualmente con un nombre de ejecución único y el JSON `{"operationId":"ENSAYO-0007"}`, sustituyendo el ejemplo por la corrida configurada. El perfil original del kit tiene 50 horas de margen y puede resultar `NotDue`. Para observar una orden requiere un KPI propio o un perfil sintético autorizado que acerque el mantenimiento a menos de 24 horas. Registrar ARN de la ejecución, su resultado, el identificador de la orden y `plan.reorders`. Anotar cuál fue la rama visitada.

En una invocación síncrona separada, enviar a la Lambda `{"action":"check_offline","operationId":"ENSAYO-0007"}` con la misma operación. Conservar el estado y `lastSeen` antes y después, `offlineSince` y la lista devuelta. Un reintento del detector sobre el perfil ya marcado devolverá una lista vacía. El inventario y los cambios de Academy requieren la autorización de esa corrida.
