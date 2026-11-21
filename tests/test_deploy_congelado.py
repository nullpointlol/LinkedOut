"""Producción congelada: mientras exista `CONGELADO` en la carpeta de config de producción,
`tools/deploy.ps1` y `tools/rollback.ps1` se niegan a correr y dicen por qué y cómo se libera.

Corre los scripts reales con PowerShell sobre una carpeta de config simulada (`LINKEDOUT_CONFIG_DIR`): la
producción de la config es una carpeta vacía, así que sin el candado fallan antes de tocar nada."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import powershell_utf8

RAIZ = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"),
                                reason="scripts de despliegue de Windows")


@pytest.fixture
def config(tmp_path):
    carpeta = tmp_path / "config"
    carpeta.mkdir()
    (tmp_path / "produccion-vacia").mkdir()
    (carpeta / "despliegue.json").write_text(json.dumps({
        "produccion": str(tmp_path / "produccion-vacia"), "datos": str(tmp_path / "datos"),
        "tarea": "linkedout-tarea-inexistente-de-prueba", "puerto": 1, "registro": str(tmp_path / "registro.log"),
    }), encoding="utf-8")
    return carpeta


def _correr(script, config):
    # Doble seguridad (una versión de este test corrió el deploy real porque el script todavía no leía
    # LINKEDOUT_CONFIG_DIR): el script tiene que soportar la variable, y $HOME del subproceso es la carpeta
    # de prueba, así que ni un script viejo llega a la config real.
    assert "LINKEDOUT_CONFIG_DIR" in (RAIZ / "tools" / "despliegue_comun.ps1").read_text(encoding="utf-8-sig"),         "despliegue_comun.ps1 no lee LINKEDOUT_CONFIG_DIR: correrlo usaría la config real de producción"
    hogar = config.parent / "hogar"
    hogar.mkdir(exist_ok=True)
    entorno = dict(os.environ, LINKEDOUT_CONFIG_DIR=str(config), USERPROFILE=str(hogar), HOME=str(hogar))
    return powershell_utf8.correr(RAIZ / "tools" / script, ["-Produccion"], entorno)


@pytest.mark.parametrize("script", ["deploy.ps1", "rollback.ps1"])
def test_con_congelado_se_niega_y_dice_como_liberarlo(config, script):
    candado = config / "CONGELADO"
    candado.write_text("Congelado a mano", encoding="utf-8")
    codigo, salida = _correr(script, config)
    assert codigo != 0
    assert "Producción congelada" in salida
    assert str(candado) in salida and "Remove-Item" in salida
    assert "Congelado a mano" in salida  # el motivo escrito en el archivo
    assert not (config.parent / "registro.log").exists()  # no registró nada: no hizo nada


@pytest.mark.parametrize("script", ["deploy.ps1", "rollback.ps1"])
def test_sin_congelado_sigue_de_largo(config, script):
    codigo, salida = _correr(script, config)
    assert codigo != 0  # la producción simulada es una carpeta vacía: falla después, en git
    assert "Producción congelada" not in salida
