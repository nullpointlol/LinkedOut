#!/usr/bin/env bash
# Despacha una misión a AGY con gemini-3.8-flash-high. Solo si está saturado (503) usa un modelo de respaldo en esa ronda.
# Uso: bash tools/agents/agy_loop.sh <worktree> <prompt.md>
# Sale con 0 si salió bien, 3 si no hay cuota (el coordinador avisa al creador) y 1 si falló (ver _ref/status.txt).
W="$1"; PR="$2"; ST="$W/_ref/status.txt"; D="$(cd "$(dirname "$0")" && pwd)"
[ -f "$D/lib.sh" ] && . "$D/lib.sh"
# El kit de la integración manda: su repo.env (junto a este script) antes que la copia de la rama de la misión.
if [ -f "$D/repo.env" ]; then . <(tr -d '\r' < "$D/repo.env"); elif [ -f "$W/tools/agents/repo.env" ]; then . <(tr -d '\r' < "$W/tools/agents/repo.env"); fi
: "${AGY_MODEL:=gemini-3.8-flash-high}"; export AGY_MODEL
# Respaldo SOLO ante modelo saturado (503), nunca ante cuota agotada (autorizado por el creador el 06/11/2026).
: "${AGY_FALLBACK_MODELS:=claude-opus-4-6-thinking claude-sonnet-4-6}"; export AGY_FALLBACK_MODELS
mkdir -p "$W/_ref"; rm -f "$ST"
if [ -f "$W/_ref/agy_run.pids" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    [ -z "$line" ] && continue
    if p=$(pid_ours "$line"); then
      kill_tree "$p"
    fi
  done < "$W/_ref/agy_run.pids"
  rm -f "$W/_ref/agy_run.pids"
fi
py_d="$D"; py_w="$W"; py_pr="$PR"; py_st="$ST"
if command -v cygpath >/dev/null 2>&1; then
  py_d=$(cygpath -m "$D")
  py_w=$(cygpath -m "$W")
  py_pr=$(cygpath -m "$PR")
  py_st=$(cygpath -m "$ST")
fi
python -X utf8 "$py_d/agy_run.py" "$py_w" "$py_pr" "$py_st" >> "$W/_ref/run-loop.log" 2>&1 < /dev/null; c=$?
used=$(sed -n 's/.*"model_used": "\([^"]*\)".*/\1/p' "$ST" 2>/dev/null | head -n 1)
echo "$(date -u +%H:%MZ) modelo=${used:-$AGY_MODEL} salida=$c" | tee -a "$W/_ref/run-loop.log"
exit $c
