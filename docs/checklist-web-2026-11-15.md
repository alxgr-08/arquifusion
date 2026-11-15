# Web live y autenticación · checklist verificado el 15/11/2026

## Qué está disponible

AWS Academy Learner Lab, perfil `default`, región `us-east-1`. El frontend live está publicado en la aplicación `d2ygcm34t8aku9`, rama manual `demo`, y origen `https://demo.d2ygcm34t8aku9.amplifyapp.com`. La aplicación comparte cuenta con otros proyectos y el curso limita la cuenta a diez entornos Lambda concurrentes. El ensayo será de un usuario y un incidente sintético. EventBridge debe permanecer `DISABLED`.

La API HTTP ya está desplegada en `https://8wrfp8ncbh.execute-api.us-east-1.amazonaws.com/dev`, con el endpoint habilitado y las doce rutas protegidas por JWT. El cotejo de metadatos confirmó el emisor Google, la audiencia acordada y CORS para el origen exacto de Amplify. La web usa GIS y el JWT nativo de API Gateway, sin Cognito. El rol y la operación proceden de registros DynamoDB confiables, nunca del selector de rol del navegador.

El corte de las 14:59 (Lima) registró los datos y la web pendientes. Después otra ejecución sembró las siete filas y publicó Amplify job 1, SUCCEED a las 15:06. Al asumir la continuación se comprobaron 7 SAME y que index/entrada JavaScript son idénticos al ZIP live revisado. Se crearon únicamente los dos grants faltantes en una transacción condicional y se verificó su contenido. La pantalla pública renderiza ArquiFusion y el botón Google. El login de los participantes y los callbacks HTTPS siguen pendientes. [Evidencia actual y coordinación](cierre-rol1-2026-11-15.md)

