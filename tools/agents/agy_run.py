"""Despacho supervisado de AGY. Uso: python -X utf8 agy_run.py <cwd> <prompt.md> <estado.json>
Despacha con --output-format stream-json: cada evento se guarda en _ref/agy-stream.jsonl y se imprime una línea
de avance por paso (run-loop.log crece en vivo y el watchdog ve el progreso).
- Cuota agotada: sale con 3 (el coordinador avisa al creador para cambiar de cuenta). Nunca cambia de modelo por cuota.
- Modelo saturado (503 / «No capacity available»): NO es cuota. Reintenta; si sigue saturado AGY_FALLBACK_AFTER_MIN minutos
  (10 por defecto) pasa, SOLO para esta ronda, al siguiente modelo de AGY_FALLBACK_MODELS (máximo razonamiento), autorizado
  por el creador el 06/11/2026. A los 20 min avisa «saturado hace más de 20 min» (el watchdog despierta al coordinador).
  Si la saturación supera AGY_SATURATION_GIVEUP_MIN (60) sale con 1.
Entorno: AGY_BIN («agy»), AGY_MODEL, AGY_FALLBACK_MODELS, AGY_RETRY_SLEEP (60), AGY_TIMEOUT (5400)."""
import json, os, re, shlex, subprocess, sys, threading, time
from pathlib import Path

cwd, prompt, status_path = sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8"), Path(sys.argv[3])
stream_path = Path(cwd) / "_ref" / "agy-stream.jsonl"
PRIMARY = os.environ.get("AGY_MODEL", "")
FALLBACKS = [m for m in os.environ.get("AGY_FALLBACK_MODELS", "").split() if m and m != PRIMARY]
status = {"started": time.strftime("%Y-%m-%d %H:%M"), "model": PRIMARY, "model_used": PRIMARY, "attempts": [], "ok": False}
save = lambda: status_path.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
QUOTA = re.compile(r"RESOURCE_EXHAUSTED|429 Too Many|exhausted your capacity|quota|rate limit", re.I)
SATURATED = re.compile(r"No capacity available|\b503\b|UNAVAILABLE|overloaded", re.I)
DENIED = re.compile(r"denied permission|auto-denied|permission check failed", re.I)
SLEEP = int(os.environ.get("AGY_RETRY_SLEEP", "60"))
TIMEOUT = int(os.environ.get("AGY_TIMEOUT", "2700"))  # 45 min por intento: una ronda colgada no frena horas
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atexit, proc_id

pid = os.getpid()
hora = proc_id.get_proc_time(pid)
PIDFILE = Path(cwd) / "_ref" / "agy_run.pid"
PIDSFILE = Path(cwd) / "_ref" / "agy_run.pids"
PIDFILE.parent.mkdir(parents=True, exist_ok=True)
my_ident = f"{pid} {hora if hora is not None else ''}".strip()
PIDFILE.write_text(my_ident, encoding="utf-8")  # cleanup.sh y best_of_2.sh lo usan para cortar el árbol de procesos
try:
    with PIDSFILE.open("a", encoding="utf-8") as f:
        f.write(f"{my_ident}\n")
except Exception:
    pass


def remove_pids_entry():
    try:
        if PIDFILE.exists() and PIDFILE.read_text(encoding="utf-8").strip() == my_ident:
            PIDFILE.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        if PIDSFILE.exists():
            remaining = [l for l in PIDSFILE.read_text(encoding="utf-8").splitlines() if l.strip() and l.strip() != my_ident]
            if remaining:
                PIDSFILE.write_text("\n".join(remaining) + "\n", encoding="utf-8")
            else:
                PIDSFILE.unlink(missing_ok=True)
    except Exception:
        pass


atexit.register(remove_pids_entry)
FALLBACK_AFTER = float(os.environ.get("AGY_FALLBACK_AFTER_MIN", "10")) * 60
WARN_AFTER = float(os.environ.get("AGY_SATURATION_WARN_MIN", "20")) * 60
GIVEUP_AFTER = float(os.environ.get("AGY_SATURATION_GIVEUP_MIN", "60")) * 60


