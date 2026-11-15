# Web live y autenticación · preparación del 15/11/2026

## Qué está disponible

AWS Academy Learner Lab, perfil `default`, región `us-east-1`. El hosting manual ya tiene la aplicación `d2ygcm34t8aku9`, rama `demo`, y origen `https://demo.d2ygcm34t8aku9.amplifyapp.com`. No está publicado el frontend. La aplicación comparte cuenta con otros proyectos y el curso limita la cuenta a diez entornos Lambda concurrentes. El ensayo será de un usuario y un incidente sintético. EventBridge debe permanecer `DISABLED`.

El plan de API preparado conserva los 29 recursos actuales y añade las doce rutas JWT del dashboard con endpoint cerrado. La web usa GIS y el JWT nativo de API Gateway, sin Cognito. El Client ID público real procede de #41; el rol y la operación proceden de registros DynamoDB confiables, nunca del selector de rol del navegador.

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
python infra/hosting/release_live.py prepare --operation MINA-NORTE --terraform terraform
python infra/hosting/release_live.py probe --operation MINA-NORTE --terraform terraform
```

El directorio `build/readiness-20261115/web/` guarda evidencia local y queda excluido de Git. El build de comprobación sin una URL real se ejecuta en modo `mock`, se identifica como validación y no se publica. No se entrega un ZIP live con una URL inventada.

## Condiciones antes de publicar

1. Leonardo autoriza y despliega el plan cerrado de la API; conserva 29 recursos previos y la regla deshabilitada.
2. Alexis confirma en #41 que el origen exacto de Amplify está en **Authorized JavaScript origins** del cliente Google. No requiere intercambiar secretos OAuth.
3. Sebastián revisa las concesiones de las personas de la demo para la operación acordada. Un TECHNICIAN debe tener `techId` y estar asignado al incidente. Cada persona usa su propia identidad Google.
4. Leonardo revisa y autoriza un plan explícito de apertura y luego la publicación del ZIP cuyo hash figura en el manifiesto. Configurar el build `live` con la URL real que incluye `/dev`.
5. Confirmar desde HTTPS carga de assets, navegación SPA y ausencia de errores de GIS/CORS. Google exige el mismo origen de navegador que se registró.

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

Después de abrir la API con autorización, se pueden ejecutar los GET/OPTIONS del probe y guardar solo códigos HTTP, CORS y request IDs:

```powershell
python infra/hosting/release_live.py probe --operation MINA-NORTE --terraform terraform --send
```

## Ensayo del acuse, con escritura autorizada

El kit de Sebastián prepara un único incidente sintético. No reutilizar máquinas o stock compartido. Los POST y las siembras de datos quedan fuera del probe de lectura y requieren la etapa de ensayo autorizada. [Runbook HTTP](../infra/aws/api/ensayo.md) · [Contrato v1.2](../contracts/api.md)

1. Entrar desde la web con Google, leer el detalle y conservar el ETag fuerte de la espera observada. Registrar solo códigos y IDs de solicitud; mantener JWT, task tokens y cuerpos privados fuera de logs, capturas e issues.
2. Confirmar ARRIVED con ese ETag. Repetir un clic idéntico y comprobar un único recibo/efecto. Para RESOLVED volver a leer la nueva espera y usar su nuevo ETag.
3. Un 412 obliga a releer la espera y pedir nueva confirmación. Un 409 requiere revisar el conflicto, sin repetir automáticamente. Un 403 obliga a revisar la concesión/asignación vigente.
4. Ante `503 CALLBACK_PENDING`, el frontend conserva el mismo cuerpo y ETag y respeta `Retry-After`, con hasta cuatro intentos. Una respuesta de red perdida o JSON truncado conserva también la intención original. No repetir con otra versión o con piezas/notas distintas.
5. Si se agotaron los intentos, conservar el incidente y los recibos y revisar la evidencia certificada del workflow. No interpretar una ejecución fallida como aceptación del acuse. La reconciliación debe confirmar el efecto antes de informar éxito.
6. Tras RESOLVED comprobar un mantenimiento, un consumo por pieza, liberación de técnico y máquina y un ARRIVED/RESOLVED en el timeline. El caso de respuesta perdida se cubre primero en pruebas locales y después mediante un ensayo de fallo acordado, sin interrumpir recursos ajenos.

La pantalla de máquina hace polling cerca de una solicitud por segundo; el stage limita a una por segundo y ráfaga de cuatro. Comprobar 429 con un usuario antes de aumentar tráfico. Ese throttling no impone por sí solo el límite educativo de concurrencia Lambda.

## Pendientes de cada rol

- Leonardo: creación/apertura de API por planes aprobados, generación del ZIP con URL real, publicación autorizada y evidencia por HTTPS.
- Alexis: confirmar el origen Google y validar la vista live. Su frontend y contratos permanecen intactos en esta preparación.
- Sebastián: revisar la concesión y técnico real de demo y participar en las dos confirmaciones. Su backend permanece intacto.

El check local, tests y build no acreditan login válido, CORS de la API desplegada, callbacks HTTPS ni una publicación web. Esas evidencias siguen pendientes de las etapas anteriores.
