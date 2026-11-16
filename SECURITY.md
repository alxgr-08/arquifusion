# Seguridad

El repositorio es público. Solo se versionan código, configuración de ejemplo y evidencia saneada.

## Configuración y credenciales

- Usar el proveedor de credenciales del SDK, roles existentes, identidades de servicio y SSM. Mantener credenciales temporales, claves Google y tokens fuera del checkout.
- Todo `VITE_*` es público y forma parte del JavaScript distribuido. Solo admite el modo, URL de API, operación, estilo de mapa público y OAuth client ID. Un secreto de cliente OAuth no pertenece al frontend.
- No versionar `.env` reales, claves privadas, `application_default_credentials.json`, archivos de participantes, `terraform.tfstate`, planes ni `.terraform/`. `.env.example` y `*.tfvars.example` contienen únicamente valores públicos o vacíos.
- No registrar cabeceras `Authorization`, tokens de callback, credenciales, cuerpos completos de solicitudes autenticadas ni volcados del entorno. La API devuelve proyecciones que excluyen claves de almacenamiento y tokens de Step Functions.

## Controles automáticos

`CI / repository` rechaza archivos sensibles o generados, conflictos de merge, JSON inválido y enlaces Markdown locales rotos. `Security` ejecuta Gitleaks sobre todo el historial disponible y los archivos del checkout con `--redact=100`; audita dependencias Python, locks de Lambda y npm en cada PR, en `main` y semanalmente. Dependabot prepara actualizaciones semanales de Actions, Python y npm.

Las Actions se fijan a commits completos y ejecutan con `contents: read`, sin conservar credenciales de Git en el checkout. Los controles de CI no necesitan credenciales AWS ni Google. Los cambios de infraestructura usan planes revisados y conservan los controles propios del Learner Lab.

La única excepción de Gitleaks es el fingerprint exacto de un cursor sintético de paginación del ejemplo original de API, eliminado en #40. El cursor codifica campos de una clave DynamoDB; no autentica solicitudes. No se excluyen carpetas de código o documentación del análisis del historial.

Referencias: [uso seguro de GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use), [Gitleaks](https://github.com/gitleaks/gitleaks) y [pip-audit](https://github.com/pypa/pip-audit).

## Si aparece una credencial

Revocar o rotar primero la credencial en su proveedor y revisar su uso. Quitar el valor del archivo actual no lo retira del historial Git. Coordinar la limpieza del historial con el equipo y conservar únicamente evidencia saneada: ruta, línea, tipo de hallazgo y commit, sin copiar el valor a issues, PR, logs o capturas.
