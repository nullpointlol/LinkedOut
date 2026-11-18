#!/usr/bin/env bash
# Valida una misión ANTES de despachar, para que no rebote (plan de remediación del 07/11/2026, punto 3).
# Uso: bash tools/agents/mision_lint.sh <worktree> <mision.md> [agy|codex|auto]
# Sale con 0 si está bien (puede haber AVISOS), 1 si hay ERRORES. Deja el detalle en <worktree>/_ref/lint.md.
# Errores:
#   - faltan campos obligatorios o el carril («Carril: rápido | normal | s4»), o el carril no corresponde al tamaño o la clase;
#   - con AGY como ejecutor: comandos de «Comandos permitidos» fuera de su allowlist;
#   - se pide cambiar un texto que algún test existente fija, y ese test no está en «Archivos:» ni en «Tests bloqueados:».
# Avisos: reglas propias de AGY en una misión de Codex (el kit igual las saca del prompt de Codex).
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/lib.sh"
[ $# -ge 2 ] || die "uso: mision_lint.sh <worktree> <mision.md> [agy|codex|auto]"
W="$(cd "$1" && pwd)" || die "no existe $1"; M="$2"; [ -f "$M" ] || die "no existe la misión $M"
load_env "$W"; mkdir -p "$W/_ref"; OUT="$W/_ref/lint.md"; : > "$OUT"
EJ="${3:-${EXECUTOR:-agy}}"
err() { echo "ERROR: $*" >> "$OUT"; }; warn() { echo "AVISO: $*" >> "$OUT"; }

tipo=$(mission_field "$M" Tipo); clase=$(mission_field "$M" Clase); carril=$(mission_carril "$M")
[ -n "$(mission_title "$M")" ] || err "falta el título («# Misión: …»)"
for f in Tipo Clase Objetivo "Hecho cuando"; do [ -n "$(mission_field "$M" "$f")" ] || err "falta el campo «$f:»"; done
[ -n "$(mission_field "$M" Carril)" ] || warn "falta «Carril: rápido | normal | s4»: sigue el proceso completo (s4). Las misiones nuevas lo declaran"
case "$carril" in rapido|normal|s4) ;; *) err "carril inválido: «$(mission_field "$M" Carril)» (rápido | normal | s4)";; esac
[ "$tipo" = lectura ] || [ -n "$(mission_list "$M" Archivos)" ] || err "falta «Archivos:»"
if [ "$carril" = rapido ]; then
  n=$(mission_list "$M" Archivos | wc -l)
  [ "$n" -le 2 ] || err "carril rápido con $n archivos (máximo 2): usá el carril normal"
  printf '%s' "$clase" | grep -qi '^S4' && err "carril rápido con clase $clase: lo delicado va en carril s4"
fi
printf '%s' "$clase" | grep -qiE "${S4_STRICT_REGEX:-concurrencia|cancelaci|envio|envío|aprobaci|migraci|seguridad}" && [ "$carril" != s4 ] &&
  err "clase $clase exige carril s4"

# Decisiones abiertas en la misión
open_pats='requiere pregunta|pendiente de autorizaci[oó]n|pregunt[aá] al|a definir|\bTBD\b|el coordinador puede suministrar'
matched_open=$(grep -i -o -E "$open_pats" "$M" 2>/dev/null | head -n 1)
if [ -n "$matched_open" ]; then
  err "decisión abierta en la misión: $matched_open; resolvela antes de despachar"
fi

if grep -qi 'PREGUNTAS PARA EL CREADOR' "$M"; then
  has_ans=$(awk '
    BEGIN { IGNORECASE=1; found=0; ans=0 }
    /PREGUNTAS PARA EL CREADOR/ { found=1; next }
    found && /^[[:space:]]*Respuesta[[:space:]]*:/ { ans=1 }
    END { print ans }
  ' "$M")
  if [ "$has_ans" != "1" ]; then
    err "decisión abierta en la misión: PREGUNTAS PARA EL CREADOR; resolvela antes de despachar"
  fi
fi

# Tests heredados afectados en misiones de implementación
if [ "$tipo" = "implementación" ] || [ "$tipo" = "implementacion" ]; then
  if [ "$EJ" = "agy" ] || [ "$EJ" = "auto" ]; then
    if ! grep -qiE '^Tests heredados afectados:' "$M"; then
      err "falta «Tests heredados afectados:» (declarar tests rotos con su decisión o «ninguno»)"
    fi
  fi
fi

# Allowlist de AGY (solo si AGY va a ejecutar; con auto, el loop pasa a Codex si falta algo).
if [ "$EJ" = agy ] && ! allowlist_check "$W" "$M"; then
  while IFS= read -r c; do [ -n "$c" ] && err "comando fuera de la allowlist de AGY: $c"; done < "$W/_ref/allowlist-faltantes.txt"
elif [ "$EJ" = auto ] && ! allowlist_check "$W" "$M"; then
  warn "comandos fuera de la allowlist de AGY ($(paste -sd'|' "$W/_ref/allowlist-faltantes.txt")): con EXECUTOR=auto ejecutará Codex"
fi

# Textos que se piden cambiar y que algún test fija: los tests tienen que estar en la misión.
prot=$(printf '%s' "$PROTECTED" | to_lines); arch=$(mission_list "$M" Archivos; mission_list "$M" "Tests bloqueados")
tests_files=$(git -C "$W" ls-files | while IFS= read -r f; do matches_any "$f" "$prot" && echo "$f"; done)
grep -E '^[[:space:]]*Cambia el texto:' "$M" | tr -d '\r' | grep -E '→|->' | sed -E 's/(→|->).*//' | grep -oE '«[^»]+»|"[^"]+"' |
  sed 's/^«//; s/»$//; s/^"//; s/"$//' | sort -u | while IFS= read -r txt; do   # «» son 2 bytes: no van en [ ]
    [ -n "$txt" ] || continue
    printf '%s' "$txt" | grep -qE '^[A-Za-z0-9_./-]+\.[a-z]{1,4}$' && continue   # rutas de archivo, no textos
    while IFS= read -r tf; do
      [ -n "$tf" ] || continue
      matches_any "$tf" "$arch" || err "el test $tf fija «$txt», que la misión cambia: sumalo a «Archivos:» (o conservá el texto)"
    done < <(printf '%s\n' "$tests_files" | sed '/^$/d' | while IFS= read -r f; do grep -qF -- "$txt" "$W/$f" 2>/dev/null && echo "$f"; done)
  done

# La suite completa la corre la compuerta, no AGY: el kit saca GATE_CMD de «Comandos permitidos» del prompt de AGY.
if [ -n "${GATE_CMD:-}" ] && mission_cmds "$M" | grep -qxF -- "$(printf '%s\n%s' "$GATE_CMD" "${SUITE_CMDS:-}" | sed '/^$/d')"; then
  warn "«Comandos permitidos» lista la suite completa ($GATE_CMD): la corre la compuerta; el kit se la saca a AGY (usá GATE_FILE_CMD con los tests de la misión)"
fi

# Reglas de AGY en una misión de Codex: el kit las saca, pero ensucian el pedido.
if [ "$EJ" = codex ] && grep -qE 'usá SOLO tus herramientas|Nunca comandos de shell|^Comandos permitidos' "$M"; then
  warn "la misión trae reglas propias de AGY y la ejecuta Codex (el kit se las saca del prompt)"
fi

if grep -q '^ERROR' "$OUT"; then
  { echo "# Misión mal armada: $(basename "$M")"; echo; cat "$OUT"; } > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"; cat "$OUT"; exit 1
fi
[ -s "$OUT" ] && cat "$OUT"
exit 0
