<#
.SYNOPSIS
  Portal dev LinkedOut en el puerto 1112, solo para pruebas (producción es lo real y siempre está
  prendida; el dev, apagado hasta que haga falta): develop con datos copiados de producción, sin LinkedIn. El mail va
  en modo prueba a la casilla del remitente (o a -MailPrueba <dirección>); a los destinatarios reales solo con -MailReal.

.DESCRIPTION
  -Iniciar           arranca el dev (crea el código y los datos si faltan) y deja la marca <Base>\dev-activo: sin
                     ella, el guardián de procesos apaga el 1112. Sin tarea programada ni ventana.
  -Detener           lo detiene y quita la marca.
  -ActualizarCodigo  lleva el código del dev a la punta de develop (lo reinicia si estaba corriendo).
  -RefrescarDatos    vuelve a copiar los datos reales (lo reinicia si estaba corriendo).

  Código: <Base>\dev, un worktree del repo en develop. Datos: <Base>\dev-datos, copia de la carpeta de datos
  real (las bases con la API de backup de SQLite) sin linkedin_profile*, sin desktop_profile, sin backups y sin el
  .env. .env propio (<Base>\dev.env): las credenciales de Gmail y la clave de Jooble copiadas del real y
  LINKEDOUT_MAIL_PRUEBA (la casilla del remitente, o -MailPrueba), salvo -MailReal.
  Con datos recién copiados: Radar detenido, búsquedas apagadas, análisis y generación en pausa. Un reinicio
  reanuda el Radar si quedó corriendo (sin LINKEDOUT_SIN_RADAR). Puerto fijo 1112: nunca toca
  el portal real (1111), sus datos ni su .env.

.EXAMPLE
  powershell -File tools\dev.ps1 -Iniciar
  powershell -File tools\dev.ps1 -ActualizarCodigo
#>
param(
    [switch]$Iniciar,
    [switch]$Detener,
    [switch]$ActualizarCodigo,
    [switch]$RefrescarDatos,
    [int]$Puerto = 1112,
    [string]$Base = (Join-Path $HOME 'LinkedOut'),
    [string]$Datos,
    [string]$EnvArchivo,
    # Dirección de prueba del mail; vacío = la casilla del remitente (el dev es de prueba)
    [string]$MailPrueba = '',
    # Los mails del dev salen a sus destinatarios reales: solo a propósito
    [switch]$MailReal
)
. (Join-Path $PSScriptRoot 'despliegue_comun.ps1')
$PUERTO_DEV = 1112

if (-not ($Iniciar -or $Detener -or $ActualizarCodigo -or $RefrescarDatos)) {
    Write-Host 'Uso: tools\dev.ps1 -Iniciar | -Detener | -ActualizarCodigo | -RefrescarDatos'
    exit 2
}

