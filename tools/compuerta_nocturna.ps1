<#
.SYNOPSIS
  Compuerta nocturna de LinkedOut: la suite completa sobre develop y las ramas pendientes, de noche en esta PC.

.DESCRIPTION
  Sin parámetros (lo que corre la tarea LinkedOut-compuerta a las 03:00):
    1. lleva su worktree de herramienta (~\LinkedOut\compuerta\herramienta) a la punta de develop, si develop ya trae
       la compuerta nocturna;
    2. corre tools\compuerta_nocturna.py: develop y cada rama pendiente de merge, de a una, con el portal real vigilado;
    3. el informe queda en ~\LinkedOut\compuerta\AAAAMMDD.md y ~\LinkedOut\compuerta\ultimo.json.
  -Registrar           registra la tarea diaria (03:00, oculta con conhost --headless, tope de 3 h).
  -Registrar -Simular  muestra lo que registraría, sin registrar ni crear nada.

.EXAMPLE
  powershell -File tools\compuerta_nocturna.ps1 -Registrar
  powershell -File tools\compuerta_nocturna.ps1
#>
param(
    [switch]$Registrar,
    [switch]$Simular,
    [string]$NombreTarea = 'LinkedOut-compuerta',
    [string]$Hora = '03:00'
)
$ErrorActionPreference = 'Stop'

$carpeta = Join-Path (Join-Path $HOME 'LinkedOut') 'compuerta'
$herramienta = Join-Path $carpeta 'herramienta'
$script = Join-Path $herramienta 'tools\compuerta_nocturna.ps1'
$tope = New-TimeSpan -Hours 3

function Invoke-GitEn([string]$dir, [string[]]$argumentos) {
    $ErrorActionPreference = 'Continue'
    $salida = & git -C $dir @argumentos 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "git $($argumentos -join ' '): $salida" }
    return $salida
}

if ($Registrar) {
    $argumentos = "--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`""
    Write-Host "Tarea: $NombreTarea, diaria a las $Hora, tope de $($tope.TotalHours) h, solo con la sesión iniciada."
    Write-Host "Acción: conhost.exe $argumentos"
    Write-Host "Herramienta: $herramienta (worktree en develop; se actualiza sola cada noche)"
    if ($Simular) { Write-Host 'Simulado: no se registró nada.'; exit 0 }

    # El worktree de la herramienta: la rama de este script hasta que develop traiga la compuerta nocturna
    if (-not (Test-Path -LiteralPath (Join-Path $herramienta '.git'))) {
        New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
        $repo = Split-Path $PSScriptRoot -Parent
        Invoke-GitEn $repo @('worktree', 'add', '--quiet', '--detach', $herramienta, 'HEAD') | Out-Null
    }
    $accion = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument $argumentos -WorkingDirectory $herramienta
    $disparo = New-ScheduledTaskTrigger -Daily -At $Hora
    $ajustes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit $tope -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $NombreTarea -Action $accion -Trigger $disparo -Settings $ajustes `
        -Description 'LinkedOut: suite completa de noche sobre develop y las ramas pendientes (tools\compuerta_nocturna.ps1)' `
        -Force | Out-Null
    Write-Host "Registrada: $NombreTarea."
    exit 0
}

# ---------------------------------------------------------------- la corrida de la noche
New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
$registro = Join-Path $carpeta 'noche.log'
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') compuerta nocturna ===" | Add-Content -LiteralPath $registro -Encoding UTF8
$repo = Split-Path $PSScriptRoot -Parent
try {
    Invoke-GitEn $repo @('fetch', '--quiet', 'origin') | Out-Null
    # Se actualiza a develop solo si develop ya trae la compuerta nocturna (si no, sigue con la versión actual)
    & git -C $repo cat-file -e 'origin/develop:tools/compuerta_nocturna.py' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Invoke-GitEn $repo @('checkout', '--quiet', '--detach', '--force', 'origin/develop') | Out-Null
    }
    $python = (Get-Command python -ErrorAction Stop).Source
    & $python (Join-Path $repo 'tools\compuerta_nocturna.py') --repo $repo --salida $carpeta *>> $registro
    "=== fin $(Get-Date -Format 'HH:mm:ss'), código $LASTEXITCODE ===" | Add-Content -LiteralPath $registro -Encoding UTF8
} catch {
    "ERROR: $_" | Add-Content -LiteralPath $registro -Encoding UTF8
    exit 1
}
