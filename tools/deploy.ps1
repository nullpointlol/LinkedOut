<#
.SYNOPSIS
  Único camino a producción: despliega una versión linkedout-estable-* de develop solo si pasó la compuerta.

.DESCRIPTION
  Producción es un clon propio del repo (despliegue.json: «produccion») que corre la tarea «LinkedOut-portal».
  1. La versión es un tag linkedout-estable-* (se trae de origin) cuyo commit está en origin/develop.
  2. Su CI en GitHub tiene que estar en verde. Sin CI (sin internet o sin gh), corre la compuerta local
     (tools/verificar.py) en una copia aparte del commit; si falla, no despliega.
  3. Si cambió requirements.txt respecto de lo que corre, no despliega: primero se instalan los requisitos.
  4. No despliega con un CV generándose.
  5. Backup de la base, el clon pasa al commit y el portal se reinicia.
  6. Chequeo de salud (200, estado del Radar, proceso nuevo). Si falla, vuelve solo al commit anterior.
  Cada despliegue queda con un tag local deploy-AAAAMMDD-HHMMSS en el clon (versión, commit anterior y backup,
  que usa rollback.ps1) y una línea en despliegues.log.

.EXAMPLE
  # La versión se crea antes, sobre develop con el CI en verde:
  #   git tag -a linkedout-estable-AAAAMMDD <commit> -m "..." ; git push origin refs/tags/linkedout-estable-AAAAMMDD
  powershell -File tools\deploy.ps1 -Produccion -Version linkedout-estable-20261114
#>
param(
    [switch]$Produccion,   # obligatorio para tocar producción (13/11)
    [string]$Version,      # tag linkedout-estable-* (14/11)
    [switch]$SinCI   # salteá la consulta a GitHub y usá solo la compuerta local
)
. (Join-Path $PSScriptRoot 'despliegue_comun.ps1')
Assert-Produccion $Produccion.IsPresent 'deploy.ps1'  # sin -Produccion no se toca nada
$cfg = Get-Config
Assert-NoCongelado $cfg  # producción congelada: no se toca (antes que cualquier otra cosa)
if (-not $Version) {
    Write-Host 'Falta la versión: powershell -File tools\deploy.ps1 -Produccion -Version linkedout-estable-AAAAMMDD'
    exit 2
}

$sha = Resolve-Version $cfg $Version

# Compuerta: CI de GitHub o, si no hay, la local
$ciVerde = $false
if (-not $SinCI -and (Get-Command gh -ErrorAction SilentlyContinue)) {
    $repo = ((Invoke-Git $cfg @('remote', 'get-url', 'origin')) | Select-Object -First 1) -replace '^https://github.com/', '' -replace '\.git$', ''
    $corridas = & gh run list -R $repo --commit $sha --workflow CI --json status,conclusion 2>$null | ConvertFrom-Json
    if ($corridas | Where-Object { $_.status -eq 'completed' -and $_.conclusion -eq 'success' }) { $ciVerde = $true }
    elseif ($corridas | Where-Object { $_.status -eq 'completed' }) { throw "El CI de $sha falló: no se despliega." }
    elseif ($corridas) { throw "El CI de $sha todavía corre: esperá a que termine." }
}
if (-not $ciVerde) {
    Write-Host "Sin CI de GitHub para ${sha}: corro la compuerta local en una copia aparte."
    $copia = Join-Path $env:TEMP "linkedout-verificar-$($sha.Substring(0, 8))"
    if (Test-Path $copia) { & git -C $cfg.produccion worktree remove --force $copia 2>$null }
    Invoke-Git $cfg @('worktree', 'add', '--detach', '--quiet', $copia, $sha) | Out-Null
    try {
        Push-Location $copia
        & python tools/verificar.py
        $resultado = $LASTEXITCODE
    } finally {
        Pop-Location
        & git -C $cfg.produccion worktree remove --force $copia 2>$null
    }
    if ($resultado -ne 0) { throw "La compuerta local falló para ${sha}: no se despliega." }
}

$anterior = Get-CommitProduccion $cfg
if ($anterior -eq $sha) { Write-Host "Producción ya está en $Version ($sha)."; exit 0 }
if (-not (Test-RequisitosIguales $cfg $anterior $sha)) {
    throw "requirements.txt cambió entre $anterior y ${Version}: instalá antes los requisitos (python -m pip install -r requirements.txt de esa versión) y volvé a correr el deploy."
}
Assert-SinGeneracion $cfg
$backup = Backup-Base $cfg 'antes-de-deploy'
$tag = 'deploy-' + (Get-Date -Format 'yyyyMMdd-HHmmss')

if (Set-Produccion $cfg $sha) {
    Invoke-Git $cfg @('tag', '-a', $tag, $sha, '-m', "Desplegado $Version. Anterior: $anterior. Backup: $backup") | Out-Null
    Write-Registro $cfg "OK $tag $Version $sha (antes $anterior) backup $backup"
    exit 0
}

Write-Registro $cfg "FALLÓ la salud de ${sha}: vuelvo a $anterior"
if (Set-Produccion $cfg $anterior) {
    Write-Registro $cfg "ROLLBACK automático a ${anterior}: portal sano"
} else {
    Write-Registro $cfg "ROLLBACK a $anterior TAMBIÉN sin salud: revisar a mano (backup $backup)"
}
exit 1
