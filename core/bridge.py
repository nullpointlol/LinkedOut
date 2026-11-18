"""Webhook Bridge para el CLI de Antigravity (AGY, agy.exe).
Permite invocar el motor de razonamiento de Antigravity sin requerir claves API comerciales externas.
"""
import contextlib
import logging
import os
import sys
import json
import tempfile
import re
import shutil
import asyncio
import subprocess
import time
import signal
import threading
import weakref
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Union, List, Tuple

from .paths import variable_entorno
from .prioridades import CARRIL_AGY, carril_agy

DEFAULT_MODEL = "gemini-3.8-flash-high"

logger = logging.getLogger(__name__)


_ADMISIONES = weakref.WeakSet()
_LOCK_ADMISIONES = threading.Lock()
# Llamadas a AGY ejecutando ahora en TODO el proceso (12/11): el portal y el Radar corren en loops distintos
# y el tope (2, o 1 en modo serie) es uno solo para los dos. Cada loop tiene su cola; el cupo es común.
_ACTIVOS_PROCESO = 0
_LOCK_ACTIVOS = threading.Lock()


def _tomar_cupo(limite: int) -> bool:
    global _ACTIVOS_PROCESO
    with _LOCK_ACTIVOS:
        if _ACTIVOS_PROCESO >= limite:
            return False
        _ACTIVOS_PROCESO += 1
        return True


def _soltar_cupo() -> None:
    global _ACTIVOS_PROCESO
    with _LOCK_ACTIVOS:
        _ACTIVOS_PROCESO = max(0, _ACTIVOS_PROCESO - 1)
SEGUNDOS_SERIE_AGY = 900


def _capacidad_agy() -> int:
    valor = variable_entorno("AGY_MAX_CONCURRENTES")
    try:
        capacidad = int(valor)
        if capacidad <= 0:
            raise ValueError
    except (TypeError, ValueError):
        capacidad = 2
        logger.warning("[AGY] LINKEDOUT_AGY_MAX_CONCURRENTES ausente o inválido (%r): se usa 2", valor)
    return min(2, capacidad)


def _avisar_admisiones() -> None:
    with _LOCK_ADMISIONES:
        admisiones = list(_ADMISIONES)
    for admision in admisiones:
        if not admision.loop.is_closed():
            try:
                admision.loop.call_soon_threadsafe(admision.despertar)
            except RuntimeError:  # el loop pudo cerrarse entre ambas operaciones
                pass


def _estado_admision(capacidad: int, *, modo_serie=None, saturacion=False) -> Dict[str, Any]:
    """Lee siempre SQLite y registra transiciones atómicas, también tras un reinicio.

    No mantiene una copia del freno: un PATCH o una recuperación externa se observa
    en la siguiente admisión. La transacción sólo toca los tres campos de AGY.
    """
    from . import radar_store

    ahora = time.time()

    def calcular(st):
        """Estado del freno a partir de la fila, y si hay que guardar una transición."""
        manual = st["modo_serie"] if modo_serie is None else modo_serie
        hasta = st["agy_serie_hasta"]
        historial = list(st["agy_historial"])
        cambio = False

        def registrar(motivo):
            nonlocal cambio
            historial.append({"fecha": datetime.fromtimestamp(ahora, timezone.utc).isoformat(),
                              "limite": 1 if manual or hasta is not None else capacidad,
                              "motivo": motivo})
            cambio = True

        if hasta is not None and ahora >= hasta:
            hasta = None
            registrar("Fin del modo serie automático")
        anterior = False
        for evento in reversed(historial):
            motivo = evento.get("motivo") if isinstance(evento, dict) else None
            if motivo in ("Modo serie activado", "Modo serie desactivado"):
                anterior = motivo == "Modo serie activado"
                break
        if manual != anterior:
            registrar("Modo serie activado" if manual else "Modo serie desactivado")
        if saturacion:
            hasta = ahora + SEGUNDOS_SERIE_AGY
            registrar("Saturación AGY (503)")
        return manual, hasta, historial, cambio, cambio or manual != st["modo_serie"]

    conn = radar_store._connect()
    try:
        # Una lectura no toma el lock de escritura (12/11): solo se abre la transacción si hay una
        # transición que guardar, y adentro se vuelve a calcular sobre la fila vigente.
        fila = conn.execute("SELECT * FROM radar_state WHERE id = 1").fetchone()
        manual, hasta, historial, cambio, escribir = calcular(radar_store._state_row_to_dict(fila))
        if escribir:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                fila = conn.execute("SELECT * FROM radar_state WHERE id = 1").fetchone()
                manual, hasta, historial, cambio, escribir = calcular(radar_store._state_row_to_dict(fila))
                if escribir:
                    conn.execute(
                        "UPDATE radar_state SET modo_serie=?, agy_serie_hasta=?, agy_historial=? WHERE id=1",
                        (int(manual), hasta, json.dumps(historial, ensure_ascii=False)))
    finally:
        conn.close()
    if cambio:
        _avisar_admisiones()
    return {"limite": 1 if manual or hasta is not None else capacidad,
            "modo_serie": manual, "serie_hasta": hasta, "historial": historial}