def say(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def command(model):
    c = shlex.split(os.environ.get("AGY_BIN", "agy")) + ["-p", prompt, "--mode", "accept-edits", "--output-format", "stream-json"]
    return c + (["--model", model] if model else [])


def progress(ev):
    """Una línea legible por evento; vacío si no aporta."""
    kind = ev.get("event")
    if kind == "init":
        return f"AGY arrancó ({ev.get('init', {}).get('model', '?')})"
    if kind == "step_update":
        s = ev.get("step_update", {})
        if s.get("state") == "ACTIVE" and s.get("step_type") != "tool":
            return ""
        what = s.get("tool_name") or s.get("step_type", "?")
        info = s.get("tool_info", {}) or {}
        params = info.get("parameters", {}) or {}
        target = params.get("Path") or params.get("CommandLine") or params.get("TargetFile") or ""
        line = f"paso {s.get('step_index')} {what} {s.get('state')}" + (f" · {str(target)[-80:]}" if target else "")
        err = (info.get("error") or {}).get("message", "")
        if err:
            line += (" · auto-denied: " if DENIED.search(err) else " · error: ") + err.splitlines()[0][:160]
        return line
    if kind == "result":
        r = ev.get("result", {})
        return f"resultado {r.get('status')} · turnos {r.get('num_turns')} · denegadas {len(r.get('denied_actions') or [])}"
    return ""


COUNTS = {"lecturas": 0, "ediciones": 0}
READ_TOOLS = {"view_file", "read_file", "grep_search", "find_by_name", "list_dir", "read_resource"}
EDIT_TOOLS = {"write_to_file", "write_file", "replace_file_content", "multi_replace_file_content", "sed_file", "edit_file", "notebook_edit"}


CMD_STALL = int(os.environ.get("AGY_CMD_STALL_SEC", "300"))
STUCK = {"active": None, "since": 0.0, "killed": False}


def run_once(model):
    """Corre AGY una vez. Devuelve (código, salida cruda, stderr, evento result o None)."""
    lines, result = [], None
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    with stream_path.open("a", encoding="utf-8") as raw:
        raw.write(json.dumps({"event": "kit_attempt", "model": model, "at": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
        p = subprocess.Popen(command(model), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        timer = threading.Timer(TIMEOUT, p.kill); timer.start()
        err_chunks = []
        t_err = threading.Thread(target=lambda: err_chunks.append(p.stderr.read())); t_err.start()
        # Un comando de AGY sin ningún evento nuevo por más de AGY_CMD_STALL_SEC (300) se considera colgado (por ejemplo,
        # una suite que no termina): se corta AGY y el loop sigue con la compuerta, que corre los tests por su cuenta.
        STUCK["active"], STUCK["since"], STUCK["killed"] = None, time.time(), False
        def vigia():
            while p.poll() is None:
                time.sleep(5)
                if STUCK["active"] and time.time() - STUCK["since"] > CMD_STALL:
                    STUCK["killed"] = True
                    say(f"comando de AGY colgado hace más de {CMD_STALL // 60} min ({STUCK['active'][-80:]}): corto AGY y sigue la compuerta")
                    # todo el árbol: en Windows, matar solo a AGY deja vivo al pytest hijo y la salida no se cierra
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
                    else:
                        p.kill()
                    return
        threading.Thread(target=vigia, daemon=True).start()
        for b in p.stdout:
            line = b.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                continue
            lines.append(line); raw.write(line + "\n"); raw.flush()
            try:
                ev = json.loads(line)
            except ValueError:
                say(line[:200]); continue
            if ev.get("event") == "result":
                result = ev.get("result", {})
            su = ev.get("step_update") or {}
            STUCK["since"] = time.time()
            if su.get("tool_name") == "run_command":
                STUCK["active"] = ((su.get("tool_info") or {}).get("parameters") or {}).get("CommandLine", "?") if su.get("state") == "ACTIVE" else None
            if su.get("state") == "DONE" and su.get("tool_name") in READ_TOOLS:
                COUNTS["lecturas"] += 1
            elif su.get("state") == "DONE" and su.get("tool_name") in EDIT_TOOLS:
                COUNTS["ediciones"] += 1
            msg = progress(ev)
            if msg:
                say(msg)
        p.wait(); timer.cancel(); t_err.join()
    err = b"".join(err_chunks).decode("utf-8", "replace")
    return p.returncode, "\n".join(lines), err, result


code, attempt, model, fb_idx = 1, 0, PRIMARY, -1
sat_since, warned = None, False
while attempt < 4:
    try:
        rc, out, err, result = run_once(model)
        res = result or {}
        non_step = "\n".join(l for l in out.splitlines() if '"step_update"' not in l)  # la salida de herramientas no cuenta
        rec = {"attempt": attempt + 1, "model": model, "code": rc, "result_status": res.get("status"), "stdout_tail": out[-4000:],
               "stderr_tail": err[-1500:], "denied": bool(res.get("denied_actions")), "denied_actions": res.get("denied_actions") or [],
               "empty": result is not None and not (res.get("response") or "").strip(), "no_result": result is None}
        status["attempts"].append(rec); save()
        if STUCK["killed"]:  # comando colgado: no se reintenta, decide la compuerta con lo que haya
            rec["stuck"] = True; status["stuck"] = True; code = 1; save(); break
        if err.strip():
            say("stderr: " + err.strip().splitlines()[0][:200])
        # 503 / sin capacidad: el modelo está saturado, NO es cuota agotada. No consume intentos: se mide por tiempo.
        if SATURATED.search(non_step + err) and (rc != 0 or rec["no_result"]):
            rec["saturated"] = True; status["saturated"] = True; code = 1
            sat_since = sat_since or time.time(); waited = time.time() - sat_since
            if waited >= GIVEUP_AFTER:
                say(f"AGY saturado hace {int(waited // 60)} min (también los modelos de respaldo): abandono la ronda"); break
            if waited >= WARN_AFTER and not warned:
                warned = True; say(f"AGY saturado hace más de {int(WARN_AFTER // 60)} min (503): avisar al creador")
            if waited >= FALLBACK_AFTER and fb_idx + 1 < len(FALLBACKS):
                fb_idx += 1; model = FALLBACKS[fb_idx]; status["model_used"] = model; status["fallback"] = True; save()
                say(f"modelo de respaldo: {model} ({PRIMARY} saturado hace {int(waited // 60)} min; solo esta ronda)")
                continue
            pause = SLEEP * 2
            say(f"AGY saturado (503, sin capacidad de {model}): no es cuota; reintento en {pause} s")
            save(); time.sleep(pause); continue
        attempt += 1
        if QUOTA.search(non_step + err) and (rc != 0 or rec["empty"] or rec["no_result"]):
            if model != PRIMARY and fb_idx + 1 < len(FALLBACKS):  # el respaldo sin cuota: probar el siguiente respaldo
                fb_idx += 1; model = FALLBACKS[fb_idx]; status["model_used"] = model; save()
                say(f"respaldo sin cuota; paso a {model}"); continue
            if model != PRIMARY:
                say("los modelos de respaldo no tienen cuota y el principal está saturado: reintentá más tarde"); code = 1; break
            status["quota"] = True; code = 3; break
        if rec["denied"]:
            status["denied"] = True; code = 1
            say("auto-denied: " + ", ".join(d.get("action", "?") for d in rec["denied_actions"]) + " (revisar la allowlist)")
            break  # no se reintenta: lo resuelve el coordinador (allowlist)
        if rc == 0 and result is not None and res.get("status") == "SUCCESS" and not rec["empty"]:
            status["ok"] = True; code = 0; break
        say(f"intento {attempt} sin resultado útil (código {rc}, estado {res.get('status')}); reintento")
        time.sleep(SLEEP * attempt)
    except Exception as exc:  # noqa: BLE001
        attempt += 1
        status["attempts"].append({"attempt": attempt, "exception": repr(exc)}); save(); time.sleep(SLEEP * attempt)
status["finished"] = time.strftime("%Y-%m-%d %H:%M"); status.update(COUNTS); save()
say(f"lecturas/ediciones de AGY en esta ronda: {COUNTS['lecturas']}/{COUNTS['ediciones']}")
if status.get("fallback") and status["ok"]:
    say(f"ronda hecha con el modelo de respaldo {status['model_used']}; la próxima vuelve a {PRIMARY}")
remove_pids_entry()
print({0: "OK", 3: "CUOTA"}.get(code, "SATURADO" if status.get("saturated") and not status["ok"] else "FALLÓ"), status_path, flush=True)
sys.exit(code)
