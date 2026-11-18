"""El aislamiento cubre también la importación del servidor y los respaldos antiguos."""
import os
import subprocess
import sys
from pathlib import Path

from core import database


def test_importar_conftest_no_inicializa_datos_configurados_del_usuario(tmp_path):
    datos_usuario = tmp_path / "datos_usuario_simulados"
    entorno = dict(os.environ, LINKEDOUT_DATA_DIR=str(datos_usuario))
    subprocess.run(
        [sys.executable, "-c", "import runpy; runpy.run_path('tests/conftest.py')"],
        cwd=Path(__file__).resolve().parent.parent,
        env=entorno, check=True, capture_output=True, text=True,
    )
    assert not (datos_usuario / "postulaciones.db").exists()


def test_respaldo_legacy_de_postulacion_queda_en_temporal(tmp_path):
    respaldo = database.BASE_DIR / "data" / "postulaciones.db"
    assert respaldo.is_relative_to(tmp_path)
