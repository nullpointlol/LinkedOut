#!/usr/bin/env bash
# Tablero automático (plan del 07/11/2026, punto 6): regenera el estado de todos los worktrees del repo.
# Uso: bash tools/agents/estado.sh <worktree-cualquiera-del-repo>
# Escribe ESTADO_FILE (repo.env; por defecto $KIT_HOME/estado/<repo>.md). Lo llama review_loop.sh en cada arranque y salida.
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/lib.sh"
W="$(cd "${1:-.}" && pwd)" || exit 1
load_env "$W"
OUT="${ESTADO_FILE:-$KIT_HOME/estado/$REPO_NAME.md}"; mkdir -p "$(dirname "$OUT")"
ahora=$(date '+%Y-%m-%d %H:%M')
{
  echo "# Estado de los agentes — $REPO_NAME"
  echo
  echo "Generado automáticamente: $ahora. No editar a mano."
  echo
  echo "| Worktree | Estado | Misión | Carril | Ejecutor | Desde | Último resultado |"
  echo "|---|---|---|---|---|---|---|"
  git -C "$W" worktree list --porcelain 2>/dev/null | sed -n 's/^worktree //p' | while IFS= read -r wt; do
    lg="$wt/_ref/review-loop.log"; [ -f "$lg" ] || continue
    ini=$(grep -n '=== misión' "$lg" | tail -n 1); [ -n "$ini" ] || continue
    n=${ini%%:*}; linea=${ini#*:}
    mision=$(printf '%s' "$linea" | sed -n 's/.*misión «\(.*\)» tipo=.*/\1/p')
    carril=$(printf '%s' "$linea" | sed -n 's/.*carril=\([^ ]*\).*/\1/p'); ej=$(printf '%s' "$linea" | sed -n 's/.*ejecutor=\([^ ]*\).*/\1/p')
    desde=$(printf '%s' "$linea" | cut -d' ' -f1)
    fin=$(tail -n +"$n" "$lg" | grep -E '^salida [0-9]+$' | tail -n 1)
    estado="terminada"; [ -z "$fin" ] && estado="corriendo"
    [ -f "$wt/_ref/loop.pid" ] && pid_ours "$wt/_ref/loop.pid" >/dev/null 2>&1 && estado="corriendo"
    [ "$estado" = corriendo ] && [ -z "$fin" ] && fin="$(tail -n 1 "$lg" | cut -c1-80)"
    echo "| $(basename "$wt") | $estado | ${mision:-?} | ${carril:-?} | ${ej:-?} | $desde | ${fin:-?} |"
  done
  echo
  if [ -s "$METRICS" ]; then
    echo "## Últimos cambios (de $METRICS)"
    echo
    echo "| Fecha | Misión | Carril | Ejecutor | Rondas | Salida | Minutos del loop | Minutos desde el pedido |"
    echo "|---|---|---|---|---|---|---|---|"
    tail -n 8 "$METRICS" | awk -F, '{printf "| %s | %s | %s | %s | %s | %s | %s | %s |\n", $1, $4, $15, $7, $8, $12, $13, $16}'
  fi
} > "$OUT.tmp" && mv -f "$OUT.tmp" "$OUT"
echo "$OUT"
