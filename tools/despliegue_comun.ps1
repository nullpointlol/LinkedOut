# Funciones comunes de tools/deploy.ps1 y tools/rollback.ps1 (Windows PowerShell 5.1 o PowerShell 7).
#
# Configuración (obligatoria, sin valores por defecto que apunten a producción): <config>\despliegue.json
#   { "produccion": "C:\\...\\produccion",   carpeta de producción: un clon propio del repo (rama local «produccion»)
#     "datos": "C:\\...\\datos",             carpeta de datos del portal (LINKEDOUT_DATA_DIR)
#     "tarea": "...",                        tarea programada que lanza el portal desde la carpeta de producción
#     "puerto": 1111,
#     "env": "C:\\...\\produccion.env" }     .env real (por defecto, produccion.env junto a la carpeta de datos)
# <config> es $HOME\LinkedOut (desde el corte del 14/11), u otra carpeta con LINKEDOUT_CONFIG_DIR (tests).
#
# deploy.ps1 y rollback.ps1 tocan producción solo con -Produccion explícito (13/11): sin la bandera no hacen nada
# (Assert-Produccion). Y mientras exista CONGELADO en la carpeta de config, o el de antes del corte
# ($HOME\$UBICACION_VIEJA\CONGELADO), tampoco (Assert-NoCongelado). Se despliegan solo versiones linkedout-estable-*
# de origin/develop (Resolve-Version).

$ErrorActionPreference = 'Stop'
# Carpeta de producción y config de antes del corte (14/11), con el nombre viejo del proyecto: sigue en la PC
$UBICACION_VIEJA = '.buscatrabajo4'

function Assert-Produccion([bool]$explicito, [string]$script) {
    if (-not $explicito) {
        Write-Host "$script toca producción solo con -Produccion explícito: no se hizo nada."
        Write-Host "Uso (solo el coordinador, con el OK del creador): powershell -File tools\$script -Produccion ..."
        exit 2
    }
}

function Get-Config {
    $base = if ($env:LINKEDOUT_CONFIG_DIR) { $env:LINKEDOUT_CONFIG_DIR } else { Join-Path $HOME 'LinkedOut' }
    $archivo = Join-Path $base 'despliegue.json'
    $cfg = @{
        config     = $base
        congelado  = Join-Path $base 'CONGELADO'
        # El candado de antes del corte (14/11) sigue valiendo: solo se saca con el OK del creador
        congelados = @((Join-Path $base 'CONGELADO'), (Join-Path (Join-Path $HOME $UBICACION_VIEJA) 'CONGELADO'))
        registro   = Join-Path $base 'despliegues.log'
    }
    if (-not (Test-Path $archivo)) { throw "Falta la configuración: no existe $archivo (despliegue.json)" }
    $json = Get-Content $archivo -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $json.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
    foreach ($clave in 'produccion', 'datos', 'tarea', 'puerto') {
        if (-not $cfg[$clave]) { throw "Falta «$clave» en $archivo (despliegue.json): no hay valores por defecto." }
    }
    if (-not $cfg.env) { $cfg.env = Join-Path (Split-Path $cfg.datos -Parent) 'produccion.env' }
    return $cfg
}

