# Restaurar el portal local desde un backup

El backup lo arma `tools/backup_local.ps1` (ver el encabezado del script). Queda en
`<repo>\backups_locales\linkedout-local-AAAAMMDD-HHMMSS.7z`, con un informe `.txt` al lado. Nunca se sube a git.

## Qué trae el 7z

| En el 7z | Va a | Qué es |
|---|---|---|
| `buscatrabajo4\` | `~\Downloads\Projects\buscatrabajo4` | checkout de desarrollo con `.git` completo (todas las ramas, tags, stash), los worktrees de `.claude\worktrees` y `dev.env` |
| `LinkedOut\` | `~\LinkedOut` | producción desde el corte del 14/11: `produccion\` (clon propio, tags `deploy-*` locales), `datos\`, `produccion.env`, `portal.cmd` y `.vbs`, `vigilante.ps1`, `despliegue.json`, `despliegues.log`; además el portal dev (`dev\`, `dev-datos\`, `dev.env`) |
| `LinkedOut\datos\` | (adentro del anterior) | `LINKEDOUT_DATA_DIR`: bases, `candidato\`, CVs, exports, `radar_debug\`, `tickets\`, `backups\` y los perfiles de Edge `linkedin_profile` y `linkedin_profile_2` (las sesiones de LinkedIn) |
| `.buscatrabajo4\` | `~\.buscatrabajo4` | lo de antes del corte: el worktree `produccion\` viejo, `portal-real.cmd` y `.vbs`, `CONGELADO`, logs y `.cmd` viejos |
| `_backup\` | carpeta temporal | `tarea-LinkedOut-portal.xml` (tarea programada), `requirements-freeze.txt`, `python.txt`, `git.txt`, `bases.txt`, `no_copiados.txt`, `origen.txt` y esta guía |

Un backup de antes del 14/11 no tiene `LinkedOut\`: ahí los datos están en `buscatrabajo4\data\` y la tarea es
`buscatrabajo4-portal` (`portal-real.vbs`).

Las bases SQLite (`postulaciones.db`, `tickets\tickets.db`, `radar.db`, las de `backups\`, etc.) son copias consistentes
hechas con la API de backup de SQLite: van sin `-wal` ni `-shm`, todo adentro del `.db`. `no_copiados.txt` lista
lo que 7-Zip no pudo leer (archivos que otro proceso tenía bloqueados).

**Rutas fijas:** restaurá en las mismas rutas. Los worktrees, `portal.cmd`, `vigilante.ps1` y `despliegue.json`
guardan rutas absolutas. En otra ruta, corré `git worktree repair` y corregí esos tres archivos.

**Cookies de la cuenta que usa el Radar:** mientras el Radar corre, Edge abre en exclusiva
`linkedin_profile*\Default\Network\Cookies` y nadie lo puede leer (ni 7-Zip con `-ssw`). El script no la copia en
crudo. La espera hasta `-EsperaBloqueadosMin` (10 min por defecto) y la copia con SQLite apenas Edge la suelta. Si
no se libera, usa la copia más nueva que dejó el último deploy en `<datos>\backups\sesiones\` (`Save-SesionesEdge`, con
el portal detenido) y la pone en la ruta real. El informe dice de qué deploy es («NOTA: ... copia del deploy ...»).
Sin esa copia, el backup termina «CON ADVERTENCIAS» y lo dice: esa sesión no está en el 7z. Mirá el informe `.txt`
antes de confiar en un backup.

**Sesiones de LinkedIn:** Edge cifra las cookies con la cuenta de Windows (DPAPI). En la misma PC y el mismo
usuario de Windows, las sesiones vuelven solas. En otra PC u otro usuario, Edge no las puede leer: hay que volver a
iniciar sesión con `python tools/login_cuenta.py --principal` y `python tools/login_cuenta.py` (con el Radar
detenido).

## Pasos (Windows PowerShell 5.1)

1. Requisitos: Windows, 7-Zip, git, Microsoft Edge y Python con la versión de `_backup\python.txt` (hoy 3.14, en
   `C:\Python314`).

2. Si el portal está corriendo, detenelo:

   ```powershell
   Disable-ScheduledTask -TaskName LinkedOut-vigilante; Stop-ScheduledTask -TaskName LinkedOut-portal; Get-NetTCPConnection -LocalPort 1111 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
   ```

3. Si ya existen las carpetas de destino, renombralas (no las borres):

   ```powershell
   $m = Get-Date -Format yyyyMMdd-HHmmss; Rename-Item $HOME\Downloads\Projects\buscatrabajo4 "buscatrabajo4.antes-$m" -ErrorAction SilentlyContinue; Rename-Item $HOME\LinkedOut "LinkedOut.antes-$m" -ErrorAction SilentlyContinue; Rename-Item $HOME\.buscatrabajo4 ".buscatrabajo4.antes-$m" -ErrorAction SilentlyContinue
   ```

   Si el backup está dentro de `buscatrabajo4\backups_locales`, movelo antes a otra carpeta (por ejemplo `~\Downloads`).

4. Descomprimí cada parte en su lugar (cambiá el nombre del archivo):

   ```powershell
   $z = 'C:\Program Files\7-Zip\7z.exe'; $a = "$HOME\Downloads\linkedout-local-AAAAMMDD-HHMMSS.7z"
   & $z x $a "-o$HOME\Downloads\Projects" 'buscatrabajo4\*' -y; & $z x $a "-o$HOME" 'LinkedOut\*' -y; & $z x $a "-o$HOME" '.buscatrabajo4\*' -y; & $z x $a "-o$env:TEMP\linkedout-backup" '_backup\*' -y
   ```

5. Dependencias de Python (solo si es una instalación nueva):

   ```powershell
   python -m pip install -r "$env:TEMP\linkedout-backup\_backup\requirements-freeze.txt"
   ```

6. Tarea programada (lanza `~\LinkedOut\portal.vbs` al iniciar sesión):

   ```powershell
   Register-ScheduledTask -TaskName LinkedOut-portal -Xml (Get-Content "$env:TEMP\linkedout-backup\_backup\tarea-LinkedOut-portal.xml" -Raw) -Force
   ```

   Equivalente: `schtasks /create /tn LinkedOut-portal /xml "$env:TEMP\linkedout-backup\_backup\tarea-LinkedOut-portal.xml" /f`.
   Después: `Enable-ScheduledTask -TaskName LinkedOut-vigilante`.

7. Arrancá el portal y comprobá que responde:

   ```powershell
   Start-ScheduledTask -TaskName LinkedOut-portal; Start-Sleep 20; Invoke-RestMethod http://127.0.0.1:1111/api/radar/status
   ```

8. Comprobá git: `git -C $HOME\Downloads\Projects\buscatrabajo4 worktree list` y `git ... branch` deben
   coincidir con `_backup\git.txt`. Si un worktree figura como «prunable», corré `git worktree repair`.

9. Abrí http://127.0.0.1:1111, revisá el Radar (las dos cuentas) y Mi perfil. Después borrá `$env:TEMP\linkedout-backup`.

## Probar un backup sin tocar el portal real

Descomprimí todo en una carpeta temporal y levantá un portal de prueba en el puerto 1199, sin Radar y sin `.env`
(así no envía mails):

```powershell
$t = "$env:TEMP\linkedout-prueba"; & 'C:\Program Files\7-Zip\7z.exe' x C:\ruta\linkedout-local-AAAAMMDD-HHMMSS.7z "-o$t" -y
$env:LINKEDOUT_DATA_DIR = "$t\buscatrabajo4\data"; $env:LINKEDOUT_SIN_RADAR = '1'; $env:LINKEDOUT_ENV_FILE = "$t\sin.env"; $env:PYTHONUTF8 = '1'
$env:BUSCATRABAJO_DATA_DIR = $env:LINKEDOUT_DATA_DIR; $env:BUSCATRABAJO_SIN_RADAR = '1'; $env:BUSCATRABAJO_ENV_FILE = $env:LINKEDOUT_ENV_FILE  # compatibilidad: el código del backup puede ser anterior al nombre LinkedOut
Set-Location "$t\.buscatrabajo4\produccion"; python -m uvicorn server:app --host 127.0.0.1 --port 1199
```

En otra consola: `Invoke-RestMethod http://127.0.0.1:1199/api/radar/status`. Al terminar, cortá el portal
(Ctrl+C) y borrá `$env:TEMP\linkedout-prueba`.
