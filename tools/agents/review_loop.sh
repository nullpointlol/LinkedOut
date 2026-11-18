#!/usr/bin/env bash
# Ciclo por microtarea del workflow v2 (docs/workflow-agentes.md §4–§5). No gasta Claude.
# Uso: bash tools/agents/review_loop.sh <worktree> <mision.md> [--revisar-primero] [--codex]
#   --revisar-primero  retoma un trabajo ya hecho: pasa por la compuerta (y la revisión) antes de que AGY ejecute.
#   --codex            ejecuta Codex (paso 3 del escalamiento); su revisión es compuerta + holdout + auditoría.
# Salidas: 0 aprobado y commiteado · 2 verde sin aprobación tras 2 revisiones (decide el coordinador)
#          3 AGY sin cuota · 4 Codex tampoco lo logró · 5 Codex no pudo revisar
#          6 escalado por señal (S1/S2/S3/tope de rondas): ver _ref/escalamiento.md y _ref/diagnostico.md
#          7 AGY sin cambios y con permisos denegados: falla del sistema, la resuelve el coordinador (no se escala)
set -u
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/lib.sh"
[ $# -ge 2 ] || die "uso: review_loop.sh <worktree> <mision.md> [--revisar-primero] [--codex]"
W="$(cd "$1" && pwd)" || die "no existe el worktree $1"
M="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; [ -f "$M" ] || die "no existe la misión $2"
shift 2; REVISAR=0; EJEC=""
for a in "$@"; do case "$a" in --revisar-primero) REVISAR=1;; --codex) EJEC=codex;; *) die "argumento desconocido: $a";; esac; done
load_env "$W"; mkdir -p "$W/_ref"; L="$W/_ref/review-loop.log"
# Ejecutor: --codex, o EXECUTOR=agy|codex|auto (entorno o repo.env). auto: el que tenga cuota (plan del 07/11/2026, punto 5).
EJEC="${EJEC:-${EXECUTOR:-agy}}"; case "$EJEC" in agy|codex|auto) ;; *) die "EXECUTOR inválido: $EJEC";; esac
take_lock "$W"
# Si el loop termina por cualquier motivo (incluida una señal), no deja AGY corriendo ni el candado tomado.
trap 'kill_agy "$W"; release_lock "$W"' EXIT
trap 'log "loop interrumpido: corto AGY"; exit 130' INT TERM
TIPO=$(mission_field "$M" Tipo); TIPO=${TIPO:-implementación}
case "$TIPO" in tests|tests-fix|implementación|implementacion|lectura) ;; *) die "Tipo inválido: $TIPO";; esac
[ "$TIPO" = implementacion ] && TIPO=implementación
CLASE=$(mission_field "$M" Clase); CLASE=${CLASE:-normal}
REVISION=$(mission_field "$M" Revisión); FEATURE=$(mission_field "$M" Feature); TITULO=$(mission_title "$M")
[ "$TIPO" != lectura ] && [ -z "$(mission_list "$M" Archivos)" ] && die "la misión no tiene «Archivos:»"
CARRIL=$(mission_carril "$M")
rounds=0; reviews=${REVIEWS_DONE:-0}; retry=0; senal=""; START=$(date +%s); AUTO=""; codex_tried=""; agy_tried=""; agy_quota_first_wait=""
# auto: el carril rápido arranca con Codex (más rápido y certero); el resto con AGY. Sin cuota o saturado, se cambia solo.
if [ "$EJEC" = auto ]; then
  AUTO=1; export CODEX_LIMIT_RETURN=1 AGY_SATURATION_GIVEUP_MIN="${AGY_SATURATION_GIVEUP_MIN:-10}"
  [ "$CARRIL" = rapido ] && EJEC=codex || EJEC=agy
fi
log "=== misión «$TITULO» tipo=$TIPO clase=$CLASE carril=$CARRIL ejecutor=$EJEC${AUTO:+ (auto)}"

