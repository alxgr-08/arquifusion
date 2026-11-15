# Hosting de la SPA

Amplify Hosting es el destino HTTPS elegido, pendiente de alta y registro de origen. La consulta de lectura en Academy no acredita permiso para publicar. Para un despliegue manual se entrega un ZIP con index.html y assets en la raíz, y una reescritura de rutas de la SPA hacia /index.html. [Arquitectura](../../docs/arquitectura.md) · [Despliegues manuales de Amplify](https://docs.aws.amazon.com/amplify/latest/userguide/manual-deploys.html)

Después del build de Vite que se describe abajo, generar el ZIP desde la raíz del repositorio.

```powershell
python infra/hosting/prepare.py amplify
```

El comando genera `infra/hosting/build/amplify-…/arquifusion-web.zip` y muestra su SHA-256. Los mismos bytes de entrada producen el mismo ZIP, independientemente de las fechas y permisos del checkout. El ZIP contiene `index.html` y `assets/` al primer nivel. A su lado queda `amplify-rewrites.json`, para el editor JSON de **Rewrites and redirects** de la aplicación. Su regla 200 devuelve `index.html` para las rutas de navegación, incluidas `/machines/EXC-0042` e `/incidents`. Las solicitudes bajo `/assets/` y las rutas con extensión siguen el tratamiento de archivos estáticos, incluido el 404 si falta un recurso. [Reglas SPA de Amplify](https://docs.aws.amazon.com/amplify/latest/userguide/redirect-rewrite-examples.html)

Para la publicación autorizada elegir la aplicación y rama de ensayo del proyecto en `us-east-1`, revisar sus reglas existentes y cargar el ZIP como despliegue manual. Confirmar el dominio HTTPS que asignó Amplify a la rama antes de configurarlo con Alexis como origen GIS y pasarlo a `ack_frontend_origin`. La variante simulada sirve para probar las rutas y assets. La prueba autenticada depende además de la API y del protocolo de confirmación descritos al final. [Preparación de orígenes GIS](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid)

## Construir y revisar el paquete

Desde la raíz del repositorio, en un checkout limpio con Node y las dependencias del frontend fijadas por `package-lock.json`, construir primero en modo simulado.

```powershell
$env:VITE_API_MODE = 'mock'
$env:VITE_OPERATION_ID = 'MINA-NORTE'
python infra/hosting/prepare.py check-env
if ($LASTEXITCODE -ne 0) { throw 'Revisar las variables públicas' }
npm.cmd --prefix frontend ci
if ($LASTEXITCODE -ne 0) { throw 'Falló la instalación del lockfile' }
npm.cmd --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw 'Falló el build del frontend' }
python infra/hosting/prepare.py amplify
```

## Alternativa Firebase

Esta opción prepara localmente la SPA de React y Vite para Firebase Hosting clásico. El proyecto GCP previsto es `arquifusion`. El acceso a Firebase, la existencia de un sitio de ensayo y su URL pública están pendientes de comprobación. El paquete se puede construir y revisar antes de obtener esos permisos. Después del mismo build, ejecutar `python infra/hosting/prepare.py stage` para preparar este destino.

`stage` lee exclusivamente `frontend/dist/index.html` y los assets de Vite con hash de `frontend/dist/assets/`. Verifica los módulos, hojas de estilo y precargas del HTML y crea un directorio nuevo bajo `infra/hosting/build/ensayo-…/`. La ruta impresa contiene `firebase.json` y su propio `frontend/dist/`. La carpeta pública queda dentro del proyecto de Firebase y solo contiene esos archivos de distribución. Los mapas de fuentes, archivos ocultos, JSON y material de claves quedan fuera de la entrega. Firebase sirve los assets existentes y redirige las rutas de la SPA a `/index.html`. [Configuración de Firebase Hosting](https://firebase.google.com/docs/hosting/full-config)

## Variables públicas del build

| Variable | Valor para el ensayo |
| --- | --- |
| `VITE_API_MODE` | `mock` muestra la flota simulada en el navegador. `live` activa GIS y el cliente HTTP. |
| `VITE_OPERATION_ID` | `MINA-NORTE` por defecto. Para un ensayo integrado usar la operación acordada y una concesión real para el usuario. |
| `VITE_GOOGLE_CLIENT_ID` | En `live` usar el mismo ID público del cliente web de Google registrado en #41 y aceptado por `ack_google_web_client_id`. |
| `VITE_API_URL` | En `live` usar la salida Terraform `incident_api_base_url` del API aprobado. Incluye `https://`, el host de API Gateway en `us-east-1` y el stage `/dev`, sin barra final. |
| `VITE_MAP_STYLE_URL` | Opcional. Vacía usa el mapa OpenStreetMap del frontend. Si se usa un estilo externo, su URL y cualquier clave de mapas se distribuyen a los navegadores. |

`check-env` comprueba las variables de proceso antes del build. Vite también lee sus propios `.env*` cuando existen. Usar las variables públicas revisadas en un checkout limpio permite reproducir el resultado. El ID de cliente web es público. Los tokens, secretos OAuth y archivos de cuentas de servicio pertenecen fuera del frontend. Cambiar las variables de Vite requiere reconstruir y volver a preparar el paquete. [Entorno de Vite](https://vite.dev/guide/env-and-mode) · [Cliente del frontend](../../frontend/src/api/index.ts) · [Configuración de GIS](../../frontend/src/lib/auth.ts)

## Comprobar el sitio y acordar el origen

Los pasos con Firebase CLI requieren una sesión autorizada y se ejecutan desde el directorio de paquete que imprimió `stage`.

1. Con la identidad autorizada del proyecto `arquifusion`, comprobar si Firebase está habilitado y listar los sitios con `firebase hosting:sites:list --project arquifusion`. Hosting Viewer permite consultar sitios. Si falta el servicio o un sitio dedicado, el propietario del proyecto decide su alta. Para una publicación posterior, comprobar los permisos de Hosting y el rol API Keys Viewer que la documentación de Firebase indica para el despliegue con CLI. [Roles de Firebase Hosting](https://firebase.google.com/docs/projects/iam/roles-predefined-product#hosting)
2. Elegir con Alexis el sitio de ensayo autorizado y su URL HTTPS exacta. El dominio habitual es `https://<SITE_ID>.web.app` y el panel de Firebase muestra los dominios efectivos. La selección debe identificar un sitio que acepte una nueva versión de ensayo. [Sitios y dominios de Firebase](https://firebase.google.com/docs/hosting/multisites)
3. Asociar el sitio confirmado con `firebase target:apply hosting ensayo <SITE_ID_CONFIRMADO> --project arquifusion`. Este paso guarda la asociación en el `.firebaserc` local del paquete. Tras autorizar la publicación del sitio elegido, el comando acotado es `firebase deploy --only hosting:ensayo --project arquifusion`. [Targets de despliegue](https://firebase.google.com/docs/cli/targets)
4. Para login real, registrar con Alexis ese mismo origen en **Authorized JavaScript origins** del cliente GIS y pasar el valor a `ack_frontend_origin` en el plan AWS. El origen contiene esquema y host, sin rutas ni barra final. La API admite un origen exacto por configuración. El origen de desarrollo `http://localhost:5173` y el dominio de un canal de vista previa son orígenes distintos al del sitio estable. Verificar la petición CORS desde el dominio elegido después de un despliegue autorizado. [Orígenes de Google Identity Services](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid) · [HTTP API y CORS](../aws/api.tf)

## Alcance del modo live

La API de incidentes propone dos rutas protegidas con JWT, `GET /machines/{id}/incidents/{incId}` y `POST /machines/{id}/incidents/{incId}/ack`. El plan opcional `enable_fleet_read_routes` añade diez lecturas para máquinas, incidentes, técnicos, KPIs y repuestos. Tanto la creación como la apertura del endpoint HTTP y las rutas adicionales parten deshabilitadas. La navegación con datos reales depende del despliegue autorizado, de las concesiones por operación y de los datos disponibles. [Rutas de Terraform](../aws/api.tf) · [Llamadas de la interfaz](../../frontend/src/api/client.ts)

El contrato v1.2 devuelve el `ETag` de la espera en el GET de detalle y exige ese valor entre comillas en el `If-Match` del POST. El cliente de esta entrega conserva el `ETag` de la espera revisada y lo reenvía al repetir un 503. En el ensayo autenticado comprobar desde el navegador que pueda leer ese encabezado y que una espera obsoleta produzca un 412 y una nueva revisión. El plan CORS permite `Authorization`, `Content-Type` e `If-Match` y expone `ETag` y `Retry-After`. [Contrato de confirmación](../../contracts/api.md) · [Cliente actual](../../frontend/src/api/client.ts)

## Preparar el origen HTTPS de la demo

En el estado de la aplicación, `prepare_manual_web_hosting=true` propone dos recursos adicionales, `af-dev-arquifusion-web` (Amplify WEB) y su rama manual `demo`. Terraform configura la misma reescritura del empaquetador. Se omiten repositorio Git, tokens, rol nuevo, builds automáticos y previews de PR. El output `manual_web_origin` se resuelve al crear la aplicación. Entregar ese valor exacto a Alexis para registrar el origen JavaScript en GIS y configurar CORS. La entrega del ZIP y la apertura de la API corresponden a etapas posteriores. En el Learner Lab solo se observó la lectura del listado de apps. Si se deniega el alta, evaluar Firebase con permisos confirmados del proyecto. [Terraform](../aws/hosting.tf) · [Pruebas](../aws/tests/hosting.tftest.hcl)
