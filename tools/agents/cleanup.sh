#!/usr/bin/env bash
# Limpia un worktree después de que se cortó la sesión coordinadora: corta el AGY y el loop que hayan quedado
# corriendo (huérfanos) y libera el candado. No toca archivos del repo.
# Uso: bash tools/agents/cleanup.sh <worktree> [<worktree> ...]
D="$(cd "$(dirname "$0")" && pwd)"; . "$D/lib.sh"
for w in "$@"; do
  W="$(cd "$w" && pwd)" || continue
  if [ -f "$W/_ref/feature.pid" ]; then
    if p=$(pid_ours "$W/_ref/feature.pid"); then
      echo "$W: corto feature.sh (pid $p)"
      kill_tree "$p"
      rm -f "$W/_ref/feature.pid"
    else
      rm -f "$W/_ref/feature.pid"
      log "pid viejo de otro proceso: no se corta ($W/_ref/feature.pid)"
    fi
  fi
  if [ -f "$W/_ref/loop.pid" ]; then
    if p=$(pid_ours "$W/_ref/loop.pid"); then
      echo "$W: corto el loop (pid $p)"
      kill_tree "$p"
      rm -f "$W/_ref/loop.pid"
    else
      rm -f "$W/_ref/loop.pid"
      log "pid viejo de otro proceso: no se corta ($W/_ref/loop.pid)"
    fi
  fi
  if [ -f "$W/_ref/agy_run.pids" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      [ -z "$line" ] && continue
      if p=$(pid_ours "$line"); then
        echo "$W: corto agy_run (pid $p)"
        kill_tree "$p"
      else
        log "pid viejo de otro proceso: no se corta ($line)"
      fi
    done < "$W/_ref/agy_run.pids"
    rm -f "$W/_ref/agy_run.pids"
  fi
  if [ -f "$W/_ref/agy_run.pid" ]; then
    if p=$(pid_ours "$W/_ref/agy_run.pid"); then
      echo "$W: corto AGY (pid $p)"
      kill_tree "$p"
      rm -f "$W/_ref/agy_run.pid"
    else
      rm -f "$W/_ref/agy_run.pid"
      log "pid viejo de otro proceso: no se corta ($W/_ref/agy_run.pid)"
    fi
  fi
  if [ -f "$W/_ref/watchdog.pid" ]; then
    if p=$(pid_ours "$W/_ref/watchdog.pid"); then
      echo "$W: corto watchdog (pid $p)"
      kill_tree "$p"
      rm -f "$W/_ref/watchdog.pid"
    else
      rm -f "$W/_ref/watchdog.pid"
      log "pid viejo de otro proceso: no se corta ($W/_ref/watchdog.pid)"
    fi
  fi
  echo "$W: limpio"
done
