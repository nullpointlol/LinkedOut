# lib.sh — funciones comunes del kit de agentes (workflow v2). Se carga con: . "$D/lib.sh"
# Toda la configuración por repo vive en <repo>/tools/agents/repo.env.

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "$(date -u +%H:%MZ) $*" | tee -a "${L:-/dev/null}"; }

KIT_BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
load_env() {  # $1 = worktree
  # El kit se usa desde la integración (los scripts que corren): su repo.env manda sobre la copia de la rama de la misión.
  local env="${REPO_ENV:-}"
  [ -n "$env" ] || { [ -f "$KIT_BIN/repo.env" ] && env="$KIT_BIN/repo.env"; } || env="$1/tools/agents/repo.env"
  [ -f "$env" ] || die "falta $env (instalá el kit: bash ~/.claude/agent-workflow/install.sh <repo>)"
  local env_exec="${EXECUTOR:-}"  # EXECUTOR del entorno manda sobre repo.env (pedido de BT, 07/11/2026)
  local env_gate_min="${GATE_TIMEOUT_MIN:-}" env_gate_sec="${GATE_TIMEOUT_SEC:-}"
  local env_feat_rev_max="${FEATURE_REVIEW_MAX:-}"
  local env_codex_rev="${CODEX_REVIEW:-}"
  local env_agy_quota_wait="${AGY_QUOTA_WAIT:-}" env_agy_quota_max_h="${AGY_QUOTA_MAX_H:-}"
  local env_agy_quota_wait_sec="${AGY_QUOTA_WAIT_SEC:-}" env_agy_quota_max_sec="${AGY_QUOTA_MAX_SEC:-}"
  local env_codex_wait_max_min="${CODEX_WAIT_MAX_MIN:-}"
  local env_test_file_regex="${TEST_FILE_REGEX:-}"
  # shellcheck disable=SC1090
  . <(tr -d '\r' < "$env")  # tolera CRLF de checkouts en Windows
  [ -n "$env_exec" ] && EXECUTOR="$env_exec"
  [ -n "$env_gate_min" ] && GATE_TIMEOUT_MIN="$env_gate_min"
  [ -n "$env_gate_sec" ] && GATE_TIMEOUT_SEC="$env_gate_sec"
  [ -n "$env_feat_rev_max" ] && FEATURE_REVIEW_MAX="$env_feat_rev_max"
  [ -n "$env_codex_rev" ] && CODEX_REVIEW="$env_codex_rev"
  [ -n "$env_agy_quota_wait" ] && AGY_QUOTA_WAIT="$env_agy_quota_wait"
  [ -n "$env_agy_quota_max_h" ] && AGY_QUOTA_MAX_H="$env_agy_quota_max_h"
  [ -n "$env_agy_quota_wait_sec" ] && AGY_QUOTA_WAIT_SEC="$env_agy_quota_wait_sec"
  [ -n "$env_agy_quota_max_sec" ] && AGY_QUOTA_MAX_SEC="$env_agy_quota_max_sec"
  [ -n "$env_codex_wait_max_min" ] && CODEX_WAIT_MAX_MIN="$env_codex_wait_max_min"
  [ -n "$env_test_file_regex" ] && TEST_FILE_REGEX="$env_test_file_regex"
  [ -n "${REPO_NAME:-}" ] || die "repo.env sin REPO_NAME"
  [ -n "${GATE_CMD:-}" ] || die "repo.env sin GATE_CMD"
  : "${GATE_GREP:=FAIL|FAILED|ERROR|Error|✖|AssertionError}"
  : "${GATE_RESTORE:=}"
  : "${PROTECTED:=}"
  : "${KIT_HOME:=$HOME/.claude/agent-workflow}"
  : "${FEATURES_DIR:=$KIT_HOME/features/$REPO_NAME}"
  : "${METRICS:=$KIT_HOME/metricas/$REPO_NAME.csv}"
  : "${BASE_BRANCH:=}"
  : "${AGY_MODEL:=gemini-3.8-flash-high}"
  : "${CODEX_BIN:=codex}"
  : "${CODEX_MODEL:=}"
  : "${CODEX_WAIT:=1800}"
  : "${CODEX_WAIT_MAX_MIN:=120}"
  : "${CODEX_REVIEW:=}"
  : "${AGY_QUOTA_WAIT:=}"
  : "${AGY_QUOTA_MAX_H:=96}"
  : "${CODEX_HIGH_REGEX:=envio|envío|aprobaci|seguridad|concurrencia|cancelaci}"
  : "${SKIP_REGEX:=pytest\.mark\.(skip|xfail)|unittest\.skip|\b(it|test|describe)\.(skip|only|todo)\b|\bx(it|describe)\(|skip\s*[:=]\s*true|todo\s*[:=]\s*true}"
  : "${ASSERT_REGEX:=\bassert\b|assert\.|self\.assert|expect\(}"
  : "${AUTO_COMMIT:=1}"
  : "${MAX_ROUNDS:=6}"
  : "${FEATURE_REVIEW_MAX:=2}"
  : "${TEST_FILE_REGEX:=(^|/)(test_[^/]*\.[a-z]+|[^/]*_test\.[a-z]+|[^/]*\.test\.[a-z]+)$}"
  export AGY_MODEL FEATURE_REVIEW_MAX CODEX_REVIEW AGY_QUOTA_WAIT AGY_QUOTA_MAX_H CODEX_WAIT_MAX_MIN TEST_FILE_REGEX
  READ_TOOLS_NOTE=$(read_tools_note "$1")
  # Python en Windows escribe en cp1252 si no se le pide UTF-8: rompería los prompts a Codex.
  export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
  read -ra CODEX_CMD <<< "$CODEX_BIN"
}

# Campos de la misión: «Nombre: valor» al comienzo de la línea.
mission_field() { sed -n "s/^$2:[[:space:]]*//p" "$1" | head -n 1 | tr -d '\r' | sed 's/[[:space:]]*$//'; }
# Listas separadas por coma o espacio → una por línea.
to_lines() { tr ',' '\n' | tr ' ' '\n' | tr -d '\r' | sed '/^$/d'; }
mission_list() { mission_field "$1" "$2" | sed 's/^<.*>$//; s/^—$//; s/^-$//; s/^ninguno$//I' | to_lines; }
# Modo de compuerta: el de la misión; un tests-fix sin «Compuerta:» usa la incremental (el repo puede tener fallos previos
# ajenos, como QA conocido, y la misión solo tiene que estabilizar sus tests sin romper nada nuevo).
mission_compuerta() { local c; c=$(mission_field "$1" Compuerta | tr '[:upper:]' '[:lower:]' | sed 's/[[:space:]].*//'); [ -z "$c" ] && [ "$(mission_field "$1" Tipo)" = tests-fix ] && c=incremental; printf "%s" "$c"; }
mission_title() { sed -n 's/^# Misión:[[:space:]]*//p' "$1" | head -n 1 | tr -d '\r'; }
# Carril de la misión (rapido | normal | s4). Sin «Carril:» (misiones anteriores al 07/11/2026) sigue el proceso completo: s4.
mission_carril() {
  local c; c=$(mission_field "$1" Carril | tr '[:upper:]' '[:lower:]' | sed 's/á/a/g; s/[[:space:]].*//')
  printf '%s' "${c:-s4}"
}

# ¿El archivo $1 coincide con algún patrón de la lista $2 (una por línea)?
# «**/» equivale a cualquier subcarpeta (o ninguna); una ruta que termina en «/» es una carpeta.
matches_any() {
  local f="$1" p
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    p="${p//\*\*\//*}"; p="${p//\*\*/*}"; [ "${p%/}" != "$p" ] && p="${p}*"
    # shellcheck disable=SC2053
    [[ "$f" == $p ]] && return 0
  done <<< "$2"
  return 1
}

: "${TEST_FILE_REGEX:=(^|/)(test_[^/]*\.[a-z]+|[^/]*_test\.[a-z]+|[^/]*\.test\.[a-z]+)$}"
is_test_file() {
  local re="${TEST_FILE_REGEX:-(^|/)(test_[^/]*\.[a-z]+|[^/]*_test\.[a-z]+|[^/]*\.test\.[a-z]+)$}"
  printf '%s\n' "$1" | grep -qE "$re"
}

changed_files() {  # cambios del worktree respecto de HEAD, sin _ref/
  { git -C "$1" -c core.quotepath=off diff --name-only HEAD
    git -C "$1" -c core.quotepath=off ls-files --others --exclude-standard; } | grep -v '^_ref/' | sort -u
}
added_lines() {  # $1 worktree, $2 archivo
  if git -C "$1" ls-files --error-unmatch -- "$2" >/dev/null 2>&1; then
    git -C "$1" diff HEAD -U0 -- "$2" | grep '^+' | grep -v '^+++'
  elif [ -f "$1/$2" ]; then sed 's/^/+/' "$1/$2"; fi
}
removed_lines() { git -C "$1" diff HEAD -U0 -- "$2" 2>/dev/null | grep '^-' | grep -v '^---'; }
full_diff() {  # diff completo respecto de HEAD, incluidos los archivos nuevos
  git -C "$1" -c core.quotepath=off diff HEAD -- . ':(exclude)_ref'
  git -C "$1" -c core.quotepath=off ls-files --others --exclude-standard | grep -v '^_ref/' |
    while IFS= read -r f; do (cd "$1" && git diff --no-index -- /dev/null "$f"); done
}

git_estado() {  # $1 worktree
  local W="$1" out="$1/_ref/git-estado.txt"
  mkdir -p "$W/_ref"
  {
    echo "## Rama"
    git -C "$W" rev-parse --abbrev-ref HEAD 2>/dev/null
    echo
    echo "## HEAD"
    git -C "$W" log -1 --format="%h %s" 2>/dev/null
    echo
    echo "## Últimos 5 commits"
    git -C "$W" log --oneline -5 2>/dev/null
    echo
    echo "## Estado (git status)"
    git -C "$W" -c core.quotepath=off status --short 2>/dev/null | grep -vE '(^|[[:space:]])_ref(/|$)' || true
    echo
    echo "## Cambios (git diff --stat HEAD)"
    git -C "$W" -c core.quotepath=off diff --stat HEAD -- . ':(exclude)_ref' 2>/dev/null | grep -v ' _ref/' || true
  } > "$out"
}

