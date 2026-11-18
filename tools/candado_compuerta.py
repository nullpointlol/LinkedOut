"""Candado del sistema para la compuerta (12/11): una sola `tools/verificar.py` a la vez en la PC.

Dos compuertas en paralelo (de sesiones distintas, o una y un deploy, que también corre verificar.py) se
pisan y fallan los tests de tiempo. El candado es un archivo con el PID de quien lo tiene en %TEMP%
(o en `LINKEDOUT_CANDADO_COMPUERTA`). Si lo tiene otro proceso vivo, se espera y se avisa en la
salida; si su PID ya no existe (una compuerta cortada), se libera solo. Durante la transición al nombre LinkedOut
se toman los dos archivos (el del nombre viejo primero): una compuerta con el código anterior solo conoce el viejo.

    with candado("compuerta"):
        ...
"""
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.paths import variable_entorno  # noqa: E402

VARIABLE = "LINKEDOUT_CANDADO_COMPUERTA"
NOMBRE = "linkedout-compuerta.lock"
NOMBRE_VIEJO = "buscatrabajo4-compuerta.lock"  # compatibilidad: se borra tras el deploy
SONDEO_S = 5.0          # cada cuánto se vuelve a mirar si se liberó
AVISO_CADA_S = 60.0     # cada cuánto se repite «Esperando…» en la salida


def rutas() -> List[Path]:
    """Los archivos del candado, en el orden en que se toman. Con la variable, solo ese (los tests)."""
    propia = variable_entorno("CANDADO_COMPUERTA")
    if propia:
        return [Path(propia)]
    temporal = Path(tempfile.gettempdir())
    return [temporal / NOMBRE_VIEJO, temporal / NOMBRE]  # compatibilidad: se borra tras el deploy (queda NOMBRE)


def pid_vivo(pid: int) -> bool:
    """¿Existe el proceso? En Windows sin `os.kill(pid, 0)`, que ahí termina el proceso."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # acceso denegado: existe, es de otro usuario
        try:
            codigo = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(codigo)):
                return True
            return codigo.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _leer(archivo: Path) -> Optional[dict]:
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}


def _tomar(archivo: Path, quien: str) -> bool:
    try:
        fd = os.open(archivo, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "quien": quien, "desde": datetime.now().strftime("%d/%m %H:%M:%S")}, f)
    return True


def _esperar_y_tomar(archivo: Path, quien: str, sondeo_s: float, avisar: Callable[[str], None]) -> None:
    archivo.parent.mkdir(parents=True, exist_ok=True)
    ultimo_aviso = None
    while not _tomar(archivo, quien):
        dueno = _leer(archivo)
        if dueno is None:
            continue  # se liberó entre el intento y la lectura
        pid = dueno.get("pid")
        if not isinstance(pid, int) or not pid_vivo(pid):
            avisar(f"[compuerta] El candado era de un proceso que ya no existe ({dueno}): se libera.")
            if _leer(archivo) == dueno:  # nadie lo tomó mientras tanto
                try:
                    archivo.unlink()
                except FileNotFoundError:
                    pass
            continue
        ahora = time.monotonic()
        if ultimo_aviso is None or ahora - ultimo_aviso >= AVISO_CADA_S:
            avisar(f"[compuerta] Esperando: otra compuerta corre (PID {pid}, «{dueno.get('quien', '?')}», "
                   f"desde {dueno.get('desde', '?')}). Arranca cuando termine.")
            ultimo_aviso = ahora
        time.sleep(sondeo_s)


def _soltar(archivo: Path) -> None:
    if (_leer(archivo) or {}).get("pid") == os.getpid():
        try:
            archivo.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def candado(quien: str, *, sondeo_s: float = SONDEO_S, avisar: Callable[[str], None] = None):
    """Toma el candado de la compuerta (espera lo que haga falta) y lo suelta al salir, aunque falle."""
    avisar = avisar or (lambda texto: print(texto, flush=True))
    tomados = []
    try:
        for archivo in rutas():
            _esperar_y_tomar(archivo, quien, sondeo_s, avisar)
            tomados.append(archivo)
        yield
    finally:
        for archivo in reversed(tomados):
            _soltar(archivo)
