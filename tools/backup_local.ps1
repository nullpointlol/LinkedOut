# Backup completo del portal local en un 7z: código con su .git, producción, datos, sesiones de
# LinkedIn (perfiles de Edge), .env, %USERPROFILE%\LinkedOut (producción desde el 14/11), la carpeta de antes del
# corte (UBICACION_VIEJA) y la tarea programada. Restauración: tools\restaurar_local.md.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\backup_local.ps1
#
# Deja <repo>\backups_locales\linkedout-local-AAAAMMDD-HHMMSS.7z y, al lado, el informe .txt con la verificación.
# No detiene el portal ni el Radar y no modifica ningún dato: las bases SQLite se copian abiertas de solo lectura con la
# API de backup (tools\backup_bases.py) y el resto lo lee 7-Zip con -ssw (archivos abiertos por otro proceso).
# Los archivos que igual no se pudieron leer quedan anotados en el informe. Ningún contenido de archivo va a la salida.
# Termina con 0 si todo está bien, 1 si el 7z quedó con advertencias (ver el informe) y 2 si falló.

param(
    [string]$Repo = '',
    # Carpeta de configuración local (la misma que usan deploy.ps1 y rollback.ps1). Desde el corte del 14/11 es
    # ~/LinkedOut, con producción adentro (clon, datos, produccion.env, lanzador y vigilante).
    [string]$Base = $(if ($env:LINKEDOUT_CONFIG_DIR) { $env:LINKEDOUT_CONFIG_DIR } else { Join-Path $HOME 'LinkedOut' }),
    # La de antes del corte (worktree y logs viejos, CONGELADO): también va al 7z si existe. Es la UBICACION_VIEJA,
    # con el nombre viejo del proyecto (param no admite una variable previa: el nombre va acá, una sola vez)
    [string]$BaseAnterior = $(if ($env:LINKEDOUT_CONFIG_DIR) { '' } else { Join-Path $HOME '.buscatrabajo4' }),  # UBICACION_VIEJA
    [string]$Datos = '',
    [string]$Destino = '',
    [string]$Tarea = 'LinkedOut-portal',
    [string]$SieteZip = 'C:\Program Files\7-Zip\7z.exe',
    [string]$Python = 'python',
    # Minutos que se espera a que Edge suelte los archivos que importan (las cookies de la sesión); 0 = no esperar
    [int]$EsperaBloqueadosMin = 10
)

$ErrorActionPreference = 'Stop'

# Carpetas que no se recorren al buscar bases SQLite (se incluyen igual en el 7z, en crudo).
$NO_RECORRER = @('.git', 'node_modules', '__pycache__', '.venv', 'venv', '.pytest_cache', '.ruff_cache', 'backups_locales')
# Dentro de un perfil de Edge solo la base de cookies (las sesiones) se intenta copiar consistente.
$BASES_DE_PERFIL = @('Cookies')
# Cachés de Edge: si están bloqueadas no importan (Edge las rehace). Sessions\ son las pestañas abiertas.
# Carpetas temporales de pytest en los worktrees que quedan sin permiso de lectura: basura de tests, no hacen falta.
$TEMPORAL_DE_TESTS = '\\.claude\\worktrees\\.*\\(tmp-[^\\]*|test-tmp|pytest-[^\\]*)\\'
$CACHE_DE_EDGE = '\\(Cache|Code Cache|GPUCache|ShaderCache|GrShaderCache|GraphiteDawnCache|DawnGraphiteCache|DawnWebGPUCache|Sessions|Sessions_Encrypted)\\'