# Fotos del worktree: guardan los archivos cambiados respecto de HEAD para ignorar trabajo previo ajeno
# (archivos sueltos del creador) y para restaurar sin borrar nada que no haya tocado el agente.
snap_hash() { [ -f "$1/$2" ] && git -C "$1" hash-object -- "$2" || echo DELETED; }
snap_take() {  # $1 worktree, $2 nombre
  local d="$1/_ref/.snap-$2" f
  rm -rf "$d"; mkdir -p "$d/files"; : > "$d/index"
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    printf '%s\t%s\n' "$f" "$(snap_hash "$1" "$f")" >> "$d/index"
    [ -f "$1/$f" ] && { mkdir -p "$(dirname "$d/files/$f")"; cp -p "$1/$f" "$d/files/$f"; }
  done < <(changed_files "$1")
}
snap_entry() { [ -f "$1/_ref/.snap-$2/index" ] && awk -F'\t' -v f="$3" '$1==f {print $2; exit}' "$1/_ref/.snap-$2/index"; }
snap_same() {  # ¿el archivo está igual que en la foto (y estaba cambiado ya entonces)?
  local h; h=$(snap_entry "$1" "$2" "$3"); [ -n "$h" ] && [ "$h" = "$(snap_hash "$1" "$3")" ]
}
snap_restore() {  # deja cada archivo como estaba en la foto; lo que no estaba en la foto vuelve a HEAD
  local W="$1" d="$1/_ref/.snap-$2" f h
  [ -f "$d/index" ] || return 0
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    snap_same "$W" "$2" "$f" && continue
    h=$(snap_entry "$W" "$2" "$f")
    if [ -n "$h" ] && [ "$h" != DELETED ]; then mkdir -p "$(dirname "$W/$f")"; cp -p "$d/files/$f" "$W/$f"
    elif [ "$h" = DELETED ]; then rm -f "$W/$f"
    elif git -C "$W" cat-file -e "HEAD:$f" 2>/dev/null; then git -C "$W" checkout -q HEAD -- "$f"
    else rm -f "$W/$f"; fi
  done < <({ changed_files "$W"; cut -f1 "$d/index"; } | sort -u)
}

# Control de integridad (§4.2). Escribe _ref/integrity.md. 0 = limpio, 1 = violación (S3).
# Ignora los archivos que ya estaban cambiados al empezar la misión y siguen iguales (foto «base»).
integrity() {
  local W="$1" M="$2" out="$1/_ref/integrity.md" tipo arch bloq prot f add rem
  tipo=$(mission_field "$M" Tipo); arch=$(mission_list "$M" Archivos)
  bloq=$(mission_list "$M" "Tests bloqueados"); prot=$(printf '%s' "$PROTECTED" | to_lines)
  : > "$out"
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    snap_same "$W" base "$f" && continue
    # Artefactos de herramientas (Chromium escribe debug.log en la raíz, etc.): no son trabajo del agente.
    printf '%s' "$f" | grep -qE "${INTEGRITY_IGNORE:-(^|/)(debug|npm-debug|chrome_debug)\.log$}" && continue
    if [ "$tipo" = lectura ]; then echo "- \`$f\`: una misión de lectura no puede cambiar archivos (solo _ref/)." >> "$out"; continue; fi
    matches_any "$f" "$arch" || echo "- \`$f\`: está fuera de «Archivos:» de la misión." >> "$out"
    [ -n "$bloq" ] && matches_any "$f" "$bloq" && echo "- \`$f\`: figura en «Tests bloqueados:»." >> "$out"
    if matches_any "$f" "$prot"; then
      added_lines "$W" "$f" | grep -qE "$SKIP_REGEX" &&
        echo "- \`$f\`: agrega marcas de skip/xfail/only/todo: $(added_lines "$W" "$f" | grep -oE "$SKIP_REGEX" | head -n 1)" >> "$out"
      add=$(added_lines "$W" "$f" | grep -cE "$ASSERT_REGEX"); rem=$(removed_lines "$W" "$f" | grep -cE "$ASSERT_REGEX")
      [ "$rem" -gt "$add" ] && echo "- \`$f\`: quita aserciones ($rem quitadas, $add agregadas)." >> "$out"
      [ -f "$W/$f" ] || echo "- \`$f\`: borra un archivo protegido." >> "$out"
    fi
  done < <(changed_files "$W")
  [ ! -s "$out" ]
}

# Normaliza líneas de fallo para compararlas entre corridas (sin duraciones ni espacios finales).
norm_fails() { sed -E 's/[[:space:]]*\(?[0-9]+(\.[0-9]+)? ?(ms|s)\)?//g; s/[[:space:]]+$//' | sed '/^$/d' | sort -u; }

# Clave de comparación de una línea de fallo (una por línea, sin reordenar). Con FAIL_NUM_MASK=1 (repo.env) los números
# se reemplazan por «#»: una medida que mejora sin quedar perfecta («margen -2.8 px» → «margen -1.1 px») es el mismo fallo.
# AGY sigue recibiendo las líneas originales; la clave solo se usa para comparar (incremental y S1).
# Siempre (sin depender de FAIL_NUM_MASK): fechas/horas ISO y direcciones de memoria cambian en cada corrida sin ser otro fallo.
TS_RE='[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2}([.,][0-9]+)?)?(Z|[+-][0-9]{2}:?[0-9]{2})?'
HEX_RE='0x[0-9a-fA-F]{6,}'
fail_key_line() {
  sed -E "s/$TS_RE/<fecha>/g; s/$HEX_RE/0x#/g" | if [ "${FAIL_NUM_MASK:-0}" = 1 ]; then sed -E 's/-?[0-9]+([.,][0-9]+)?/#/g'; else cat; fi
}
fail_keys() { fail_key_line | sed '/^$/d' | sort -u; }
new_fails() {  # $1 base, $2 actual: imprime las líneas de $2 con alguna parte cuya clave no está en la base.
  # FAIL_SPLIT (regex ERE en repo.env) parte una línea agregada («48 desbordes: a; b; c») en partes: arreglar un elemento no crea un fallo nuevo.
  awk -v sep="${FAIL_SPLIT:-}" -v mask="${FAIL_NUM_MASK:-0}" -v ts="$TS_RE" -v hex="$HEX_RE" '
    function key(t) { gsub(ts, "<fecha>", t); gsub(hex, "0x#", t); if (mask == 1) gsub(/-?[0-9]+([.,][0-9]+)?/, "#", t); gsub(/^[ \t]+|[ \t\r]+$/, "", t); return t }
    function parts(l, arr) { if (sep == "") { arr[1] = l; return 1 } return split(l, arr, sep) }
    FILENAME == ARGV[1] { n = parts($0, a); for (i = 1; i <= n; i++) base[key(a[i])]; next }  # no NR==FNR: falla con base vacía
    { n = parts($0, a); for (i = 1; i <= n; i++) { k = key(a[i]); if (k != "" && !(k in base)) { print; break } } }' "$1" "$2"
}

# Líneas de resultado que dicen PASS («contenedor justo: PASS», «[juego] PASS {…}», «· PASS») sin ningún FAIL:
# GATE_GREP las atrapa por una palabra del chequeo, pero son aprobaciones, no fallos.
drop_pass_lines() { awk '!(/[]:·] PASS([[:space:]]|$)/ && !/FAIL/)'; }

# Clave corta y supervisor de compuertas
gate_cmd_key() {  # clave de comando para guardar duraciones: hash corto (cksum)
  printf '%s' "$1" | cksum | awk '{print $1}'
}

gate_effective_timeout() {  # $1 worktree, $2 comando → tiempo máximo efectivo en segundos
  if [ -n "${GATE_TIMEOUT_SEC:-}" ]; then
    echo "$GATE_TIMEOUT_SEC"; return 0
  fi
  local max_sec=$(( ${GATE_TIMEOUT_MIN:-30} * 60 ))
  local clave; clave=$(gate_cmd_key "$2")
  local f="$1/_ref/.gate-dur-$clave"
  if [ -f "$f" ]; then
    local dur prop
    dur=$(tr -d ' \r\n' < "$f")
    if [ -n "$dur" ] && [ "$dur" -ge 0 ] 2>/dev/null; then
      prop=$(( dur * 4 ))
      [ "$prop" -lt 300 ] && prop=300
      [ "$prop" -gt "$max_sec" ] && prop="$max_sec"
      echo "$prop"; return 0
    fi
  fi
  echo "$max_sec"
}