def estado_admision() -> Dict[str, Any]:
    try:
        capacidad = _admision_agy().capacidad
    except RuntimeError:  # panel de procesos ejecutado en un thread sin event loop
        capacidad = _capacidad_agy()
    return _estado_admision(capacidad)


def configurar_modo_serie(activo: bool) -> None:
    """Persiste el control manual y su evento sin alterar pausas ni cola."""
    try:
        capacidad = _admision_agy().capacidad
    except RuntimeError:
        capacidad = _capacidad_agy()
    _estado_admision(capacidad, modo_serie=activo)


class _AdmisionAGY:
    def __init__(self, loop):
        self.loop = loop
        self.capacidad = _capacidad_agy()
        self.condition = asyncio.Condition()
        self.activos = 0
        self.cola = []
        self.secuencia = 0
        self._timer = None
        self._timer_hasta = None
        with _LOCK_ADMISIONES:
            _ADMISIONES.add(self)

    @property
    def _value(self):
        return max(0, _estado_admision(self.capacidad)["limite"] - _ACTIVOS_PROCESO)

    def despertar(self):
        if self.cola:
            self.loop.create_task(self._notificar())

    async def _notificar(self):
        async with self.condition:
            self.condition.notify_all()

    def _programar_vencimiento(self, hasta):
        if hasta == self._timer_hasta:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer_hasta = hasta
        self._timer = (self.loop.call_later(max(0, hasta - time.time()), self.despertar)
                       if hasta is not None else None)

    async def __aenter__(self):
        async with self.condition:
            self.secuencia += 1
            pedido = (0 if CARRIL_AGY.get() == "generacion" else 1, self.secuencia)
            self.cola.append(pedido)
            try:
                while True:
                    st = _estado_admision(self.capacidad)
                    self._programar_vencimiento(st["serie_hasta"])
                    if pedido == min(self.cola) and _tomar_cupo(st["limite"]):
                        # Sin await entre decidir, sacar de cola y ocupar el permiso.
                        self.cola.remove(pedido)
                        self.activos += 1
                        self.condition.notify_all()
                        return self
                    await self.condition.wait()
            except BaseException:
                self.cola.remove(pedido)
                self.condition.notify_all()
                raise
            finally:
                if not self.cola:
                    self._programar_vencimiento(None)

    async def __aexit__(self, *exc):
        # Primero el cupo común (aunque una cancelación corte la espera de la condición, no se pierde)
        _soltar_cupo()
        async with self.condition:
            self.activos -= 1
            self.condition.notify_all()
        # El cupo es de todo el proceso: también se despierta a las colas de los otros loops
        _avisar_admisiones()


def _admision_agy() -> _AdmisionAGY:
    """Una cola por loop; cambiar el modo sólo cambia su límite, nunca el pool."""
    loop = asyncio.get_running_loop()
    admision = getattr(loop, "_linkedout_agy_admision", None)
    if admision is None:
        admision = _AdmisionAGY(loop)
        loop._linkedout_agy_admision = admision
    return admision


async def _crear_proceso(*cmd, **kwargs):
    if sys.platform == "win32":
        from .procesos_windows import ProcesoWindows
        return ProcesoWindows(cmd, **kwargs)
    return await asyncio.create_subprocess_exec(*cmd, **kwargs)


def _cerrar_proceso(process):
    if sys.platform == "win32":
        from .procesos_windows import ProcesoWindows
        if isinstance(process, ProcesoWindows):
            process.close()


async def _terminar_arbol(process) -> None:
    if sys.platform == "win32":
        await process.terminar_arbol()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