function Get-Completa([string]$ruta) { return [System.IO.Path]::GetFullPath($ruta).TrimEnd('\') }

function Test-Dentro([string]$ruta, [string]$carpeta) {
    if (-not $ruta -or -not $carpeta) { return $false }
    $r = Get-Completa $ruta
    $c = Get-Completa $carpeta
    return $r.Equals($c, [StringComparison]::OrdinalIgnoreCase) -or
           $r.StartsWith($c + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Stop-ConError([string]$texto) {
    Write-Host $texto
    exit 2
}

# ---------------------------------------------------------------- validaciones (antes de hacer nada)
$cfg = Get-Config
$Repo = Split-Path $PSScriptRoot -Parent
if (-not $Datos) { $Datos = Join-Path $Base 'dev-datos' }
if (-not $EnvArchivo) { $EnvArchivo = Join-Path $Base 'dev.env' }
$Codigo = Join-Path $Base 'dev'

if ($Puerto -ne $PUERTO_DEV -or $Puerto -eq [int]$cfg.puerto) {
    Stop-ConError "El dev corre solo en el puerto $PUERTO_DEV (pediste $Puerto). El $($cfg.puerto) es el portal real: no se toca."
}

$principal = $null
try {
    $comun = (& git -C $Repo rev-parse --path-format=absolute --git-common-dir 2>$null | Select-Object -First 1)
    if ($comun) { $principal = Split-Path $comun.Trim() -Parent }
} catch {}
$datosReales = @($cfg.datos, (Join-Path $Repo 'data'))
$envReales = @($cfg.env, (Join-Path $Repo '.env'))
if ($principal) { $datosReales += (Join-Path $principal 'data'); $envReales += (Join-Path $principal '.env') }
foreach ($prefijo in 'LINKEDOUT_', 'BUSCATRABAJO_') {  # compatibilidad: se borra tras el deploy (queda LINKEDOUT_)
    $d = [Environment]::GetEnvironmentVariable("${prefijo}DATA_DIR"); if ($d) { $datosReales += $d }
    $e = [Environment]::GetEnvironmentVariable("${prefijo}ENV_FILE"); if ($e) { $envReales += $e }
}

# Carpeta de producción y config de antes del corte, con el nombre viejo del proyecto: sigue en la PC
$UBICACION_VIEJA = '.buscatrabajo4'
# Producción, antes del corte (~/$UBICACION_VIEJA) y después (~/LinkedOut/{produccion,datos,produccion.env}): nunca destino
$produccion = @((Join-Path $HOME $UBICACION_VIEJA), (Join-Path $HOME 'LinkedOut\produccion'), (Join-Path $HOME 'LinkedOut\datos'))
$envReales += (Join-Path $HOME 'LinkedOut\produccion.env')
# Archivos sueltos de producción en ~/LinkedOut, al lado del dev: lanzador, vigilante, config y candado
$archivosProduccion = @('portal.cmd', 'portal.vbs', 'portal.log', 'vigilante.ps1', 'vigilante.log', 'despliegue.json',
                        'despliegues.log', 'CONGELADO') | ForEach-Object { Join-Path $HOME "LinkedOut\$_" }
foreach ($real in $archivosProduccion) {
    foreach ($destino in @($Datos, $Codigo, $EnvArchivo)) {
        if (Test-Dentro $real $destino) {
            Stop-ConError "El destino del dev ($destino) es o contiene $(Split-Path -Leaf $real) de producción: no se toca."
        }
    }
}
foreach ($real in $produccion) {
    foreach ($destino in @($Datos, $Codigo, $EnvArchivo)) {
        if ((Test-Dentro $destino $real) -or (Test-Dentro $real $destino)) {
            Stop-ConError "El destino del dev ($destino) es, contiene o está adentro de producción ($real): no se toca."
        }
    }
}
foreach ($real in $datosReales) {
    if ((Test-Dentro $Datos $real) -or (Test-Dentro $real $Datos) -or (Test-Dentro $Codigo $real)) {
        Stop-ConError "La carpeta del dev ($Datos) es, contiene o está adentro de los datos reales ($real): no se toca."
    }
}
foreach ($real in $envReales) {
    if ($real -and (Get-Completa $EnvArchivo).Equals((Get-Completa $real), [StringComparison]::OrdinalIgnoreCase)) {
        Stop-ConError "El .env del dev ($EnvArchivo) es el .env real de producción: no se toca."
    }
}
foreach ($real in $datosReales) {
    if (Test-Dentro $EnvArchivo $real) { Stop-ConError "El .env del dev no puede estar en los datos reales ($real)." }
}
if ((Test-Dentro $Codigo $cfg.produccion) -or (Test-Dentro $cfg.produccion $Codigo)) {
    Stop-ConError "El código del dev no puede ser la carpeta de producción ($($cfg.produccion))."
}

# ---------------------------------------------------------------- funciones
$archivoPid = Join-Path $Base 'dev.pid'
$marcaDev = Join-Path $Base 'dev-activo'  # sin ella el guardián apaga el 1112
$python = (Get-Command python -ErrorAction Stop).Source

function Invoke-GitEn([string]$carpeta, [string[]]$argumentos) {
    $ErrorActionPreference = 'Continue'
    $salida = & git -C $carpeta @argumentos 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "git $($argumentos -join ' '): $salida" }
    return $salida
}

function Invoke-DevDatos([string[]]$argumentos) {
    & $python (Join-Path $PSScriptRoot 'dev_datos.py') @argumentos
    if ($LASTEXITCODE -ne 0) { throw "dev_datos.py $($argumentos[0]) falló" }
}

# El proceso del dev: el del archivo de PID, vivo y con «--port 1112» en su línea de comandos
function Get-ProcesoDev {
    if (-not (Test-Path -LiteralPath $archivoPid)) { return $null }
    $id = [int](Get-Content -LiteralPath $archivoPid -Raw)
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
    if ($p -and $p.CommandLine -and $p.CommandLine -match "--port $PUERTO_DEV\b") { return $id }
    return $null
}

function Stop-Dev {
    $id = Get-ProcesoDev
    if ($id) {
        # Con su árbol: los Edge del Radar y de los portales y los AGY no quedan huérfanos
        & taskkill.exe /T /F /PID $id 2>&1 | Out-Null
        for ($i = 0; $i -lt 20 -and (Get-NetTCPConnection -LocalPort $PUERTO_DEV -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep 1 }
        Write-Host "Dev detenido (PID $id)."
    } else {
        Write-Host 'El dev no estaba corriendo.'
    }
    Remove-Item -LiteralPath $archivoPid -ErrorAction SilentlyContinue
}

function Update-Codigo {
    New-Item -ItemType Directory -Force -Path $Base | Out-Null
    Invoke-GitEn $Repo @('fetch', '--quiet', 'origin', 'develop') | Out-Null
    if (Test-Path -LiteralPath (Join-Path $Codigo '.git')) {
        Invoke-GitEn $Codigo @('checkout', '--quiet', '--detach', '--force', 'origin/develop') | Out-Null
    } else {
        Invoke-GitEn $Repo @('worktree', 'add', '--quiet', '--detach', $Codigo, 'origin/develop') | Out-Null
    }
    $commit = (Invoke-GitEn $Codigo @('log', '-1', '--format=%h %s') | Select-Object -First 1)
    Write-Host "Código del dev en develop: $commit"
}

function Update-Datos {
    Write-Host "Copiando los datos de $($cfg.datos) a $Datos (sin perfiles de Edge ni .env)..."
    Invoke-DevDatos @('copiar', '--origen', $cfg.datos, '--destino', $Datos)
    $sesiones = Get-ChildItem -LiteralPath $Datos -Filter 'linkedin_profile*' -ErrorAction SilentlyContinue
    if ($sesiones) { throw "Quedó un perfil de Edge en $Datos" }
    # Solo con datos recién copiados: Radar detenido y en pausa. Un reinicio conserva lo que prendió el usuario.
    Invoke-DevDatos @('preparar', '--datos', $Datos)
    Write-Host 'Datos del dev listos.'
}

function Start-Dev {
    if (Get-ProcesoDev) { Write-Host 'El dev ya está corriendo.'; return }
    if (Get-NetTCPConnection -LocalPort $PUERTO_DEV -State Listen -ErrorAction SilentlyContinue) {
        throw "El puerto $PUERTO_DEV está ocupado por otro proceso: no arranco el dev."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Codigo '.git'))) { Update-Codigo }
    if (-not (Test-Path -LiteralPath (Join-Path $Datos 'postulaciones.db'))) { Update-Datos }
    $argsEnv = @('env', '--real', $cfg.env, '--destino', $EnvArchivo)
    if (-not $MailReal) { $argsEnv += @('--prueba', $(if ($MailPrueba) { $MailPrueba } else { 'propio' })) }
    Invoke-DevDatos $argsEnv
    Set-Content -LiteralPath $marcaDev -Value (Get-Date -Format 's') -Encoding ASCII

    # Un lanzador .cmd con las variables y la redirección del log, iniciado oculto y sin heredar handles: con
    # -RedirectStandardOutput el portal heredaba la salida de quien corrió el script y esa llamada no terminaba.
    $log = Join-Path $Base 'dev.log'
    $lanzador = Join-Path $Base 'dev-lanzador.cmd'
    @(
        '@echo off',
        'rem Portal dev LinkedOut (puerto 1112). Lo genera tools\dev.ps1: no editar a mano.',
        "set LINKEDOUT_DATA_DIR=$Datos",
        "set LINKEDOUT_ENV_FILE=$EnvArchivo",
        # Un código del dev anterior al nombre LinkedOut lee solo las variables viejas
        "set BUSCATRABAJO_DATA_DIR=$Datos",  # compatibilidad: se borra tras el deploy
        "set BUSCATRABAJO_ENV_FILE=$EnvArchivo",  # compatibilidad: se borra tras el deploy
        'set PYTHONUTF8=1',
        "cd /d `"$Codigo`"",
        "`"$python`" -m uvicorn server:app --host 127.0.0.1 --port $PUERTO_DEV >> `"$log`" 2>&1"
    ) | Set-Content -LiteralPath $lanzador -Encoding ASCII
    Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', "`"$lanzador`"") -WindowStyle Hidden
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 1
        $escucha = Get-NetTCPConnection -LocalPort $PUERTO_DEV -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $escucha) { continue }
        Set-Content -LiteralPath $archivoPid -Value $escucha.OwningProcess -Encoding ASCII
        try {
            $estado = Invoke-RestMethod "http://127.0.0.1:$PUERTO_DEV/api/radar/status" -TimeoutSec 5
            Write-Host ("Dev LinkedOut en http://127.0.0.1:{0} (PID {1}): desired_running={2}, mail_prueba={3}" -f `
                $PUERTO_DEV, $escucha.OwningProcess, $estado.desired_running, $estado.mail_prueba)
            return
        } catch {}
    }
    Stop-Dev
    throw "El dev no respondió en el puerto $PUERTO_DEV. Mirá $log."
}

# ---------------------------------------------------------------- acciones
$estabaCorriendo = [bool](Get-ProcesoDev)
if ($Detener) { Stop-Dev; Remove-Item -LiteralPath $marcaDev -ErrorAction SilentlyContinue }
elseif (($ActualizarCodigo -or $RefrescarDatos) -and $estabaCorriendo) { Stop-Dev }
if ($ActualizarCodigo) { Update-Codigo }
if ($RefrescarDatos) { Update-Datos }
if ($Iniciar -or (-not $Detener -and $estabaCorriendo -and ($ActualizarCodigo -or $RefrescarDatos))) { Start-Dev }
exit 0