finish() {
  local ped desde=""; ped=$(mission_field "$M" Pedido)
  [ -n "$ped" ] && ped=$(date -d "$ped" +%s 2>/dev/null) && desde=$(( ($(date +%s) - ped) / 60 ))
  metrics_row "$(date +%Y-%m-%d)" "$REPO_NAME" "$FEATURE" "$TITULO" "$TIPO" "$CLASE" "$EJEC${AUTO:+(auto)}${FALLBACK_USED:+(respaldo:$FALLBACK_USED)}" "$rounds" "$retry" "$reviews" "$senal" "$1" "$(( ($(date +%s) - START) / 60 ))" "" "$CARRIL" "$desde"
  if [ "$1" -eq 0 ]; then
    local feat; feat=$(mission_field "$M" Feature)
    case "$feat" in
      ""|"—"|"-"|"<"*) ;;
      *)
        local b; b=$(basename "$M")
        if [ -f "$FEATURES_DIR/$feat/misiones/$b" ]; then
          mkdir -p "$FEATURES_DIR/$feat"
          touch "$FEATURES_DIR/$feat/progreso.txt"
          grep -qx "$b" "$FEATURES_DIR/$feat/progreso.txt" 2>/dev/null || echo "$b" >> "$FEATURES_DIR/$feat/progreso.txt"
        fi
        ;;
    esac
  fi
  if [ -n "${LOOP_CHAINED:-}" ]; then echo "salida-misión $1" >> "$L"; else echo "salida $1" >> "$L"; fi
  bash "$D/estado.sh" "$W" >/dev/null 2>&1
  exit "$1"
}

# Validación de la misión antes de despachar (salida 8: misión mal armada; no se gasta ninguna ronda).
lint_ej=$EJEC; [ -n "$AUTO" ] && lint_ej=auto
if [ "${MISION_LINT:-1}" = 1 ] && ! bash "$D/mision_lint.sh" "$W" "$M" "$lint_ej" > /dev/null 2>&1; then
  senal=LINT; cp "$W/_ref/lint.md" "$W/_ref/escalamiento.md"
  log "misión mal armada (salida 8, ver _ref/lint.md): $(grep '^ERROR' "$W/_ref/lint.md" | head -n 2 | tr '\n' ' ')"; finish 8
fi
# La rama arranca al día con la integración (salida 8 si choca; el merge se aborta y no cambia nada).
if [ $REVISAR -eq 0 ] && ! merge_base_branch "$W"; then
  senal=MERGE; { echo "# No se pudo traer la rama de integración (salida 8)"; echo; cat "$W/_ref/merge-base.txt"; } > "$W/_ref/escalamiento.md"; finish 8
fi
# Foto «base»: lo que ya estaba cambiado antes de la misión no cuenta como trabajo del agente ni se toca.
if [ $REVISAR -eq 0 ] || [ ! -f "$W/_ref/.snap-base/index" ]; then
  [ $REVISAR -eq 1 ] && log "AVISO: --revisar-primero sin foto base; todo cambio actual cuenta como trabajo del agente"
  [ $REVISAR -eq 0 ] && snap_take "$W" base
fi
{ [ $REVISAR -eq 0 ] || [ ! -f "$W/_ref/.gate-base.txt" ]; } && { [ "$TIPO" = implementación ] || [ "$TIPO" = tests-fix ]; } && gate_base_take "$W" "$M"
write_mission_tests "$W" "$M"
bash "$D/estado.sh" "$W" >/dev/null 2>&1

diagnose() {  # Codex diagnostica, no implementa (§5 paso 1)
  local pf="$W/_ref/diagnostico-prompt.md"
  { templ "$W" diagnose.md; echo; echo "## Señal"; echo "$1"; echo; echo "## Misión"; cat "$M"
    echo; echo "## Fallos de la compuerta"; cat "$W/_ref/gate.md"; echo; echo "## Diff"; echo '```diff'; full_diff "$W"; echo '```'; } > "$pf"
  codex_run read-only "$(effort_for "$CLASE")" "$W/_ref/diagnostico.md" "$W" "$pf" "$W/_ref/codex-diagnose.log" ||
    echo "Codex no pudo diagnosticar (ver _ref/codex-diagnose.log)." > "$W/_ref/diagnostico.md"
}