async def _terminar_proceso(process) -> None:
    async def limpiar():
        try:
            await _terminar_arbol(process)
        except Exception:
            logger.exception("[AGY] Falló la terminación del árbol de procesos")
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except Exception:
            logger.exception("[AGY] Falló la terminación del proceso padre")
        try:
            await process.wait()
        except Exception:
            logger.exception("[AGY] Falló la espera del proceso padre")

    # Un segundo descarte/stop no debe interrumpir la limpieza ya iniciada.
    limpieza = asyncio.create_task(limpiar())
    cancelacion = None
    while not limpieza.done():
        try:
            await asyncio.shield(limpieza)
        except asyncio.CancelledError as exc:
            cancelacion = exc
    limpieza.result()
    if cancelacion is not None:
        raise cancelacion

# Sin cuota de AGY: la llamada no falla, espera y se repite cuando vuelve la cuota (ver `CUOTA`)
_RE_CUOTA = re.compile(
    r"quota|resource[_ ]?exhausted|\b429\b|rate[ -]?limit|usage limit|too many requests|exceeded your|"
    r"limit (?:reached|exceeded)|out of credits|insufficient (?:credits|quota)",
    re.IGNORECASE)
_RE_SATURACION = re.compile(r"\b503\b|service unavailable|overloaded|no capacity available", re.IGNORECASE)


def es_saturacion_agy(error: Any) -> bool:
    texto = str(error or "")
    return bool(_RE_SATURACION.search(texto)) and not bool(_RE_CUOTA.search(texto))

INTERVALO_CUOTA_S = 15 * 60       # cada cuánto se reintenta (una sola llamada) mientras no hay cuota
SONDEO_ESPERA_S = 5.0             # las demás llamadas miran cada tanto si ya volvió
MAX_ERROR_LOG_CHARS = 1000
_ESTADOS_OK = ("SUCCESS", "OK", "COMPLETED", "")
# "Resets in 100h18m14s": cuándo dice AGY que se renueva la cuota de esta cuenta
_RE_RENUEVA = re.compile(r"resets? in\s+(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?",
                         re.IGNORECASE)
PROMPT_SONDEO = 'Respondé exactamente este JSON: {"ok": true}'


