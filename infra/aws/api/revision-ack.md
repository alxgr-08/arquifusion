# Acuse de incidentes v1.2

Sebastián integró el protocolo en #49. Rol 1 empaqueta su handler y prepara las rutas de detalle y acuse. El endpoint queda cerrado por defecto y su apertura de ensayo requiere un plan explícito. El borrador previo del worktree de planificación usaba una identidad `PRINCIPAL#`, un adaptador y un recibo propios. La integración actual usa las claves `GRANT#`, el recibo y el workflow del servicio de Rol 2. [PR #49](https://github.com/alxgr-08/arquifusion/pull/49) · [Contrato](../../../contracts/api.md) · [Servicio](../../../services/api/README.md)

| Paso | Comportamiento integrado |
|---|---|
| Leer | El detalle autenticado devuelve `ETag` cuando hay una espera con `waitGeneration`. El cliente conserva esa versión mientras el usuario decide. |
| Confirmar | La API recibe `If-Match`, verifica la concesión y la espera y crea un recibo `ACK#{incId}#{stage}#{generation}` antes de enviar el callback. |
| Reintentar | Con 503 `CALLBACK_PENDING`, se respeta `Retry-After` y se conserva el mismo ETag y cuerpo. El cambio de espera devuelve 412 y requiere que la persona revise el nuevo detalle. |
| Registrar | `CertifyArrival` y `CloseIncident` certifican el resultado. El navegador recibe el DTO del incidente. El token de Step Functions permanece en el backend. |

En el commit `53f9755` el GET comprobaba la operación del `PROFILE` con una lectura eventual. Si el perfil faltaba, `operation_of` devolvía `None` y la comprobación admitía una concesión de cualquier operación. El GET presentaba el incidente sin volver a comprobar su propio `operationId`. El criterio de aceptación de la corrección abarca el perfil ausente y la máquina trasladada y exige un grant vigente sobre la operación del incidente. El POST comprueba el grant del incidente. [Consulta y autorización](../../../services/api/routes.py) · [Comprobación de la concesión](../../../shared/af_shared/grants.py)

En ese mismo commit, `_if_match` normalizaba prefijos `W/` y comillas y la web omitía `If-Match`. Para el ensayo, el cliente v1.2 debe reenviar literalmente el ETag fuerte del GET y el backend debe comprobarlo. El sitio público también requiere su origen HTTPS registrado. [Ensayo](./ensayo.md)

La corrección local ya exige un perfil consistente y atribuible y comprueba la operación del incidente antes de entregar el detalle. Las pruebas obtuvieron 403 sin `ETag` después de un traslado y 404 al faltar el perfil. El POST exige un único ETag fuerte y el cliente conserva las comillas al enviarlo. El ensayo del stage real queda pendiente. [Pruebas de aislamiento](../../../services/api/tests/test_operation_scope.py) · [Contrato de los acuses](../../../services/api/tests/test_ack.py) · [Cliente HTTP](../../../frontend/src/api/client.ts)