function Get-Completa([string]$ruta) { return [IO.Path]::GetFullPath($ruta).TrimEnd('\') }

function Invoke-Externo([string]$exe, [string[]]$argumentos) {
    # En PowerShell 5.1, con «Stop», cualquier línea en stderr de un ejecutable se vuelve excepción.
    $ErrorActionPreference = 'Continue'
    $salida = & $exe @argumentos 2>&1 | ForEach-Object { "$_" }
    return @{ Codigo = $LASTEXITCODE; Salida = @($salida) }
}

function Invoke-7z([string[]]$argumentos) {
    # -bse1: los avisos van a la salida normal (así no hay stderr que PowerShell 5.1 convierta en error).
    return Invoke-Externo $SieteZip ($argumentos + @('-bso1', '-bse1', '-bsp0', '-bb0', '-scsUTF-8'))
}

function Write-ListaUtf8([string]$ruta, [string[]]$lineas) {
    [IO.File]::WriteAllLines($ruta, $lineas, (New-Object Text.UTF8Encoding $false))
}

$informe = New-Object System.Collections.Generic.List[string]
function Write-Paso([string]$texto) {
    $linea = '{0} {1}' -f (Get-Date -Format 'HH:mm:ss'), $texto
    $informe.Add($linea)
    Write-Host $linea
}

function Test-SeLee([string]$ruta) {
    try { $f = [IO.File]::Open($ruta, 'Open', 'Read', 'ReadWrite'); $f.Close(); return $true } catch { return $false }
}

# Copia más nueva de las cookies de un perfil que dejó el deploy con el portal detenido (Save-SesionesEdge en
# tools\despliegue_comun.ps1): <datos>\backups\sesiones\<perfil>-Cookies-AAAAMMDD-HHMMSS
function Get-CopiaDeSesion([string]$ruta) {
    if (-not $ruta.StartsWith($Datos + '\', [StringComparison]::OrdinalIgnoreCase)) { return $null }
    $perfil = $ruta.Substring($Datos.Length + 1).Split('\')[0]
    $carpeta = Join-Path $Datos 'backups\sesiones'
    if (-not (Test-Path -LiteralPath $carpeta)) { return $null }
    return (Get-ChildItem -LiteralPath $carpeta -File -Filter "$perfil-Cookies-*" | Sort-Object Name -Descending | Select-Object -First 1)
}

function Format-MB([double]$bytes) { return ('{0:N1} MB' -f ($bytes / 1MB)) }

# ---------------------------------------------------------------- rutas
if (-not $Repo) {
    $comun = Invoke-Externo 'git' @('-C', $PSScriptRoot, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if ($comun.Codigo -ne 0) { throw "No se encontró el repo desde $PSScriptRoot" }
    $Repo = Split-Path -Parent ($comun.Salida[0].Trim())   # el checkout principal, aunque se corra desde un worktree
}
$Repo = Get-Completa $Repo
$Base = Get-Completa $Base
if (-not $Datos) { $Datos = $env:LINKEDOUT_DATA_DIR }
if (-not $Datos) { $Datos = $env:BUSCATRABAJO_DATA_DIR }  # compatibilidad: se borra tras el deploy
$configDespliegue = Join-Path $Base 'despliegue.json'
if (-not $Datos -and (Test-Path -LiteralPath $configDespliegue)) {
    $Datos = (Get-Content -LiteralPath $configDespliegue -Raw -Encoding UTF8 | ConvertFrom-Json).datos
}
if (-not $Datos) { $Datos = Join-Path $Repo 'data' }
$Datos = Get-Completa $Datos
if (-not $Destino) { $Destino = Join-Path $Repo 'backups_locales' }
$Destino = Get-Completa $Destino

foreach ($necesario in @($Repo, $Datos, $SieteZip)) {
    if (-not (Test-Path -LiteralPath $necesario)) { throw "No existe: $necesario" }
}

# Raíces del 7z: cada una se guarda con su nombre de carpeta (el repo, LinkedOut y la carpeta de antes del corte).
$raices = New-Object System.Collections.Generic.List[string]
$raices.Add($Repo)
if (Test-Path -LiteralPath $Base) { $raices.Add($Base) }
if ($BaseAnterior -and (Test-Path -LiteralPath $BaseAnterior)) { $raices.Add((Get-Completa $BaseAnterior)) }
function Get-RutaEnArchivo([string]$ruta) {
    $ruta = Get-Completa $ruta
    foreach ($r in $raices) {
        if ($ruta -eq $r) { return (Split-Path -Leaf $r) }
        if ($ruta.StartsWith($r + '\', [StringComparison]::OrdinalIgnoreCase)) {
            return (Split-Path -Leaf $r) + $ruta.Substring($r.Length)
        }
    }
    return $null
}
if (-not (Get-RutaEnArchivo $Datos)) { $raices.Add($Datos) }
$hojas = @($raices | ForEach-Object { (Split-Path -Leaf $_).ToLowerInvariant() })
if (@($hojas | Select-Object -Unique).Count -ne $hojas.Count) { throw "Dos carpetas de origen con el mismo nombre: $($raices -join ', ')" }

$marca = Get-Date -Format 'yyyyMMdd-HHmmss'
$nombre = "linkedout-local-$marca"
New-Item -ItemType Directory -Force -Path $Destino | Out-Null
$archivo = Join-Path $Destino "$nombre.7z"
$parcial = Join-Path $Destino "$nombre.parcial.7z"
$archivoInforme = Join-Path $Destino "$nombre.txt"
$prep = Join-Path $Destino "_preparando-$marca"
$verif = Join-Path $Destino "_verificando-$marca"
$meta = Join-Path $prep '_backup'
New-Item -ItemType Directory -Force -Path $meta | Out-Null

$advertencias = New-Object System.Collections.Generic.List[string]
$codigoFinal = 2
try {
    Write-Paso "Backup $nombre"
    foreach ($r in $raices) { Write-Paso "Origen: $r" }
    Write-Paso "Datos: $Datos"

    # ------------------------------------------------------------ 1. bases SQLite, copias consistentes
    $candidatas = New-Object System.Collections.Generic.List[object]
    $pila = New-Object System.Collections.Stack
    foreach ($r in $raices) { $pila.Push([pscustomobject]@{ Dir = $r; Perfil = $false }) }
    while ($pila.Count -gt 0) {
        $item = $pila.Pop()
        $dir = $item.Dir
        if ($NO_RECORRER -contains (Split-Path -Leaf $dir)) { continue }
        if ($dir -eq $Destino) { continue }
        $perfil = $item.Perfil -or (Test-Path -LiteralPath (Join-Path $dir 'Local State'))
        try { $archivos = [IO.Directory]::GetFiles($dir) } catch { continue }
        foreach ($a in $archivos) {
            $n = [IO.Path]::GetFileName($a)
            if ($perfil) {
                if ($BASES_DE_PERFIL -contains $n) { $candidatas.Add([pscustomobject]@{ Ruta = $a; Edge = $true }) }
            } elseif ($n -match '\.(db|sqlite|sqlite3)$') {
                $candidatas.Add([pscustomobject]@{ Ruta = $a; Edge = $false })
            }
        }
        try { $subs = [IO.Directory]::GetDirectories($dir) } catch { continue }
        foreach ($s in $subs) {
            if (([IO.File]::GetAttributes($s) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
            $pila.Push([pscustomobject]@{ Dir = $s; Perfil = $perfil })
        }
    }
    $pares = @($candidatas | ForEach-Object {
        $espera = 60; if ($_.Edge) { $espera = 5 }
        [pscustomobject]@{ origen = $_.Ruta; destino = (Join-Path $prep (Get-RutaEnArchivo $_.Ruta)); espera_s = $espera }
    })
    $archivoPares = Join-Path $Destino "_pares-$marca.json"
    $archivoCopias = Join-Path $Destino "_copias-$marca.json"
    Write-ListaUtf8 $archivoPares @(ConvertTo-Json -InputObject $pares -Depth 3)
    Write-Paso "Bases SQLite encontradas: $($pares.Count). Copiando de solo lectura con la API de backup..."
    $r = Invoke-Externo $Python @('-I', (Join-Path $PSScriptRoot 'backup_bases.py'), 'copiar', $archivoPares, $archivoCopias)
    if ($r.Codigo -ne 0) { throw "backup_bases.py terminó con $($r.Codigo): $($r.Salida -join ' | ')" }
    # PowerShell 5.1 devuelve el arreglo JSON como un solo objeto: el ForEach-Object lo enumera
    $copias = @(Get-Content -LiteralPath $archivoCopias -Raw -Encoding UTF8 | ConvertFrom-Json | ForEach-Object { $_ })
    Remove-Item -LiteralPath $archivoPares, $archivoCopias -Force

    $excluir = New-Object System.Collections.Generic.List[string]   # bases ya copiadas: no van en crudo
    $copiadas = @()
    $lineasBases = New-Object System.Collections.Generic.List[string]
    # Cookies de Edge que no se pudieron copiar ahora: nunca van en crudo (si Edge vuelve a tomarlas mientras 7z
    # las lee, 7z aborta con «locked a portion of the file»); se esperan y se copian con SQLite en el paso 3.
    $pendientesEdge = New-Object System.Collections.Generic.List[object]
    foreach ($c in $copias) {
        $enArchivo = Get-RutaEnArchivo $c.origen
        $esEdge = [bool]($candidatas | Where-Object { $_.Ruta -eq $c.origen -and $_.Edge })
        if ($esEdge) { foreach ($sufijo in @('', '-wal', '-shm', '-journal')) { $excluir.Add("$enArchivo$sufijo") } }
        if ($esEdge -and $c.estado -ne 'copiada') {
            $lineasBases.Add("pendiente $enArchivo  (Edge la tiene abierta: se espera y se copia aparte)")
            $pendientesEdge.Add([pscustomobject]@{ Ruta = $c.origen; Motivo = 'Edge la tiene abierta'; Cache = $false; Tipo = 'importa' })
            continue
        }
        if ($c.estado -eq 'copiada') {
            if (-not $esEdge) { foreach ($sufijo in @('', '-wal', '-shm', '-journal')) { $excluir.Add("$enArchivo$sufijo") } }
            $copiadas += [pscustomobject]@{ EnArchivo = $enArchivo; Bytes = [int64]$c.bytes; Integridad = $c.integridad }
            $lineasBases.Add("copiada  $enArchivo  $(Format-MB $c.bytes)  integrity_check=$($c.integridad)")
            if ($c.integridad -ne 'ok') { $advertencias.Add("integrity_check de la copia de $enArchivo = $($c.integridad)") }
        } elseif ($c.estado -eq 'error') {
            $lineasBases.Add("en crudo $enArchivo  (no se pudo abrir de solo lectura: $($c.motivo))")
            if (-not $esEdge) { $advertencias.Add("$enArchivo va en crudo, sin copia consistente: $($c.motivo)") }
        }
    }
    foreach ($l in $lineasBases) { Write-Paso "  $l" }
    $noSqlite = @($copias | Where-Object { $_.estado -eq 'no_sqlite' }).Count
    if ($noSqlite) { Write-Paso "  $noSqlite archivo(s) .db que no son SQLite van en crudo" }

    # ------------------------------------------------------------ 2. tarea programada, entorno y git
    # Export-ScheduledTask da el mismo XML que «schtasks /query /xml», sin pasar por la página de códigos de la consola
    $xmlTarea = $null
    try { $xmlTarea = Export-ScheduledTask -TaskName $Tarea -ErrorAction Stop } catch {
        $t = Invoke-Externo 'schtasks' @('/query', '/tn', $Tarea, '/xml')
        if ($t.Codigo -eq 0) { $xmlTarea = $t.Salida -join "`r`n" }
    }
    if ($xmlTarea) {
        # UTF-16 con BOM, como lo declara el XML: así lo acepta «schtasks /create /xml»
        [IO.File]::WriteAllText((Join-Path $meta "tarea-$Tarea.xml"), $xmlTarea, [Text.Encoding]::Unicode)
        Write-Paso "Tarea programada exportada: _backup\tarea-$Tarea.xml"
    } else {
        $advertencias.Add("No se pudo exportar la tarea programada «$Tarea»")
    }
    $py = Invoke-Externo $Python @('-c', 'import sys; print(sys.version); print(sys.executable)')
    $pip = Invoke-Externo $Python @('-m', 'pip', 'freeze', '--disable-pip-version-check')
    # Una URL con usuario o token (git+https://usuario:token@...) no se guarda
    $lineasPip = @($pip.Salida | ForEach-Object { $_ -replace '://[^/@\s]+@', '://***@' })
    Write-ListaUtf8 (Join-Path $meta 'python.txt') @($py.Salida)
    Write-ListaUtf8 (Join-Path $meta 'requirements-freeze.txt') $lineasPip   # python -m pip install -r
    $lineasGit = New-Object System.Collections.Generic.List[string]
    foreach ($consulta in @(@('branch', '-a', '-vv'), @('worktree', 'list'), @('tag'), @('stash', 'list'), @('remote'))) {
        $g = Invoke-Externo 'git' (@('-C', $Repo) + $consulta)
        $lineasGit.Add("# git $($consulta -join ' ')")
        foreach ($l in $g.Salida) { $lineasGit.Add($l) }
        $lineasGit.Add('')
    }
    Write-ListaUtf8 (Join-Path $meta 'git.txt') $lineasGit
    $ramasOrigen = @((Invoke-Externo 'git' @('-C', $Repo, 'for-each-ref', '--format=%(refname)', 'refs/heads', 'refs/tags')).Salida).Count
    $origenTxt = @("Backup $nombre", "Repo=$Repo", "Base=$Base", "Datos=$Datos", "Tarea=$Tarea", "Ramas y tags locales=$ramasOrigen", '')
    $origenTxt += @($raices | ForEach-Object { "Raíz: $_ -> $(Split-Path -Leaf $_)\" })
    Write-ListaUtf8 (Join-Path $meta 'origen.txt') $origenTxt
    Write-ListaUtf8 (Join-Path $meta 'bases.txt') $lineasBases
    $guia = Join-Path $PSScriptRoot 'restaurar_local.md'
    if (Test-Path -LiteralPath $guia) { Copy-Item -LiteralPath $guia -Destination $meta }

    # ------------------------------------------------------------ 3. 7z: todo en crudo menos las bases ya copiadas
    $listaExcluir = Join-Path $Destino "_excluir-$marca.txt"
    Write-ListaUtf8 $listaExcluir @($excluir)
    Write-Paso "Comprimiendo (puede tardar varios minutos)..."
    $argumentos = @('a', '-t7z', $parcial) + @($raices) + @('-ssw', '-xr!backups_locales', "-x@$listaExcluir")   # -xr: este backup y los anteriores
    # Un archivo que otro proceso bloquea a mitad de la lectura aborta 7z (código 2): se reintenta una vez.
    for ($intento = 1; $intento -le 2; $intento++) {
        $z = Invoke-7z $argumentos
        if ($z.Codigo -le 1) { break }
        $motivo = ($z.Salida | Select-Object -Last 15) -join ' | '
        if (Test-Path -LiteralPath $parcial) { Remove-Item -LiteralPath $parcial -Force }
        if ($intento -eq 2) { throw "7z terminó con $($z.Codigo) dos veces: $motivo" }
        Write-Paso "7z terminó con $($z.Codigo) (lo reintento en 30 s): $motivo"
        Start-Sleep -Seconds 30
    }
    Remove-Item -LiteralPath $listaExcluir -Force
    # Lo que 7z no pudo leer: «Scan WARNINGS for files and folders:» (sin permiso) y «WARNINGS for files:»
    # (bloqueados), con líneas «ruta : motivo»
    $noCopiados = New-Object System.Collections.Generic.List[object]
    $enBloque = $false
    foreach ($l in $z.Salida) {
        if ($l -match '^(Scan )?WARNINGS for files') { $enBloque = $true; continue }
        if ($enBloque -and $l -match '^-{5,}') { $enBloque = $false; continue }
        if ($enBloque -and $l -match '^(.+?) : (.+)$') {
            $ruta = $Matches[1]; $motivoLinea = $Matches[2]; $tipo = 'importa'   # antes de otro -match, que pisa $Matches
            if ($ruta -match $CACHE_DE_EDGE) { $tipo = 'caché de Edge' }
            elseif ($ruta -match $TEMPORAL_DE_TESTS) { $tipo = 'temporal de tests' }
            $noCopiados.Add([pscustomobject]@{ Ruta = $ruta; Motivo = $motivoLinea; Cache = ($tipo -ne 'importa'); Tipo = $tipo })
        }
    }
    if ($z.Codigo -eq 1 -and $noCopiados.Count -eq 0) {
        $advertencias.Add("7z terminó con advertencias: $(($z.Salida | Where-Object { $_ -match 'WARNING' }) -join ' | ')")
    }
    foreach ($p in $pendientesEdge) { $noCopiados.Add($p) }

    # Edge abierto (el Radar usa la cuenta activa) no deja leer ni siquiera con -ssw sus cookies: la sesión de
    # LinkedIn. Se espera a que se liberen (por ejemplo, si el usuario pausa el Radar un momento) y se agregan.
    $criticos = @($noCopiados | Where-Object { -not $_.Cache })
    # Las cookies que tienen copia del último deploy no se esperan: se usa esa copia (paso de abajo)
    $criticos = @($criticos | Where-Object { -not ((Split-Path -Leaf $_.Ruta) -eq 'Cookies' -and (Get-CopiaDeSesion $_.Ruta)) })
    if ($criticos.Count -gt 0 -and $EsperaBloqueadosMin -gt 0) {
        Write-Paso "Edge tiene tomados $($criticos.Count) archivo(s) que importan (sesión de LinkedIn incluida). Espero hasta $EsperaBloqueadosMin min a que se liberen: si pausás el Radar desde el portal, se completa solo."
        $limite = (Get-Date).AddMinutes($EsperaBloqueadosMin)
        do {
            $libres = @($criticos | Where-Object { Test-SeLee $_.Ruta })
            if ($libres.Count -eq $criticos.Count) { break }
            Start-Sleep -Seconds 30
        } while ((Get-Date) -lt $limite)
    }
    # Lo que ya se puede leer (crítico o caché) se copia a la carpeta de preparación y entra en el paso 4
    $recuperados = @($noCopiados | Where-Object { Test-SeLee $_.Ruta })
    $paresRecuperados = @()
    foreach ($n in $recuperados) {
        $destinoPrep = Join-Path $prep (Get-RutaEnArchivo $n.Ruta)
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destinoPrep) | Out-Null
        if ((Split-Path -Leaf $n.Ruta) -eq 'Cookies') {
            $paresRecuperados += [pscustomobject]@{ origen = $n.Ruta; destino = $destinoPrep; espera_s = 5 }
        } else {
            try { Copy-Item -LiteralPath $n.Ruta -Destination $destinoPrep -Force } catch { continue }
        }
        [void]$noCopiados.Remove($n)
    }
    if ($paresRecuperados.Count -gt 0) {
        $archivoPares = Join-Path $Destino "_pares2-$marca.json"
        $archivoCopias = Join-Path $Destino "_copias2-$marca.json"
        Write-ListaUtf8 $archivoPares @(ConvertTo-Json -InputObject $paresRecuperados -Depth 3)
        $r = Invoke-Externo $Python @('-I', (Join-Path $PSScriptRoot 'backup_bases.py'), 'copiar', $archivoPares, $archivoCopias)
        $copias2 = @(Get-Content -LiteralPath $archivoCopias -Raw -Encoding UTF8 | ConvertFrom-Json | ForEach-Object { $_ })
        Remove-Item -LiteralPath $archivoPares, $archivoCopias -Force
        foreach ($c in $copias2) {
            $enArchivo = Get-RutaEnArchivo $c.origen
            if ($c.estado -eq 'copiada') {
                $copiadas += [pscustomobject]@{ EnArchivo = $enArchivo; Bytes = [int64]$c.bytes; Integridad = $c.integridad }
                $lineasBases.Add("copiada  $enArchivo  $(Format-MB $c.bytes)  integrity_check=$($c.integridad)  (al liberarse)")
                Write-Paso "  copiada al liberarse: $enArchivo  integrity_check=$($c.integridad)"
            } else {
                $noCopiados.Add([pscustomobject]@{ Ruta = $c.origen; Motivo = "$($c.estado) $($c.motivo)"; Cache = $false; Tipo = 'importa' })
            }
        }
        Write-ListaUtf8 (Join-Path $meta 'bases.txt') $lineasBases
    }
    if ($recuperados.Count -gt 0) { Write-Paso "Recuperados al liberarse: $($recuperados.Count)" }

    # Cookies que siguen tomadas: entra la copia del último deploy, en la ruta real (restaurar = descomprimir)
    $notas = New-Object System.Collections.Generic.List[string]
    foreach ($n in @($noCopiados | Where-Object { (Split-Path -Leaf $_.Ruta) -eq 'Cookies' })) {
        $copiaDeploy = Get-CopiaDeSesion $n.Ruta
        if (-not $copiaDeploy) { continue }
        $enArchivo = Get-RutaEnArchivo $n.Ruta
        $destinoPrep = Join-Path $prep $enArchivo
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destinoPrep) | Out-Null
        Copy-Item -LiteralPath $copiaDeploy.FullName -Destination $destinoPrep -Force
        $horas = [Math]::Round(((Get-Date) - $copiaDeploy.LastWriteTime).TotalHours, 1)
        $copiadas += [pscustomobject]@{ EnArchivo = $enArchivo; Bytes = [int64]$copiaDeploy.Length; Integridad = 'se verifica en el 7z' }
        $nota = "$enArchivo es la copia del deploy $($copiaDeploy.Name) (hace $horas h): Edge tenía tomada la viva"
        $notas.Add($nota)
        $lineasBases.Add("copia del deploy  $nota")
        Write-Paso "  $nota"
        [void]$noCopiados.Remove($n)
    }
    if ($notas.Count -gt 0) { Write-ListaUtf8 (Join-Path $meta 'bases.txt') $lineasBases }

    $criticos = @($noCopiados | Where-Object { -not $_.Cache })
    $caches = @($noCopiados | Where-Object { $_.Cache })
    $temporales = @($caches | Where-Object { $_.Tipo -eq 'temporal de tests' }).Count
    Write-Paso "Archivos o carpetas que no se pudieron leer: $($noCopiados.Count) ($($criticos.Count) que importan, $($caches.Count - $temporales) de caché de Edge, que Edge rehace solo, y $temporales temporales de tests)"
    foreach ($n in $criticos) {
        Write-Paso "  NO COPIADO: $($n.Ruta)  ($($n.Motivo))"
        if ((Split-Path -Leaf $n.Ruta) -eq 'Cookies') {
            $advertencias.Add("Falta la sesión de LinkedIn de $(Split-Path -Leaf (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $n.Ruta)))) (Edge tiene abierto $($n.Ruta)): pausá el Radar y volvé a correr el backup")
        } else {
            $advertencias.Add("No se pudo leer $($n.Ruta) ($($n.Motivo))")
        }
    }
    foreach ($n in $caches) { Write-Paso "  no copiado ($($n.Tipo)): $($n.Ruta)  ($($n.Motivo))" }
    $lineasNoCopiados = @('Archivos que 7-Zip no pudo leer (bloqueados por otro proceso):') +
        @($noCopiados | ForEach-Object { "$($_.Tipo)  $($_.Ruta)  ($($_.Motivo))" })
    Write-ListaUtf8 (Join-Path $meta 'no_copiados.txt') $lineasNoCopiados

    # ------------------------------------------------------------ 4. 7z: copias de las bases y _backup\
    $nombresPrep = @(Get-ChildItem -LiteralPath $prep -Force | ForEach-Object { $_.Name })
    Push-Location $prep
    try { $z2 = Invoke-7z (@('a', '-t7z', $parcial) + $nombresPrep) } finally { Pop-Location }
    if ($z2.Codigo -ne 0) { throw "7z (bases copiadas) terminó con $($z2.Codigo): $(($z2.Salida | Select-Object -Last 15) -join ' | ')" }
    Move-Item -LiteralPath $parcial -Destination $archivo

    # ------------------------------------------------------------ 5. verificación
    $prueba = Invoke-7z @('t', $archivo)
    $pruebaOk = ($prueba.Codigo -eq 0) -and (@($prueba.Salida | Where-Object { $_ -match 'Everything is Ok' }).Count -gt 0)
    Write-Paso "7z t: $(if ($pruebaOk) { 'OK' } else { "FALLÓ (código $($prueba.Codigo))" })"
    if (-not $pruebaOk) { throw "7z t falló: $(($prueba.Salida | Select-Object -Last 15) -join ' | ')" }

    $lista = Invoke-7z @('l', '-ba', $archivo)
    $entradas = @{}
    $grupos = @{}
    foreach ($l in $lista.Salida) {
        if ($l -notmatch '^\S+ \S+ (\S{5}) +(\d+) +(\d*) +(.+)$') { continue }
        $ruta = $Matches[4]; $esDir = $Matches[1].Contains('D'); $bytes = [int64]$Matches[2]
        $entradas[$ruta.ToLowerInvariant()] = $true
        if ($esDir) { continue }
        $partes = $ruta.Split('\')
        $prof = 2
        if ($partes.Count -gt 3 -and ($partes[1] -eq '.claude' -or $partes[1] -eq (Split-Path -Leaf $Datos))) { $prof = 3 }
        $clave = ($partes[0..([Math]::Min($prof, $partes.Count) - 1)] -join '\')
        if (-not $grupos.ContainsKey($clave)) { $grupos[$clave] = [pscustomobject]@{ Archivos = 0; Bytes = [int64]0 } }
        $grupos[$clave].Archivos++
        $grupos[$clave].Bytes += $bytes
    }
    Write-Paso "Contenido del 7z (archivos y tamaño sin comprimir):"
    $totalArchivos = 0; $totalBytes = [int64]0
    foreach ($clave in ($grupos.Keys | Sort-Object)) {
        $g = $grupos[$clave]
        $totalArchivos += $g.Archivos; $totalBytes += $g.Bytes
        Write-Paso ('  {0,-70} {1,8} archivos {2,12}' -f $clave, $g.Archivos, (Format-MB $g.Bytes))
    }
    Write-Paso ('  {0,-70} {1,8} archivos {2,12}' -f 'TOTAL', $totalArchivos, (Format-MB $totalBytes))

    # Completitud: todo lo que hay en la primera capa de cada raíz y de la carpeta de datos está en el 7z
    $bloqueados = @($noCopiados | ForEach-Object { Get-RutaEnArchivo $_.Ruta })
    $faltantes = New-Object System.Collections.Generic.List[string]
    foreach ($carpeta in (@($raices) + @($Datos) | Select-Object -Unique)) {
        foreach ($hijo in (Get-ChildItem -LiteralPath $carpeta -Force)) {
            if ($hijo.Name -eq 'backups_locales') { continue }
            $enArchivo = Get-RutaEnArchivo $hijo.FullName
            if ($excluir -contains $enArchivo) { continue }   # -wal/-shm de una base copiada: ya está dentro de la copia
            if (-not $entradas.ContainsKey($enArchivo.ToLowerInvariant()) -and ($bloqueados -notcontains $enArchivo)) { $faltantes.Add($enArchivo) }
        }
    }
    foreach ($obligatorio in @("_backup\tarea-$Tarea.xml", "$(Split-Path -Leaf $Repo)\.git\HEAD")) {
        if (-not $entradas.ContainsKey($obligatorio.ToLowerInvariant())) { $faltantes.Add($obligatorio) }
    }
    foreach ($c in $copiadas) { if (-not $entradas.ContainsKey($c.EnArchivo.ToLowerInvariant())) { $faltantes.Add($c.EnArchivo) } }
    Write-Paso "Completitud (primera capa de cada origen y de los datos): $(if ($faltantes.Count -eq 0) { 'OK' } else { "FALTAN $($faltantes.Count)" })"
    foreach ($f in $faltantes) { Write-Paso "  falta: $f"; $advertencias.Add("Falta en el 7z: $f") }

    # integrity_check de las bases tal como quedaron dentro del 7z (y de las cookies de Edge, informativo)
    $aVerificar = @($copiadas | ForEach-Object { $_.EnArchivo })
    # Cookies de Edge que fueron en crudo (Edge las tenía tomadas); -contains no distingue mayúsculas
    $cookies = @($entradas.Keys | Where-Object { $_ -match '\\cookies$' -and $aVerificar -notcontains $_ })
    $listaIncluir = Join-Path $Destino "_incluir-$marca.txt"
    Write-ListaUtf8 $listaIncluir (@($aVerificar) + @($cookies))
    $x = Invoke-7z @('x', $archivo, "-o$verif", "-i@$listaIncluir", '-y')
    Remove-Item -LiteralPath $listaIncluir -Force
    if ($x.Codigo -ne 0) { throw "No se pudieron extraer las bases para verificarlas (7z x terminó con $($x.Codigo))" }
    $rutasVerif = @(@($aVerificar) + @($cookies) | ForEach-Object { Join-Path $verif $_ })
    $v = Invoke-Externo $Python (@('-I', (Join-Path $PSScriptRoot 'backup_bases.py'), 'verificar') + $rutasVerif)
    if ($v.Codigo -ne 0) { throw "backup_bases.py verificar terminó con $($v.Codigo)" }
    $resultados = @(($v.Salida -join "`n") | ConvertFrom-Json | ForEach-Object { $_ })
    $basesOk = 0
    foreach ($res in $resultados) {
        $rel = $res.base.Substring($verif.Length + 1)
        $copiada = $aVerificar -contains $rel
        if ($res.integridad -eq 'ok') { if ($copiada) { $basesOk++ } }
        elseif ($copiada) { $advertencias.Add("integrity_check dentro del 7z de $rel = $($res.integridad)") }
        else { $advertencias.Add("Cookies de Edge en crudo en el 7z: integrity_check = $($res.integridad) ($rel)") }
        $tipo = 'copia consistente'; if (-not $copiada) { $tipo = 'en crudo' }
        Write-Paso "  integrity_check en el 7z: $rel = $($res.integridad) ($tipo)"
    }
    Write-Paso "Bases verificadas dentro del 7z: $basesOk de $($aVerificar.Count) con integrity_check = ok"

    $tam = (Get-Item -LiteralPath $archivo).Length
    Write-Paso "Archivo: $archivo ($(Format-MB $tam))"
    if ($advertencias.Count -eq 0) {
        Write-Paso 'Resultado: OK'
        foreach ($nota in $notas) { Write-Paso "  NOTA: $nota" }
        $codigoFinal = 0
    } else {
        Write-Paso "Resultado: CON ADVERTENCIAS ($($advertencias.Count))"
        foreach ($a in $advertencias) { Write-Paso "  ADVERTENCIA: $a" }
        foreach ($nota in $notas) { Write-Paso "  NOTA: $nota" }
        $codigoFinal = 1
    }
} catch {
    Write-Paso "FALLÓ: $($_.Exception.Message)"
    if (Test-Path -LiteralPath $parcial) { Remove-Item -LiteralPath $parcial -Force }
    $codigoFinal = 2
} finally {
    foreach ($tmp in @($prep, $verif)) {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    }
    Write-ListaUtf8 $archivoInforme $informe
}
exit $codigoFinal