def segundos_hasta_renovar(mensaje: str) -> Optional[float]:
    m = _RE_RENUEVA.search(mensaje or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return float(((d * 24 + h) * 60 + mi) * 60 + s)


class CuotaAgotada(RuntimeError):
    """AGY respondió que no queda cuota o que se alcanzó un límite de uso."""


class _EstadoCuota:
    """Estado compartido por todas las llamadas a AGY del proceso.

    Cuando una llamada choca con el límite, las demás no lo intentan: esperan. Una sola (la que
    toma el turno) reintenta su propia llamada a la hora de `reanuda_en`; si responde, todas
    siguen; si no, se corre `reanuda_en` otros `INTERVALO_CUOTA_S`. Nada se cancela ni pasa a error.
    """

    def __init__(self) -> None:
        self.reanuda_en: Optional[float] = None
        self.desde: Optional[float] = None
        self.renueva_en: Optional[float] = None
        self.mensaje = ""
        self._probando = False
        self.generacion: int = 0

    def agotada(self, mensaje: str, soltar_turno: bool = False) -> None:
        """Una llamada chocó con el límite. Solo la que tenía el turno lo suelta: las que ya estaban
        en vuelo cuando se agotó solo corren el próximo intento."""
        self.generacion += 1
        ahora = time.time()
        if self.desde is None:
            self.desde = ahora
            logger.warning(f"[AGY] Sin cuota: las llamadas quedan en espera ({mensaje[:200]})")
        self.reanuda_en = ahora + INTERVALO_CUOTA_S
        self.mensaje = mensaje[:300]
        segundos = segundos_hasta_renovar(mensaje)
        if segundos is not None:
            self.renueva_en = ahora + segundos
        if soltar_turno:
            self._probando = False

    def restablecida(self, generacion: Optional[int] = None) -> None:
        if generacion is not None and generacion != self.generacion:
            return
        if self.reanuda_en is not None:
            logger.info("[AGY] Volvió la cuota: se retoman las llamadas en espera")
        self.reanuda_en = self.desde = self.renueva_en = None
        self.mensaje = ""
        self._probando = False

    def reintentar_ya(self) -> None:
        """El usuario cambió de cuenta o renovó el plan: la llamada en espera reintenta ahora."""
        if self.reanuda_en is not None:
            self.reanuda_en = time.time()

    def soltar_turno(self) -> None:
        """La llamada que tenía el turno se canceló (stop): otra lo toma, sin cambiar el estado."""
        self._probando = False

    async def esperar_turno(self, dormir=asyncio.sleep) -> bool:
        """Vuelve enseguida si hay cuota (False). Si no, espera: una llamada sale a la hora del
        reintento con el turno (True) y las demás esperan a que ese reintento resuelva."""
        while self.reanuda_en is not None:
            if not self._probando:
                self._probando = True
                try:
                    while self.reanuda_en is not None and self.reanuda_en > time.time():
                        await dormir(min(SONDEO_ESPERA_S, self.reanuda_en - time.time()))
                except BaseException:
                    self._probando = False
                    raise
                return True
            await dormir(SONDEO_ESPERA_S)
        return False

    def estado(self) -> Dict[str, Any]:
        def iso(t: Optional[float]) -> Optional[str]:
            return datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None
        return {"agotada": self.reanuda_en is not None, "desde": iso(self.desde),
                "reintento": iso(self.reanuda_en), "renueva": iso(self.renueva_en), "mensaje": self.mensaje}


CUOTA = _EstadoCuota()


async def sondear_cuota() -> Dict[str, Any]:
    """Reintento manual: adelanta el próximo intento y hace una llamada mínima (Low). Si hay
    cuota, todas las llamadas en espera siguen; si no, el aviso queda con el próximo intento."""
    CUOTA.reintentar_ya()
    try:
        await asyncio.wait_for(AntigravityBridge(effort="low").run_prompt(
            PROMPT_SONDEO, json_mode=True, timeout_seconds=60, effort="low"), timeout=90)
    except asyncio.TimeoutError:
        pass  # sigue sin cuota: la llamada queda esperando su turno y se descarta
    except Exception as e:
        logger.info(f"[AGY] Sondeo de cuota: {e}")
    return CUOTA.estado()


def texto_de_error(stdout: str, stderr: str, returncode: Optional[int], json_mode: bool = True) -> str:
    """Lo que AGY dijo al fallar ("" si respondió bien): el wrapper JSON con estado distinto de
    éxito o con `error`, la salida de un código de error, o un texto suelto en vez de JSON."""
    partes = []
    try:
        outer = json.loads(stdout.strip())
    except Exception:
        outer = None
    if isinstance(outer, dict) and (outer.get("error")
                                    or str(outer.get("status") or "").upper() not in _ESTADOS_OK):
        partes.append(json.dumps({k: (str(v)[:300] if k == "response" else v) for k, v in outer.items()},
                                 ensure_ascii=False, default=str))
    if returncode not in (0, None):
        # La salida estándar solo cuenta si no es una respuesta (un CV puede decir "quota")
        partes += [stderr.strip(), stdout.strip()[:MAX_ERROR_LOG_CHARS] if outer is None else ""]
    elif json_mode and outer is None and stdout.strip() and len(stdout.strip()) < 500 and "{" not in stdout:
        partes.append(stdout.strip())
    elif not json_mode and outer is None and len(stdout.strip()) < 500 and re.fullmatch(
        r"(?:error\s*[:\-]?\s*)?(?:503(?:\s+service unavailable)?|service unavailable|"
        r"no capacity available(?:[^\r\n]*)?|overloaded)\s*[.!]?", stdout.strip(), re.IGNORECASE
    ):
        partes.append(stdout.strip())
    return "\n".join(x for x in partes if x)[:MAX_ERROR_LOG_CHARS]


def _ticket_ia(tipo: str, mensaje: str, datos: Dict[str, Any]) -> None:
    """Ticket de error de la IA (core/tickets.py). Nunca corta la llamada."""
    try:
        from core.tickets import reportar_error
        reportar_error("ia", tipo, mensaje, datos=datos)
    except Exception:
        pass


def _registrar_error_agy(texto: str) -> None:
    """Guarda lo que AGY respondió al fallar (para reconocer mensajes nuevos, como el de cuota)."""
    try:
        from core.paths import DATA_DIR
        with open(DATA_DIR / "agy_errores.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {texto.replace(chr(10), ' | ')}\n")
    except Exception:
        pass
_PREFIJO_CWD_AISLADO = "linkedout_radar_agy_"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Etapa en curso de un pipeline (`etapa()`): cada `run_prompt` le suma su llamada
_ETAPA_ACTUAL: ContextVar[Optional[Dict[str, Any]]] = ContextVar("etapa_agy", default=None)


@contextlib.contextmanager
def etapa(nombre: str):
    """Mide una etapa (analisis, redactor, auditoria, mail…): segundos de reloj de todo el bloque y,
    de las llamadas a agy que ocurran adentro, cuántas fueron, modelo, razonamiento, tokens y si
    alguna se cortó. Devuelve el dict que se completa al salir (también si la etapa falla)."""
    registro: Dict[str, Any] = {"etapa": nombre, "segundos": 0.0, "llamadas": 0, "modelo": None,
                                "effort": None, "tokens": 0, "error": None}
    token = _ETAPA_ACTUAL.set(registro)
    inicio = time.monotonic()
    try:
        yield registro
    except BaseException as e:
        registro["error"] = (str(e) or type(e).__name__)[:200]
        raise
    finally:
        registro["segundos"] = round(time.monotonic() - inicio, 1)
        _ETAPA_ACTUAL.reset(token)


def resumen_etapas(etapas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """{"etapas": [...], "total_s": suma de segundos} para guardar en la telemetría."""
    return {"etapas": list(etapas), "total_s": round(sum(float(e.get("segundos") or 0) for e in etapas), 1)}


# Rutas estándar de instalación de Antigravity CLI en Windows
# Windows corta la línea de comandos en 32 767 caracteres (con las comillas escapadas): un pedido más largo que esto
# va por stdin, en el formato stream-json de agy (WinError 206 del 15/11, análisis de una oferta larga)
LARGO_MAX_PEDIDO_EN_LINEA = 16000


def mensaje_stdin(prompt: str) -> bytes:
    """El pedido como un mensaje de usuario de `--input-format stream-json`."""
    return (json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n").encode("utf-8")


def resultado_stream(stdout: str, json_mode: bool) -> str:
    """La salida stream-json, como la daría `--output-format json` (o el texto de la respuesta en modo texto)."""
    for linea in reversed(stdout.splitlines()):
        try:
            evento = json.loads(linea)
        except ValueError:
            continue
        if isinstance(evento, dict) and evento.get("event") == "result" and isinstance(evento.get("result"), dict):
            resultado = evento["result"]
            if json_mode or resultado.get("status") == "ERROR":
                return json.dumps(resultado, ensure_ascii=False)
            return str(resultado.get("response") or "")
    return stdout


DEFAULT_AGY_PATHS = [
    Path(os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe")),
    Path(os.path.expanduser(r"~\AppData\Local\agy\bin\agy.exe")),
]

def extract_json_payload(raw_text: str) -> Dict[str, Any]:
    """Extrae de manera resiliente un diccionario JSON desde texto crudo, markdown o wrapper de agy."""
    if not raw_text or not raw_text.strip():
        return {}

    text = raw_text.strip()

    # Si es el wrapper de agy {"conversation_id": "...", "status": "...", "response": "..."}
    try:
        outer = json.loads(text)
        if isinstance(outer, dict):
            if "response" in outer and isinstance(outer["response"], str):
                inner_text = outer["response"].strip()
                # Extraer recursivamente
                parsed_inner = extract_json_payload(inner_text)
                if parsed_inner:
                    return parsed_inner
            elif "response" in outer and isinstance(outer["response"], dict):
                return outer["response"]
            elif "status" in outer and ("conversation_id" in outer or "duration_seconds" in outer):
                # Es wrapper pero sin response útil o response ya parseada
                return outer
            elif "error" not in outer or len(outer) > 1:
                return outer
    except json.JSONDecodeError:
        pass

    # Buscar bloque de código markdown ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        snippet = match.group(1).strip()
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"items": parsed}
        except json.JSONDecodeError:
            pass

    # Intentar parsear el primer objeto JSON balanceado con regex
    json_obj_match = re.search(r"(\{[\s\S]*\})", text)
    if json_obj_match:
        try:
            parsed = json.loads(json_obj_match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Fallback si no es un JSON puro
    return {"raw": text}


class AntigravityBridge:
    """Interfase de conexión directa al CLI local de Antigravity (agy)."""

    active_model: str = DEFAULT_MODEL
    active_effort: str = "high"
    _cached_available_models: Optional[List[str]] = None
    _cached_models_details: Optional[List[Dict[str, str]]] = None

    def __init__(
        self,
        agy_path: Optional[Union[str, Path]] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
    ):
        self.agy_path = self._resolve_binary(agy_path)
        self.model = model or AntigravityBridge.active_model
        self.effort = effort or self._infer_effort(self.model)
        self.last_telemetry: Optional[Dict[str, Any]] = None

    @classmethod
    def _infer_effort(cls, model_name: str) -> str:
        """Infiere el esfuerzo de razonamiento adecuado a partir del nombre del modelo."""
        m = model_name.lower()
        if "low" in m:
            return "low"
        if "medium" in m:
            return "medium"
        return "high"

    @classmethod
    def _format_display_name(cls, model_id: str, raw_name: Optional[str] = None) -> str:
        """Formatea el nombre legible para humanos a partir del CLI o del ID."""
        if raw_name and raw_name.strip():
            return raw_name.strip()
        m = model_id.lower()
        if m == "gemini-3.8-flash-high":
            return "Gemini 3.8 Flash (High)"
        if m == "gemini-3.8-flash-medium":
            return "Gemini 3.8 Flash (Medium)"
        if m == "gemini-3.8-flash-low":
            return "Gemini 3.8 Flash (Low)"
        if "claude-sonnet" in m:
            return "Claude Sonnet 4.6 (Thinking)"
        if "claude-opus" in m:
            return "Claude Opus 4.6 (Thinking)"
        if "gpt-oss" in m:
            return "GPT-OSS 120B (Medium)"
        return model_id

    def set_active_model(self, model_name: str, effort: Optional[str] = None):
        """Actualiza el modelo activo tanto en la instancia como a nivel global."""
        self.model = model_name
        self.effort = effort or self._infer_effort(model_name)
        AntigravityBridge.active_model = model_name
        AntigravityBridge.active_effort = self.effort

    def _resolve_binary(self, custom_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
        if custom_path:
            p = Path(custom_path)
            if p.exists():
                return p

        which_agy = shutil.which("agy") or shutil.which("agy.exe")
        if which_agy:
            return Path(which_agy)

        for candidate in DEFAULT_AGY_PATHS:
            if candidate.exists():
                return candidate

        return None

    @classmethod
    def resolve_model_and_effort(
        cls, base_model: str, requested_effort: Optional[str] = None
    ) -> Tuple[str, Optional[str]]:
        """Resuelve el modelo compatible y el flag de esfuerzo para el CLI agy evitando conflictos.

        Reglas del CLI agy:
        - Si el modelo es gemini-3.8-flash-*, agy rechaza un --effort que no coincida con el sufijo.
          Por tanto, al pedir un esfuerzo específico se muta el nombre del modelo (ej: gemini-3.8-flash-low)
          y se pasa el flag coincidente --effort low.
        - Para modelos que no soportan flag --effort (como claude-* o gpt-oss-*), nunca se envía --effort.
        """
        m = (base_model or DEFAULT_MODEL).strip()
        eff = (requested_effort or cls._infer_effort(m)).lower()

        # Familia Gemini 3.8 Flash
        if "gemini-3.8-flash" in m.lower():
            resolved_model = f"gemini-3.8-flash-{eff}"
            return resolved_model, eff

        # Modelos que no soportan --effort en el CLI agy
        if any(no_effort in m.lower() for no_effort in ("claude", "opus", "sonnet")):
            return m, None

        # Si el modelo termina con un sufijo de esfuerzo explícito (ej: gpt-oss-120b-medium)
        suffix_eff = cls._infer_effort(m)
        if eff == suffix_eff:
            return m, eff
        return m, None

    def _build_command(
        self, prompt: str, json_mode: bool = True, effort: Optional[str] = None, por_stdin: bool = False
    ) -> List[str]:
        """Construye los argumentos para agy.exe garantizando el modelo, bypass de permisos y omisión de slash commands."""
        chosen_effort = effort if effort is not None else self.effort
        resolved_model, flag_effort = self.resolve_model_and_effort(self.model, chosen_effort)
        cmd = [
            str(self.agy_path),
            "--model",
            resolved_model,
        ]
        if flag_effort:
            cmd.extend(["--effort", flag_effort])
        cmd.extend(["--dangerously-skip-permissions", "--disable-slash-commands"])
        if por_stdin:
            # El pedido llega por stdin (`mensaje_stdin`); la salida se lee con `resultado_stream`
            cmd.extend(["--input-format", "stream-json", "--output-format", "stream-json"])
        else:
            cmd.extend(["--output-format", "json" if json_mode else "text", "-p", prompt])
        return cmd

    def _anotar_llamada(self, effort: Optional[str], error: Optional[str] = None) -> None:
        """Suma la llamada a la etapa en curso (si hay una)."""
        registro = _ETAPA_ACTUAL.get()
        if registro is None:
            return
        modelo, _ = self.resolve_model_and_effort(self.model, effort or self.effort)
        registro["llamadas"] += 1
        registro["modelo"] = modelo
        registro["effort"] = effort or self.effort
        if error:
            registro["error"] = error
        elif isinstance(self.last_telemetry, dict):
            registro["tokens"] += int(self.last_telemetry.get("total_tokens") or 0)

    def _record_telemetry(self, stdout: str, effort: Optional[str] = None):
        """Extrae y registra métricas verificables de uso y tiempo de agy.exe."""
        try:
            outer = json.loads(stdout.strip())
            if isinstance(outer, dict) and ("conversation_id" in outer or "duration_seconds" in outer):
                usage = outer.get("usage", {})
                chosen_effort = effort or self.effort
                resolved_model, _ = self.resolve_model_and_effort(self.model, chosen_effort)
                self.last_telemetry = {
                    "model": resolved_model,
                    "effort": chosen_effort,
                    "conversation_id": outer.get("conversation_id"),
                    "duration_seconds": outer.get("duration_seconds", 0.0),
                    "status": outer.get("status", "SUCCESS"),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "thinking_tokens": usage.get("thinking_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        except Exception:
            pass

    def get_model_info(self) -> Dict[str, Any]:
        """Consulta los modelos reales disponibles en el CLI de Antigravity y el estado actual."""
        cli_available = bool(self.agy_path and self.agy_path.exists())
        available_models = []
        models_details = []

        if AntigravityBridge._cached_available_models and AntigravityBridge._cached_models_details:
            available_models = list(AntigravityBridge._cached_available_models)
            models_details = list(AntigravityBridge._cached_models_details)
        elif cli_available:
            try:
                run_kwargs = {
                    "capture_output": True,
                    "text": True,
                    "timeout": 5,
                    "errors": "replace",
                }
                if sys.platform == "win32":
                    run_kwargs["creationflags"] = CREATE_NO_WINDOW

                res = subprocess.run(
                    [str(self.agy_path), "models"],
                    **run_kwargs,
                )
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        line = line.strip()
                        if line and not line.startswith("Fetching"):
                            parts = line.split(None, 1)
                            if parts:
                                m_id = parts[0]
                                raw_title = parts[1] if len(parts) > 1 else None
                                disp = self._format_display_name(m_id, raw_title)
                                eff = self._infer_effort(m_id)
                                available_models.append(m_id)
                                models_details.append({
                                    "id": m_id,
                                    "display_name": disp,
                                    "effort": eff,
                                })
                    if available_models:
                        AntigravityBridge._cached_available_models = available_models
                        AntigravityBridge._cached_models_details = models_details
            except Exception:
                pass

        if not available_models:
            fallback_specs = [
                ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)", "high"),
                ("gemini-3.8-flash-medium", "Gemini 3.8 Flash (Medium)", "medium"),
                ("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)", "low"),
                ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)", "high"),
                ("claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)", "high"),
                ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)", "medium"),
            ]
            available_models = [spec[0] for spec in fallback_specs]
            models_details = [
                {"id": spec[0], "display_name": spec[1], "effort": spec[2]}
                for spec in fallback_specs
            ]

        display_name = self._format_display_name(self.model)

        return {
            "configured_model": self.model,
            "display_name": display_name,
            "effort": self.effort,
            "cli_path": str(self.agy_path) if self.agy_path else None,
            "cli_available": cli_available,
            "available_models": available_models,
            "models_details": models_details,
            "last_telemetry": self.last_telemetry,
        }

    async def run_prompt(
        self,
        prompt: str,
        json_mode: bool = True,
        timeout_seconds: int = 180,
        effort: Optional[str] = None,
        cwd: Optional[Union[str, Path]] = None,
    ) -> Union[Dict[str, Any], str]:
        """`_ejecutar_prompt`, pero sin cuota no falla: espera (`CUOTA`) y repite la misma llamada
        cuando vuelve. Un stop la cancela como siempre."""
        admision = _admision_agy()
        while True:
            # La espera de cuota nunca reserva un permiso de ejecución.
            turno = await CUOTA.esperar_turno()
            gen = CUOTA.generacion
            try:
                async with admision:
                    # Otra llamada pudo agotar la cuota mientras esperábamos cupo.
                    # También un dueño de sondeo obsoleto debe volver a esperar turno.
                    if gen != CUOTA.generacion or (CUOTA.reanuda_en is not None and not turno):
                        continue
                    gen = CUOTA.generacion
                    try:
                        resultado = await self._ejecutar_prompt(prompt, json_mode, timeout_seconds, effort, cwd)
                    except CuotaAgotada as e:
                        # Publicar el agotamiento ANTES de ceder cupo a otro admitido.
                        CUOTA.agotada(str(e))
                        continue
                    except Exception as e:
                        if es_saturacion_agy(e):
                            _estado_admision(admision.capacidad, saturacion=True)
                        # AGY respondió (con otro error): la cuota no es el problema.
                        CUOTA.restablecida(generacion=gen)
                        raise
                    CUOTA.restablecida(generacion=gen)
                    return resultado
            finally:
                # Incluye cancelación al esperar admisión y durante el intento.
                # Un seguidor nunca suelta el turno que pertenece a otra llamada.
                if turno:
                    CUOTA.soltar_turno()

    async def _ejecutar_prompt(
        self,
        prompt: str,
        json_mode: bool = True,
        timeout_seconds: int = 180,
        effort: Optional[str] = None,
        cwd: Optional[Union[str, Path]] = None,
    ) -> Union[Dict[str, Any], str]:
        """Ejecuta un prompt no-interactivo invocando agy.exe con modelo explícito y bypass de permisos.

        `cwd`: directorio de trabajo del proceso agy. Los prompts con texto de terceros (Radar) lo
        corren en un directorio vacío y descartable (`radar_agy_cwd`), así un prompt injection no
        encuentra el repo ni los datos del candidato al alcance de las herramientas de agy.
        Sin `cwd` (flujo manual) se hereda el del servidor, como siempre.
        """
        if not self.agy_path or not self.agy_path.exists():
            raise FileNotFoundError(
                f"No se encontró el ejecutable de Antigravity CLI ('agy.exe'). "
                f"Verifique la instalación en {DEFAULT_AGY_PATHS[0]} o en el PATH del sistema."
            )

        por_stdin = len(prompt) > LARGO_MAX_PEDIDO_EN_LINEA
        cmd = self._build_command(prompt, json_mode=json_mode, effort=effort, por_stdin=por_stdin)

        process = None
        try:
            exec_kwargs = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if por_stdin:
                exec_kwargs["stdin"] = asyncio.subprocess.PIPE
            if sys.platform == "win32":
                exec_kwargs["creationflags"] = CREATE_NO_WINDOW
            else:
                exec_kwargs["start_new_session"] = True
            if cwd is not None:
                exec_kwargs["cwd"] = str(cwd)

            process = await _crear_proceso(
                *cmd,
                **exec_kwargs,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(mensaje_stdin(prompt)) if por_stdin else process.communicate(),
                timeout=timeout_seconds
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            if por_stdin:
                stdout = resultado_stream(stdout, json_mode)
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            self._record_telemetry(stdout, effort=effort)
            error_agy = texto_de_error(stdout, stderr, process.returncode, json_mode)
            if error_agy:
                _registrar_error_agy(error_agy)
                _ticket_ia("falla_agy", error_agy, {"modelo": self.model, "esfuerzo": effort,
                                                     "codigo_salida": process.returncode})
            if error_agy and _RE_CUOTA.search(error_agy):
                self._anotar_llamada(effort, error="sin cuota")
                raise CuotaAgotada(error_agy)
            if error_agy and _RE_SATURACION.search(error_agy):
                self._anotar_llamada(effort, error="Saturación AGY (503)")
                raise RuntimeError(error_agy)
            self._anotar_llamada(effort)

            if process.returncode != 0:
                if stdout.strip():
                    return extract_json_payload(stdout) if json_mode else stdout
                raise RuntimeError(
                    f"Fallo al ejecutar agy CLI (código {process.returncode}): {stderr.strip()}"
                )

            if json_mode:
                return extract_json_payload(stdout)
            return stdout

        except asyncio.CancelledError:
            if process is not None:
                await _terminar_proceso(process)
            raise
        except asyncio.TimeoutError:
            if process is not None:
                await _terminar_proceso(process)
            self._anotar_llamada(effort, error=f"timeout {timeout_seconds}s")
            _ticket_ia("timeout_agy", f"AGY no respondió en {timeout_seconds} s",
                       {"modelo": self.model, "esfuerzo": effort})
            raise TimeoutError(
                f"La llamada a Antigravity CLI excedió el tiempo límite de {timeout_seconds}s."
            )
        finally:
            if process is not None:
                try:
                    _cerrar_proceso(process)
                except Exception:
                    logger.exception("[AGY] Falló el cierre de los recursos del proceso")



@contextlib.contextmanager
def radar_agy_cwd():
    """Directorio vacío y propio (bajo el temp del sistema, nunca el repo) para correr agy sobre
    texto de terceros; se borra al salir. Uso: `with radar_agy_cwd() as cwd: run_prompt(..., cwd=cwd)`."""
    with tempfile.TemporaryDirectory(prefix=_PREFIJO_CWD_AISLADO, ignore_cleanup_errors=True) as d:
        yield Path(d)
