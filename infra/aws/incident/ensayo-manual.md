# Ensayo manual de incidente con un almacén sintético

El programa `run_case.py` prepara localmente el caso 2 del kit de Sebastián. La opción `--execute` escribe las cinco filas propias de la corrida, inicia manualmente `af-dev-incident-entry` y confirma ARRIVED y RESOLVED mediante el handler API del checkout. La cuenta debe coincidir con la Academy revisada, perfil `default`, región `us-east-1`. La regla de incidentes permanece deshabilitada. La prueba produce un consumo de `BRG-SPH-22` en el almacén de la corrida. [Kit del equipo](../../../scripts/ensayo_kit.py) · [Contrato de los acuses](../../../contracts/api.md)

## Preparación offline

Desde la raíz, usar Python 3.12 con las dependencias de desarrollo. Elegir un número libre de cuatro cifras. La preparación crea un directorio nuevo y no consulta AWS.

```powershell
python infra/aws/incident/run_case.py --run 81
```

La salida predeterminada es `build/night-20261115/incident-0081/`. `case.json` contiene el kit congelado y `journal.json` conserva su huella, identidad de la corrida, token de idempotencia de la transacción y nombres de ejecución. Es posible elegir `--folder`. La carpeta debe ser nueva para preparar una corrida.

## Ventana autorizada de ejecución

El operador debe contar con autorización para este ensayo. El perfil, operación, técnico, concesión y almacén se comprueban antes de escribir. Preparar primero los ZIP definitivos de la base y comprobar que los cinco paquetes desplegados y ambos ASL coinciden con el checkout.

```powershell
python infra/aws/incident/run_case.py --execute --account 975050277936 --folder build/night-20261115/incident-0081
```

Los controles verifican los ARN, Python 3.12, LabRole, estado de actualización y SHA-256 de las cinco Lambdas. Comprueban las etiquetas de proyecto de tabla, Lambdas y workflows. Las definiciones Standard deben coincidir con los ASL locales. Todos los roles de `/af/notify/topics` deben apuntar al tópico vacío del proyecto. La regla debe estar DISABLED y el tópico debe tener cero suscriptores confirmados y cero pendientes.

El sembrado usa una transacción condicional con `ClientRequestToken` durable. Rechaza datos anteriores en las particiones de máquina, operación, técnico y concesión y stock de ese almacén. Se etiqueta cada fila con la identidad del diario. Las filas compartidas y los parámetros SSM conservan su configuración.

La entrada nativa devuelve el ARN de su hija. El programa espera la llegada, obtiene el detalle de la API y su ETag fuerte y guarda cuerpo y versión antes de solicitar el callback. Repite el mismo pedido cuando el resultado sea 503. Para la resolución revisa la espera nueva y solicita consumir una sola unidad de la pieza reservada. El acuse usa la concesión MAINT_LEAD sintética del kit y comprueba que el técnico asignado coincide con el técnico de ensayo.

Después del cierre inicia directamente una segunda ejecución de la hija con la misma anomalía. Su historial debe pasar por `DuplicateIgnored`. La comparación final exige el mismo incidente, un solo mantenimiento, dos recibos aceptados y stock propio en cero. Esta segunda ejecución comprueba la deduplicación de negocio después de un cierre. El despacho repetido de la entrada nativa constituye otro mecanismo de idempotencia.

## Evidencia y recuperación

`evidence.json` conserva los ARN de entrada, hija y replay, identificadores de solicitudes disponibles, estado del incidente, técnico, piezas usadas, historial público, claves de mantenimiento, recibos saneados y cantidades finales. El diario conserva los ETag y comandos de ambas esperas. Los tokens de Step Functions permanecen dentro de las llamadas del handler.

Si el proceso termina por un timeout, conservar el directorio y volver a ejecutar el mismo comando `--execute`. Antes de cualquier reintento se comprueba la huella del caso. Una respuesta perdida del sembrado se concilia con sus cinco filas y marcadores. Una ejecución ya iniciada debe tener el mismo nombre y exactamente el mismo input. Los callbacks reutilizan cuerpo y versión. Las diferencias de propiedad, cantidad de filas o asignación interrumpen la prueba para inspección.

El ensayo usa un operador autenticado por IAM, la API ejecutada localmente y claims sintéticos. La validación de un ID token de Google, el autorizador JWT de API Gateway y el sitio web requieren su propia prueba. La entrada del ensayo es una anomalía del kit. La ingesta por Health Evaluator se verifica por separado. No se acredita recepción humana de SNS porque el tópico se exige vacío.

## Pruebas del programa

```powershell
python -m pytest -q --disable-socket infra/aws/incident/test_manual_case.py
```

Los tests usan boto3 y DynamoDB en Moto y un cliente de workflow controlado. Comprueban aislamiento, seed condicional, respuesta perdida del seed, respuesta perdida de StartExecution, respuesta perdida del callback, reanudación y evidencia saneada. La simulación de los estados valida el controlador del ensayo. El recorrido real del ASL se prueba por separado en las suites de `statemachines/` y del servicio API.
