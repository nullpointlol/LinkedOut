"""Compuerta de calidad: la corre la compuerta nocturna (tools/compuerta_nocturna.ps1) o el CI de GitHub, si vuelve.

En la PC no corre (13/11): aun con 2 workers en prioridad baja trababa al portal real. Fuera del CI (sin la
variable CI) sale con código 2 y remite a tools/tests_rapidos.py, que corre solo los tests de los archivos tocados.

    python tools/verificar.py            # todo: suite con cobertura, navegador y rendimiento (CI)
    python tools/verificar.py --rapido   # solo la suite en paralelo (sin navegador ni rendimiento) (CI)

Antes de arrancar toma el candado de la compuerta (tools/candado_compuerta.py): si otra compuerta o un deploy
corre en la PC, espera a que termine y lo avisa en la salida.

Etapas (corta en la primera que falla, con salida distinta de 0):
1. Suite en paralelo, sin navegador ni rendimiento, con cobertura de core/ y server.py y piso COBERTURA_MINIMA.
2. Tests de interfaz con navegador real (Playwright), en serie.
3. Tests de rendimiento, en serie y sin cobertura: el tiempo medido no se contamina con otra carga.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from candado_compuerta import candado  # noqa: E402  (una sola compuerta a la vez en la PC)
# Prioridad BelowNormal en Windows (13/11): la compuerta y sus hijos (pytest -n, el navegador) no le roban CPU
# al portal real. Los hijos la heredan y además se pide al lanzarlos.
BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def bajar_prioridad() -> None:
    """Deja este proceso en BelowNormal (en Windows; en otros sistemas, `nice`). Nunca frena la compuerta."""
    try:
        if sys.platform == "win32":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.nice(5)
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo bajar la prioridad de la compuerta: {e}", flush=True)


def _prioridad_baja() -> dict:
    return {"creationflags": BELOW_NORMAL_PRIORITY_CLASS} if sys.platform == "win32" else {}


# Workers fijos (13/11): con -n auto (16 en esta PC) la compuerta frenaba al portal real. LINKEDOUT_WORKERS lo cambia.
WORKERS_POR_DEFECTO = 8


def workers() -> str:
    """LINKEDOUT_WORKERS: un número o «auto» (el CI de Linux usa todos los núcleos del runner)."""
    valor = (os.environ.get("LINKEDOUT_WORKERS") or "").strip()
    if valor == "auto" or (valor.isdigit() and int(valor) > 0):
        return valor
    return str(WORKERS_POR_DEFECTO)


COBERTURA_MINIMA = 96  # medida el 12/11: 96,07 % (13351 líneas, 525 sin cubrir); ver docs/cobertura.md


def _correr(nombre: str, args: list) -> None:
    print(f"\n=== {nombre} ===", flush=True)
    inicio = time.monotonic()
    entorno = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=RAIZ, env=entorno,
                       **_prioridad_baja())
    print(f"=== {nombre}: {'OK' if r.returncode == 0 else 'FALLÓ'} en {time.monotonic() - inicio:.0f} s ===", flush=True)
    if r.returncode != 0:
        sys.exit(r.returncode)


def main() -> None:
    if not os.environ.get("CI"):
        print("La suite completa corre en el CI de GitHub (.github/workflows/ci.yml): en esta PC frena al portal real.\n"
              "Acá: python tools/tests_rapidos.py (los tests de los archivos tocados, con un worker).\n"
              "Resultado de la rama: gh run list --branch <rama> --limit 1", flush=True)
        sys.exit(2)
    bajar_prioridad()
    # Una compuerta a la vez en toda la PC (otras sesiones y el deploy): si hay otra, espera y lo dice
    with candado(f"verificar.py en {RAIZ}"):
        _etapas()


COBERTURA_SIN_PISO = ["--cov=core", "--cov=server", "--cov-report="]


def _etapa(nombre: str) -> None:
    """Etapas del CI (13/11), en jobs separados: la suite en Linux (x1 en minutos facturados), los tests de Windows
    en un job chico de Windows, el navegador y el rendimiento en Linux, y la cobertura de los dos sistemas combinada."""
    if nombre == "linux":
        _correr("Suite en Linux (sin los tests de Windows)", [
            "-n", workers(), "-m", "not ui and not rendimiento and not windows", *COBERTURA_SIN_PISO])
    elif nombre == "windows":
        _correr("Tests de Windows", ["-n", "2", "-m", "(windows or tambien_windows) and not ui and not rendimiento",
                                     *COBERTURA_SIN_PISO])
    elif nombre == "navegador":
        _correr("Interfaz con navegador real", ["-m", "ui and not rendimiento"])
        _correr("Rendimiento", ["-s", "-m", "rendimiento"])
    elif nombre == "cobertura":
        # Combina los .coverage.* de los jobs (rutas relativas, .coveragerc) y exige el piso
        for args in (["combine"], ["report", "--skip-covered", f"--fail-under={COBERTURA_MINIMA}"]):
            r = subprocess.run([sys.executable, "-m", "coverage", *args], cwd=RAIZ)
            if r.returncode != 0:
                sys.exit(r.returncode)
    else:
        print(f"Etapa desconocida: {nombre} (linux, windows, navegador o cobertura)", flush=True)
        sys.exit(2)


def _etapas() -> None:
    if "--etapa" in sys.argv:
        _etapa(sys.argv[sys.argv.index("--etapa") + 1])
        return
    # Una sola máquina: --rapido (solo la suite) y --sin-suite (navegador y rendimiento)
    rapido = "--rapido" in sys.argv
    if "--sin-suite" not in sys.argv:
        _correr("Suite (paralelo, con cobertura)", [
            "-n", workers(), "-m", "not ui and not rendimiento",
            "--cov=core", "--cov=server", "--cov-report=term:skip-covered", f"--cov-fail-under={COBERTURA_MINIMA}",
        ])
    if rapido:
        return
    _correr("Interfaz con navegador real", ["-m", "ui and not rendimiento"])
    _correr("Rendimiento", ["-s", "-m", "rendimiento"])
    print("\nCompuerta de calidad en verde.")


if __name__ == "__main__":
    main()