# Un supervisor para todas las compuertas. Conserva bytes y código; solo al vencer agrega FAIL y devuelve 124.
# Python ya es dependencia del kit: en Windows Popen.pid es el WINPID, no el PID de MSYS.
gate_command() {  # $1 worktree, $2 comando, $3 archivo de salida
  local cwd="$1" cmd="$2" out="$3" shell="$BASH" limit="${GATE_TIMEOUT_SEC:-}" unit=s
  local clave; clave=$(gate_cmd_key "$cmd")
  if [ -n "$limit" ]; then
    unit=s
  elif [ -f "$cwd/_ref/.gate-dur-$clave" ]; then
    limit=$(gate_effective_timeout "$cwd" "$cmd")
    unit=s
  else
    limit="${GATE_TIMEOUT_MIN:-30}"
    unit=min
  fi
  local t_start; t_start=$(date +%s)
  if command -v cygpath >/dev/null 2>&1; then
    cwd=$(cygpath -m "$cwd"); out=$(cygpath -m "$out"); shell=$(cygpath -m "$shell")
  fi
  python -c '
import math, os, re, signal, subprocess, sys
cwd, cmd, out, shell, limit, unit = sys.argv[1:]
with open(out, "wb") as output:
    try:
        seconds = float(limit) * (60 if unit == "min" else 1)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("el tiempo máximo debe ser positivo y finito")
        # El bash exterior espera al interior: su WINPID sigue vivo aunque el comando haga exec.
        proc = subprocess.Popen([shell, "-c", "bash -c \"$1\"; exit $?", "gate", cmd],
                                cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=os.name != "nt")
    except (OSError, ValueError) as error:
        output.write(("FAIL: no se pudo ejecutar la compuerta: %s\n" % error).encode("utf-8"))
        sys.exit(1)
    try:
        code = proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes as wt
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
            kernel.OpenProcess.restype = wt.HANDLE
            kernel.CloseHandle.argtypes = [wt.HANDLE]
            kernel.GetProcessTimes.argtypes = [
                wt.HANDLE,
                ctypes.POINTER(wt.FILETIME),
                ctypes.POINTER(wt.FILETIME),
                ctypes.POINTER(wt.FILETIME),
                ctypes.POINTER(wt.FILETIME),
            ]
            kernel.GetProcessTimes.restype = wt.BOOL

            proc_times = {}
            def get_time(pid):
                if pid in proc_times:
                    return proc_times[pid]
                h = kernel.OpenProcess(0x1000, False, pid)
                if not h:
                    h = kernel.OpenProcess(0x0400, False, pid)
                if not h:
                    proc_times[pid] = None
                    return None
                try:
                    c, e, k, u = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
                    if kernel.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                        val = (int(c.dwHighDateTime) << 32) | int(c.dwLowDateTime)
                        if val > 0:
                            proc_times[pid] = val
                            return val
                finally:
                    kernel.CloseHandle(h)
                proc_times[pid] = None
                return None

            # MSYS puede usar padres nativos transitorios al hacer fork: sumar sus WINPID antes de taskkill.
            rows = subprocess.check_output([os.path.join(os.path.dirname(shell), "ps.exe"), "-W"])
            msys = [line.split(None, 4) for line in rows.splitlines()[1:]]
            msys = [list(map(int, row[:4])) for row in msys if len(row) >= 4 and all(v.isdigit() for v in row[:4])]
            msys_to_win = {row[0]: row[3] for row in msys}
            roots = {row[0] for row in msys if row[3] == proc.pid}
            while True:
                added = set()
                for row in msys:
                    child_msys, parent_msys, _, child_win = row
                    if child_msys not in roots and parent_msys in roots:
                        p_win = msys_to_win.get(parent_msys)
                        if p_win:
                            p_t = get_time(p_win)
                            c_t = get_time(child_win)
                            if p_t is not None and c_t is not None and c_t >= p_t:
                                added.add(child_msys)
                if not added:
                    break
                roots |= added
            winpids = [proc.pid] + [row[3] for row in msys if row[0] in roots and row[3] != proc.pid]
            killed = subprocess.run(["taskkill", "/T", "/F"] + [arg for pid in reversed(winpids) for arg in ("/PID", str(pid))],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if killed.returncode:
                # taskkill necesita WMI, que algunos sandboxes bloquean. Toolhelp enumera también hijos nativos.
                class Entry(ctypes.Structure):
                    _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD),
                                ("heap", ctypes.c_size_t), ("module", wt.DWORD), ("threads", wt.DWORD),
                                ("parent", wt.DWORD), ("priority", wt.LONG), ("flags", wt.DWORD),
                                ("exe", ctypes.c_char * 260)]
                kernel.CreateToolhelp32Snapshot.restype = wt.HANDLE
                kernel.Process32First.argtypes = kernel.Process32Next.argtypes = [wt.HANDLE, ctypes.POINTER(Entry)]
                kernel.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
                kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
                snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
                if snapshot == wt.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                entry = Entry(); entry.size = ctypes.sizeof(entry)
                parents = {}
                more = kernel.Process32First(snapshot, ctypes.byref(entry))
                while more:
                    parents.setdefault(entry.parent, []).append(entry.pid)
                    more = kernel.Process32Next(snapshot, ctypes.byref(entry))
                kernel.CloseHandle(snapshot)
                tree = list(dict.fromkeys(winpids))
                for pid in tree:
                    p_t = get_time(pid)
                    if p_t is None:
                        continue
                    for child in parents.get(pid, []):
                        if child not in tree:
                            c_t = get_time(child)
                            if c_t is not None and c_t >= p_t:
                                tree.append(child)
                handles = []
                for pid in tree:
                    handle = kernel.OpenProcess(0x100001, False, pid)  # SYNCHRONIZE | PROCESS_TERMINATE
                    if not handle:
                        if ctypes.get_last_error() == 87:  # ya terminó
                            continue
                        raise OSError("OpenProcess(%s): %s" % (pid, ctypes.WinError(ctypes.get_last_error())))
                    handles.append((pid, handle))
                # Abrir antes de cortar: MSYS puede terminar otros miembros al morir un padre.
                # Cortar padres primero impide que sigan creando workers mientras se termina el árbol conocido.
                for pid, handle in handles:
                    try:
                        kernel.TerminateProcess(handle, 124)  # puede estar terminando por el corte de su padre
                        if kernel.WaitForSingleObject(handle, 5000) != 0:
                            raise RuntimeError("no terminó el proceso %s" % pid)
                    finally:
                        kernel.CloseHandle(handle)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        with open(out, "rb") as saved:
            lines = re.split(rb"[\r\n]", saved.read())
        last = next((line.decode("utf-8", "replace") for line in reversed(lines)
                     if b"::" in line or b"test_" in line), "(sin test identificado)")
        output.write(("\nFAIL: compuerta colgada más de %s %s (cortada por el kit); último test en la salida: %s\n"
                      % (limit, unit, last)).encode("utf-8"))
        code = 124
sys.exit(128 - code if code < 0 else code)
' "$cwd" "$cmd" "$out" "$shell" "$limit" "$unit"
  local code=$?
  local t_end; t_end=$(date +%s)
  if [ $code -ne 124 ]; then
    local dur=$(( t_end - t_start ))
    mkdir -p "$1/_ref" 2>/dev/null
    echo "$dur" > "$1/_ref/.gate-dur-$clave"
  fi
  return $code
}

# Corre GATE_CMD. Deja la salida en _ref/gate-out.txt y los fallos normalizados en $2. Devuelve el código de GATE_CMD.
gate_run() {
  local W="$1" out="$2" t
  gate_command "$W" "$GATE_CMD" "$W/_ref/gate-out.txt"; t=$?
  # shellcheck disable=SC2086
  [ -n "$GATE_RESTORE" ] && (cd "$W" && git checkout -q -- $GATE_RESTORE 2>/dev/null)
  : > "$out"
  if [ $t -ne 0 ]; then
    { grep -E "$GATE_GREP" "$W/_ref/gate-out.txt" | grep -vE "${GATE_IGNORE:-^[[:space:]]*(✔|ok [0-9])}"
      grep '^FAIL: compuerta colgada' "$W/_ref/gate-out.txt"; } | drop_pass_lines | norm_fails > "$out"  # completo: se compara entero
    [ -s "$out" ] || tail -n 30 "$W/_ref/gate-out.txt" | norm_fails > "$out"
  fi
  return $t
}

# Compuerta incremental (misiones con «Compuerta: incremental»): foto de los fallos ANTES de que AGY trabaje.
# Usa la foto que dejó el commit anterior del loop si el worktree no tenía cambios ajenos; si no, corre la compuerta una vez.
gate_base_take() {  # $1 worktree, $2 misión. La base describe el ÚLTIMO COMMIT, no el worktree sucio
  # (restos sin commitear de un intento escalado, por ejemplo): foto del commit si existe; si no, compuerta en un checkout limpio.
  [ "$(mission_compuerta "$2")" = incremental ] || return 0
  local W="$1" key tmp; key=$(git -C "$W" rev-parse HEAD)
  if [ -f "$W/_ref/.gate-at-$key.txt" ]; then
    cp "$W/_ref/.gate-at-$key.txt" "$W/_ref/.gate-base.txt"; log "compuerta incremental: base de fallos tomada del commit $key"; return 0
  fi
  if [ -z "$(changed_files "$W")" ]; then
    log "compuerta incremental: corro la compuerta una vez para la base de fallos"; gate_run "$W" "$W/_ref/.gate-base.txt"
  else
    tmp=$(mktemp -d); log "compuerta incremental: el worktree tiene cambios sin commitear; la base sale de un checkout limpio de $key"
    # KIT_TEST_FAIL_CLEAN_CHECKOUT simula la falla en los tests del kit.
    if [ -z "${KIT_TEST_FAIL_CLEAN_CHECKOUT:-}" ] && git -C "$W" worktree add -q --detach "$tmp/h" HEAD 2>/dev/null; then
      mkdir -p "$tmp/h/_ref"; gate_run "$tmp/h" "$W/_ref/.gate-base.txt"
      git -C "$W" worktree remove --force "$tmp/h" 2>/dev/null
    else
      # Una base tomada del árbol sucio haría pasar defectos nuevos como preexistentes: sin base, se exige verde total.
      rm -f "$W/_ref/.gate-base.txt"; rm -rf "$tmp"
      log "AVISO: no pude crear el checkout limpio de $key; sin base confiable, esta misión exige verde total"; return 0
    fi
    rm -rf "$tmp"
  fi
  cp "$W/_ref/.gate-base.txt" "$W/_ref/.gate-at-$key.txt"
}