escalate() {  # $1 señal, $2 motivo
  senal="$1"
  { echo "# Escalamiento $1"; echo; echo "$2"; echo; echo "- Misión: $M"; echo "- Rondas AGY: $rounds · reintento limpio: $retry · revisiones Codex: $reviews"
    echo "- Fallos: _ref/gate.md"; [ "$1" = S3 ] && echo "- Integridad: _ref/integrity.md"; [ "$1" != S3 ] && echo "- Diagnóstico de Codex: _ref/diagnostico.md"; } > "$W/_ref/escalamiento.md"
  if [ "$1" != S3 ]; then
    if [ "$CODEX_REVIEW" = diferida ]; then
      log "escalaría a Codex, pero CODEX_REVIEW=diferida: decide el coordinador"
      finish 6
    fi
    diagnose "$1: $2"
  fi
  log "ESCALADO $1: $2"; finish 6
}

review() {  # Codex revisa el diff contra la misión. 0 aprobado, 1 cambios, 5 sin informe
  local n=$1 tpl sb=read-only pf="$W/_ref/review-$1-prompt.md" out="$W/_ref/review-$1.md"
  if [ "$TIPO" = tests ]; then tpl=review-tests.md
  elif [ "$TIPO" = tests-fix ]; then tpl=review-tests-fix.md
  elif printf '%s' "$REVISION" | grep -qi '^ui'; then tpl=review-ui.md; sb=workspace-write
  else tpl=review-logica.md; fi
  { templ "$W" "$tpl"; echo; echo "## Misión"; mission_for_codex "$M"; echo; echo "## Diff"; echo '```diff'; full_diff "$W"; echo '```'
    if [ "$TIPO" = tests ]; then tests_evidence
    else echo; echo "## Resultado de la compuerta (ya corrió en verde; no hace falta volver a correrla: tu sandbox es de solo lectura)"; echo '```'; tail -n 25 "$W/_ref/gate-out.txt"; echo '```'; fi; } > "$pf"
  reviews=$((reviews+1)); log "compuerta en verde: Codex revisa ($reviews, $tpl)"
  [ "$sb" = workspace-write ] && snap_take "$W" prereview
  codex_run "$sb" "$(effort_for "$CLASE")" "$out" "$W" "$pf" "$W/_ref/codex-review.log" || { log "Codex no dejó informe"; finish 5; }
  [ "$sb" = workspace-write ] && snap_restore "$W" prereview  # la revisión UI solo escribe en _ref/
  verdict_ok "$out" || return 1
  # Solo en tests: «APROBADO CON MEJORAS» = rojo por el defecto correcto; las mejoras de endurecimiento quedan registradas.
  if [ "$TIPO" = tests ] && head -n 8 "$out" | grep -q 'VEREDICTO: APROBADO CON MEJORAS'; then
    { echo "# Mejoras sugeridas a los tests de «$TITULO»"; tail -n +2 "$out"; } > "$W/_ref/mejoras-test.md"
    local F="$FEATURES_DIR/$FEATURE"
    [ -n "$FEATURE" ] && [ -d "$F" ] && { echo; cat "$W/_ref/mejoras-test.md"; } >> "$F/mejoras-pendientes.md"
    log "APROBADO CON MEJORAS: mejoras de los tests en _ref/mejoras-test.md${FEATURE:+ y en la carpeta de la feature}"
  fi
  return 0
}

