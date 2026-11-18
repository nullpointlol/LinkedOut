#!/usr/bin/env bash
# Watchdog sin costo de cuota: vigila trabajos de AGY y Codex y SALE (despertando al coordinador)
# solo ante un evento. Mientras todo avanza no imprime nada.
# Uso: bash tools/agents/watchdog.sh <trabajo> [...]
#   <nombre>|<worktree>|loop|<minutos>   modo loop (recomendado con review_loop.sh / best_of_2.sh): actividad = cualquier
#                                        línea nueva en _ref/run-loop.log, _ref/review-loop.log o el stream de AGY;
#                                        termina solo con la salida FINAL del loop («salida N» en review-loop.log).
#   <nombre>|<log>|<carpeta>|<minutos>   modo clásico: un log y una carpeta que debe cambiar.
# Eventos: trabajo colgado esperando stdin, permiso denegado, cuota agotada, error fatal,
# sin actividad durante N minutos, y todos los trabajos terminados.
D="$(cd "$(dirname "$0")" && pwd)"; [ -f "$D/lib.sh" ] && . "$D/lib.sh"

if [ $# -ge 1 ] && [ -d "$1" ] && [[ "$1" != *"|"* ]]; then
  set -- "$(basename "$1")|$1|loop|10"
fi

WATCHDOG_WTS=()
for job in "$@"; do
  IFS='|' read -r j_name j_src j_dir _ <<<"$job"
  wt=""
  if [ "$j_dir" = loop ] && [ -d "$j_src" ]; then
    wt="$j_src"
  elif [ -d "$j_src" ] && [ -d "$j_src/_ref" ]; then
    wt="$j_src"
  elif [ -d "$j_dir" ] && [ -d "$j_dir/_ref" ]; then
    wt="$j_dir"
  fi
  if [ -n "$wt" ]; then
    wt="$(cd "$wt" && pwd)"
    case " ${WATCHDOG_WTS[*]:-} " in
      *" $wt "*) ;;
      *) WATCHDOG_WTS+=("$wt") ;;
    esac
  fi
done

for wt in "${WATCHDOG_WTS[@]}"; do
  mkdir -p "$wt/_ref"
  pid_write "$wt/_ref/watchdog.pid" "$$"
done

cleanup_watchdog() {
  for wt in "${WATCHDOG_WTS[@]}"; do
    rm -f "$wt/_ref/watchdog.pid"
  done
}
trap cleanup_watchdog EXIT INT TERM

STALL='Reading additional input from stdin|auto-denied|cannot prompt'
# Cuota: solo señales propias del kit o el error de la CLI de Codex, nunca texto suelto (un test puede imprimir
# «RESOURCE_EXHAUSTED» como dato). agy_run.py ya decide la cuota de AGY con el evento final «result».
QUOTA='salida=3$|^CUOTA |AGY sin cuota|Codex en límite|^ERROR:.*(usage limit|hit your)'
SATURATED_LONG='saturado hace más de|abandono la ronda'
FATAL='Traceback \(most recent|panic:|ENOSPC'
DONE='salida=[01]$|^salida [0-9]+$|"finished"'
LOOP_DONE='^salida [0-9]+$'
: "${FATAL_GRACE:=300}"  # segundos sin que crezca el log para considerar fatal un traceback
alert() { echo "WATCHDOG $(date +%H:%M) · $1"; exit 2; }
declare -A done_ off_
logs_of() {  # $1 log-o-worktree, $2 carpeta-o-«loop»: archivos de log a analizar
  if [ "$2" = loop ]; then echo "$1/_ref/review-loop.log"; echo "$1/_ref/run-loop.log"; echo "$1/_ref/best-of-2.log"; else echo "$1"; fi
}
# Solo cuentan las líneas escritas después de arrancar el watchdog (los logs se reutilizan entre rondas).
for job in "$@"; do
  IFS='|' read -r name src dir _ <<<"$job"
  while IFS= read -r f; do off_["$name|$f"]=$( [ -f "$f" ] && wc -l < "$f" || echo 0 ); done < <(logs_of "$src" "$dir")
