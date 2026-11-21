"""`tools/requisitos_instalados.py`: el deploy ya no frena porque cambió `requirements.txt` si lo instalado
coincide con los pines de la versión nueva; frena solo si falta un paquete o su versión es otra."""
import importlib.metadata
import subprocess
import sys
from pathlib import Path

from tools import requisitos_instalados as ri

RAIZ = Path(__file__).resolve().parent.parent
PYTEST = importlib.metadata.version("pytest")


def test_todo_instalado_con_la_version_del_pin():
    assert ri.faltantes(f"# comentario\n\npytest=={PYTEST}\n") == []


def test_sin_pin_alcanza_con_que_este_instalado():
    assert ri.faltantes("pytest\n") == []


def test_paquete_faltante():
    assert ri.faltantes("paquete-que-no-existe-linkedout==1.0\n") == ["paquete-que-no-existe-linkedout==1.0 (no está instalado)"]


def test_version_distinta():
    assert ri.faltantes("pytest==0.0.1\n") == [f"pytest==0.0.1 (instalado {PYTEST})"]


def test_nombres_con_guion_bajo_o_mayusculas():
    assert ri.faltantes(f"PyTest=={PYTEST}\n") == []


def test_cli_lee_de_stdin_y_sale_con_1_si_falta_algo():
    ok = subprocess.run([sys.executable, str(RAIZ / "tools" / "requisitos_instalados.py"), "-"],
                        input=f"pytest=={PYTEST}\n", capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    mal = subprocess.run([sys.executable, str(RAIZ / "tools" / "requisitos_instalados.py"), "-"],
                         input="pytest==0.0.1\n", capture_output=True, text=True)
    assert mal.returncode == 1 and "pytest==0.0.1" in mal.stdout