tests_evidence() {  # evidencia del rojo para la revisión de tests: corre solo los tests de la misión (GATE_FILE_CMD)
  local files="" f cmd
  while IFS= read -r f; do [ -n "$f" ] && [ -f "$W/$f" ] && matches_any "$f" "$(printf '%s' "$PROTECTED" | to_lines)" && is_test_file "$f" && files="$files $f"; done < <(changed_files "$W")
  files="${files# }"
  if [ -n "${GATE_FILE_CMD:-}" ] && [ -n "$files" ]; then
    cmd="${GATE_FILE_CMD//\{files\}/$files}"
    (cd "$W" && bash -c "$cmd") > "$W/_ref/tests-mision-out.txt" 2>&1
    [ -n "$GATE_RESTORE" ] && (cd "$W" && git checkout -q -- $GATE_RESTORE 2>/dev/null)
    echo; echo "## Evidencia del rojo: ejecución de los tests de la misión ($files)"; echo '```'; tail -n 150 "$W/_ref/tests-mision-out.txt"; echo '```'
  else
    echo; echo "## Salida de la compuerta (se esperan rojos solo en los tests nuevos)"; echo '```'; tail -n 80 "$W/_ref/gate-out.txt"; echo '```'
  fi
  [ -s "$W/_ref/invariantes-out.txt" ] && { echo; echo "## Corrida con INVARIANT_CHECK=1 (solo invariantes; tiene que estar en verde)"; echo '```'; tail -n 60 "$W/_ref/invariantes-out.txt"; echo '```'; }
}

compose() {  # $1 = archivo con correcciones (opcional)
  local p="$W/_ref/task-r$rounds.md"
  write_mission_tests "$W" "$M"
  git_estado "$W"
  { mission_for_agy "$M"; feature_ctx "$W" "$M"; file_map "$W" "$M"; agy_exec_rules "$W"; [ -n "${1:-}" ] && [ -f "$1" ] && { echo; echo; cat "$1"; } } > "$p"; echo "$p"
}

# Preflight: si la allowlist de AGY no tiene los comandos de la misión, frena ya (salida 7) en vez de perder rondas.
if [ "$EJEC" = agy ] && [ -n "$AUTO" ] && ! allowlist_check "$W" "$M"; then
  log "faltan comandos en la allowlist de AGY ($(paste -sd'|' "$W/_ref/allowlist-faltantes.txt")): EXECUTOR=auto, ejecuta Codex"; EJEC=codex
fi
if [ "$EJEC" = agy ] && ! allowlist_check "$W" "$M"; then
  senal=PERMISO
  { echo "# Faltan comandos en la allowlist de AGY (salida 7, antes de despachar)"; echo
    echo "La misión los declara en «Comandos permitidos» pero no están en ~/.gemini/antigravity-cli/settings.json."
    echo "El coordinador los agrega (si son seguros) o los saca de la misión, y relanza:"; echo
    sed 's/^/- command(/; s/$/)/' "$W/_ref/allowlist-faltantes.txt"; } > "$W/_ref/escalamiento.md"
  log "faltan comandos en la allowlist de AGY: $(paste -sd'|' "$W/_ref/allowlist-faltantes.txt"); salida 7 sin gastar rondas"
  finish 7
fi

# ---- lectura: AGY explora, sin compuerta ni Codex; solo puede escribir en _ref/
# Una lectura solo termina bien si dejó lo que promete: cada archivo de «Archivos:» (en _ref/) o, si no lista ninguno,
# _ref/exploracion.md. Devuelve 0 si está todo y deja la lista de faltantes en _ref/lectura-faltantes.txt.
lectura_outputs_ok() {
  local f falt=""
  while IFS= read -r f; do [ -n "$f" ] && [ ! -s "$W/$f" ] && falt="$falt $f"; done < <(mission_list "$M" Archivos)
  [ -z "$(mission_list "$M" Archivos)" ] && [ ! -s "$W/_ref/exploracion.md" ] && falt=" _ref/exploracion.md"
  printf '%s\n' "${falt# }" > "$W/_ref/lectura-faltantes.txt"; [ -z "$falt" ]
}
if [ "$TIPO" = lectura ] && [ "$EJEC" = codex ]; then
  if [ "$CODEX_REVIEW" = diferida ]; then
    log "escalaría a Codex, pero CODEX_REVIEW=diferida: decide el coordinador"
    finish 6
  fi
  pf="$W/_ref/codex-lectura-prompt.md"
  { templ "$W" codex-exec.md; echo; echo "## Misión (de LECTURA: no cambies archivos del repo; escribí tus resultados SOLO en _ref/, en las rutas de «Archivos:»)"; mission_for_codex "$M"
    feature_ctx "$W" "$M"; } > "$pf"
  # Puede escribir (levantar servidor, navegador, capturas), pero todo lo que toque fuera de _ref/ se revierte al terminar.
  snap_take "$W" prelectura
  codex_run workspace-write "$(effort_for "$CLASE")" "$W/_ref/codex-lectura.md" "$W" "$pf" "$W/_ref/codex-exec.log" || { snap_restore "$W" prelectura; log "Codex no pudo hacer la lectura"; finish 5; }
  snap_restore "$W" prelectura
  [ -s "$W/_ref/exploracion.md" ] || cp "$W/_ref/codex-lectura.md" "$W/_ref/exploracion.md"
  integrity "$W" "$M" || escalate S3 "la misión de lectura cambió archivos del repo"
  lectura_outputs_ok || { log "lectura de Codex incompleta: faltan $(cat "$W/_ref/lectura-faltantes.txt")"; finish 4; }
  log "lectura hecha por Codex"; finish 0
