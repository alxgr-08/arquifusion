# Onboarding de ArquiFusion

Comienza por la [arquitectura vigente y las responsabilidades](arquitectura.md), [contratos](../contracts/README.md) y [decisiones](decisiones.md). Las ramas de trabajo conservan cambios locales. Consulta GitHub y revisa el diff frente a `origin/main` antes de integrar.

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

El frontend usa `mock` por defecto. Para el ensayo autorizado utiliza `live`, el client ID público, el origen registrado y la URL de la API con stage. Los datos de demo y los cambios de contrato se revisan con el equipo. En desarrollo, los servicios se prueban con fakes/moto sin sesión cloud.

## Operación

Leonardo conserva el backend Terraform exclusivo y el estado separado del bootstrap. Los usuarios trabajan con su propia sesión autorizada. Nunca intercambiar credenciales. Para iniciar una etapa preparar los ZIP y revisar `terraform fmt`, `validate` y el plan con backend identificado. [Instrucciones de AWS](../infra/aws/README.md) · [GCP](../infra/gcp/README.md)

La autoría se encuentra en [CODEOWNERS](../.github/CODEOWNERS). Los cambios de contrato requieren acuerdo entre los tres roles. Usar PR para integrar las contribuciones y revisar la CI antes de una demostración.