# Compuerta: GATE_CMD + control de integridad. 0 = verde, 1 = rojo, 2 = violación de integridad (S3).
# Incremental: verde si no aparecen fallos nuevos frente a la base Y los tests del par («Tests bloqueados») pasan
# con GATE_FILE_CMD. El cierre de la feature (holdout + revisión) exige la compuerta total en verde.
# Tests inestables: si la compuerta queda en rojo con pocos fallos, se repiten una vez (solo los archivos de test que
# fallaron, con GATE_FILE_CMD; si no se pueden identificar, la compuerta entera). Lo que no reaparece es inestable:
# se registra en _ref/inestables.txt y no cuenta. Devuelve 0 si todo resultó inestable. FLAKY_RERUN=0 lo desactiva.
flaky_filter() {  # $1 worktree
  local W="$1" n files="" f cmd t2 prot
  [ "${FLAKY_RERUN:-1}" = 1 ] || return 1
  n=$(sed '/^$/d' "$W/_ref/gate-fallos.txt" | wc -l); [ "$n" -ge 1 ] && [ "$n" -le "${FLAKY_MAX:-8}" ] || return 1
  prot=$(printf '%s' "$PROTECTED" | to_lines)
  while IFS= read -r f; do
    f="${f//\\//}"; [ -f "$W/$f" ] && matches_any "$f" "$prot" && is_test_file "$f" && case " $files " in *" $f "*) ;; *) files="$files $f";; esac
  done < <(grep -oE '[A-Za-z0-9_.\\/-]+\.(py|mjs|cjs|js|ts|tsx)' "$W/_ref/gate-fallos.txt" | sort -u)
  files="${files# }"
  cp "$W/_ref/gate-fallos.txt" "$W/_ref/gate-fallos-1.txt"; cp "$W/_ref/gate-out.txt" "$W/_ref/gate-out-1.txt"
  if [ -n "$files" ] && [ -n "${GATE_FILE_CMD:-}" ]; then
    log "compuerta en rojo con $n fallos: repito una vez los tests que fallaron ($files) por si son inestables"
    cmd="${GATE_FILE_CMD//\{files\}/$files}"
    gate_command "$W" "$cmd" "$W/_ref/gate-out-2.txt"; t2=$?
    # shellcheck disable=SC2086
    [ -n "$GATE_RESTORE" ] && (cd "$W" && git checkout -q -- $GATE_RESTORE 2>/dev/null)
    if [ $t2 -eq 0 ]; then
      { echo "# $(date '+%Y-%m-%d %H:%M') · fallos que no se repitieron (inestables):"; cat "$W/_ref/gate-fallos-1.txt"; } >> "$W/_ref/inestables.txt"
      : > "$W/_ref/gate-fallos.txt"; log "los $n fallos no se repitieron: inestables (ver _ref/inestables.txt); compuerta en verde"; return 0
    fi
    grep '^FAIL: compuerta colgada' "$W/_ref/gate-out-2.txt" >> "$W/_ref/gate-fallos.txt"
    return 1
  fi
  log "compuerta en rojo con $n fallos: repito la compuerta una vez por si son inestables"
  gate_run "$W" "$W/_ref/gate-fallos-2.txt"; t2=$?
  [ $t2 -eq 0 ] && { { echo "# $(date '+%Y-%m-%d %H:%M') · fallos que no se repitieron (inestables):"; cat "$W/_ref/gate-fallos-1.txt"; } >> "$W/_ref/inestables.txt"
    : > "$W/_ref/gate-fallos.txt"; log "los $n fallos no se repitieron: inestables (ver _ref/inestables.txt); compuerta en verde"; return 0; }
  # Quedan solo los que se repitieron (los demás, inestables).
  new_fails "$W/_ref/gate-fallos-2.txt" "$W/_ref/gate-fallos-1.txt" > "$W/_ref/inestables-ronda.txt"
  [ -s "$W/_ref/inestables-ronda.txt" ] && { echo "# $(date '+%Y-%m-%d %H:%M') · inestables:"; cat "$W/_ref/inestables-ronda.txt"; } >> "$W/_ref/inestables.txt"
  cp "$W/_ref/gate-fallos-2.txt" "$W/_ref/gate-fallos.txt"
  return 1
}

