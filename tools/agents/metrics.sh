#!/usr/bin/env bash
# Métricas de costo por tarea aceptada (workflow §9).
# Uso:
#   metrics.sh audit  <worktree> <feature> aceptada|rechazada   marca la auditoría de Claude en las filas de la feature
#   metrics.sh report <worktree>                                resumen por clase y sugerencias de S4
set -u
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/lib.sh"
CMD="${1:-}"; W="$(cd "${2:-.}" && pwd)"; load_env "$W"
[ -s "$METRICS" ] || die "sin métricas todavía ($METRICS)"
case "$CMD" in
  audit)
    FEAT="${3:?feature}"; RES="${4:?aceptada|rechazada}"
    awk -F, -v OFS=, -v f="$FEAT" -v r="$RES" 'NR>1 && $3==f && $14=="" {$14=r} {print}' "$METRICS" > "$METRICS.tmp" && mv "$METRICS.tmp" "$METRICS"
    echo "auditoría «$RES» registrada para $FEAT" ;;
  report)
    awk -F, 'NR>1 && $5!="lectura" {
        k=$6; n[k]++; if ($8<=1 && $10<=1 && $12==0) p[k]++; if ($11!="") e[k]++; r[k]+=$10
        if ($14=="rechazada") x[k]++ }
      END { printf "%-28s %5s %8s %7s %8s %9s  %s\n", "clase", "tareas", "1ª pasada", "escal.", "rev/tarea", "rechazos", "sugerencia"
        for (k in n) { a = p[k]/n[k]; s = ""
          if (n[k] >= 5 && k == "normal" && a < 0.7) s = "revisar: la clase normal está floja"
          else if (n[k] >= 5 && k != "normal" && k !~ /^S4/ && a < 0.7) s = "pasar a S4"
          else if (n[k] >= 5 && k ~ /^S4/ && a > 0.9) s = "sacar de S4"
          printf "%-28s %5d %7.0f%% %7d %8.1f %9d  %s\n", k, n[k], 100*a, e[k], r[k]/n[k], x[k], s } }' "$METRICS" ;;
  *) die "uso: metrics.sh audit|report <worktree> ..." ;;
esac