fi
if [ "$TIPO" = lectura ]; then
  rounds=1
  while :; do
    bash "$D/agy_loop.sh" "$W" "$(compose)"; c=$?
    if [ $c -eq 3 ]; then
      if [ -n "${AGY_QUOTA_WAIT:-}" ] || [ -n "${AGY_QUOTA_WAIT_SEC:-}" ]; then
        now=$(date +%s)
        max_s=$(( ${AGY_QUOTA_MAX_H:-96} * 3600 ))
        [ -n "${AGY_QUOTA_MAX_SEC:-}" ] && max_s="$AGY_QUOTA_MAX_SEC"
        if [ -n "$agy_quota_first_wait" ] && [ $(( now - agy_quota_first_wait )) -ge "$max_s" ]; then
          log "AGY sin cuota"; finish 3
        fi
        [ -z "$agy_quota_first_wait" ] && agy_quota_first_wait="$now"
        if [ $(( now - agy_quota_first_wait )) -ge "$max_s" ] && [ "$max_s" -le 0 ]; then
          log "AGY sin cuota"; finish 3
        fi
        wait_s=$(( ${AGY_QUOTA_WAIT:-0} * 60 ))
        [ -n "${AGY_QUOTA_WAIT_SEC:-}" ] && wait_s="$AGY_QUOTA_WAIT_SEC"
        n="${AGY_QUOTA_WAIT:-$AGY_QUOTA_WAIT_SEC}"
        log "espero cuota de AGY ($n min)"
        sleep "$wait_s"
        continue
      fi
      finish 3
    fi
    break
  done
  [ $c -ne 0 ] && agy_denied "$W" && [ ! -s "$W/_ref/exploracion.md" ] && { denied_exit "$W"; finish 7; }
  integrity "$W" "$M" || escalate S3 "la misión de lectura cambió archivos del repo"
  lectura_outputs_ok || { log "lectura incompleta: faltan $(cat "$W/_ref/lectura-faltantes.txt")"; finish 1; }
  [ $c -eq 0 ] && finish 0; finish 1
fi

