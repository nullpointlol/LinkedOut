"""Candado de la compuerta: dos `tools/verificar.py` a la vez se pisan y fallan los tests de tiempo.
La regla «de a una» dependía de la memoria de cada sesión; ahora la compuerta toma un candado del sistema
(un archivo con el PID en %TEMP%) y la segunda espera, diciéndolo en la salida. Un candado de un PID muerto
se libera solo. Los tests usan un archivo propio (`LINKEDOUT_CANDADO_COMPUERTA`), nunca el real."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import candado_compuerta  # noqa: E402

GUARDA = 60

USAR = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
import candado_compuerta
with candado_compuerta.candado(sys.argv[2], sondeo_s=0.2):
    inicio = time.time()
    time.sleep(float(sys.argv[3]))
    fin = time.time()
print("TRAMO " + json.dumps([inicio, fin]), flush=True)
"""


@pytest.fixture
def archivo(tmp_path, monkeypatch):
    ruta = tmp_path / "compuerta.lock"
    monkeypatch.setenv(candado_compuerta.VARIABLE, str(ruta))
    return ruta


def _lanzar(quien, segundos):
    return subprocess.Popen([sys.executable, "-c", USAR, str(RAIZ / "tools"), quien, str(segundos)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            env=dict(os.environ, PYTHONUTF8="1"))


def _tramo(salida):
    linea = next(l for l in salida.splitlines() if l.startswith("TRAMO "))
    return json.loads(linea[len("TRAMO "):])


def test_dos_compuertas_a_la_vez_la_segunda_espera_a_la_primera(archivo):
    primera = _lanzar("primera", 2.0)
    limite = time.monotonic() + GUARDA
    while not archivo.exists():
        assert time.monotonic() < limite and primera.poll() is None, "la primera no tomó el candado"
        time.sleep(0.05)
    segunda = _lanzar("segunda", 0.1)
    salida_1, _ = primera.communicate(timeout=GUARDA)
    salida_2, _ = segunda.communicate(timeout=GUARDA)
    assert primera.returncode == 0 and segunda.returncode == 0, (salida_1, salida_2)
    inicio_1, fin_1 = _tramo(salida_1)
    inicio_2, _ = _tramo(salida_2)
    assert inicio_2 >= fin_1, "la segunda corrió mientras la primera tenía el candado"
    assert "Esperando" in salida_2 and "primera" in salida_2 and str(primera.pid) in salida_2
    assert not archivo.exists(), "el candado quedó tomado al terminar"


def test_un_candado_de_un_pid_muerto_se_libera_solo(archivo):
    muerto = subprocess.Popen([sys.executable, "-c", "pass"])
    muerto.wait(GUARDA)
    archivo.write_text(json.dumps({"pid": muerto.pid, "quien": "compuerta cortada", "desde": "12/11 14:00"}),
                       encoding="utf-8")
    avisos = []
    inicio = time.monotonic()
    with candado_compuerta.candado("nueva", sondeo_s=0.2, avisar=avisos.append):
        assert json.loads(archivo.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert time.monotonic() - inicio < 5
    assert any("se libera" in a for a in avisos), avisos
    assert not archivo.exists()


def test_el_candado_se_suelta_aunque_la_compuerta_falle(archivo):
    with pytest.raises(SystemExit):
        with candado_compuerta.candado("con error"):
            sys.exit(1)
    assert not archivo.exists()


def test_el_pid_propio_esta_vivo_y_uno_terminado_no():
    assert candado_compuerta.pid_vivo(os.getpid())
    terminado = subprocess.Popen([sys.executable, "-c", "pass"])
    terminado.wait(GUARDA)
    assert not candado_compuerta.pid_vivo(terminado.pid)


def test_verificar_corre_todo_con_el_candado(archivo, monkeypatch):
    import verificar
    vistos = []

    def correr(nombre, args):
        vistos.append((nombre, archivo.exists()))

    monkeypatch.setattr(verificar, "_correr", correr)
    monkeypatch.setenv("CI", "true")  # la suite completa corre solo en el CI
    monkeypatch.setattr(sys, "argv", ["verificar.py"])
    verificar.main()
    assert vistos and all(tomado for _, tomado in vistos), vistos
    assert not archivo.exists()
