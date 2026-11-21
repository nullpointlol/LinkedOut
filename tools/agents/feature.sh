#!/usr/bin/env bash
# Ciclo por feature del workflow v2 (§2 y §6). La carpeta de la feature vive FUERA del repo
# ($FEATURES_DIR/<feature>/), así AGY nunca ve los tests ocultos.
# Uso:
#   feature.sh new     <worktree> <feature>            crea spec.md desde la plantilla (lo completa Claude)
#   feature.sh plan    <worktree> <feature> [--high]   Codex escribe plan.md, misiones/ y holdout/
#   feature.sh holdout <worktree> <feature>            corre los tests ocultos contra el worktree
#   feature.sh review  <worktree> <feature> [--high] [--solo-p1] holdout + revisión de la feature completa (base...HEAD)
#   feature.sh path    <worktree> <feature>            imprime la carpeta de la feature
#   feature.sh run     <worktree> <feature> [--high]   corre TODAS las misiones del plan en orden (review_loop), sin
#                                                      despertar al coordinador entre misiones; frena en la primera
#                                                      salida distinta de 0 y, si todas aprueban, corre «review».
#                                                      Retoma donde quedó (misiones aprobadas en progreso.txt).
# Salidas: 0 ok/aprobado · 1 cambios o holdout en rojo · 5 Codex no pudo.
set -u
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/lib.sh"
[ $# -ge 3 ] || die "uso: feature.sh new|plan|holdout|review|path <worktree> <feature> [--high] [--solo-p1]"
CMD="$1"; W="$(cd "$2" && pwd)" || die "no existe $2"; FEAT="$3"
HIGH=""
SOLO_P1=0
for arg in "${@:4}"; do
  case "$arg" in
    --high) HIGH="--high" ;;
    --solo-p1) SOLO_P1=1 ;;
  esac
done
load_env "$W"; mkdir -p "$W/_ref"; L="$W/_ref/feature.log"; F="$FEATURES_DIR/$FEAT"
eff() { [ "$HIGH" = --high ] && echo high || echo medium; }

holdout() {  # copia holdout/ al worktree, corre HOLDOUT_CMD (o GATE_CMD) y borra lo copiado
  local H="$F/holdout" f t copied=() sha comp_file use_fast=0
  [ -d "$H" ] || { echo "Sin tests ocultos." > "$W/_ref/holdout.md"; return 0; }
  sha=$(git -C "$W" rev-parse --short HEAD 2>/dev/null)
  comp_file="$W/_ref/compuerta-$sha.md"
  if [ -z "${HOLDOUT_CMD:-}" ] && [ -z "$(changed_files "$W")" ] && [ -f "$comp_file" ] && grep -q "fallos previos que siguen: 0" "$comp_file" && [ -n "${GATE_FILE_CMD:-}" ] && [ "${HOLDOUT_FULL:-0}" != 1 ]; then
    use_fast=1
  fi
  while IFS= read -r f; do
    [ -e "$W/$f" ] && die "el test oculto $f ya existe en el worktree (los ocultos tienen que ser archivos nuevos)"
    mkdir -p "$(dirname "$W/$f")"; cp "$H/$f" "$W/$f"; copied+=("$f")
  done < <(cd "$H" && find . -type f | sed 's|^\./||')
  if [ "$use_fast" = 1 ]; then
    log "holdout: solo los tests ocultos (la suite ya pasó en $sha)"
    local escaped=()
    for f in "${copied[@]}"; do
      escaped+=("$(printf %q "$f")")
    done
    local hfiles="${escaped[*]}"
    local hcmd="${GATE_FILE_CMD//\{files\}/$hfiles}"
    gate_command "$W" "$hcmd" "$W/_ref/holdout-out.txt"; t=$?
  else
    gate_command "$W" "${HOLDOUT_CMD:-$GATE_CMD}" "$W/_ref/holdout-out.txt"; t=$?
  fi
  for f in "${copied[@]}"; do rm -f "$W/$f"; rmdir -p "$(dirname "$W/$f")" 2>/dev/null; done
  [ -n "$GATE_RESTORE" ] && (cd "$W" && git checkout -q -- $GATE_RESTORE 2>/dev/null)
  { echo "## Tests ocultos: $([ $t -eq 0 ] && echo VERDE || echo ROJO)"; [ $t -ne 0 ] && { grep -E "$GATE_GREP" "$W/_ref/holdout-out.txt"; grep '^FAIL: compuerta colgada' "$W/_ref/holdout-out.txt"; } | sort -u | head -n 60; } > "$W/_ref/holdout.md"
  cp "$W/_ref/holdout.md" "$F/holdout-resultado.md"
  return $t
}