# ---- Codex ejecuta (§5 paso 3). Con EXECUTOR=auto, si Codex está sin cuota devuelve 9 para pasar a AGY.
codex_exec_path() {
  if [ "$CODEX_REVIEW" = diferida ]; then
    log "escalaría a Codex, pero CODEX_REVIEW=diferida: decide el coordinador"
    finish 6
  fi
  codex_tried=1; EJEC=codex
  pf="$W/_ref/codex-exec-prompt.md"
  # Las restricciones de comandos y de lectura de la misión son de AGY: no se le pasan a Codex.
  { templ "$W" codex-exec.md; echo; echo "## Misión"; mission_for_codex "$M"; feature_ctx "$W" "$M"
    [ -f "$W/_ref/diagnostico.md" ] && { echo; echo "## Diagnóstico previo"; cat "$W/_ref/diagnostico.md"; }
    # Si se relanza después de un rojo, Codex ve qué falló la última vez (incluido el QA).
    { tail -n 1 "$L" 2>/dev/null | grep -qE '^salida(-misión)? [1-9]'; } && [ -s "$W/_ref/gate.md" ] && { echo; echo "## Fallos de la compuerta en la corrida anterior (resolvelos)"; head -n 80 "$W/_ref/gate.md"; }
    echo; if tc=$(mission_tests_cmd "$W" "$M"); then echo "## Tests de la misión (para verificar; no corras la suite completa)"; echo "$tc"
    else echo "## Compuerta del repo (no hay tests de la misión identificables: corrélo una sola vez al final)"; echo "$GATE_CMD"; fi
    [ -n "${QA_CMD:-}" ] && { echo; echo "## QA del repo (corrélo si la misión es visual o si la compuerta anterior falló por QA; verificá que no aparezca ningún fallo nuevo)"; echo "$QA_CMD"; }; } > "$pf"
  local exec_prompt="$pf" fix_round=0 cr
  while :; do
    codex_run workspace-write "$(effort_for "$CLASE")" "$W/_ref/codex-exec.md" "$W" "$pf" "$W/_ref/codex-exec.log"; cr=$?
    [ $cr -eq 9 ] && return 9
    [ $cr -eq 0 ] || { log "Codex no pudo ejecutar"; finish 5; }
    rev=$(revert_out_of_scope "$W" "$M"); [ -n "$rev" ] && log "fuera de «Archivos:», revertido después de Codex: $(echo $rev)"
    agent_changed "$W" || { log "Codex no dejó cambios (ver _ref/codex-exec.md): el coordinador decide"; finish 4; }
    gate "$W" "$M"; g=$?
    [ $g -eq 0 ] && { log "Codex lo logró: compuerta en verde (falta holdout y auditoría)"; commit_task "$W" "$TITULO" "$M"; finish 0; }
    [ $g -eq 1 ] && [ "$fix_round" -lt "${CODEX_EXEC_FIX_ROUNDS:-1}" ] || break
    fix_round=$((fix_round+1)); log "Codex: ronda de corrección $fix_round (compuerta en rojo)"
    pf="$W/_ref/codex-exec-fix-$fix_round-prompt.md"
    { cat "$exec_prompt"; echo; echo "## Corrección de la compuerta (ronda $fix_round)"
      echo "La misión ya está implementada. Corregí solo los fallos de la compuerta indicados abajo, sin rehacer la misión ni ampliar su alcance."
      head -n 80 "$W/_ref/gate.md"; } > "$pf"
  done
  [ $g -eq 2 ] && senal=S3
  log "Codex tampoco lo logró: el coordinador decide (reescribir el requerimiento o Claude con confirmación)"; finish 4
}
no_quota_exit() {  # ningún ejecutor con cuota: lo resuelve el coordinador (carril rápido) o se espera
  senal=CUOTA; { echo "# Sin cuota en AGY ni en Codex (salida 3)"; echo; echo "Con EXECUTOR=auto se probaron los dos. Carril rápido: lo puede hacer el coordinador (plan, punto 7)."; } > "$W/_ref/escalamiento.md"
  log "ni AGY ni Codex tienen cuota: salida 3"; finish 3
}
if [ "$EJEC" = codex ]; then
  if [ "$CODEX_REVIEW" = diferida ]; then
    log "escalaría a Codex, pero CODEX_REVIEW=diferida: decide el coordinador"
    finish 6
  fi
  codex_exec_path
  # solo vuelve si Codex está sin cuota en modo auto
  [ -n "$AUTO" ] && [ -z "$agy_tried" ] && { log "Codex sin cuota: EXECUTOR=auto, pasa a AGY"; EJEC=agy; } || no_quota_exit
fi

