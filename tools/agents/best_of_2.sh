#!/usr/bin/env bash
# Best-of-2 (workflow §7): dos AGY con la misma misión en dos worktrees; la compuerta descarta;
# si los dos quedan en verde, Codex elige y revisa en una sola llamada. Solo clases S4, nunca migraciones.
# Uso: bash tools/agents/best_of_2.sh <worktree1> <worktree2> <mision.md>
# Sale como review_loop.sh (0 aprobado y commiteado en el ganador; ver _ref/best-of-2.md del worktree 1).
set -u
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/lib.sh"
[ $# -eq 3 ] || die "uso: best_of_2.sh <worktree1> <worktree2> <mision.md>"
W1="$(cd "$1" && pwd)"; W2="$(cd "$2" && pwd)"; M="$(cd "$(dirname "$3")" && pwd)/$(basename "$3")"
[ "$W1" != "$W2" ] || die "best-of-2 necesita dos worktrees distintos"
load_env "$W1"; mkdir -p "$W1/_ref" "$W2/_ref"; L="$W1/_ref/best-of-2.log"
[ "${CODEX_REVIEW:-}" = diferida ] && { log "best-of-2 no aplica con CODEX_REVIEW=diferida (requiere revisión de Codex)"; exit 8; }
mission_field "$M" Clase | grep -qi migraci && die "best-of-2 no aplica a migraciones"
# Solo donde paga el doble costo: concurrencia y camino de envío/aprobación (pedido del creador, 06/11/2026).
mission_field "$M" Clase | grep -qiE "${BEST_OF_2_CLASSES:-concurrencia|cancelaci|envio|envío|aprobaci}" ||
  die "best-of-2 solo para clases S4 de concurrencia o envío/aprobación (Clase: $(mission_field "$M" Clase)); usá review_loop.sh"
for w in "$W1" "$W2"; do
  git_estado "$w"
  { mission_for_agy "$M"; feature_ctx "$w" "$M"; file_map "$w" "$M"; agy_exec_rules "$w"; } > "$w/_ref/task-r1.md"
  snap_take "$w" base
  L="$w/_ref/best-of-2.log" gate_base_take "$w" "$M"
done
L="$W1/_ref/best-of-2.log"
take_lock "$W1"
if [ -f "$W2/_ref/loop.pid" ]; then
  if p=$(pid_ours "$W2/_ref/loop.pid"); then
    die "ya hay un loop corriendo en $W2 (pid $p)"
  else
    rm -f "$W2/_ref/loop.pid"
    log "pid viejo de otro proceso: no se corta ($W2/_ref/loop.pid)"
  fi
fi
pid_write "$W2/_ref/loop.pid" "$$"
# Si best-of-2 se corta (o muere la sesión que lo lanzó), no quedan AGY huérfanos ni candados tomados.
trap 'kill_agy "$W1"; kill_agy "$W2"; release_lock "$W1"; release_lock "$W2"' EXIT
trap 'log "best-of-2 interrumpido: corto los dos AGY"; exit 130' INT TERM
allowlist_check "$W1" "$M" || { log "faltan comandos en la allowlist de AGY: $(paste -sd'|' "$W1/_ref/allowlist-faltantes.txt"); salida 7"; exit 7; }
log "best-of-2: AGY en paralelo (tope ${BEST_OF_2_MAX_MIN:-90} min)"
bash "$D/agy_loop.sh" "$W1" "$W1/_ref/task-r1.md" & p1=$!
bash "$D/agy_loop.sh" "$W2" "$W2/_ref/task-r1.md" & p2=$!
limit=$(( $(date +%s) + ${BEST_OF_2_MAX_MIN:-90} * 60 ))
while kill -0 $p1 2>/dev/null || kill -0 $p2 2>/dev/null; do
  if [ "$(date +%s)" -ge "$limit" ]; then
    log "best-of-2: se superó el tope de ${BEST_OF_2_MAX_MIN:-90} min; corto los AGY que siguen"
    kill_agy "$W1"; kill_agy "$W2"; kill $p1 $p2 2>/dev/null; break
  fi
  sleep "${BEST_OF_2_POLL:-10}"
done
wait $p1; c1=$?; wait $p2; c2=$?
[ $c1 -eq 3 ] && [ $c2 -eq 3 ] && { log "AGY sin cuota"; exit 3; }
# Un candidato sin cambios propios queda descartado antes de la compuerta (no gasta compuerta ni Codex).
v1=0; v2=0; agent_changed "$W1" && v1=1; agent_changed "$W2" && v2=1
log "candidatos con cambios: w1=$v1 (AGY salió $c1) w2=$v2 (AGY salió $c2)"
if [ $v1 -eq 0 ] && [ $v2 -eq 0 ]; then
  if agy_denied "$W1" || agy_denied "$W2"; then w=$W1; agy_denied "$W2" && w=$W2; L="$w/_ref/review-loop.log"; denied_exit "$w"; echo "salida 7" >> "$L"; exit 7; fi
  log "ningún candidato dejó cambios: sigue el ciclo normal en w1"; release_lock "$W1"; release_lock "$W2"; trap - EXIT; exec bash "$D/review_loop.sh" "$W1" "$M"
fi
[ $v1 -eq 1 ] && [ $v2 -eq 0 ] && { log "w2 sin cambios: sigue w1"; release_lock "$W1"; release_lock "$W2"; trap - EXIT; exec bash "$D/review_loop.sh" "$W1" "$M" --revisar-primero; }
[ $v2 -eq 1 ] && [ $v1 -eq 0 ] && { log "w1 sin cambios: sigue w2"; release_lock "$W1"; release_lock "$W2"; trap - EXIT; exec bash "$D/review_loop.sh" "$W2" "$M" --revisar-primero; }
gate "$W1" "$M"; g1=$?; gate "$W2" "$M"; g2=$?
log "compuerta: w1=$g1 w2=$g2 (0 verde, 1 rojo, 2 integridad)"
if [ $g1 -ne 0 ] && [ $g2 -ne 0 ]; then
  # ninguno en verde: sigue el ciclo normal con el que tenga menos fallos (y sin violación de integridad)
  w=$W1; { [ $g1 -eq 2 ] || { [ $g2 -eq 1 ] && [ "$(wc -l < "$W2/_ref/gate-fallos.txt")" -lt "$(wc -l < "$W1/_ref/gate-fallos.txt")" ]; }; } && w=$W2
  log "ninguno en verde: sigue review_loop en $w"; release_lock "$W1"; release_lock "$W2"; trap - EXIT; exec bash "$D/review_loop.sh" "$w" "$M" --revisar-primero
fi
[ $g1 -eq 0 ] && [ $g2 -ne 0 ] && { log "gana w1 por compuerta"; release_lock "$W1"; release_lock "$W2"; trap - EXIT; exec bash "$D/review_loop.sh" "$W1" "$M" --revisar-primero; }
[ $g2 -eq 0 ] && [ $g1 -ne 0 ] && { log "gana w2 por compuerta"; release_lock "$W1"; release_lock "$W2"; trap - EXIT; exec bash "$D/review_loop.sh" "$W2" "$M" --revisar-primero; }
pf="$W1/_ref/best-of-2-prompt.md"
{ templ "$W1" review-pick.md; echo; echo "## Misión"; cat "$M"
  echo; echo "## Candidato 1"; echo '```diff'; full_diff "$W1"; echo '```'; echo; echo "## Candidato 2"; echo '```diff'; full_diff "$W2"; echo '```'; } > "$pf"
codex_run read-only "$(effort_for "$(mission_field "$M" Clase)")" "$W1/_ref/best-of-2.md" "$W1" "$pf" "$W1/_ref/codex-review.log" || { log "Codex no pudo elegir"; exit 5; }
win=$(grep -m1 -oE 'ELEGIDO: [12]' "$W1/_ref/best-of-2.md" | grep -oE '[12]'); win=${win:-1}
w=$W1; [ "$win" = 2 ] && w=$W2
[ "$w" != "$W1" ] && cp "$W1/_ref/best-of-2.md" "$w/_ref/"
log "Codex eligió el candidato $win ($w)"
if verdict_ok "$W1/_ref/best-of-2.md"; then
  L="$w/_ref/review-loop.log"; log "APROBADO (best-of-2)"; commit_task "$w" "$(mission_title "$M")" "$M"
  metrics_row "$(date +%Y-%m-%d)" "$REPO_NAME" "$(mission_field "$M" Feature)" "$(mission_title "$M")" "$(mission_field "$M" Tipo)" \
    "$(mission_field "$M" Clase)" "agy-best-of-2" 2 0 1 "" 0 "" ""
  echo "salida 0" >> "$w/_ref/review-loop.log"; exit 0
fi
{ printf '\n## Correcciones del revisor; resolvé todas\n\n'; sed '/^ELEGIDO:/d; /^VEREDICTO:/d' "$W1/_ref/best-of-2.md"; } > "$w/_ref/fix.md"
REVIEWS_DONE=1 INITIAL_EXTRA="$w/_ref/fix.md" release_lock "$W1"; release_lock "$W2"; trap - EXIT; exec bash "$D/review_loop.sh" "$w" "$M"
