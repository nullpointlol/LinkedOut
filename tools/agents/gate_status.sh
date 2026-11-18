#!/usr/bin/env bash
# ¿Hace falta volver a correr la suite? (plan del 07/11/2026, punto 2: una sola corrida por cambio)
# Uso: bash tools/agents/gate_status.sh <worktree>
# Sale con 0 e imprime el resultado guardado si HEAD tiene una compuerta en verde registrada por el loop y el árbol no
# cambió desde entonces (la auditoría la reutiliza). Sale con 1 si hay que correr la suite.
W="$(cd "${1:-.}" && pwd)" || exit 1
f="$W/_ref/compuerta-$(git -C "$W" rev-parse --short HEAD 2>/dev/null).md"
[ -s "$f" ] || { echo "HEAD $(git -C "$W" rev-parse --short HEAD) no tiene compuerta registrada: correr la suite"; exit 1; }
sucio=$(git -C "$W" status --porcelain -- . ':(exclude)_ref' | grep -vE '(^|/)(debug|npm-debug|chrome_debug)\.log$')
[ -z "$sucio" ] || { echo "el árbol cambió desde la compuerta: correr la suite"; printf '%s\n' "$sucio" | head -n 5; exit 1; }
echo "compuerta ya corrida para este commit (reutilizable, no hace falta repetir la suite):"; cat "$f"