format_gate() {  # $1 worktree, $2 misión, $3 título (opcional)
  local W="$1" M="$2" title="${3:-## Fallos de la compuerta (tests y QA)}"
  local py_w="$W" py_m="$M"
  if command -v cygpath >/dev/null 2>&1; then
    py_w=$(cygpath -m "$W"); py_m=$(cygpath -m "$M")
  fi
  python -X utf8 -c '
import os, re, sys

w_dir = sys.argv[1]
m_file = sys.argv[2]
title = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else "## Fallos de la compuerta (tests y QA)"
fallos_path = os.path.join(w_dir, "_ref", "gate-fallos.txt")
gate_md_path = os.path.join(w_dir, "_ref", "gate.md")
mision_tests_path = os.path.join(w_dir, "_ref", "mision-tests.txt")
gate_out_path = os.path.join(w_dir, "_ref", "gate-out.txt")
if not os.path.exists(gate_out_path):
    for alt in ["gate-par-out.txt", "gate-out-1.txt", "gate-out-2.txt"]:
        p = os.path.join(w_dir, "_ref", alt)
        if os.path.exists(p):
            gate_out_path = p
            break

def norm_path(p):
    return p.replace("\\", "/").strip().lstrip("./")

mision_files = set()
if os.path.exists(mision_tests_path):
    with open(mision_tests_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = norm_path(line)
            if line:
                mision_files.add(line)

actualizar_expectativa = set()
if os.path.exists(m_file):
    with open(m_file, "r", encoding="utf-8", errors="replace") as f:
        m_content = f.read()

    m = re.search(r"^Archivos:\s*(.*)", m_content, re.M)
    if m:
        for part in re.split(r"[, \t\r\n]+", m.group(1)):
            part = norm_path(part)
            if part and part.lower() != "ninguno":
                mision_files.add(part)

    m = re.search(r"^Tests bloqueados:\s*(.*)", m_content, re.M)
    if m:
        for part in re.split(r"[, \t\r\n]+", m.group(1)):
            part = norm_path(part)
            if part and part.lower() != "ninguno":
                mision_files.add(part)

    m = re.search(r"^Tests(\s*\([^)]*\))?:\s*(.*)", m_content, re.M)
    if m:
        for part in re.split(r"[, \t\r\n]+", m.group(2)):
            part = norm_path(part)
            if part and part.lower() != "ninguno":
                mision_files.add(part.split("::")[0])

    m = re.search(r"^Tests heredados afectados:\s*(.*?)(?=^\S[^:\n]*:\s|^## |\Z)", m_content, re.S | re.M)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line or line.lower() == "ninguno":
                continue
            if re.search(r"actualizar\s+expectativa", line, re.I):
                test_spec = re.split(r"[—–\-]", line)[0].strip().lstrip("-* ").strip("`")
                if test_spec:
                    norm_spec = norm_path(test_spec)
                    actualizar_expectativa.add(norm_spec)
                    actualizar_expectativa.add(norm_spec.split("::")[0])

fallos = []
if os.path.exists(fallos_path):
    with open(fallos_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                fallos.append(line)

gate_out_lines = []
if os.path.exists(gate_out_path):
    with open(gate_out_path, "r", encoding="utf-8", errors="replace") as f:
        gate_out_lines = [l.rstrip("\r\n") for l in f]

def find_e_line(fail_str):
    m = re.search(r"([a-zA-Z0-9_./\\-]+\.[a-zA-Z0-9]+)(?:::([a-zA-Z0-9_]+))?", fail_str)
    file_part = m.group(1) if m else ""
    func_part = m.group(2) if m else ""

    if func_part:
        sec_re = re.compile(r"_{3,}\s*(?:.*[ .])?" + re.escape(func_part) + r"\s*_{3,}")
        in_sec = False
        for l in gate_out_lines:
            if sec_re.search(l):
                in_sec = True
                continue
            if in_sec:
                if re.match(r"_{3,}", l) or re.match(r"={3,}", l):
                    break
                em = re.match(r"^\s*(E\s+.*|E:.*)$", l)
                if em:
                    return em.group(1).strip()

    target_pats = []
    if func_part:
        target_pats.append(re.compile(re.escape(func_part)))
    if file_part:
        target_pats.append(re.compile(re.escape(norm_path(file_part))))
    target_pats.append(re.compile(re.escape(fail_str.strip())))

    for idx, l in enumerate(gate_out_lines):
        norm_l = norm_path(l)
        matched = False
        for pat in target_pats:
            if pat.search(l) or pat.search(norm_l):
                matched = True
                break
        if matched:
            for forward in gate_out_lines[idx+1:idx+36]:
                if re.search(r"^(FAIL|FAILED)\b", forward) and not re.search(r"^\s*E\b", forward):
                    break
                em = re.match(r"^\s*(E\s+.*|E:.*)$", forward)
                if em:
                    return em.group(1).strip()
    return None

def is_mision_test(fail_str):
    norm_fail = norm_path(fail_str)
    for mf in mision_files:
        if mf and (mf in norm_fail or norm_fail in mf):
            return True
    for ae in actualizar_expectativa:
        if ae and (ae in norm_fail or norm_fail in ae):
            return True
    m = re.search(r"[a-zA-Z0-9_./\\-]+\.[a-zA-Z0-9]+", fail_str)
    if m:
        f = norm_path(m.group(0))
        if f in mision_files or f in actualizar_expectativa:
            return True
        base = os.path.basename(f)
        for mf in mision_files:
            if os.path.basename(mf) == base:
                return True
    return False

mision_items = []
regresion_items = []
for fail_str in fallos[:60]:
    e_line = find_e_line(fail_str)
    item = {"fail": fail_str, "e": e_line}
    if is_mision_test(fail_str):
        mision_items.append(item)
    else:
        regresion_items.append(item)

out_lines = [title, ""]
if mision_items:
    out_lines.append("### Tests de la misión (cambio pedido)")
    for it in mision_items:
        fail_txt = it["fail"]
        out_lines.append("- " + fail_txt)
        if it["e"]:
            out_lines.append("  " + it["e"])
    out_lines.append("")

if regresion_items:
    out_lines.append("### Regresiones (tests ajenos a la misión que rompiste)")
    for it in regresion_items:
        fail_txt = it["fail"]
        out_lines.append("- " + fail_txt)
        if it["e"]:
            out_lines.append("  " + it["e"])
    out_lines.append("")

if not mision_items and not regresion_items and fallos:
    out_lines.append("### Tests de la misión (cambio pedido)")
    for fail_str in fallos[:60]:
        out_lines.append("- " + fail_str)
    out_lines.append("")

if len(fallos) > 60:
    out_lines.append("… (más fallos en _ref/gate-fallos.txt)")
    out_lines.append("")

content = "\n".join(out_lines).rstrip() + "\n"
with open(gate_md_path, "w", encoding="utf-8") as f:
    f.write(content)
' "$py_w" "$py_m" "$title"
}

gate() {
  local W="$1" M="$2" t new pair cmd p
  GATE_KIND=full
  gate_run "$W" "$W/_ref/gate-fallos.txt"; t=$?
  [ $t -ne 0 ] && [ "$(mission_field "$M" Tipo)" != tests ] && [ "$(mission_compuerta "$M")" != incremental ] && flaky_filter "$W" && t=0
  format_gate "$W" "$M" "## Fallos de la compuerta (tests y QA)"
  if ! integrity "$W" "$M"; then
    { echo; echo "## Control de integridad (S3)"; cat "$W/_ref/integrity.md"; } >> "$W/_ref/gate.md"; return 2
  fi
  # Misión de tests: el rojo es esperado; la compuerta es informativa y Codex juzga si falla por la razón correcta.
  # Con INVARIANT_CHECK=1 en repo.env, los invariantes de los tests de la misión tienen que pasar HOY (sobre la base).
  [ "$(mission_field "$M" Tipo)" = tests ] && { invariant_check "$W" "$M"; return $?; }
  # tests-fix sobre un test de defecto todavía no implementado («Defecto pendiente: sí»): sus invariantes tienen que pasar,
  # la parte de defecto puede seguir en rojo, y no puede aparecer ningún fallo nuevo en el resto.
  local pend=""
  if [ "$(mission_field "$M" Tipo)" = tests-fix ] && mission_field "$M" "Defecto pendiente" | grep -qiE '^s'; then
    pend=1; invariant_check "$W" "$M" || return 1
  fi
  [ $t -eq 0 ] && return 0
  [ "$(mission_compuerta "$M")" = incremental ] || return 1
  if [ ! -f "$W/_ref/.gate-base.txt" ]; then log "compuerta incremental sin base de fallos: se exige verde total"; return 1; fi
  new=$(new_fails "$W/_ref/.gate-base.txt" "$W/_ref/gate-fallos.txt")
  pair=$(mission_test_files "$W" "$M")
  if [ -z "${GATE_FILE_CMD:-}" ] || [ -z "$pair" ]; then
    log "compuerta incremental no aplicable (falta GATE_FILE_CMD en repo.env o «Tests bloqueados» en la misión): se exige verde total"; return 1
  fi
  cmd="${GATE_FILE_CMD//\{files\}/$pair}"
  gate_command "$W" "$cmd" "$W/_ref/gate-par-out.txt"; p=$?
  [ -n "$GATE_RESTORE" ] && (cd "$W" && git checkout -q -- $GATE_RESTORE 2>/dev/null)
  [ -n "$pend" ] && { [ $p -ne 0 ] && log "defecto pendiente: los tests de la misión siguen en rojo a propósito (sus invariantes ya pasaron)"; p=0; }
  # Inestables: si algo quedó en rojo, se repite una vez; lo que no reaparece no cuenta (queda en _ref/inestables.txt).
  if [ "${FLAKY_RERUN:-1}" = 1 ] && { [ -n "$new" ] || [ $p -ne 0 ]; }; then
    log "compuerta incremental en rojo: repito una vez por si es inestable"
    if [ $p -ne 0 ]; then
      gate_command "$W" "$cmd" "$W/_ref/gate-par-out.txt"; p=$?
      [ -n "$GATE_RESTORE" ] && (cd "$W" && git checkout -q -- $GATE_RESTORE 2>/dev/null)
    fi
    if [ -n "$new" ]; then
      printf '%s\n' "$new" > "$W/_ref/nuevos-1.txt"
      gate_run "$W" "$W/_ref/gate-fallos-2.txt"
      new_fails "$W/_ref/.gate-base.txt" "$W/_ref/gate-fallos-2.txt" > "$W/_ref/nuevos-2.txt"
      new_fails "$W/_ref/nuevos-2.txt" "$W/_ref/nuevos-1.txt" > "$W/_ref/inestables-ronda.txt"
      [ -s "$W/_ref/inestables-ronda.txt" ] && { echo "# $(date '+%Y-%m-%d %H:%M') · inestables (incremental):"; cat "$W/_ref/inestables-ronda.txt"; } >> "$W/_ref/inestables.txt"
      new=$(new_fails "$W/_ref/inestables-ronda.txt" "$W/_ref/nuevos-1.txt")
      cp "$W/_ref/gate-fallos-2.txt" "$W/_ref/gate-fallos.txt"
    fi
  fi
  if [ -z "$new" ] && [ $p -eq 0 ]; then
    log "compuerta incremental en verde: tests del par OK y sin fallos nuevos ($(wc -l < "$W/_ref/gate-fallos.txt") fallos previos siguen)"; return 0
  fi
  { printf '%s\n' "$new"; [ $p -ne 0 ] && { grep -E "$GATE_GREP" "$W/_ref/gate-par-out.txt"; grep '^FAIL: compuerta colgada' "$W/_ref/gate-par-out.txt"; } | norm_fails | head -n 30; [ $p -ne 0 ] && echo "Fallan los tests del par: $pair"; } | sed '/^$/d' > "$W/_ref/gate-fallos.txt"
  format_gate "$W" "$M" "## Compuerta incremental en rojo"
  return 1
}

# Chequeo de invariantes de una misión de tests: corre los tests de la misión con INVARIANT_CHECK=1 (cada test corta
# antes de sus aserciones de defecto) y exige verde. Una referencia base inventada hace que el test nunca pueda pasar.
# 0 = verde o no aplica (INVARIANT_CHECK distinto de 1 en repo.env, sin GATE_FILE_CMD o sin tests tocados), 1 = rojo.
invariant_check() {
  local W="$1" M="$2" files="" f cmd t prot
  [ "${INVARIANT_CHECK:-0}" = 1 ] && [ -n "${GATE_FILE_CMD:-}" ] || return 0
  prot=$(printf '%s' "$PROTECTED" | to_lines)
  while IFS= read -r f; do [ -n "$f" ] && [ -f "$W/$f" ] && matches_any "$f" "$prot" && is_test_file "$f" && files="$files $f"; done < <(changed_files "$W")
  files="${files# }"; [ -n "$files" ] || return 0
  cmd="${GATE_FILE_CMD//\{files\}/$files}"
  INVARIANT_CHECK=1 gate_command "$W" "$cmd" "$W/_ref/invariantes-out.txt"; t=$?
  # shellcheck disable=SC2086
  [ -n "$GATE_RESTORE" ] && (cd "$W" && git checkout -q -- $GATE_RESTORE 2>/dev/null)
  [ $t -eq 0 ] && { log "invariantes en verde sobre la base ($files)"; return 0; }
  { grep -E "$GATE_GREP" "$W/_ref/invariantes-out.txt" | grep -vE "${GATE_IGNORE:-^[[:space:]]*(✔|ok [0-9])}"
    grep '^FAIL: compuerta colgada' "$W/_ref/invariantes-out.txt"; } | drop_pass_lines | norm_fails > "$W/_ref/gate-fallos.txt"
  [ -s "$W/_ref/gate-fallos.txt" ] || tail -n 30 "$W/_ref/invariantes-out.txt" | norm_fails > "$W/_ref/gate-fallos.txt"
  { echo "## Invariantes en rojo sobre la base (INVARIANT_CHECK=1)"
    echo "Las aserciones de invariante tienen que pasar HOY: medí sus referencias base del estado actual. El rojo tiene que venir solo de las aserciones de defecto."
    head -n 60 "$W/_ref/gate-fallos.txt"; } > "$W/_ref/gate.md"
  log "invariantes en rojo sobre la base: vuelve a AGY"; return 1
}

# Compuerta rápida para rondas intermedias de AGY (misiones de implementación): solo los tests de la misión
# («Tests bloqueados» + tests tocados) con GATE_FILE_CMD, más el control de integridad. Si queda en verde,
# el loop corre la compuerta completa antes de llamar a Codex. 0 verde, 1 rojo, 2 integridad, 3 no aplica.
fast_gate() {
  local W="$1" M="$2" files cmd t
  [ -n "${GATE_FILE_CMD:-}" ] || return 3
  # Con un defecto pendiente, los tests de la misión siguen en rojo a propósito: decide la compuerta completa.
  mission_field "$M" "Defecto pendiente" | grep -qiE '^s' && return 3
  files=$(mission_test_files "$W" "$M"); [ -n "$files" ] || return 3
  GATE_KIND=fast
  cmd="${GATE_FILE_CMD//\{files\}/$files}"
  gate_command "$W" "$cmd" "$W/_ref/gate-out.txt"; t=$?
  # shellcheck disable=SC2086
  [ -n "$GATE_RESTORE" ] && (cd "$W" && git checkout -q -- $GATE_RESTORE 2>/dev/null)
  : > "$W/_ref/gate-fallos.txt"
  if [ $t -ne 0 ]; then
    { grep -E "$GATE_GREP" "$W/_ref/gate-out.txt" | grep -vE "${GATE_IGNORE:-^[[:space:]]*(✔|ok [0-9])}"
      grep '^FAIL: compuerta colgada' "$W/_ref/gate-out.txt"; } | drop_pass_lines | norm_fails > "$W/_ref/gate-fallos.txt"
    [ -s "$W/_ref/gate-fallos.txt" ] || tail -n 30 "$W/_ref/gate-out.txt" | norm_fails > "$W/_ref/gate-fallos.txt"
  fi
  format_gate "$W" "$M" "## Compuerta rápida (solo los tests de la misión: $files)"
  if ! integrity "$W" "$M"; then
    { echo; echo "## Control de integridad (S3)"; cat "$W/_ref/integrity.md"; } >> "$W/_ref/gate.md"; return 2
  fi
  [ $t -eq 0 ] && { log "compuerta rápida en verde ($files): corro la compuerta completa"; return 0; }
  return 1
}

# Codex con reintento si está en límite. $1 sandbox, $2 esfuerzo, $3 salida, $4 cwd, $5 prompt, $6 log
codex_run() {
  local sb="$1" eff="$2" out="$3" cwd="$4" pf="$5" lg="$6" i
  # Codex rechaza prompts que no son UTF-8 válido (salidas de herramientas en cp1252, binarios): se reemplazan los bytes inválidos.
  python -X utf8 -c "import sys; p=sys.argv[1]; d=open(p,'rb').read().decode('utf-8','replace'); open(p,'w',encoding='utf-8',newline='').write(d)" "$pf"
  for i in $(seq 1 12); do
    rm -f "$out"
    # Temporales propios FUERA del repo, habilitados con --add-dir: el sandbox de Codex no puede escribir en el TEMP del
    # usuario (pytest-of-<usuario>) y hay tests que exigen un temporal fuera del repo.
    local tmpd="${CODEX_TMP_ROOT:-$HOME/.cache/agent-workflow-tmp}/$(basename "$cwd")"
    mkdir -p "$tmpd" 2>/dev/null
    TMP="$tmpd" TEMP="$tmpd" TMPDIR="$tmpd" "${CODEX_CMD[@]}" exec --skip-git-repo-check -C "$cwd" -s "$sb" --add-dir "$tmpd" -c model_reasoning_effort="$eff" ${CODEX_MODEL:+-m "$CODEX_MODEL"} \
      --output-last-message "$out" - < "$pf" >> "$lg" 2>&1
    [ -s "$out" ] && return 0
    if tail -n 30 "$lg" | grep -qiE "usage limit|rate limit|hit your"; then
      local py_lg="$lg" limit_date=""
      command -v cygpath >/dev/null 2>&1 && py_lg=$(cygpath -m "$lg")
      limit_date=$(python -c '
import sys, re
from datetime import datetime, timedelta

max_min = float(sys.argv[1])
try:
    with open(sys.argv[2], "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
except Exception:
    sys.exit(0)

m = re.search(r"try again at\s+([^.\n\r]+)", text, re.I)
if not m:
    sys.exit(0)

raw_date = m.group(1).strip().rstrip(".")
cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw_date)
now = datetime.now()
target = None

for fmt in ("%b %d, %Y %I:%M %p", "%b %d %Y %I:%M %p", "%B %d, %Y %I:%M %p", "%B %d %Y %I:%M %p",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%b %d, %Y %H:%M", "%b %d %Y %H:%M"):
    try:
        target = datetime.strptime(cleaned, fmt)
        break
    except ValueError:
        pass

if not target:
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            t = datetime.strptime(cleaned, fmt)
            target = datetime.combine(now.date(), t.time())
            if target < now - timedelta(minutes=5):
                target += timedelta(days=1)
            break
        except ValueError:
            pass

if target:
    diff_min = (target - now).total_seconds() / 60.0
    if diff_min > max_min:
        print(raw_date)
' "${CODEX_WAIT_MAX_MIN:-120}" "$py_lg" 2>/dev/null)

      if [ -n "$limit_date" ]; then
        log "Codex sin cuota hasta $limit_date: no espero"
        [ -n "${CODEX_LIMIT_RETURN:-}" ] && return 9
        return 1
      fi
      # Con EXECUTOR=auto no se espera: el loop pasa a AGY (código 9).
      [ -n "${CODEX_LIMIT_RETURN:-}" ] && { log "Codex en límite: no espero (EXECUTOR=auto)"; return 9; }
      log "Codex en límite: espero $((CODEX_WAIT/60)) min"; sleep "$CODEX_WAIT"; continue
    fi
    return 1
  done
  return 1
}
verdict_ok() { head -n 8 "$1" | grep -m1 -oE 'VEREDICTO: (APROBADO|CAMBIOS)' | grep -q APROBADO; }
effort_for() { printf '%s' "$1" | grep -qiE "$CODEX_HIGH_REGEX" && echo high || echo medium; }

templ() {  # plantilla: la del kit que corre (integración), después la del worktree, después la del kit global
  local W="$1" n="$2"
  for d in "$KIT_BIN/templates" "$W/tools/agents/templates" "$KIT_HOME/templates"; do [ -f "$d/$n" ] && { cat "$d/$n"; return 0; }; done
  die "falta la plantilla $n"
}

metrics_row() {  # fecha,repo,feature,mision,tipo,clase,ejecutor,rondas_agy,reintento_limpio,revisiones_codex,senal,salida,minutos,auditoria,carril,minutos_desde_pedido
  local H="fecha,repo,feature,mision,tipo,clase,ejecutor,rondas_agy,reintento_limpio,revisiones_codex,senal,salida,minutos,auditoria,carril,minutos_desde_pedido"
  mkdir -p "$(dirname "$METRICS")"
  [ -s "$METRICS" ] || echo "$H" > "$METRICS"
  head -n 1 "$METRICS" | grep -q ',carril,' || { { echo "$H"; tail -n +2 "$METRICS"; } > "$METRICS.tmp" && mv "$METRICS.tmp" "$METRICS"; }
  local IFS=,; local row=("$@"); local i
  for i in "${!row[@]}"; do row[$i]="${row[$i]//,/;}"; done
  echo "${row[*]}" >> "$METRICS"
}

# Contexto de la feature para el ejecutor: copia spec.md, plan.md y datos (NUNCA holdout/) a <worktree>/_ref/feature/,
# porque en modo headless no pueden leer fuera de su worktree. Imprime la nota para la misión.
feature_ctx() {  # $1 worktree, $2 misión
  local feat F f df data_files=()
  feat=$(mission_field "$2" Feature); case "$feat" in ""|"—"|"-"|"<"*) return 0;; esac
  F="$FEATURES_DIR/$feat"; [ -d "$F" ] || return 0
  mkdir -p "$1/_ref/feature"
  for f in spec.md plan.md exploracion.md casos-limite.md; do [ -s "$F/$f" ] && cp "$F/$f" "$1/_ref/feature/$f"; done
  for f in "$F"/*.json "$F"/*.csv "$F"/*.yaml "$F"/*.yml; do
    [ -f "$f" ] || continue
    df=$(basename "$f")
    cp "$f" "$1/_ref/feature/$df"
    data_files+=("$df")
  done
  [ -s "$1/_ref/feature/spec.md" ] || [ -s "$1/_ref/feature/plan.md" ] || [ -s "$F/casos-limite.md" ] || [ ${#data_files[@]} -gt 0 ] || return 0
  if [ -s "$1/_ref/feature/spec.md" ] || [ -s "$1/_ref/feature/plan.md" ] || [ ${#data_files[@]} -gt 0 ]; then
    printf '\n## Contexto de la feature «%s»\n' "$feat"
    if [ -s "$1/_ref/feature/spec.md" ] || [ -s "$1/_ref/feature/plan.md" ]; then
      printf 'La spec y el plan están en `_ref/feature/` (spec.md, plan.md) dentro de tu worktree. Leelos si la misión los cita; no los modifiques.\n'
    fi
    for df in "${data_files[@]}"; do
      echo "Datos de la feature: _ref/feature/$df"
    done
  fi
  if [ -s "$F/casos-limite.md" ]; then
    printf '\n## Casos límite del plan (revisión de Codex)\n'
    cat "$F/casos-limite.md"
    echo
  fi
}

# ¿El agente dejó algún cambio propio (fuera de lo que ya estaba en la foto base)?
agent_changed() { local f; while IFS= read -r f; do [ -z "$f" ] && continue; snap_same "$1" base "$f" || return 0; done < <(changed_files "$1"); return 1; }
agy_denied() { grep -q '"denied": true' "$1/_ref/status.txt" 2>/dev/null; }
# ¿En la última corrida de AGY se le negó un comando de shell para leer o buscar (en vez de usar sus herramientas)?
READ_CMD_REGEX='(^|[^[:alnum:]_-])(Select-String|Get-Content|Get-ChildItem|grep|rg|cat|type|ls|dir|findstr|head|tail|find|sed|awk)([^[:alnum:]_-]|$)'
denied_read_cmd() {  # mira solo la última corrida (desde la última línea «AGY arrancó») y solo el comando denegado
  local lg="$1/_ref/run-loop.log" n
  [ -f "$lg" ] || return 1
  n=$(grep -n 'AGY arrancó' "$lg" | tail -n 1 | cut -d: -f1); n=${n:-1}
  tail -n +"$n" "$lg" | tr -d '\r' | grep 'auto-denied' | sed -n 's/.*run_command ERROR · \(.*\) · auto-denied.*/\1/p' | grep -qiE "$READ_CMD_REGEX"
}
real_worktree_path() {  # $1 worktree
  local w="${1:-${W:-.}}"
  [ -d "$w" ] && w="$(cd "$w" 2>/dev/null && pwd -P || echo "$w")"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$w" 2>/dev/null || echo "$w"
  else
    echo "$w"
  fi
}
read_tools_note() {  # $1 worktree
  local wt; wt=$(real_worktree_path "${1:-${W:-.}}")
  printf '## Corrección: usá tus herramientas de lectura\nEn la ronda anterior intentaste un comando que no está en «Comandos permitidos» y se te negó: no dejaste cambios.\nNo vuelvas a intentar comandos fuera de esa lista (ni git diff/log con argumentos, ni pytest -k, ni Select-String/grep/cat). Para leer y buscar usá SOLO tus herramientas (view_file, grep_search, find_by_name, list_dir); para verificar, corré exactamente la compuerta permitida. Hacé la misión completa.\nTu worktree es %s: toda ruta (incluida _ref/feature/) es relativa a esa carpeta, no a la raíz del repo.\n' "$wt"
}
READ_TOOLS_NOTE=$(read_tools_note "${W:-.}")

denied_exit() {  # sin cambios y con permisos denegados: falla del sistema, la resuelve el coordinador (salida 7)
  senal=PERMISO
  { echo "# Permisos denegados (salida 7)"; echo; echo "AGY no dejó cambios y tuvo acciones denegadas en modo headless. No se escala: lo resuelve el coordinador (allowlist o misión)."
    echo; grep -E 'auto-denied' "$1/_ref/run-loop.log" | tail -n 5; } > "$1/_ref/escalamiento.md"
  log "AGY sin cambios y con permisos denegados: salida 7 (ver _ref/escalamiento.md)"
}

# Mapa de archivos grandes de «Archivos:» (secciones con número de línea) en _ref/mapa-archivos.md, para que AGY
# lea por rangos y no relea archivos enteros. Imprime la nota para la misión (vacía si no hay archivos grandes).
file_map() {  # $1 worktree, $2 misión
  local W="$1" M="$2" f n out="$1/_ref/mapa-archivos.md" any=""
  : > "$out"
  while IFS= read -r f; do
    [ -f "$W/$f" ] || continue
    n=$(wc -l < "$W/$f"); [ "$n" -ge "${MAP_MIN_LINES:-300}" ] || continue
    any=1
    { echo "## $f ($n líneas)"
      case "$f" in
        *.css|*.scss) grep -nE '^@media|^[^[:space:]@}/][^{;]*\{' "$W/$f" ;;
        *.py) grep -nE '^[[:space:]]*(class|def|async def) ' "$W/$f" ;;
        *.js|*.mjs|*.cjs|*.ts|*.tsx|*.jsx) grep -nE '^[[:space:]]*(export |function |class |(const|let) [A-Za-z_$][A-Za-z0-9_$]* = (\(|async|function))' "$W/$f" ;;
        *.md) grep -nE '^#{1,3} ' "$W/$f" ;;
        *.html) grep -nE '<(section|main|header|footer|nav|template)|id="' "$W/$f" ;;
        *) grep -nE '^[^[:space:]]' "$W/$f" ;;
      esac | head -n 400 | sed 's/^/- /'; echo; } >> "$out"
  done < <(mission_list "$M" Archivos)
  [ -n "$any" ] || return 0
  printf '\n## Mapa de archivos grandes\nAntes de leer un archivo grande, mirá `_ref/mapa-archivos.md`: tiene sus secciones con número de línea. Leé solo los rangos que necesitás (view_file con StartLine/EndLine) y no vuelvas a leer lo que ya leíste en esta ronda.\n'
}

