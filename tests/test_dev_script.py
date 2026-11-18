"""`tools/dev.ps1` (portal dev LinkedOut, 13/11) se niega a tocar producción: rechaza el puerto 1111 (y todo
puerto que no sea el 1112), la carpeta de datos real y el `.env` real, antes de hacer nada.

Corre el script real con PowerShell sobre una config simulada (`LINKEDOUT_CONFIG_DIR`) y con `$HOME` del
subproceso en la carpeta de prueba: los datos y el `.env` «reales» de la prueba son carpetas temporales."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import powershell_utf8
from tests.guardas_produccion import UBICACION_VIEJA

RAIZ = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"),
                                reason="script de Windows")


@pytest.fixture
def entorno(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    datos_reales = tmp_path / "datos-reales"
    datos_reales.mkdir()
    env_real = tmp_path / "real.env"
    env_real.write_text("GMAIL_ADDRESS=yo@example.com\n", encoding="utf-8")
    (config / "despliegue.json").write_text(json.dumps({
        "produccion": str(tmp_path / "produccion-vacia"), "datos": str(datos_reales), "env": str(env_real),
        "tarea": "linkedout-tarea-inexistente-de-prueba", "puerto": 1111, "registro": str(tmp_path / "registro.log"),
    }), encoding="utf-8")
    hogar = tmp_path / "hogar"
    hogar.mkdir()
    return {"config": config, "datos": datos_reales, "env": env_real, "hogar": hogar}


def _dev(entorno, *argumentos):
    for script in ("dev.ps1", "despliegue_comun.ps1"):
        assert "LINKEDOUT_CONFIG_DIR" in (RAIZ / "tools" / "despliegue_comun.ps1").read_text(encoding="utf-8-sig")
    variables = dict(os.environ, LINKEDOUT_CONFIG_DIR=str(entorno["config"]), USERPROFILE=str(entorno["hogar"]),
                     HOME=str(entorno["hogar"]))
    return powershell_utf8.correr(RAIZ / "tools" / "dev.ps1", argumentos, variables)


def _sin_efectos(entorno):
    for creado in ("dev", "dev-datos", "dev.env"):
        assert not (entorno["hogar"] / "LinkedOut" / creado).exists(), "el script creó cosas antes de validar"


@pytest.mark.parametrize("destino", [
    "LinkedOut/produccion", "LinkedOut/datos", "LinkedOut/datos/candidato", UBICACION_VIEJA, f"{UBICACION_VIEJA}/produccion",
])
def test_rechaza_las_carpetas_de_produccion_como_datos(entorno, destino):
    """Producción de antes del corte (~/UBICACION_VIEJA) y la de después del corte (~/LinkedOut/{produccion,datos})."""
    codigo, salida = _dev(entorno, "-RefrescarDatos", "-Datos", str(entorno["hogar"] / destino))
    assert codigo != 0 and "producción" in salida
    _sin_efectos(entorno)


@pytest.mark.parametrize("archivo", ["portal.cmd", "portal.vbs", "vigilante.ps1", "despliegue.json", "CONGELADO"])
def test_rechaza_pisar_un_archivo_de_produccion(entorno, archivo):
    """El lanzador, el vigilante y la config de producción (14/11) viven sueltos en ~/LinkedOut, al lado del dev."""
    destino = entorno["hogar"] / "LinkedOut" / archivo
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("original", encoding="utf-8")
    codigo, salida = _dev(entorno, "-Iniciar", "-EnvArchivo", str(destino))
    assert codigo != 0 and "producción" in salida and archivo in salida
    assert destino.read_text(encoding="utf-8") == "original"
    _sin_efectos(entorno)


def test_rechaza_el_env_de_produccion_despues_del_corte(entorno):
    codigo, salida = _dev(entorno, "-Iniciar", "-EnvArchivo", str(entorno["hogar"] / "LinkedOut" / "produccion.env"))
    assert codigo != 0 and ".env" in salida and "producción" in salida
    _sin_efectos(entorno)


def test_por_defecto_usa_las_carpetas_de_desarrollo():
    texto = (RAIZ / "tools" / "dev.ps1").read_text(encoding="utf-8-sig")
    assert "Join-Path $HOME 'LinkedOut'" in texto
    for nombre in ("'dev'", "'dev-datos'", "'dev.env'"):
        assert nombre in texto


@pytest.mark.parametrize("puerto", ["1111", "1113"])
def test_rechaza_cualquier_puerto_que_no_sea_el_1112(entorno, puerto):
    codigo, salida = _dev(entorno, "-Iniciar", "-Puerto", puerto)
    assert codigo != 0 and "1112" in salida and puerto in salida
    _sin_efectos(entorno)


@pytest.mark.parametrize("adentro", [False, True])
def test_rechaza_la_carpeta_de_datos_real(entorno, adentro):
    datos = entorno["datos"] / "sub" if adentro else entorno["datos"]
    codigo, salida = _dev(entorno, "-RefrescarDatos", "-Datos", str(datos))
    assert codigo != 0 and "datos reales" in salida
    _sin_efectos(entorno)


def test_rechaza_el_env_real(entorno):
    codigo, salida = _dev(entorno, "-Iniciar", "-EnvArchivo", str(entorno["env"]))
    assert codigo != 0 and ".env real" in salida
    _sin_efectos(entorno)


def test_sin_accion_explica_el_uso(entorno):
    codigo, salida = _dev(entorno)
    assert codigo != 0 and "-Iniciar" in salida
    _sin_efectos(entorno)


def test_el_script_tiene_bom_para_powershell_5():
    assert (RAIZ / "tools" / "dev.ps1").read_bytes()[:3] == b"\xef\xbb\xbf"


def test_reiniciar_el_dev_no_apaga_el_radar():
    # «preparar» (Radar detenido, sin búsquedas, en pausa) vale solo para datos recién copiados de producción;
    # en cada arranque apagaba el Radar y las búsquedas que el creador había prendido (16/11)
    import re
    script = (RAIZ / "tools" / "dev.ps1").read_text(encoding="utf-8-sig")
    funciones = dict(re.findall(r"function ([\w-]+) \{\r?\n(.*?)\r?\n\}", script, re.S))
    assert "'preparar'" not in funciones["Start-Dev"]
    assert "'preparar'" in funciones["Update-Datos"]


def test_detener_el_dev_cierra_su_arbol():
    # Detener o actualizar el dev cierra también sus Edge y sus AGY: no quedan huérfanos (creador, 16/11)
    import re
    script = (RAIZ / "tools" / "dev.ps1").read_text(encoding="utf-8-sig")
    funciones = dict(re.findall(r"function ([\w-]+) \{\r?\n(.*?)\r?\n\}", script, re.S))
    assert re.search(r"taskkill(?:\.exe)?\s+/T\s+/F\s+/PID", funciones["Stop-Dev"])
    assert "Stop-Process -Id $id" not in funciones["Stop-Dev"]


def test_el_dev_reanuda_el_radar_al_reiniciar():
    # Con LINKEDOUT_SIN_RADAR=1 cada reinicio dejaba el Radar y los portales parados aunque el estado dijera
    # «CORRIENDO» (creador, 16/11: «no veo los radares prendidos»). Solo los portales de prueba lo llevan.
    script = (RAIZ / "tools" / "dev.ps1").read_text(encoding="utf-8-sig")
    assert "set LINKEDOUT_SIN_RADAR=1" not in script


def test_el_dev_es_de_prueba_por_defecto():
    # Creador, 17/11: producción es lo real; el dev arranca con el mail en modo prueba salvo -MailReal
    import re
    script = (RAIZ / "tools" / "dev.ps1").read_text(encoding="utf-8-sig")
    assert "[switch]$MailReal" in script
    funciones = dict(re.findall(r"function ([\w-]+) \{\r?\n(.*?)\r?\n\}", script, re.S))
    assert "'propio'" in funciones["Start-Dev"] and "$MailReal" in funciones["Start-Dev"]


def test_el_dev_prendido_deja_su_marca_y_detenido_la_quita():
    # Sin la marca dev-activo, el guardián apaga el 1112 (creador, 17/11: el dev apagado hasta que haga falta)
    import re
    script = (RAIZ / "tools" / "dev.ps1").read_text(encoding="utf-8-sig")
    funciones = dict(re.findall(r"function ([\w-]+) \{\r?\n(.*?)\r?\n\}", script, re.S))
    assert "$marcaDev" in funciones["Start-Dev"]
    assert re.search(r"if \(\$Detener\) \{[^}]*Remove-Item -LiteralPath \$marcaDev", script)
