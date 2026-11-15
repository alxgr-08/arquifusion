# Google Cloud CLI portátil para Leonardo

La preparación local usa la distribución oficial **593.0.0** para Windows x86_64, con Python incluido. El archivo publicado por Google tiene SHA-256 `8FBDC9D644FFDD25A7F515C2643EDC87100D3D48BE9D3AA635454F6D7FB6A53C`. [Archivo y checksum oficiales](https://docs.cloud.google.com/sdk/docs/downloads-versioned-archives)

La herramienta se prepara en `C:\Users\Cuycito\Proyectos\af-tools\gc593`, fuera de Git. Esta ruta corta evita límites de nombres de Windows. No se cambian el PATH global, el registro, ExecutionPolicy ni las protecciones del sistema. La descarga y extracción no crean recursos GCP ni habilitan APIs.

El resultado de la instalación y las versiones comprobadas se guardan en `build/readiness-20261115/tools/installation.json`. Una instalación disponible no demuestra acceso al proyecto; la sesión autorizada del operador y sus permisos siguen pendientes.

## Uso en una sesión local de PowerShell

Desde la raíz del worktree:

```powershell
$gcpBin = (Resolve-Path '../af-tools/gc593/bin').Path
$env:PATH = "$gcpBin;$env:PATH"
$env:CLOUDSDK_CORE_DISABLE_USAGE_REPORTING = 'true'
$env:CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK = 'true'
# Configuración de este ensayo, separada de otras sesiones/proyectos:
$env:CLOUDSDK_CONFIG = Join-Path (Get-Location) 'build/gcp-operator-config'
gcloud.cmd version
bq.cmd version
```

Estos cambios afectan solo a esa sesión. La carpeta de configuración está ignorada por Git. La comprobación de versiones de esta preparación utilizó otra configuración vacía y credenciales deshabilitadas; no se hizo login.

## Autenticación pendiente del operador

Después de que el propietario confirme acceso al proyecto `arquifusion`, el operador inicia personalmente su sesión con `gcloud.cmd auth login` y, para el SDK Python del puente, `gcloud.cmd auth application-default login`. Debe seleccionar la cuenta Google autorizada y revisar el consentimiento. Estos comandos no se ejecutaron automáticamente. No compartir contraseñas, ID/access/refresh tokens ni una llave privada de cuenta de servicio.

Con su sesión autorizada, puede comprobar únicamente metadatos:

```powershell
python infra/gcp/check_bigquery.py arquifusion --bridge
```

El proyecto y datasets se identifican explícitamente. El preflight no habilita servicios ni envía jobs o consultas. `ACCESS_DENIED` identifica un permiso pendiente; no demuestra que el recurso no exista. Las cargas y el retorno de KPI son etapas de escritura distintas y requieren la aprobación del caso. [Permisos y ensayo](ensayo-preparado-2026-11-15.md) · [ADC oficial](https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment)
