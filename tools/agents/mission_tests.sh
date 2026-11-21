#!/usr/bin/env bash
# Corre solo los tests de la misión para AGY usando GATE_FILE_CMD.
# Uso: bash tools/agents/mission_tests.sh (sin argumentos, desde la raíz del worktree)
set -u
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; . "$D/lib.sh"
W="$(pwd)"
load_env "$W"

f_tests="$W/_ref/mision-tests.txt"
if [ ! -f "$f_tests" ]; then
  echo "sin tests de la misión"
  exit 0
fi

files=()
while IFS= read -r line || [ -n "$line" ]; do
  line=$(printf '%s' "$line" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
  [ -n "$line" ] && files+=("$line")
done < "$f_tests"

if [ ${#files[@]} -eq 0 ]; then
  echo "sin tests de la misión"
  exit 0
fi

if [ -z "${GATE_FILE_CMD:-}" ]; then
  die "falta GATE_FILE_CMD en repo.env"
fi

escaped=()
for f in "${files[@]}"; do
  escaped+=("$(printf %q "$f")")
done
mfiles="${escaped[*]}"
cmd="${GATE_FILE_CMD//\{files\}/$mfiles}"
mkdir -p "$W/_ref"
out="$W/_ref/mission-tests-out.txt"
gate_command "$W" "$cmd" "$out"
code=$?
[ -n "${GATE_RESTORE:-}" ] && (cd "$W" && git checkout -q -- $GATE_RESTORE 2>/dev/null)
[ -f "$out" ] && cat "$out"
exit $code