case "$CMD" in
  path) echo "$F" ;;
  run)
    [ -d "$F/misiones" ] || die "no hay $F/misiones (corré feature.sh plan)"
    feat_pid="$W/_ref/feature.pid"
    if [ -f "$feat_pid" ]; then
      if p=$(pid_ours "$feat_pid"); then
        die "ya hay un feature.sh corriendo"
      else
        rm -f "$feat_pid"
        log "pid viejo de otro proceso: no se corta ($feat_pid)"
      fi
    fi
    pid_write "$feat_pid" "$$" || die "no pude registrar feature.pid"
    trap 'rm -f "$W/_ref/feature.pid"' EXIT
    trap 'rm -f "$W/_ref/feature.pid"; exit 130' INT TERM
    touch "$F/progreso.txt"; RL="$W/_ref/review-loop.log"
    if [ "${MISION_LINT:-1}" != 0 ]; then
      rm -f "$F/lint-plan.md"
      bad_lint=0
      for m in $(ls "$F/misiones"/*.mision.md 2>/dev/null | sort); do
        b=$(basename "$m"); grep -qxF "$b" "$F/progreso.txt" && continue
        if ! bash "$D/mision_lint.sh" "$W" "$m" "${EXECUTOR:-agy}" >/dev/null 2>&1; then
          bad_lint=$((bad_lint+1))
          { echo "## Misión: $b"; echo; [ -f "$W/_ref/lint.md" ] && cat "$W/_ref/lint.md"; echo; } >> "$F/lint-plan.md"
        fi
      done
      if [ $bad_lint -gt 0 ]; then
        log "lint previo: $bad_lint misiones mal armadas (ver $F/lint-plan.md)"
        echo "salida 8" >> "$RL"; exit 8
      fi
    fi
    for m in $(ls "$F/misiones"/*.mision.md 2>/dev/null | sort); do
      b=$(basename "$m"); grep -qxF "$b" "$F/progreso.txt" && continue
      log "feature $FEAT: misión $b"
      head_before=$(git -C "$W" rev-parse HEAD 2>/dev/null)
      LOOP_CHAINED=1 bash "$D/review_loop.sh" "$W" "$m"; c=$?
      head_after=$(git -C "$W" rev-parse HEAD 2>/dev/null)
      tipo=$(mission_field "$m" Tipo | tr '[:upper:]' '[:lower:]')
      [ "$tipo" = implementacion ] && tipo=implementación

      if [ $c -ge 128 ]; then
        awk -v b="$b" '$0 != b' "$F/progreso.txt" > "$F/progreso.txt.tmp" && mv -f "$F/progreso.txt.tmp" "$F/progreso.txt"
        log "feature $FEAT: $b murió por señal $c; frena el encadenado"
        echo "salida $c" >> "$RL"; exit $c
      fi

      if [ $c -ne 0 ]; then
        awk -v b="$b" '$0 != b' "$F/progreso.txt" > "$F/progreso.txt.tmp" && mv -f "$F/progreso.txt.tmp" "$F/progreso.txt"
        log "feature $FEAT: $b salió con $c; frena el encadenado (detalle en _ref/)"
        echo "frenada en $b (salida $c)" >> "$F/progreso.txt"; echo "salida $c" >> "$RL"; exit $c
      fi

      if [ "$tipo" = lectura ] || [ "$head_before" != "$head_after" ]; then
        grep -qx "$b" "$F/progreso.txt" 2>/dev/null || echo "$b" >> "$F/progreso.txt"
      else
        awk -v b="$b" '$0 != b' "$F/progreso.txt" > "$F/progreso.txt.tmp" && mv -f "$F/progreso.txt.tmp" "$F/progreso.txt"
        log "feature $FEAT: $b salió 0 sin commit: no la anoto"
        echo "salida 1" >> "$RL"; exit 1
      fi
    done
    log "feature $FEAT: todas las misiones aprobadas; corre la revisión de la feature"
    p1_flag=""; [ "${SOLO_P1:-0}" = 1 ] && p1_flag="--solo-p1"
    bash "$0" review "$W" "$FEAT" $HIGH $p1_flag; c=$?
    echo "salida $c" >> "$RL"; exit $c ;;
  new)
    mkdir -p "$F/misiones" "$F/holdout"
    [ -f "$F/spec.md" ] && die "ya existe $F/spec.md"
    templ "$W" spec.md | sed "s|<feature>|$FEAT|g" > "$F/spec.md"; echo "$F/spec.md" ;;
  plan)
    [ -s "$F/spec.md" ] || die "falta $F/spec.md"
    mkdir -p "$F/misiones" "$F/holdout"; pf="$F/plan-prompt.md"
    { templ "$W" plan.md; echo; echo "## Rutas"; echo "- Repo (SOLO LECTURA, no lo modifiques): $W"; echo "- Carpeta de la feature (tu directorio de trabajo): $F"
      echo "- Compuerta del repo: $GATE_CMD"; echo "- Archivos protegidos: $PROTECTED"
      echo; echo "## Plantilla de misión"; templ "$W" mision.md; echo; echo "## spec.md"; cat "$F/spec.md"
      for x in "$F/exploracion.md" "$W/_ref/exploracion.md"; do [ -s "$x" ] && { echo; echo "## Exploración de AGY"; cat "$x"; break; }; done; } > "$pf"
    log "Codex planifica $FEAT ($(eff))"
    codex_run workspace-write "$(eff)" "$F/plan-resumen.md" "$F" "$pf" "$F/codex-plan.log" || { log "Codex no pudo planificar"; exit 5; }
    [ -s "$F/plan.md" ] || { log "Codex no escribió plan.md"; exit 5; }
    [ -n "$(git -C "$W" status --porcelain -- . ':(exclude)_ref')" ] && log "AVISO: el worktree tiene cambios después del plan; revisalos"
    if [ "${PLAN_REVIEW:-1}" != 0 ]; then
      rf="$F/review-plan-prompt.md"
      {
        templ "$W" review-plan.md
        echo; echo "## spec.md"; cat "$F/spec.md"
        echo; echo "## plan.md"; cat "$F/plan.md"
        echo; echo "## Misiones"
        for m in $(ls "$F/misiones"/*.mision.md 2>/dev/null | sort); do
          echo "### $(basename "$m")"
          cat "$m"
          echo
        done
      } > "$rf"
      log "Codex revisa el plan de $FEAT (medium)"
      if ! codex_run read-only medium "$F/casos-limite.md" "$W" "$rf" "$F/codex-plan-review.log"; then
        log "AVISO: Codex no pudo revisar el plan (continúa sin casos-limite.md)"
      fi
    fi
    log "plan listo: $F/plan.md ($(ls "$F/misiones" | wc -l) misiones, $(find "$F/holdout" -type f | wc -l) tests ocultos)"; exit 0 ;;
  holdout) holdout; t=$?; log "holdout $FEAT: $([ $t -eq 0 ] && echo verde || echo rojo)"; exit $([ $t -eq 0 ] && echo 0 || echo 1) ;;
  review)
    [ -s "$F/spec.md" ] || die "falta $F/spec.md"
    max_rev="${FEATURE_REVIEW_MAX:-2}"
    rev_count=0
    [ -f "$F/revisiones.txt" ] && rev_count=$(sed '/^$/d' "$F/revisiones.txt" | wc -l | tr -d ' ')
    n_rev=$((rev_count + 1))
    is_p1=0
    if [ "$n_rev" -ge "$((max_rev + 1))" ] || [ "$SOLO_P1" = 1 ]; then
      is_p1=1
      log "revisión de feature $n_rev en modo solo P1"
    fi
    if ! holdout; then
      { echo "VEREDICTO: CAMBIOS"; echo; echo "Fallan los tests ocultos:"; cat "$W/_ref/holdout.md"; } > "$F/revision-feature.md"
      cp "$F/revision-feature.md" "$W/_ref/"; log "feature $FEAT: holdout en rojo"; exit 1
    fi
    base="${BASE_BRANCH:-}"; [ -n "$base" ] || die "repo.env sin BASE_BRANCH"
    mb=$(git -C "$W" merge-base HEAD "$base") || die "no hay merge-base con $base"
    pf="$W/_ref/feature-review-prompt.md"
    sha_prev=""
    [ "$rev_count" -ge 1 ] && [ -f "$F/revisiones.txt" ] && sha_prev=$(sed '/^$/d' "$F/revisiones.txt" | tail -n 1 | awk '{print $NF}')
    {
      if [ "$is_p1" = 1 ]; then
        echo "Modo solo P1: pedí CAMBIOS únicamente por (1) un criterio de aceptación de spec.md incumplido, con evidencia; (2) un bug demostrable; (3) un riesgo de seguridad, concurrencia o pérdida de datos. Todo lo demás va bajo «## Mejoras no bloqueantes»."
        echo
      fi
      if [ "$rev_count" -ge 1 ]; then
        echo "Verificá punto por punto que cada CAMBIO de la revisión anterior quedó resuelto. Un hallazgo NUEVO solo puede pedir CAMBIOS si incumple un criterio de aceptación de spec.md, es un bug demostrable o es un riesgo de seguridad o concurrencia. Cualquier otro hallazgo nuevo va bajo «## Mejoras no bloqueantes» y no impide APROBADO."
        echo
      fi
      templ "$W" review-feature.md
      if [ "$rev_count" -ge 1 ]; then
        [ -s "$F/revision-anterior.md" ] && { echo; echo "## Revisión anterior"; cat "$F/revision-anterior.md"; }
        if [ -n "$sha_prev" ]; then
          echo; echo "## Diff desde la revisión anterior"; echo '```diff'; git -C "$W" diff "$sha_prev" HEAD -- . ':(exclude)_ref'; echo '```'
        fi
      fi
      echo; echo "## spec.md"; cat "$F/spec.md"; [ -f "$F/plan.md" ] && { echo; echo "## plan.md"; cat "$F/plan.md"; }
      [ -s "$F/mejoras-pendientes.md" ] && { echo; echo "## Mejoras de tests aprobadas con observaciones (verificá si siguen pendientes y si importan)"; cat "$F/mejoras-pendientes.md"; }
      echo; echo "## Commits"; git -C "$W" log --oneline "$mb..HEAD"; echo; echo "## Diff completo ($base...HEAD)"; echo '```diff'; git -C "$W" diff "$mb" HEAD -- . ':(exclude)_ref'; echo '```'
    } > "$pf"
    log "Codex revisa la feature $FEAT ($(eff))"
    codex_run read-only "$(eff)" "$F/revision-feature.md" "$W" "$pf" "$W/_ref/codex-feature.log" || { log "Codex no pudo revisar la feature"; exit 5; }
    cp "$F/revision-feature.md" "$W/_ref/"
    if grep -q '^VEREDICTO:' "$F/revision-feature.md"; then
      cp "$F/revision-feature.md" "$F/revision-anterior.md"
      echo "$(date '+%Y-%m-%d %H:%M') $(git -C "$W" rev-parse HEAD)" >> "$F/revisiones.txt"
    fi
    if grep -q '^## Mejoras no bloqueantes' "$F/revision-feature.md"; then
      { echo; awk '/^## Mejoras no bloqueantes/{flag=1; print; next} flag && /^##? /{flag=0} flag{print}' "$F/revision-feature.md"; } >> "$F/mejoras-pendientes.md"
    fi
    verdict_ok "$F/revision-feature.md" && { log "feature $FEAT APROBADA (falta la auditoría de Claude)"; exit 0; }
    log "feature $FEAT: CAMBIOS (ver $F/revision-feature.md)"; exit 1 ;;
  *) die "comando desconocido: $CMD" ;;
esac
