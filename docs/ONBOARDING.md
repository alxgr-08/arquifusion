# Onboarding de ArquiFusion

Comienza por el [relevo actual de Rol 1](cierre-rol1-2026-11-15.md), la [arquitectura por componente](arquitectura.md), [contratos](../contracts/README.md) y [decisiones](decisiones.md). Las ramas de trabajo conservan cambios locales. Consulta GitHub y revisa el diff frente a `origin/main` antes de integrar.

## Desarrollo local

Con Python 3.12 y Node en el PATH, desde la raíz:

```sh
python -m venv .venv
# Activar el entorno de la plataforma.
python -m pip install -r requirements-dev.txt
python scripts/validate_contracts.py
python scripts/run_tests.py
python -m ruff check .
npm --prefix frontend ci
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

El frontend usa `mock` por defecto en desarrollo. La web publicada usa `live`, el client ID público, el origen Amplify registrado en Google y la API real con stage. `ENSAYO-0001` ya tiene sus siete filas conciliadas y dos grants reales cargados; falta el ensayo de login y acuse HTTPS. En desarrollo, los servicios se prueban con fakes/moto sin sesión cloud. Los tokens y secretos no pertenecen a las variables Vite.

## Operación

Leonardo conserva el backend Terraform exclusivo y el estado separado del bootstrap. Los usuarios trabajan con su propia sesión autorizada. Nunca intercambiar credenciales. Para iniciar una etapa preparar los ZIP y revisar `terraform fmt`, `validate` y el plan con backend identificado. [Instrucciones de AWS](../infra/aws/README.md) · [GCP](../infra/gcp/README.md)

La autoría se encuentra en [CODEOWNERS](../.github/CODEOWNERS). Los cambios de contrato requieren acuerdo entre los tres roles. Usar PR para integrar las contribuciones y revisar la CI antes de una demostración.

La IaC de Leonardo y las correcciones de los tres roles están en `main` mediante #52; #50 se cerró sin otro merge por ser redundante. Se conservan las mejoras de polling de #51, las semillas condicionales de #52, el SQL de retorno corregido en #53 y las consultas de demo de #54.

Google ya tiene registrado el origen `https://demo.d2ygcm34t8aku9.amplifyapp.com`. Alexis reportó `arquifusion.af_bridge_stage` en US con vencimiento de tablas de 24 horas. La CLI de Leonardo está instalada pero sin identidad Google local: esa comprobación del propietario no sustituye el preflight de la futura sesión del operador. El ejemplo Terraform de staging mantiene su creación deshabilitada para evitar proponer otra alta del dataset existente.

Leonardo autorizó el plan guardado de API con SHA-256 `87F604A64FDBB40E592D04164D2030918F165798E66A60BCA5DFC5E6C79116BB`: 31 altas, cero cambios/bajas y 29 recursos previos en no-op. La API real es `https://8wrfp8ncbh.execute-api.us-east-1.amazonaws.com/dev`; el estado posterior contiene 60 recursos sin drift. Posteriormente se conciliaron las siete filas del kit y la publicación live y se crearon los dos grants aportados, sin sobrescrituras. La [web HTTPS](https://demo.d2ygcm34t8aku9.amplifyapp.com) muestra el acceso Google. El login real y el acuse requieren la participación de los usuarios; lago y puente siguen pendientes. [Evidencia y próximos pasos](cierre-rol1-2026-11-15.md)