# Archivos de test de la misión: «Tests bloqueados» que existen + archivos protegidos tocados (tests nuevos o cambiados).
mission_test_files() {  # $1 worktree, $2 misión → lista separada por espacios
  local W="$1" M="$2" files="" f prot
  prot=$(printf '%s' "$PROTECTED" | to_lines)
  while IFS= read -r f; do
    [ -n "$f" ] && [ -f "$W/$f" ] && is_test_file "$f" && case " $files " in *" $f "*) ;; *) files="$files $f";; esac
  done < <(mission_list "$M" "Tests bloqueados"; changed_files "$W" | while IFS= read -r f; do matches_any "$f" "$prot" && echo "$f"; done)
  printf '%s' "${files# }"
}

# Escribe _ref/mision-tests.txt con los tests de la misión («Tests bloqueados», «Tests» y los tests protegidos que mencionan módulos de «Archivos:», tope 15).
write_mission_tests() {  # $1 worktree, $2 misión
  local W="$1" M="$2" out="$1/_ref/mision-tests.txt"
  mkdir -p "$W/_ref"
  local collected=()
  local tf
  local add_test_file
  add_test_file() {
    local f="$1"
    f="${f//\\//}"
    f="${f%%::*}"
    f=$(printf '%s' "$f" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    [ -z "$f" ] && return
    is_test_file "$f" || return
    if [ ${#collected[@]} -gt 0 ]; then
      case " ${collected[*]} " in
        *" $f "*) return ;;
      esac
    fi
    collected+=("$f")
  }

  while IFS= read -r tf; do
    add_test_file "$tf"
  done < <({ mission_list "$M" "Tests bloqueados"; mission_list "$M" "Tests"; mission_list "$M" "Tests (rojos primero)"; })

  local prot
  prot=$(printf '%s' "$PROTECTED" | to_lines)
  local all_tests=()
  while IFS= read -r tf; do
    [ -z "$tf" ] && continue
    [ -f "$W/$tf" ] && matches_any "$tf" "$prot" && is_test_file "$tf" && all_tests+=("$tf")
  done < <({ git -C "$W" ls-files 2>/dev/null; git -C "$W" ls-files --others --exclude-standard 2>/dev/null; } | grep -v '^_ref/' | sort -u)

  local af base mod matched
  if [ ${#all_tests[@]} -gt 0 ]; then
    while IFS= read -r af; do
      [ -z "$af" ] && continue
      base=$(basename "$af")
      mod="${base%.*}"
      [ -n "$mod" ] || continue
      while IFS= read -r matched; do
        add_test_file "$matched"
      done < <((cd "$W" && grep -l -F -- "$mod" "${all_tests[@]}" 2>/dev/null))
    done < <(mission_list "$M" Archivos)
  fi

  : > "$out"
  local count=0
  if [ ${#collected[@]} -gt 0 ]; then
    for tf in "${collected[@]}"; do
      [ $count -ge 15 ] && break
      echo "$tf" >> "$out"
      count=$((count+1))
    done
  fi
}

# Escribe PID y hora de creación en un archivo .pid. $1 = archivo, $2 = msys_pid
pid_write() {  # $1 archivo, $2 msys_pid
  local f="$1" msys_pid="$2" py_proc="$KIT_BIN/proc_id.py" winpid="" hora=""
  msys_pid=$(printf '%s' "$msys_pid" | tr -d '\r\n ')
  if [ -f "/proc/$msys_pid/winpid" ]; then
    winpid=$(tr -d '\r\n ' < "/proc/$msys_pid/winpid" 2>/dev/null)
  fi
  command -v cygpath >/dev/null 2>&1 && py_proc=$(cygpath -m "$py_proc")
  if [ -z "$winpid" ]; then
    winpid=$(python "$py_proc" --self-winpid "$msys_pid" 2>/dev/null | tr -d '\r\n ')
  fi
  [ -n "$winpid" ] || winpid="$msys_pid"
  hora=$(python "$py_proc" "$winpid" 2>/dev/null | tr -d '\r\n ') || return 1
  [ -n "$hora" ] || return 1
  mkdir -p "$(dirname "$f")" 2>/dev/null
  echo "$winpid $hora" > "$f"
}

# Verifica que una línea "PID HORA" pertenezca al proceso original (PID y hora coinciden).
# Devuelve 0 e imprime el PID; devuelve 1 en cualquier otro caso.
pid_line_ours() {  # $1 = "PID HORA" o ($1 = PID, $2 = HORA)
  local pid hora extra cur_hora py_proc="$KIT_BIN/proc_id.py"
  if [ $# -ge 2 ]; then
    pid="$1"; hora="$2"; extra="${3:-}"
  else
    read -r pid hora extra <<< "$1"
  fi
  pid=$(printf '%s' "$pid" | tr -d '\r')
  hora=$(printf '%s' "$hora" | tr -d '\r')
  extra=$(printf '%s' "$extra" | tr -d '\r')
  [ -n "$pid" ] && [ -n "$hora" ] && [ -z "$extra" ] || return 1
  command -v cygpath >/dev/null 2>&1 && py_proc=$(cygpath -m "$py_proc")
  cur_hora=$(python "$py_proc" "$pid" 2>/dev/null | tr -d '\r') || return 1
  [ -n "$cur_hora" ] || return 1
  [ "$cur_hora" = "$hora" ] || return 1
  echo "$pid"
  return 0
}

# Verifica que el archivo .pid pertenezca al proceso original (PID y hora coinciden).
# También acepta directamente una línea "PID HORA".
# Devuelve 0 e imprime el PID; devuelve 1 en cualquier otro caso.
pid_ours() {  # $1 archivo o línea "PID HORA"
  local target="$1" line
  if [ -f "$target" ]; then
    read -r line < "$target" || return 1
    pid_line_ours "$line"
  else
    pid_line_ours "$target"
  fi
}

# Corta un proceso y todos sus hijos. $1 = pid de Windows (agy_run.pid) o de MSYS.
kill_tree() {
  [ -n "${1:-}" ] || return 0
  local pid="$1" winpid="" msys_p="" py_proc
  pid=$(printf '%s' "$pid" | tr -d '\r\n ')
  [ -n "$pid" ] || return 0

  if [ -f "/proc/$pid/winpid" ]; then
    winpid=$(tr -d '\r\n ' < "/proc/$pid/winpid" 2>/dev/null)
    msys_p="$pid"
  fi
  [ -n "$winpid" ] || winpid="$pid"

  if [ -z "$msys_p" ]; then
    if command -v ps >/dev/null 2>&1; then
      msys_p=$(ps -W 2>/dev/null | awk -v w="$winpid" '$4==w {print $1}')
    fi
    if [ -z "$msys_p" ]; then
      for f in /proc/[0-9]*/winpid; do
        if [ -f "$f" ] && [ "$(tr -d '\r\n ' < "$f" 2>/dev/null)" = "$winpid" ]; then
          msys_p="$(basename "$(dirname "$f")")"
          break
        fi
      done
    fi
  fi

  if command -v taskkill >/dev/null 2>&1; then
    MSYS_NO_PATHCONV=1 taskkill /T /F /PID "$winpid" >/dev/null 2>&1 || true
  fi
  py_proc="$KIT_BIN/proc_id.py"
  command -v cygpath >/dev/null 2>&1 && py_proc=$(cygpath -m "$py_proc")
  python "$py_proc" --kill "$winpid" 2>/dev/null || true
  if [ -n "$msys_p" ]; then
    kill -9 "$msys_p" 2>/dev/null || true
  fi
  # Sin taskkill (Linux/macOS) el número es un PID del sistema. En Windows NUNCA se usa el WINPID con kill de MSYS:
  # ese número puede ser otro proceso de MSYS.
  command -v taskkill >/dev/null 2>&1 || kill -9 "$pid" 2>/dev/null || true
  return 0
}
kill_agy() {  # corta el AGY que esté corriendo en el worktree $1 (si hay)
  local f="$1/_ref/agy_run.pid" p line
  if [ -f "$1/_ref/agy_run.pids" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      [ -z "$line" ] && continue
      if p=$(pid_ours "$line"); then
        kill_tree "$p"
      fi
    done < "$1/_ref/agy_run.pids"
    rm -f "$1/_ref/agy_run.pids"
  fi
  [ -f "$f" ] || return 0
  if p=$(pid_ours "$f"); then
    kill_tree "$p"
    rm -f "$f"
  else
    rm -f "$f"
    log "pid viejo de otro proceso: no se corta ($f)"
  fi
  return 0
}
# Candado por worktree: nunca dos loops (ni dos AGY) en el mismo worktree.
take_lock() {  # $1 worktree
  local f="$1/_ref/loop.pid" p
  if [ -f "$f" ]; then
    if p=$(pid_ours "$f"); then
      die "ya hay un loop corriendo en $1 (pid $p). Si quedó huérfano: bash tools/agents/cleanup.sh $1"
    else
      rm -f "$f"
      log "pid viejo de otro proceso: no se corta ($f)"
    fi
  fi
  pid_write "$f" "$$"
}
release_lock() {  # $1 worktree
  local f="$1/_ref/loop.pid" p
  [ -f "$f" ] || return 0
  if p=$(pid_ours "$f"); then
    rm -f "$f"
  else
    rm -f "$f"
    log "pid viejo de otro proceso: no se corta ($f)"
  fi
  return 0
}

# Antes de despachar a AGY: los «Comandos permitidos» de la misión tienen que estar en su allowlist (solo se LEE).
# Si falta alguno, deja la lista en _ref/allowlist-faltantes.txt y devuelve 1: el loop frena antes de gastar una ronda.
allowlist_check() {  # $1 worktree, $2 misión (se chequea lo que ve AGY: sin la suite completa)
  local st="${AGY_SETTINGS:-$HOME/.gemini/antigravity-cli/settings.json}"
  [ -f "$st" ] || return 0
  mission_for_agy "$2" > "$1/_ref/.mision-agy.md"
  if grep -q "bash tools/agents/mission_tests.sh" "$2"; then
    python -X utf8 "$(dirname "${BASH_SOURCE[0]}")/allowlist_check.py" "$1/_ref/.mision-agy.md" "$st" | tr -d '\r' > "$1/_ref/allowlist-faltantes.txt" 2>&1
  else
    python -X utf8 "$(dirname "${BASH_SOURCE[0]}")/allowlist_check.py" "$1/_ref/.mision-agy.md" "$st" | tr -d '\r' | grep -vxF "bash tools/agents/mission_tests.sh" > "$1/_ref/allowlist-faltantes.txt" 2>&1
  fi
  [ ! -s "$1/_ref/allowlist-faltantes.txt" ] && return 0
  return 1
}

# Comandos de «Comandos permitidos», uno por línea, con o sin comillas invertidas («- `cmd`» o «- cmd»).
mission_cmds() {
  awk '/^Comandos permitidos/ {b=1; next} b && /^- / { c=$0; sub(/^- +/, "", c); sub(/^`/, "", c); sub(/`[[:space:]]*$/, "", c); sub(/[[:space:]]+$/, "", c); print c; next } {b=0}' "$1"
}

# La misión tal como la ve AGY: con «bash tools/agents/mission_tests.sh» en «Comandos permitidos» y sin la suite completa
# (la corre el loop una sola vez; plan del 07/11/2026, punto 2). Así AGY no la corre ni se cuelga con ella. Acepta «- `cmd`» y «- cmd».
# SUITE_CMDS (repo.env, una por línea): otras formas de la suite completa que tampoco le llegan a AGY.
mission_for_agy() {
  SUITES="$GATE_CMD
${SUITE_CMDS:-}" awk 'BEGIN { n = split(ENVIRON["SUITES"], s, "\n"); for (i = 1; i <= n; i++) if (s[i] != "") suite[s[i]] = 1 }
    /^Comandos permitidos/ {
      blk = 1; print;
      print "- `bash tools/agents/mission_tests.sh`";
      next
    }
    blk && /^- / {
      c=$0; sub(/^- +/, "", c); sub(/^`/, "", c); sub(/`[[:space:]]*$/, "", c); sub(/[[:space:]]+$/, "", c);
      if (c == "bash tools/agents/mission_tests.sh") next;
      if (c in suite) next;
      print; next
    }
    blk && !/^- / { blk=0; print; next }
    { print }' "$1"
}

# Reglas de ejecución que AGY recibe en cada ronda (valen también para misiones viejas y para modelos de respaldo,
# que tienden a anteponer «cd <worktree> &&»: la allowlist compara el comando literal y lo niega).
agy_exec_rules() {  # $1 worktree (opcional)
  local wt; wt=$(real_worktree_path "${1:-${W:-.}}")
  printf '\n## Reglas de ejecución (siempre)\n- Tu worktree es %s: toda ruta (incluida _ref/feature/) es relativa a esa carpeta, no a la raíz del repo.\n- Ya estás en la raíz del worktree: NO uses `cd`.\n- Corré cada comando de «Comandos permitidos» EXACTO, uno por vez: sin prefijos, sin `;`, sin `&&`, sin `|`, sin `bash -c`.\n- Para leer y buscar usá tus herramientas, no la terminal. Si te niegan un comando, seguí con tus herramientas y terminá la misión.\n- No leas `.git` ni nada dentro de él: el estado de git (rama, último commit, cambios) está en `_ref/git-estado.txt`, que el loop actualiza en cada ronda.\n- Para verificar, corré `bash tools/agents/mission_tests.sh`: corre los tests de tu misión. Corrélo antes de terminar.\n- No corras la suite completa ni el QA: apenas terminás, el loop corre los tests de la misión y la compuerta, y te devuelve los fallos.\n' "$wt"
}

# Después de que Codex ejecuta: todo lo que cambió fuera de «Archivos:» (artefactos como debug.log, capturas, temporales)
# vuelve a como estaba al empezar la misión. Imprime los archivos revertidos.
revert_out_of_scope() {  # $1 worktree, $2 misión
  local W="$1" arch f h
  arch=$(mission_list "$2" Archivos)
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    snap_same "$W" base "$f" && continue
    matches_any "$f" "$arch" && continue
    h=$(snap_entry "$W" base "$f")
    if [ -n "$h" ] && [ "$h" != DELETED ]; then cp -p "$W/_ref/.snap-base/files/$f" "$W/$f"
    elif [ "$h" = DELETED ]; then rm -f "$W/$f"
    elif git -C "$W" cat-file -e "HEAD:$f" 2>/dev/null; then git -C "$W" checkout -q HEAD -- "$f"
    else rm -f "$W/$f"; fi
    echo "$f"
  done < <(changed_files "$W")
}

# Comando para correr solo los tests de la misión (lo que Codex usa para verificar; la suite completa la corre el loop).
mission_tests_cmd() {  # $1 worktree, $2 misión
  local W="$1" M="$2" files="" f prot
  [ -n "${GATE_FILE_CMD:-}" ] || return 1
  prot=$(printf '%s' "$PROTECTED" | to_lines)
  while IFS= read -r f; do
    [ -n "$f" ] && matches_any "$f" "$prot" && is_test_file "$f" && case " $files " in *" $f "*) ;; *) files="$files $f";; esac
  done < <(mission_list "$M" Archivos; mission_list "$M" "Tests bloqueados")
  files="${files# }"; [ -n "$files" ] || return 1
  printf '%s' "${GATE_FILE_CMD//\{files\}/$files}"
}

# La misión tal como la ve Codex: sin las restricciones propias de AGY (allowlist exacta, «no leas por shell»).
mission_for_codex() {
  awk '/^Comandos permitidos/ {skip=1; next} skip && /^- / {next} {skip=0} /usá SOLO tus herramientas|Si te niegan un comando|Nunca comandos de shell|No leas archivos fuera de tu worktree|No leas `\.git`|No leas \.git/ {next} {print}' "$1"
}

commit_task() {  # $1 worktree, $2 título, $3 misión
  [ "$AUTO_COMMIT" = 1 ] || return 0
  local f
  # Solo lo que cambió durante la misión: el trabajo previo ajeno (foto «base») queda sin commitear.
  while IFS= read -r f; do
    [ -z "$f" ] && continue; snap_same "$1" base "$f" && continue
    git -C "$1" add -A -- "$f" 2>/dev/null
  done < <(changed_files "$1")
  git -C "$1" diff --cached --quiet && return 0
  git -C "$1" commit -q -m "agentes: $2" -m "Misión: $(basename "$3")" && log "commit $(git -C "$1" rev-parse --short HEAD)"
  # La compuerta recién corrida describe este commit: sirve de base para la próxima misión incremental.
  [ -f "$1/_ref/gate-fallos.txt" ] && cp "$1/_ref/gate-fallos.txt" "$1/_ref/.gate-at-$(git -C "$1" rev-parse HEAD).txt"
  # Una sola corrida de suite por cambio: el resultado queda guardado con el commit y la auditoría lo reutiliza
  # (gate_status.sh) mientras el árbol no cambie.
  { echo "commit $(git -C "$1" rev-parse HEAD)"; echo "fecha $(date '+%Y-%m-%d %H:%M')"; echo "misión $(basename "$3")"
    echo "compuerta $(mission_compuerta "$3" | sed 's/^$/total/') · fallos previos que siguen: $(sed '/^$/d' "$1/_ref/gate-fallos.txt" 2>/dev/null | wc -l)"
    echo; echo '```'; tail -n 15 "$1/_ref/gate-out.txt" 2>/dev/null; echo '```'; } > "$1/_ref/compuerta-$(git -C "$1" rev-parse --short HEAD).md"
}

# Toda misión arranca con la rama de integración al día (plan del 07/11/2026, punto 4). 0 = al día; 1 = conflicto
# (el merge se aborta y no cambia nada). MERGE_BASE=0 en repo.env lo desactiva.
merge_base_branch() {  # $1 worktree
  local W="$1" ref cur
  [ "${MERGE_BASE:-1}" = 1 ] && [ -n "$BASE_BRANCH" ] || return 0
  cur=$(git -C "$W" rev-parse --abbrev-ref HEAD 2>/dev/null)
  [ "$cur" = "$BASE_BRANCH" ] && return 0
  git -C "$W" remote get-url origin >/dev/null 2>&1 && git -C "$W" fetch -q origin "$BASE_BRANCH" 2>/dev/null
  ref="$BASE_BRANCH"; git -C "$W" rev-parse --verify -q "origin/$BASE_BRANCH" >/dev/null && ref="origin/$BASE_BRANCH"
  git -C "$W" rev-parse --verify -q "$ref" >/dev/null || return 0
  git -C "$W" merge-base --is-ancestor "$ref" HEAD 2>/dev/null && return 0
  if git -C "$W" -c user.name="${GIT_AUTHOR_NAME:-agentes}" -c user.email="${GIT_AUTHOR_EMAIL:-agentes@local}" merge --no-edit -q "$ref" > "$W/_ref/merge-base.txt" 2>&1; then
    log "rama al día con $ref ($(git -C "$W" rev-parse --short HEAD))"; return 0
  fi
  git -C "$W" merge --abort 2>/dev/null
  log "no pude traer $ref: conflicto o cambios sin commitear que chocan (ver _ref/merge-base.txt)"; return 1
}