La operación acordada es **`ENSAYO-0001`**. Alexis participará como `MAINT_LEAD` y Sebastián como `TECHNICIAN`, asignado a `T-E0001`. Leonardo solo tendrá acceso como `SUPERVISOR` cuando se prepare y cargue su concesión; el mensaje del compañero no concede ese rol. [Acuerdo de participantes y operación](https://github.com/alxgr-08/arquifusion/issues/23#issuecomment-6898434990)

## Preparación local de Leonardo

- `infra/hosting/release_live.py check` comprueba la receta sin leer credenciales, sin consultar cloud y sin fabricar el endpoint.
- `prepare` obtiene `incident_api_base_url` y `manual_web_origin` del output Terraform existente. Rechaza una URL ausente, otra región/stage y otro origen Amplify. Una API todavía no desplegada impide generar el ZIP live.
- Se rechazan archivos dotenv locales sin leer su contenido. El entorno de Vite se construye solo con `VITE_API_MODE=live`, URL real, Client ID público exacto, operación elegida y estilo de mapas vacío. No se escribe un `.env`.
- Se ejecutan tests y build, se verifican los identificadores públicos dentro del JavaScript y se empaqueta `index.html` y assets en un ZIP determinista. Se genera un manifiesto con SHA-256 y condiciones pendientes. No hay publicación ni operación de escritura cloud.
- El modo `probe` es una vista previa de cuatro solicitudes. Solo con `--send` realiza GET/OPTIONS de lectura: no solicita un ID token, no hace POST, no lee respuestas de datos y no sigue redirecciones.

Comandos desde la raíz, sustituyendo `terraform` por la ruta del ejecutable si no está en PATH:

```powershell
python infra/hosting/release_live.py check
# Después de crear la API, para la operación cuya concesión se haya preparado:
python infra/hosting/release_live.py prepare --operation ENSAYO-0001 --terraform terraform
python infra/hosting/release_live.py probe --operation ENSAYO-0001 --terraform terraform
```

El directorio `build/readiness-20261115/web/` guarda evidencia local y queda excluido de Git. El build de comprobación sin una URL real se ejecuta en modo `mock`, se identifica como validación y no se publica. No se entrega un ZIP live con una URL inventada.

## Condiciones antes de publicar

1. Completado: API desplegada con endpoint habilitado y doce rutas JWT; la regla de incidentes sigue deshabilitada. No repetir el plan anterior como si la API aún no existiera.
2. Completado: Alexis confirmó en #41 el origen exacto de Amplify en **Authorized JavaScript origins** del cliente Google. [Confirmación](https://github.com/alxgr-08/arquifusion/issues/41#issuecomment-6897046368)
3. Completado: siete filas y dos grants de `ENSAYO-0001`. TECHNICIAN está vinculado a `T-E0001`; la asignación de un incidente se comprueba al iniciar el caso. Cada persona usa su identidad Google. El grant sintético del kit no permite login Google.
4. Completado: ZIP live con la API `/dev`, `VITE_OPERATION_ID=ENSAYO-0001` y hash F996DDBE… publicado; no repetir el job ni activar builds automáticos.
5. HTTPS, index/entrada y botón Google observados. Falta navegación con sesión real y revisión de GIS/CORS durante ese login.

## Ensayo de autenticación y autorización

| Caso | Resultado que debemos comprobar |
|---|---|
| Token ausente o inválido | 401 de API Gateway, sin escritura de recibos. El probe automatizado usa únicamente una cadena inválida conocida. |
| Token expirado | 401; la sesión de la web se limpia y solicita login. Usar una sesión de prueba consentida; no copiar tokens a issues/logs. |
| Google ID token válido para la audiencia real | Acceso por Gateway y carga de los datos de su operación cuando existe grant. El login solo no garantiza acceso a incidentes. |
| Sin grant, otra operación o rol no permitido | 403 con el código del contrato; la espera y las filas del ensayo permanecen intactas. |
| TECHNICIAN sin asignación | 403; ni el selector visual de rol ni un `techId` enviado por el navegador conceden permiso. |
| CORS del origen aprobado | Preflight permite GET/POST, Authorization, Content-Type e If-Match. El navegador puede leer ETag y Retry-After en las respuestas de la API. |
| Origen ajeno | No recibe cabecera Access-Control-Allow-Origin. CORS no sustituye a JWT ni grants. |

Con la API ya desplegada, se pueden ejecutar los GET/OPTIONS del probe y guardar solo códigos HTTP, CORS y request IDs:

```powershell
python infra/hosting/release_live.py probe --operation ENSAYO-0001 --terraform terraform --send
```

## Ensayo del acuse, con escritura autorizada

El kit de Sebastián prepara un único incidente sintético. No reutilizar máquinas o stock compartido. Los POST y las siembras de datos quedan fuera del probe de lectura y requieren la etapa de ensayo autorizada. [Runbook HTTP](../infra/aws/api/ensayo.md) · [Contrato v1.2](../contracts/api.md)

Para acreditar **un consumo real de pieza** hace falta el caso 2: incluir `--with-stock` al revisar/autorizar la siembra (octava fila, una unidad de `BRG-SPH-22` en `WH-E0001`) y usar `anomaly-caso2-rodamiento.json`. La siembra básica de siete filas y el caso 1 de vibración no acreditan ese consumo. [Preparación del caso 2](ensayo-lab/README.md#caso-2-descuento-real)

1. Entrar desde la web con Google, leer el detalle y conservar el ETag fuerte de la espera observada. Registrar solo códigos y IDs de solicitud; mantener JWT, task tokens y cuerpos privados fuera de logs, capturas e issues.
2. Confirmar ARRIVED con ese ETag. Repetir un clic idéntico y comprobar un único recibo/efecto. Para RESOLVED volver a leer la nueva espera y usar su nuevo ETag.
3. Un 412 obliga a releer la espera y pedir nueva confirmación. Un 409 requiere revisar el conflicto, sin repetir automáticamente. Un 403 obliga a revisar la concesión/asignación vigente.
4. Ante `503 CALLBACK_PENDING`, el frontend conserva el mismo cuerpo y ETag y respeta `Retry-After`, con hasta cuatro intentos. Una respuesta de red perdida o JSON truncado conserva también la intención original. No repetir con otra versión o con piezas/notas distintas.
5. Si se agotaron los intentos, conservar el incidente y los recibos y revisar la evidencia certificada del workflow. No interpretar una ejecución fallida como aceptación del acuse. La reconciliación debe confirmar el efecto antes de informar éxito.
6. Tras RESOLVED comprobar un mantenimiento, un consumo por pieza, liberación de técnico y máquina y un ARRIVED/RESOLVED en el timeline. El caso de respuesta perdida se cubre primero en pruebas locales y después mediante un ensayo de fallo acordado, sin interrumpir recursos ajenos.

El polling live se redujo cinco veces mediante #51; el stage limita a una solicitud por segundo y ráfaga de cuatro. Comprobar 429 con un usuario antes de aumentar tráfico. Ese throttling no impone por sí solo el límite educativo de concurrencia Lambda.

## Pendientes de cada rol

- Leonardo: API y web verificadas, kit conciliado y grants creados; coordinar el ensayo autenticado y el siguiente plan del lago/puente. Su grant SUPERVISOR aún no existe.
- Alexis: origen Google y grant disponibles; iniciar sesión y validar la vista live como `MAINT_LEAD`. La corrección local del test de audiencia conserva `auth.ts` intacto.
- Sebastián: grant del técnico `T-E0001` cargado; iniciar sesión y participar en las dos confirmaciones del caso autorizado. Su backend permanece intacto.

El check local, tests y build no acreditan login válido, CORS observado desde el navegador con sesión Google, callbacks HTTPS ni una publicación web. Los metadatos de la API confirman su configuración; esas evidencias de usuario siguen pendientes.

El puente AWS → BigQuery → AWS conserva su ensayo real pendiente. Mostrar cada nube por separado es la alternativa ya prevista en [el guion, minuto 3:45](pitch.md); no acredita el intercambio bidireccional del diseño. Suprimir ese intercambio de la entrega sería un recorte de alcance que debe declararse, no una prueba del puente.