# ---- ciclo AGY
extra="${INITIAL_EXTRA:-}"; prev_fail=""; prev_n=-1; skip=$REVISAR
while :; do
  if [ "$skip" -eq 0 ]; then
    rounds=$((rounds+1))
    [ $rounds -gt "$MAX_ROUNDS" ] && escalate TOPE "se superó el tope de $MAX_ROUNDS rondas de AGY"
    [ $rounds -gt 1 ] && [ $(( ($(date +%s) - START) / 60 )) -ge "${MAX_MISSION_MIN:-180}" ] && escalate TOPE "se superó el tope de tiempo de la misión (${MAX_MISSION_MIN:-180} min)"
    log "ronda $rounds: AGY ejecuta"; agy_tried=1; bash "$D/agy_loop.sh" "$W" "$(compose "$extra")"; c=$?
    # auto: AGY sin cuota o saturado (más de AGY_SATURATION_GIVEUP_MIN) → Codex, una vez.
    if [ -n "$AUTO" ] && [ -z "$codex_tried" ] && [ "$CODEX_REVIEW" != diferida ] && { [ $c -eq 3 ] || { [ $c -ne 0 ] && grep -q '"saturated": true' "$W/_ref/status.txt" 2>/dev/null; }; }; then
      log "AGY $([ $c -eq 3 ] && echo 'sin cuota' || echo 'saturado'): EXECUTOR=auto, pasa a Codex"
      codex_exec_path; no_quota_exit
    fi
    if [ $c -eq 3 ]; then
      if [ -n "${AGY_QUOTA_WAIT:-}" ] || [ -n "${AGY_QUOTA_WAIT_SEC:-}" ]; then
        now=$(date +%s)
        max_s=$(( ${AGY_QUOTA_MAX_H:-96} * 3600 ))
        [ -n "${AGY_QUOTA_MAX_SEC:-}" ] && max_s="$AGY_QUOTA_MAX_SEC"
        if [ -n "$agy_quota_first_wait" ] && [ $(( now - agy_quota_first_wait )) -ge "$max_s" ]; then
          log "AGY sin cuota"; [ -n "$AUTO" ] && no_quota_exit; finish 3
        fi
        [ -z "$agy_quota_first_wait" ] && agy_quota_first_wait="$now"
        if [ $(( now - agy_quota_first_wait )) -ge "$max_s" ] && [ "$max_s" -le 0 ]; then
          log "AGY sin cuota"; [ -n "$AUTO" ] && no_quota_exit; finish 3
        fi
        wait_s=$(( ${AGY_QUOTA_WAIT:-0} * 60 ))
        [ -n "${AGY_QUOTA_WAIT_SEC:-}" ] && wait_s="$AGY_QUOTA_WAIT_SEC"
        n="${AGY_QUOTA_WAIT:-$AGY_QUOTA_WAIT_SEC}"
        log "espero cuota de AGY ($n min)"
        sleep "$wait_s"
        rounds=$((rounds-1))
        continue
      fi
      log "AGY sin cuota"; [ -n "$AUTO" ] && no_quota_exit; finish 3
    fi
    grep -q '"fallback": true' "$W/_ref/status.txt" 2>/dev/null && FALLBACK_USED=$(sed -n 's/.*"model_used": "\([^"]*\)".*/\1/p' "$W/_ref/status.txt" | head -n 1)
    [ $c -ne 0 ] && log "AGY salió con $c (ver _ref/status.txt); sigue la compuerta"
  fi
  skip=0
  if [ $rounds -gt 0 ] && ! agent_changed "$W"; then
    if agy_denied "$W"; then
      if [ "${read_retry:-0}" -eq 0 ]; then  # cualquier comando negado: un reintento gratis con la corrección
        read_retry=1; read_tools_note "$W" > "$W/_ref/gate.md"; extra="$W/_ref/gate.md"
        log "AGY usó un comando denegado: reintento gratis con la corrección (no cuenta para S1)"; continue
      fi
      denied_exit "$W"; finish 7
    fi
    echo "AGY no dejó ningún cambio en esta ronda" > "$W/_ref/gate-fallos.txt"
    { echo "## La ronda anterior no dejó cambios"; echo "No modificaste ningún archivo. Hacé la misión completa en los archivos de «Archivos:»."; } > "$W/_ref/gate.md"
    log "AGY no dejó cambios: no se corre la compuerta ni se llama a Codex"; g=1
  else
    # Rondas intermedias: primero la compuerta rápida (tests de la misión); la completa solo si la rápida queda en verde.
    g=3; { [ "$TIPO" = implementación ] || [ "$TIPO" = tests-fix ]; } && [ "${FAST_GATE:-1}" = 1 ] && { fast_gate "$W" "$M"; g=$?; }
    if [ $g -eq 0 ] || [ $g -eq 3 ]; then gate "$W" "$M"; g=$?; fi
  fi
  [ $g -eq 2 ] && escalate S3 "falló el control de integridad: $(head -n 3 "$W/_ref/integrity.md" | tr '\n' ' ')"
  if [ $g -ne 0 ]; then
    cur=$(cat "$W/_ref/gate-fallos.txt"); n=$(printf '%s\n' "$cur" | sed '/^$/d' | wc -l); s=""
    # S1/S2 comparan rondas del mismo tipo de compuerta (rápida contra rápida, completa contra completa).
    [ "${GATE_KIND:-full}" != "${prev_kind:-}" ] && prev_n=-1; prev_kind="${GATE_KIND:-full}"
    if [ $prev_n -ge 0 ]; then
      [ -n "$(comm -12 <(printf '%s\n' "$prev_fail" | fail_keys) <(printf '%s\n' "$cur" | fail_keys))" ] && s=S1
      [ -z "$s" ] && [ "$n" -ge "$prev_n" ] && s=S2
    fi
    prev_fail=$cur; prev_n=$n
    if [ -n "$s" ]; then
      if [ $retry -eq 0 ]; then
        retry=1; log "$s: reintento limpio (worktree como al empezar, misión + devolución completa)"
        cp "$W/_ref/gate.md" "$W/_ref/ultimo-fallo.md"
        snap_restore "$W" base
        extra="$W/_ref/ultimo-fallo.md"; continue
      fi
      escalate "$s" "$( [ "$s" = S1 ] && echo 'el mismo fallo se repite después del reintento limpio' || echo 'la cantidad de fallos no baja después del reintento limpio')"
    fi
    log "compuerta en rojo ($n fallos): vuelve a AGY sin gastar Codex"; extra="$W/_ref/gate.md"; continue
  fi
  # Carriles (plan del 07/11/2026): rápido sin revisión de Codex; normal con una sola; s4 con hasta dos.
  if [ "$CARRIL" = rapido ]; then log "carril rápido: compuerta en verde, sin revisión de Codex (audita el coordinador)"; commit_task "$W" "$TITULO" "$M"; finish 0; fi
  if [ "$CODEX_REVIEW" = diferida ]; then
    log "revisión de Codex diferida (CODEX_REVIEW=diferida)"
    reviews=diferida
    if [ -n "$FEATURE" ]; then
      case "$FEATURE" in ""|"—"|"-"|"<"*) ;; *)
        mkdir -p "$FEATURES_DIR/$FEATURE"
        echo "revisión pendiente: $TITULO" >> "$FEATURES_DIR/$FEATURE/progreso.txt"
        ;;
      esac
    fi
    commit_task "$W" "$TITULO" "$M"
    finish 0
  fi
  if [ "$CARRIL" = normal ] && [ "$reviews" -ge 1 ]; then log "carril normal: corregido tras su única revisión y compuerta en verde"; commit_task "$W" "$TITULO" "$M"; finish 0; fi
  [ "$reviews" -ge 2 ] && { log "compuerta en verde sin aprobación después de 2 revisiones: decide el coordinador (NO es aprobación)"; finish 2; }
  review $((reviews+1))
  if [ $? -eq 0 ]; then log "APROBADO"; commit_task "$W" "$TITULO" "$M"; finish 0; fi
  { printf '\n## Correcciones del revisor; resolvé todas\n\n'; tail -n +2 "$W/_ref/review-$reviews.md"; } > "$W/_ref/fix.md"
  extra="$W/_ref/fix.md"; prev_n=-1; prev_fail=""
done