# Producción congelada: con el archivo CONGELADO en la carpeta de config no se despliega ni se hace rollback.
function Assert-NoCongelado($cfg) {
    $candados = @($cfg.congelados | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
    if ($candados.Count -eq 0) { return }
    foreach ($candado in $candados) {
        $motivo = Get-Content -LiteralPath $candado -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        Write-Host "Producción congelada: existe $candado."
        if ($motivo) { Write-Host ('Motivo: ' + $motivo.Trim()) }
    }
    Write-Host 'No se despliega ni se hace rollback: el portal real no se toca mientras esté congelado.'
    foreach ($candado in $candados) {
        Write-Host "Para liberarlo (solo con el OK del creador): Remove-Item -LiteralPath '$candado'"
    }
    exit 3
}

# Versión a desplegar: solo un tag linkedout-estable-* (traído de origin) cuyo commit está en origin/develop.
function Resolve-Version($cfg, [string]$version) {
    if ($version -notmatch '^linkedout-estable-[0-9A-Za-z][0-9A-Za-z._-]*$') {
        throw "Solo se despliegan versiones linkedout-estable-* (pediste «$version»)."
    }
    try { Invoke-Git $cfg @('fetch', '--quiet', '--tags', 'origin') | Out-Null } catch { Write-Host 'Sin conexión con GitHub: sigo con lo local.' }
    $ErrorActionPreference = 'Continue'
    $sha = & git -C $cfg.produccion rev-parse --verify --quiet "refs/tags/$version^{commit}" 2>$null | Select-Object -First 1
    if (-not $sha) { throw "La versión $version no existe (tampoco en origin)." }
    $sha = "$sha".Trim()
    & git -C $cfg.produccion merge-base --is-ancestor $sha origin/develop 2>$null
    if ($LASTEXITCODE -ne 0) { throw "La versión $version ($sha) no está en origin/develop: no se despliega." }
    return $sha
}

# $true si requirements.txt es igual en los dos commits (si cambió, hay que instalar antes de reiniciar el portal).
function Test-RequisitosIguales($cfg, [string]$desde, [string]$hasta) {
    $ErrorActionPreference = 'Continue'
    & git -C $cfg.produccion diff --quiet $desde $hasta -- requirements.txt 2>$null
    if ($LASTEXITCODE -eq 0) { return $true }
    # Cambió: alcanza con que lo instalado cumpla los pines de la versión nueva (17/11)
    $faltan = & git -C $cfg.produccion show "${hasta}:requirements.txt" 2>$null |
        & python (Join-Path $PSScriptRoot 'requisitos_instalados.py') -
    $ok = ($LASTEXITCODE -eq 0)
    if (-not $ok) { $faltan | ForEach-Object { Write-Host "Requisito sin instalar: $_" } }
    return $ok
}

function Write-Registro($cfg, [string]$texto) {
    $linea = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $texto
    Add-Content -Path $cfg.registro -Value $linea -Encoding UTF8
    Write-Host $linea
}

function Invoke-Git($cfg, [string[]]$argumentos) {
    # En PowerShell 5.1, la salida de error de git con «Stop» se vuelve excepción aunque git termine bien
    $ErrorActionPreference = 'Continue'
    $salida = & git -C $cfg.produccion @argumentos 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "git $($argumentos -join ' '): $salida" }
    return $salida
}

function Get-Estado($cfg) {
    try { return Invoke-RestMethod "http://127.0.0.1:$($cfg.puerto)/api/radar/status" -TimeoutSec 15 } catch { return $null }
}

function Assert-SinGeneracion($cfg) {
    $estado = Get-Estado $cfg
    if ($estado -and $estado.cola -and [int]$estado.cola.generando -gt 0) {
        throw "Hay un CV generándose: el despliegue cortaría su trabajo. Probá en unos minutos."
    }
}

function Backup-Base($cfg, [string]$motivo) {
    $db = Join-Path $cfg.datos 'postulaciones.db'
    $carpeta = Join-Path $cfg.datos 'backups'
    New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
    $destino = Join-Path $carpeta ('postulaciones-{0}-{1}.db' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $motivo)
    # Copia consistente aunque el portal esté escribiendo (API de backup de SQLite)
    $codigo = "import sqlite3,sys; o=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); o.backup(d); d.close(); o.close()"
    & python -c $codigo $db $destino
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $destino)) { throw "No se pudo hacer el backup de la base" }
    return $destino
}

function Stop-Portal($cfg) {
    try { Stop-ScheduledTask -TaskName $cfg.tarea -ErrorAction SilentlyContinue } catch {}
    $proc = (Get-NetTCPConnection -LocalPort $cfg.puerto -State Listen -ErrorAction SilentlyContinue).OwningProcess
    # Con su árbol: Edge y AGY de producción no quedan huérfanos (17/11)
    if ($proc) { & taskkill.exe /T /F /PID $proc 2>&1 | Out-Null }
    for ($i = 0; $i -lt 20 -and (Get-NetTCPConnection -LocalPort $cfg.puerto -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep 1 }
}

function Start-Portal($cfg) {
    Start-ScheduledTask -TaskName $cfg.tarea
}

# Chequeo de salud: la página responde 200, el estado del Radar responde y el proceso es nuevo.
function Test-Salud($cfg, $pidAnterior) {
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep 2
        try {
            $pagina = Invoke-WebRequest "http://127.0.0.1:$($cfg.puerto)/" -UseBasicParsing -TimeoutSec 10
            $estado = Get-Estado $cfg
            $proc = (Get-NetTCPConnection -LocalPort $cfg.puerto -State Listen -ErrorAction SilentlyContinue).OwningProcess
            if ($pagina.StatusCode -eq 200 -and $estado -and $proc -and $proc -ne $pidAnterior) {
                try { (Get-Process -Id $proc).PriorityClass = 'AboveNormal' } catch {}
                return $true
            }
        } catch {}
    }
    return $false
}

