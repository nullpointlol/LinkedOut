<#
.SYNOPSIS
  Vuelve producción al despliegue anterior en menos de un minuto.

.DESCRIPTION
  Sin parámetros, vuelve al commit que estaba antes del último despliegue (lo dice su tag deploy-*).
  -A <tag o commit> elige otro punto: un deploy-* del clon de producción o una versión linkedout-estable-*.
  -ConBase además restaura la base con el backup que hizo ese despliegue (lo guardado después de ese momento se
  pierde, por eso no es el comportamiento por defecto). Si requirements.txt cambió, no vuelve: primero se instalan.

.EXAMPLE
  powershell -File tools\rollback.ps1 -Produccion
  powershell -File tools\rollback.ps1 -Produccion -A linkedout-estable-20261114
  powershell -File tools\rollback.ps1 -Produccion -A deploy-20261120-1700 -ConBase
#>
param(
    [switch]$Produccion,   # obligatorio para tocar producción
    [string]$A,
    [switch]$ConBase
)
. (Join-Path $PSScriptRoot 'despliegue_comun.ps1')
Assert-Produccion $Produccion.IsPresent 'rollback.ps1'  # sin -Produccion no se toca nada
$cfg = Get-Config
Assert-NoCongelado $cfg  # producción congelada: no se toca (antes que cualquier otra cosa)

$actual = Get-CommitProduccion $cfg
$tags = @(Invoke-Git $cfg @('tag', '--list', 'deploy-*', '--sort=-creatordate'))
$ultimo = $tags | Where-Object { ((Invoke-Git $cfg @('rev-list', '-n', '1', $_)) | Select-Object -First 1).Trim() -eq $actual } | Select-Object -First 1
$mensaje = if ($ultimo) { (Invoke-Git $cfg @('tag', '-l', '--format=%(contents)', $ultimo)) -join "`n" } else { '' }

if (-not $A) {
    if ($mensaje -notmatch 'Anterior: ([0-9a-f]{40})') { throw "No sé a qué volver: pasá -A <tag o commit>." }
    $A = $Matches[1]
}
$destino = ((Invoke-Git $cfg @('rev-parse', "$A^{commit}")) | Select-Object -First 1).Trim()

if ($ConBase) {
    if ($mensaje -notmatch 'Backup: (.+\.db)') { throw "El despliegue actual no registró su backup: restaurá la base a mano." }
    $backup = $Matches[1].Trim()
    if (-not (Test-Path $backup)) { throw "No existe el backup $backup" }
}

if (-not (Test-RequisitosIguales $cfg $actual $destino)) {
    throw "requirements.txt cambió entre $actual y ${destino}: instalá antes los requisitos de esa versión y volvé a correr el rollback."
}
Assert-SinGeneracion $cfg
$seguridad = Backup-Base $cfg 'antes-de-rollback'
if ($ConBase) {
    Stop-Portal $cfg
    $db = Join-Path $cfg.datos 'postulaciones.db'
    Copy-Item $backup $db -Force
    Remove-Item "$db-wal", "$db-shm" -ErrorAction SilentlyContinue
}
if (Set-Produccion $cfg $destino) {
    Write-Registro $cfg ("ROLLBACK {0} -> {1}{2} (backup previo {3})" -f $actual, $destino, $(if ($ConBase) { " con base $backup" } else { '' }), $seguridad)
    exit 0
}
Write-Registro $cfg "ROLLBACK a $destino sin salud: revisar a mano (backup previo $seguridad)"
exit 1
