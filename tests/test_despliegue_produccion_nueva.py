"""Despliegue sobre la producción nueva (paso 5 del plan de separación, 14/11).

Producción es un clon propio (`~/LinkedOut/produccion`) que se actualiza solo a versiones `linkedout-estable-*` de
`origin/develop`, con la tarea `LinkedOut-portal`. La config pasa a `~/LinkedOut/despliegue.json`, y el `CONGELADO`
viejo (en la ubicación de antes del corte, `UBICACION_VIEJA`) sigue frenando todo.

Todo corre sobre un hogar simulado (`USERPROFILE`/`HOME`) y repos git temporales: ningún test llega a la config, los
datos, la tarea ni el portal reales."""
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
                                reason="scripts de despliegue de Windows")


def _git(carpeta, *args):
    r = subprocess.run(["git", "-C", str(carpeta), *args], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _hogar(tmp_path):
    hogar = tmp_path / "hogar"
    hogar.mkdir(exist_ok=True)
    return hogar


def _entorno(hogar, **extra):
    entorno = {k: v for k, v in os.environ.items() if k != "LINKEDOUT_CONFIG_DIR"}
    entorno.update(USERPROFILE=str(hogar), HOME=str(hogar), **extra)
    return entorno


def _args(script):
    return ["-Produccion", "-Version", "linkedout-estable-x"] if script == "deploy.ps1" else ["-Produccion"]


def _config_en(carpeta, tmp_path):
    carpeta.mkdir(parents=True, exist_ok=True)
    (tmp_path / "produccion-vacia").mkdir(exist_ok=True)
    (carpeta / "despliegue.json").write_text(json.dumps({
        "produccion": str(tmp_path / "produccion-vacia"), "datos": str(tmp_path / "datos"),
        "tarea": "linkedout-tarea-inexistente-de-prueba", "puerto": 1,
        "registro": str(tmp_path / "registro.log")}), encoding="utf-8")


# ---------------------------------------------------------------- config en ~/LinkedOut y CONGELADO viejo

@pytest.mark.parametrize("script", ["deploy.ps1", "rollback.ps1"])
def test_la_config_por_defecto_esta_en_linkedout(tmp_path, script):
    hogar = _hogar(tmp_path)
    _config_en(hogar / "LinkedOut", tmp_path)
    candado = hogar / "LinkedOut" / "CONGELADO"
    candado.write_text("congelado de prueba", encoding="utf-8")
    codigo, salida = powershell_utf8.correr(RAIZ / "tools" / script, _args(script), _entorno(hogar))
    assert codigo != 0 and "Producción congelada" in salida and str(candado) in salida
    assert not (tmp_path / "registro.log").exists()


@pytest.mark.parametrize("script", ["deploy.ps1", "rollback.ps1"])
def test_el_congelado_viejo_sigue_frenando(tmp_path, script):
    hogar = _hogar(tmp_path)
    _config_en(hogar / "LinkedOut", tmp_path)
    viejo = hogar / UBICACION_VIEJA / "CONGELADO"
    viejo.parent.mkdir()
    viejo.write_text("congelado viejo", encoding="utf-8")
    codigo, salida = powershell_utf8.correr(RAIZ / "tools" / script, _args(script), _entorno(hogar))
    assert codigo != 0 and "Producción congelada" in salida and str(viejo) in salida
    assert not (tmp_path / "registro.log").exists()


def test_sin_config_en_linkedout_no_hace_nada(tmp_path):
    hogar = _hogar(tmp_path)
    codigo, salida = powershell_utf8.correr(RAIZ / "tools" / "deploy.ps1", ["-Produccion", "-Version", "x"],
                                            _entorno(hogar))
    # PowerShell parte y abrevia las rutas largas de un error: alcanza con la carpeta y el archivo
    assert codigo != 0 and "despliegue.json" in salida and "\\LinkedOut\\despliegue.json" in "".join(salida.split())


def test_env_por_defecto_es_produccion_env_junto_a_los_datos(tmp_path):
    hogar = _hogar(tmp_path)
    _config_en(hogar / "LinkedOut", tmp_path)
    script = tmp_path / "leer.ps1"
    script.write_text(f". '{RAIZ / 'tools' / 'despliegue_comun.ps1'}'\n$c = Get-Config\nWrite-Output $c.env\n",
                      encoding="utf-8-sig")
    codigo, salida = powershell_utf8.correr(script, [], _entorno(hogar))
    assert codigo == 0, salida
    assert salida.strip() == str(tmp_path / "produccion.env")


# ---------------------------------------------------------------- solo versiones linkedout-estable-* de develop

@pytest.fixture
def repos(tmp_path):
    """origin (bare) con develop y una rama aparte; producción = clon de origin, como el real."""
    origen = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(origen))
    trabajo = tmp_path / "trabajo"
    _git(tmp_path, "init", "-q", "-b", "develop", str(trabajo))
    _git(trabajo, "config", "user.email", "prueba@example.com")
    _git(trabajo, "config", "user.name", "Prueba")
    (trabajo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    _git(trabajo, "add", "-A")
    _git(trabajo, "commit", "-q", "-m", "uno")
    _git(trabajo, "tag", "-a", "linkedout-estable-20260101", "-m", "v1")
    _git(trabajo, "tag", "-a", "v1", "-m", "otro nombre")
    _git(trabajo, "switch", "-q", "-c", "otra")
    (trabajo / "x.txt").write_text("x", encoding="utf-8")
    _git(trabajo, "add", "-A")
    _git(trabajo, "commit", "-q", "-m", "fuera de develop")
    _git(trabajo, "tag", "-a", "linkedout-estable-fuera", "-m", "no está en develop")
    _git(trabajo, "switch", "-q", "develop")
    _git(trabajo, "remote", "add", "origin", str(origen))
    _git(trabajo, "push", "-q", "origin", "develop", "otra", "--tags")
    produccion = tmp_path / "produccion"
    _git(tmp_path, "clone", "-q", str(origen), str(produccion))
    return {"trabajo": trabajo, "produccion": produccion, "tmp": tmp_path}


def _funcion(repos, codigo_ps):
    script = repos["tmp"] / "funcion.ps1"
    script.write_text(f". '{RAIZ / 'tools' / 'despliegue_comun.ps1'}'\n"
                      f"$cfg = @{{ produccion = '{repos['produccion']}' }}\n"
                      f"try {{ {codigo_ps} }} catch {{ Write-Output ('ERROR: ' + $_.Exception.Message); exit 1 }}\n",
                      encoding="utf-8-sig")
    return powershell_utf8.correr(script, [], _entorno(_hogar(repos["tmp"])))


def test_resuelve_una_version_de_develop(repos):
    codigo, salida = _funcion(repos, "Write-Output (Resolve-Version $cfg 'linkedout-estable-20260101')")
    assert codigo == 0, salida
    assert salida.strip() == _git(repos["trabajo"], "rev-parse", "linkedout-estable-20260101^{commit}")


def test_trae_una_version_nueva_de_origin(repos):
    t = repos["trabajo"]
    (t / "y.txt").write_text("y", encoding="utf-8")
    _git(t, "add", "-A")
    _git(t, "commit", "-q", "-m", "dos")
    _git(t, "tag", "-a", "linkedout-estable-20260102", "-m", "v2")
    _git(t, "push", "-q", "origin", "develop", "--tags")
    codigo, salida = _funcion(repos, "Write-Output (Resolve-Version $cfg 'linkedout-estable-20260102')")
    assert codigo == 0, salida
    assert salida.strip() == _git(t, "rev-parse", "HEAD")


@pytest.mark.parametrize("version,motivo", [
    ("v1", "linkedout-estable-"),
    ("develop", "linkedout-estable-"),
    ("linkedout-estable-fuera", "develop"),
    ("linkedout-estable-no-existe", "no existe"),
])
def test_rechaza_lo_que_no_es_una_version_estable_de_develop(repos, version, motivo):
    codigo, salida = _funcion(repos, f"Write-Output (Resolve-Version $cfg '{version}')")
    assert codigo != 0 and "ERROR" in salida and motivo in salida


def test_avisa_si_cambian_los_requisitos(repos):
    t = repos["trabajo"]
    a = _git(t, "rev-parse", "HEAD")
    codigo, salida = _funcion(repos, f"Write-Output (Test-RequisitosIguales $cfg '{a}' '{a}')")
    assert codigo == 0 and salida.strip() == "True", salida
    (t / "requirements.txt").write_text("fastapi\nnuevo\n", encoding="utf-8")
    _git(t, "commit", "-q", "-am", "requisito nuevo")
    _git(t, "push", "-q", "origin", "develop")
    _git(repos["produccion"], "fetch", "-q", "origin")
    b = _git(t, "rev-parse", "HEAD")
    codigo, salida = _funcion(repos, f"Write-Output (Test-RequisitosIguales $cfg '{a}' '{b}')")
    assert codigo == 0 and salida.strip().splitlines()[-1] == "False", salida


def test_requisitos_cambiados_pero_ya_instalados_no_frenan(repos):
    # 17/11: el deploy frenaba siempre que cambiara requirements.txt, aunque todo estuviera instalado
    import importlib.metadata
    t = repos["trabajo"]
    a = _git(t, "rev-parse", "HEAD")
    (t / "requirements.txt").write_text(f"pytest=={importlib.metadata.version('pytest')}\n", encoding="utf-8")
    _git(t, "commit", "-q", "-am", "requisito ya instalado")
    _git(t, "push", "-q", "origin", "develop")
    _git(repos["produccion"], "fetch", "-q", "origin")
    b = _git(t, "rev-parse", "HEAD")
    codigo, salida = _funcion(repos, f"Write-Output (Test-RequisitosIguales $cfg '{a}' '{b}')")
    assert codigo == 0 and salida.strip().splitlines()[-1] == "True", salida


def test_deploy_sin_version_explica_el_uso(tmp_path):
    hogar = _hogar(tmp_path)
    _config_en(hogar / "LinkedOut", tmp_path)
    codigo, salida = powershell_utf8.correr(RAIZ / "tools" / "deploy.ps1", ["-Produccion"], _entorno(hogar))
    assert codigo != 0 and "-Version linkedout-estable-" in salida
    assert not (tmp_path / "registro.log").exists()


def test_detener_produccion_cierra_su_arbol():
    # 17/11: Stop-Portal mataba solo el proceso del puerto y dejaba huérfanos los Edge y AGY de producción
    import re
    script = (RAIZ / "tools" / "despliegue_comun.ps1").read_text(encoding="utf-8-sig")
    cuerpo = re.search(r"function Stop-Portal\(\$cfg\) \{\r?\n(.*?)\r?\n\}", script, re.S).group(1)
    assert re.search(r"taskkill(?:\.exe)?\s+/T\s+/F\s+/PID", cuerpo)
    assert "Stop-Process -Id $proc" not in cuerpo