# Copia las cookies de Edge (las sesiones de LinkedIn) con el portal detenido (creador, 12/11). Con el Radar
# andando, Edge las abre en exclusiva y tools\backup_local.ps1 no las puede leer: usa la copia más nueva de acá.
# Destino: <datos>\backups\sesiones\<perfil>-Cookies-AAAAMMDD-HHMMSS, copia SQLite consistente; quedan las 3 últimas
# por perfil. Nunca corta el deploy: si algo falla, lo anota en el registro y sigue.
function Save-SesionesEdge($cfg, [int]$esperaS = 15, [int]$conservar = 3) {
    $ErrorActionPreference = 'Continue'
    try {
        $carpeta = Join-Path $cfg.datos 'backups\sesiones'
        $marca = Get-Date -Format 'yyyyMMdd-HHmmss'
        $pares = @()
        foreach ($perfil in @(Get-ChildItem -LiteralPath $cfg.datos -Directory -Filter 'linkedin_profile*')) {
            $cookies = Join-Path $perfil.FullName 'Default\Network\Cookies'
            if (Test-Path -LiteralPath $cookies) {
                $pares += [pscustomobject]@{ origen = $cookies; destino = (Join-Path $carpeta "$($perfil.Name)-Cookies-$marca"); espera_s = 5; perfil = $perfil.Name }
            }
        }
        if ($pares.Count -eq 0) { return }
        # Edge se cierra unos segundos después del portal
        $limite = (Get-Date).AddSeconds($esperaS)
        while ((Get-Date) -lt $limite) {
            $tomados = @($pares | Where-Object {
                try { $f = [IO.File]::Open($_.origen, 'Open', 'Read', 'ReadWrite'); $f.Close(); $false } catch { $true }
            })
            if ($tomados.Count -eq 0) { break }
            Start-Sleep -Milliseconds 500
        }
        New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
        $entrada = Join-Path $carpeta "_pares-$marca.json"
        $salida = Join-Path $carpeta "_copias-$marca.json"
        [IO.File]::WriteAllText($entrada, (ConvertTo-Json -InputObject @($pares | Select-Object origen, destino, espera_s) -Depth 3), (New-Object Text.UTF8Encoding $false))
        & python -I (Join-Path $PSScriptRoot 'backup_bases.py') copiar $entrada $salida 2>&1 | Out-Null
        $copias = @(Get-Content -LiteralPath $salida -Raw -Encoding UTF8 | ConvertFrom-Json | ForEach-Object { $_ })
        Remove-Item -LiteralPath $entrada, $salida -Force -ErrorAction SilentlyContinue
        foreach ($p in $pares) {
            $c = $copias | Where-Object { $_.origen -eq $p.origen } | Select-Object -First 1
            if ($c -and $c.estado -eq 'copiada' -and $c.integridad -eq 'ok') {
                Write-Registro $cfg "Sesión de Edge copiada: $($p.perfil) -> $(Split-Path -Leaf $p.destino)"
                $viejas = @(Get-ChildItem -LiteralPath $carpeta -File -Filter "$($p.perfil)-Cookies-*" | Sort-Object Name -Descending | Select-Object -Skip $conservar)
                foreach ($v in $viejas) { Remove-Item -LiteralPath $v.FullName -Force -ErrorAction SilentlyContinue }
            } else {
                $motivo = 'sin resultado'; if ($c) { $motivo = "$($c.estado) $($c.motivo) $($c.integridad)".Trim() }
                Write-Registro $cfg "Sesión de Edge NO copiada: $($p.perfil) ($motivo)"
            }
        }
    } catch {
        try { Write-Registro $cfg "Sesión de Edge NO copiada: $($_.Exception.Message)" } catch {}
    }
}

# Pone la carpeta de producción en `commit` y reinicia el portal. Devuelve $true si quedó sano.
function Set-Produccion($cfg, [string]$commit) {
    $pidAnterior = (Get-NetTCPConnection -LocalPort $cfg.puerto -State Listen -ErrorAction SilentlyContinue).OwningProcess
    Stop-Portal $cfg
    Save-SesionesEdge $cfg
    Invoke-Git $cfg @('reset', '--hard', '--quiet', $commit) | Out-Null
    Start-Portal $cfg
    return (Test-Salud $cfg $pidAnterior)
}

function Get-CommitProduccion($cfg) {
    return ((Invoke-Git $cfg @('rev-parse', 'HEAD')) | Select-Object -First 1).Trim()
}
