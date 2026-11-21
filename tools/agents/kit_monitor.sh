#!/usr/bin/env bash
# Monitor proactivo del workflow: vigila TODOS los worktrees de los proyectos y SALE (despertando a la sesión del
# workflow) apenas aparece un problema nuevo, sin esperar a que un coordinador lo reporte. Mientras no pasa nada, calla.
# Uso: bash ~/.claude/agent-workflow/bin/kit_monitor.sh [raíz-de-proyectos]   (por defecto ~/Downloads/Projects)
# Detecta (solo líneas nuevas desde la última corrida; estado en $KIT_HOME/monitor/):
#   - salidas del loop distintas de 0 (2 a 7), escalamientos, permisos o allowlist faltante, saturación larga,
#     Codex sin informe, loops interrumpidos;
#   - errores del propio kit (bash, Python, comandos inexistentes) en los logs del loop;
#   - loops vivos sin actividad hace más de MONITOR_STALE_MIN (60) y AGY huérfanos (agy_run.pid sin loop vivo).
KIT_HOME="${KIT_HOME:-$HOME/.claude/agent-workflow}"; ROOT="${1:-$HOME/Downloads/Projects}"
S="$KIT_HOME/monitor"; mkdir -p "$S"; OFF="$S/offsets.tsv"; SEEN="$S/vistos.txt"; touch "$OFF" "$SEEN"
EVENT='Codex en límite|^salida [2-7]$|ESCALADO|faltan comandos en la allowlist|saturado hace más de|abandono la ronda|Codex no dejó informe|Codex no pudo|loop interrumpido|best-of-2 interrumpido|se superó el tope'
KITERR='line [0-9]+: |command not found|syntax error|unbound variable|^Traceback|agy_run\.py", line|^ERROR: |No such file or directory'
logs() {
  find "$ROOT" -maxdepth 6 -path '*/node_modules' -prune -o \( -name review-loop.log -o -name run-loop.log -o -name best-of-2.log -o -name feature.log \) -path '*/_ref/*' -print 2>/dev/null
}
loop_alive() {  # ¿hay un review_loop/feature.sh/best_of_2 vivo cuyo comando nombra este worktree?
  local b; b=$(basename "$1")
  if command -v powershell >/dev/null 2>&1; then
    [ "$(powershell -NoProfile -Command "(Get-CimInstance Win32_Process | Where-Object { \$_.Name -eq 'bash.exe' -and \$_.CommandLine -match 'review_loop|feature\.sh|best_of_2' -and \$_.CommandLine -match [regex]::Escape('$b') } | Measure-Object).Count" 2>/dev/null | tr -d '\r')" -gt 0 ]
  else pgrep -f "(review_loop|feature\.sh|best_of_2).*$b" >/dev/null; fi
}
agy_alive() {  # pid de Windows (agy_run.pid) vivo
  [ -n "$1" ] || return 1
  if command -v tasklist >/dev/null 2>&1; then tasklist //FI "PID eq $1" 2>/dev/null | grep -q " $1 "; else kill -0 "$1" 2>/dev/null; fi
}
off_of() { awk -F'\t' -v f="$1" '$1==f {print $2; exit}' "$OFF"; }
set_off() { grep -vF "$1	" "$OFF" > "$OFF.tmp"; printf '%s\t%s\n' "$1" "$2" >> "$OFF.tmp"; mv "$OFF.tmp" "$OFF"; }
report=""; low=""
add() { grep -qxF "$1" "$SEEN" || { echo "$1" >> "$SEEN"; report="$report$1"$'\n'; }; }
# Baja prioridad (kit desactualizado): no despierta sola; se suma al próximo reporte.
addlow() { grep -qxF "$1" "$SEEN" || { echo "$1" >> "$SEEN"; low="$low$1"$'\n'; }; }
first=0; [ -s "$OFF" ] || first=1   # primera corrida: solo toma posición, no reporta el historial
while :; do
  while IFS= read -r f; do
    n=$(wc -l < "$f"); o=$(off_of "$f"); o=${o:-$([ $first -eq 1 ] && echo "$n" || echo 0)}
    [ "$n" -lt "$o" ] && o=0   # log rotado o reescrito
    [ $first -eq 1 ] && set_off "$f" "$n"
    if [ "$n" -gt "$o" ]; then
      wt="${f%/_ref/*}"
      tail -n +"$((o + 1))" "$f" | grep -v '^{"event"' | grep -E "$EVENT|$KITERR" | while IFS= read -r l; do
        echo "$wt :: $(basename "$f") :: $l"; done >> "$S/nuevos.tmp"
      set_off "$f" "$n"
    fi
  done < <(logs)
  first=0
  if [ -s "$S/nuevos.tmp" ]; then while IFS= read -r l; do add "$l"; done < "$S/nuevos.tmp"; fi
  rm -f "$S/nuevos.tmp"
  # Loops vivos sin actividad y AGY huérfanos.
  for p in $(find "$ROOT" -maxdepth 6 -path '*/node_modules' -prune -o -name loop.pid -path '*/_ref/*' -print 2>/dev/null); do
    wt="${p%/_ref/*}"; pid=$(cat "$p" 2>/dev/null)
    if kill -0 "$pid" 2>/dev/null; then
      # Kit viejo en un worktree con loop activo: se avisa una vez por versión (instalar al terminar la ronda).
      # Desactualizado = la versión instalada no incluye el último commit que tocó lo que se instala (bin/, templates/,
      # WORKFLOW.md); los cambios del monitor o de los tests no cuentan.
      kv=$(git -C "$KIT_HOME" log -1 --format=%h -- bin/lib.sh bin/review_loop.sh bin/agy_run.py bin/agy_loop.sh bin/feature.sh bin/best_of_2.sh bin/watchdog.sh bin/allowlist_check.py templates WORKFLOW.md 2>/dev/null)
      wv=$(tr -d '\r\n' < "$wt/tools/agents/VERSION" 2>/dev/null)
      [ -n "$kv" ] && [ -n "$wv" ] && ! git -C "$KIT_HOME" merge-base --is-ancestor "$kv" "$wv" 2>/dev/null &&
        addlow "$wt :: kit desactualizado ($wv; hace falta $kv): install.sh al terminar la misión"
      age=$(( ( $(date +%s) - $(stat -c %Y "$wt/_ref/run-loop.log" "$wt/_ref/review-loop.log" 2>/dev/null | sort -n | tail -n 1) ) / 60 ))
      [ "$age" -ge "${MONITOR_STALE_MIN:-60}" ] && add "$wt :: loop vivo sin actividad hace ${age} min (pid $pid) $(date +%Y-%m-%dT%H)"
    fi
  done
  for a in $(find "$ROOT" -maxdepth 6 -path '*/node_modules' -prune -o -name agy_run.pid -path '*/_ref/*' -print 2>/dev/null); do
    wt="${a%/_ref/*}"; lp="$wt/_ref/loop.pid"
    { [ -f "$lp" ] && kill -0 "$(cat "$lp")" 2>/dev/null; } && continue
    # Sin candado (loops lanzados con un kit anterior): buscar un proceso del loop vivo para este worktree.
    loop_alive "$wt" && continue
    agy_alive "$(cat "$a" 2>/dev/null)" || { rm -f "$a"; continue; }   # pid viejo: AGY ya terminó
    add "$wt :: AGY corriendo sin loop vivo (huérfano: cleanup.sh) $(date +%Y-%m-%dT%H)"
  done
  if [ -n "$report" ]; then
    echo "MONITOR DEL WORKFLOW $(date +%H:%M) · problemas nuevos:"; printf "%s" "$report"; [ -n "$low" ] && { echo "(además, baja prioridad:)"; printf "%s" "$low"; }; exit 2
  fi
  sleep "${MONITOR_INTERVAL:-120}"
done