done
START=$(date +%s)
# Las líneas crudas del stream de AGY ({"event":…}) traen salida de herramientas: no se analizan.
new() { tail -n +$(( ${off_["$1|$2"]:-0} + 1 )) "$2" 2>/dev/null | grep -v '^{"event"'; }
newest_age() { local t=0 m f; for f in "$@"; do [ -f "$f" ] && { m=$(stat -c %Y "$f"); [ "$m" -gt "$t" ] && t=$m; }; done; echo $(( $(date +%s) - t )); }
while :; do
  pending=0
  for job in "$@"; do
    IFS='|' read -r name src dir idle <<<"$job"
    [ -n "${done_[$name]}" ] && continue
    mapfile -t logs < <(logs_of "$src" "$dir")
    for log in "${logs[@]}"; do
      [ -f "$log" ] || continue
      # En modo loop, una denegación la maneja el loop (reintento gratis o salida 7): no es un cuelgue.
      st="$STALL"; [ "$dir" = loop ] && st='Reading additional input from stdin|cannot prompt'
      new "$name" "$log" | grep -qE "$st" && alert "$name colgado: $(new "$name" "$log" | grep -oE "$st" | head -1)"
      q="$QUOTA"; [ "$dir" = loop ] && q='AGY sin cuota|Codex en límite|^ERROR:.*(usage limit|hit your)'
      new "$name" "$log" | grep -v 'espero cuota de AGY' | tail -n 5 | grep -qE "$q" && alert "$name sin cuota"
      new "$name" "$log" | grep -qE "$SATURATED_LONG" && alert "$name: AGY saturado (503) hace más de 20 min; avisar al creador"
      # Un traceback puede ser salida de una herramienta que el agente leyó: solo es fatal si además el log se detuvo.
      if new "$name" "$log" | grep -qE "$FATAL"; then
        age=$(newest_age "${logs[@]}")
        [ "$age" -ge "$FATAL_GRACE" ] && alert "$name error fatal (el log no avanza hace $((age/60)) min)"
      fi
    done
    if [ "$dir" = loop ]; then
      fin=$(new "$name" "$src/_ref/review-loop.log" | grep -E "$LOOP_DONE" | tail -n 1)
      [ -n "$fin" ] && { done_[$name]=1; echo "WATCHDOG $(date +%H:%M) · $name terminó ($fin)" >&2; continue; }
      pending=1
      # Actividad: cualquier log del loop o el stream crudo de AGY (rondas largas de lectura cuentan).
      age=$(newest_age "${logs[@]}" "$src/_ref/agy-stream.jsonl")
      [ "$age" -ge $(( idle * 60 )) ] && [ $(( $(date +%s) - START )) -ge $(( idle * 60 )) ] && alert "$name sin actividad hace $((age/60)) min"
      continue
    fi
    log="$src"
    new "$name" "$log" | tail -n 3 | grep -qE "$DONE" && { done_[$name]=1; echo "WATCHDOG $(date +%H:%M) · $name terminó" >&2; continue; }
    pending=1
    recent=$(find "$log" "$dir" -newermt "-${idle} minutes" -type f 2>/dev/null | grep -v '/_ref/' | head -1)
    [ -z "$recent" ] && [ -n "$(find "$log" -newermt "-${idle} minutes" 2>/dev/null)" ] && recent=log
    # Gracia: la inactividad se cuenta desde que arranca el watchdog, no desde antes.
    [ -z "$recent" ] && [ $(( $(date +%s) - START )) -ge $(( idle * 60 )) ] && alert "$name sin avance hace ${idle} min"
  done
  [ $pending -eq 0 ] && { echo "WATCHDOG $(date +%H:%M) · todos terminaron"; exit 0; }
  sleep "${WATCHDOG_INTERVAL:-60}"
done
