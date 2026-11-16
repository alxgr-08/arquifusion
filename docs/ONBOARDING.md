# Onboarding de ArquiFusion

Comienza por la [arquitectura y estado vigente](arquitectura.md), el [puente horario](../infra/bridge_airi/README.md), los [contratos](../contracts/README.md) y las [decisiones](decisiones.md). El [relevo de Rol 1](cierre-rol1-2026-11-15.md) conserva el corte histórico de API y hosting. Las ramas de trabajo pueden contener cambios locales: revisar el diff frente a `origin/main` antes de integrar.

## Desarrollo local

Con Python 3.12 y Node.js 22 con npm en el PATH, desde la raíz:

```sh
python -m venv .venv
# Activar el entorno de la plataforma.
python -m pip install -r requirements-dev.txt
python scripts/check_repository.py
python scripts/validate_contracts.py
python scripts/run_tests.py
python -m ruff check .
npm --prefix frontend ci
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

El frontend usa `mock` por defecto en desarrollo. La web publicada usa `live`, el client ID público, el origen Amplify registrado en Google y la API real con stage. `ENSAYO-0001` tiene sus siete filas conciliadas y dos grants cargados. El técnico confirmó el login; la grabación de ambos acuses HTTPS sigue pendiente en la evidencia publicada. En desarrollo, los servicios se prueban con fakes/moto sin sesión cloud. Los tokens y secretos no pertenecen a las variables Vite.

Para reproducir las pruebas offline de CI en PowerShell:

```powershell
$env:AWS_EC2_METADATA_DISABLED = "true"
$env:PYTEST_ADDOPTS = "--disable-socket"
python scripts/run_tests.py
python -m pytest -q infra/aws/test_plan_stage.py infra/aws/api infra/aws/health_evaluator infra/aws/incident infra/aws/telemetry infra/aws/maintenance infra/gcp infra/hosting infra/bridge_airi
python infra/aws/telemetry/prepare_case.py
```

## Controles automáticos

| Workflow | Qué verifica y produce |
|---|---|
| [CI](../.github/workflows/ci.yml) | Archivos sensibles/generados, conflictos de merge, JSON, enlaces internos, lint, contratos, pruebas aisladas y build web. Publica el ZIP de vista previa `mock` como artefacto durante siete días. |
| [Terraform](../.github/workflows/terraform.yml) | Formato, proveedores bloqueados, raíces independientes sin backend remoto, paquetes Linux y planes con proveedores simulados. Se ejecuta en PR y `main` cuando cambian sus entradas. |
| [Security](../.github/workflows/security.yml) | Historial completo y checkout con Gitleaks, auditoría de requisitos Python y locks Linux, y auditoría npm. Se ejecuta en PR, `main` y semanalmente. |
| [Dependabot](../.github/dependabot.yml) | Propone actualizaciones semanales de GitHub Actions, npm y Python. |

Los tres workflows permiten ejecución manual desde Actions. Usan permisos de lectura y no necesitan credenciales AWS o Google. El ZIP de CI es una vista previa; la publicación live sigue el [procedimiento de hosting](../infra/hosting/README.md). [Seguridad y manejo de credenciales](../SECURITY.md)

## Operación

Leonardo conserva el backend Terraform exclusivo y el estado separado del bootstrap. Los usuarios trabajan con su propia sesión autorizada. Nunca intercambiar credenciales. Para iniciar una etapa preparar los ZIP y revisar `terraform fmt`, `validate` y el plan con backend identificado. [Instrucciones de AWS](../infra/aws/README.md) · [GCP](../infra/gcp/README.md)

La autoría se encuentra en [CODEOWNERS](../.github/CODEOWNERS). Los cambios de contrato requieren acuerdo entre los tres roles. Usar PR para integrar las contribuciones y revisar todos los controles antes de una demostración.

La IaC de Leonardo y las correcciones de los tres roles están en `main` mediante #52; #50 se cerró sin otro merge por ser redundante. Se conservan las mejoras de polling de #51, las semillas condicionales de #52, el SQL de retorno corregido en #53 y las consultas de demo de #54.

Google tiene registrado el origen `https://demo.d2ygcm34t8aku9.amplifyapp.com`. El staging histórico `arquifusion.af_bridge_stage` pertenece al ensayo con operador; su ejemplo Terraform mantiene la creación deshabilitada. El puente permanente usa el dataset independiente `airi-erp.af_bridge` y la identidad de servicio de Cloud Run. Cloud Scheduler lo ejecuta cada hora al minuto 52, hora de Lima, sin depender de la sesión local del operador. [Operación y evidencia](../infra/bridge_airi/README.md)

La API publicada es `https://8wrfp8ncbh.execute-api.us-east-1.amazonaws.com/dev` y sus doce rutas requieren JWT. El corte de API y hosting contiene 60 recursos; el puente permanente tiene estado Terraform separado y no forma parte de ese recuento. La regla de incidentes sigue deshabilitada por la restricción del Learner Lab. La ingesta requiere el productor Python/Boto3; los acuses de llegada y resolución requieren la intervención del técnico. [Evidencia de API y hosting](cierre-rol1-2026-11-15.md) · [Evidencia del puente horario](evidencias/2026-11-15-puente-permanente.json)
