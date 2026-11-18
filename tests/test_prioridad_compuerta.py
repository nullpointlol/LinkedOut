"""La compuerta corre en prioridad BelowNormal en Windows (13/11): ella y sus hijos (pytest -n, el navegador de
los tests de interfaz) no le roban CPU al portal real."""
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import verificar  # noqa: E402

BELOW_NORMAL = 0x4000  # BELOW_NORMAL_PRIORITY_CLASS
LEER = ("import ctypes; k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; "
        "k.GetPriorityClass.argtypes = [ctypes.c_void_p]; print(k.GetPriorityClass(k.GetCurrentProcess()))")

windows = pytest.mark.skipif(sys.platform != "win32", reason="prioridades de proceso de Windows")


def test_la_compuerta_pide_la_prioridad_baja_antes_de_correr(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDOUT_CANDADO_COMPUERTA", str(tmp_path / "compuerta.lock"))
    orden = []
    monkeypatch.setattr(verificar, "bajar_prioridad", lambda: orden.append("prioridad"))
    monkeypatch.setattr(verificar, "_correr", lambda nombre, args: orden.append(nombre))
    monkeypatch.setenv("CI", "true")  # la suite completa corre solo en el CI
    monkeypatch.setattr(sys, "argv", ["verificar.py"])
    verificar.main()
    assert orden[0] == "prioridad" and len(orden) > 1


@windows
def test_los_hijos_de_la_compuerta_se_lanzan_en_prioridad_baja(monkeypatch):
    llamadas = []

    class Resultado:
        returncode = 0

    monkeypatch.setattr(verificar.subprocess, "run", lambda *a, **kw: llamadas.append(kw) or Resultado())
    verificar._correr("prueba", ["-k", "nada"])
    assert llamadas and llamadas[0].get("creationflags", 0) & BELOW_NORMAL


@windows
def test_bajar_prioridad_deja_el_proceso_y_sus_hijos_en_below_normal():
    """En un proceso aparte (no se cambia la prioridad de la suite): él y un hijo quedan en BelowNormal."""
    codigo = (
        "import ctypes, subprocess, sys\n"
        f"sys.path.insert(0, {str(RAIZ / 'tools')!r})\n"
        "import verificar\n"
        "verificar.bajar_prioridad()\n"
        f"{LEER}\n"
        f"hijo = {LEER!r}\n"
        "print(subprocess.run([sys.executable, '-c', hijo], capture_output=True, text=True).stdout.strip())\n"
    )
    salida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=60)
    assert salida.returncode == 0, salida.stderr
    propio, hijo = [int(x) for x in salida.stdout.split()]
    assert propio == BELOW_NORMAL and hijo == BELOW_NORMAL
