"""Guardas de producción (paso 1 del plan de separación, 13/11): nada de lo que corre en desarrollo puede tocar
producción. Un test por guarda:
- la suite aborta si la carpeta de datos, el `.env` o un puerto apuntan a producción (hoy y después del corte);
- durante los tests, conectarse al portal real (127.0.0.1:1111) falla;
- `core/paths.py` y el mailer, sin variables, no caen en `<repo>/data` ni en `<repo>/.env`;
- `deploy.ps1` y `rollback.ps1` sin `-Produccion` no tocan nada, y la config no trae valores reales por defecto;
- `tools/verificar.py` usa una cantidad fija de workers (8), configurable.
Ningún test usa rutas o puertos reales: todo es una carpeta temporal o un hogar simulado."""
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests import powershell_utf8

from tests import guardas_produccion as guardas

RAIZ = Path(__file__).resolve().parent.parent
windows = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"), reason="scripts de Windows")


# ---------------------------------------------------------------- la suite aborta si apunta a producción

@pytest.fixture
def prohibidas(tmp_path):
    hogar = tmp_path / "hogar"
    principal = tmp_path / "repo-principal"
    return guardas.rutas_de_produccion(hogar, principal), hogar, principal


@pytest.mark.parametrize("caso", ["datos de hoy", "carpeta de producción de antes del corte",
                                  "adentro de la config de antes del corte",
                                  "producción después del corte", "datos después del corte"])
def test_aborta_si_los_datos_son_de_produccion(prohibidas, tmp_path, caso):
    rutas, hogar, principal = prohibidas
    datos = {"datos de hoy": principal / "data",
             "carpeta de producción de antes del corte": hogar / guardas.UBICACION_VIEJA / "produccion",
             "adentro de la config de antes del corte": hogar / guardas.UBICACION_VIEJA / "algo",
             "producción después del corte": hogar / "LinkedOut" / "produccion",
             "datos después del corte": hogar / "LinkedOut" / "datos" / "candidato"}[caso]
    problemas = guardas.problemas(datos, tmp_path / "dev.env", {}, rutas)
    assert problemas and "datos" in problemas[0]


@pytest.mark.parametrize("env", ["de hoy", "después del corte"])
def test_aborta_si_el_env_es_de_produccion(prohibidas, tmp_path, env):
    rutas, hogar, principal = prohibidas
    archivo = principal / ".env" if env == "de hoy" else hogar / "LinkedOut" / "produccion.env"
    problemas = guardas.problemas(tmp_path / "datos", archivo, {}, rutas)
    assert problemas and ".env" in problemas[0]


ARCHIVOS_DE_PRODUCCION = ["portal.cmd", "portal.vbs", "portal.log", "vigilante.ps1", "vigilante.log",
                           "despliegue.json", "despliegues.log", "CONGELADO"]


@pytest.mark.parametrize("archivo", ARCHIVOS_DE_PRODUCCION)
def test_aborta_si_el_env_es_un_archivo_de_produccion(prohibidas, tmp_path, archivo):
    """El lanzador y el vigilante de producción (14/11) viven sueltos en ~/LinkedOut: un test nunca los escribe."""
    rutas, hogar, _ = prohibidas
    problemas = guardas.problemas(tmp_path / "datos", hogar / "LinkedOut" / archivo, {}, rutas)
    assert problemas and archivo in problemas[0]


@pytest.mark.parametrize("archivo", ["portal.cmd", "portal.vbs"])
def test_aborta_si_los_datos_son_un_archivo_de_produccion(prohibidas, tmp_path, archivo):
    rutas, hogar, _ = prohibidas
    assert guardas.problemas(hogar / "LinkedOut" / archivo, tmp_path / "dev.env", {}, rutas)


def test_aborta_si_los_datos_contienen_el_lanzador_de_produccion(prohibidas, tmp_path):
    rutas, hogar, _ = prohibidas
    problemas = guardas.problemas(hogar / "LinkedOut", tmp_path / "dev.env", {}, rutas)
    assert any("portal.cmd" in p for p in problemas)


def test_las_carpetas_de_desarrollo_de_linkedout_no_son_produccion(prohibidas):
    rutas, hogar, _ = prohibidas
    assert not guardas.problemas(hogar / "LinkedOut" / "dev-datos", hogar / "LinkedOut" / "dev.env", {}, rutas)


def test_aborta_si_un_puerto_configurado_es_el_1111(prohibidas, tmp_path):
    rutas, _, _ = prohibidas
    assert guardas.problemas(tmp_path / "datos", tmp_path / "dev.env", {"PORT": "1111"}, rutas)
    assert not guardas.problemas(tmp_path / "datos", tmp_path / "dev.env", {"PORT": "1199"}, rutas)


def test_esta_suite_no_apunta_a_produccion():
    import core.mailer
    import core.paths
    rutas = guardas.rutas_de_produccion(Path.home(), guardas.repo_principal(RAIZ))
    assert guardas.problemas(core.paths.DATA_DIR, Path(os.environ["LINKEDOUT_ENV_FILE"]), os.environ, rutas) == []
    assert Path(os.environ["LINKEDOUT_ENV_FILE"]).resolve() == core.mailer.ruta_env().resolve()


def test_la_suite_se_corta_si_apunta_a_produccion(tmp_path):
    """El conftest real, con la carpeta de datos «de producción» de un hogar simulado: pytest no corre nada."""
    hogar = tmp_path / "hogar"
    (hogar / "LinkedOut" / "datos").mkdir(parents=True)
    prueba = "tests/test_guardas_produccion.py::test_conectarse_a_otro_puerto_local_anda"  # carga el conftest real
    entorno = dict(os.environ, USERPROFILE=str(hogar), HOME=str(hogar), PYTHONUTF8="1",
                   LINKEDOUT_FORZAR_DATOS_DE_PRUEBA=str(hogar / "LinkedOut" / "datos"))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", prueba],
                       cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="replace", env=entorno,
                       timeout=180)
    salida = r.stdout + r.stderr
    assert r.returncode != 0, salida
    assert "apunta a producción" in salida and "1 passed" not in salida


# ---------------------------------------------------------------- el portal real no se toca

@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_conectarse_al_portal_real_falla(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(guardas.ProduccionProhibida):
            s.connect((host, 1111))
    finally:
        s.close()


def test_conectarse_a_otro_puerto_local_anda():
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(1)
    cliente = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        cliente.connect(servidor.getsockname())
    finally:
        cliente.close()
        servidor.close()


# ---------------------------------------------------------------- sin variables, nada cae en lo real

def test_sin_variables_los_datos_y_el_env_no_son_los_del_repo():
    entorno = {k: v for k, v in os.environ.items() if not k.upper().endswith(("_DATA_DIR", "_ENV_FILE"))}
    codigo = "import core.paths, core.mailer; print(core.paths.DATA_DIR); print(core.mailer.ruta_env())"
    r = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, env=entorno,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    datos, env = (Path(x) for x in r.stdout.split("\n")[:2])
    assert datos != RAIZ / "data" and datos == RAIZ / "dev-datos"
    assert env != RAIZ / ".env" and env == RAIZ / "dev.env"


# ---------------------------------------------------------------- deploy y rollback sin -Produccion

def _config_simulada(tmp_path, **valores):
    config = tmp_path / "config"
    config.mkdir()
    (tmp_path / "produccion-vacia").mkdir()
    datos = {"produccion": str(tmp_path / "produccion-vacia"), "datos": str(tmp_path / "datos"),
             "tarea": "linkedout-tarea-inexistente-de-prueba", "puerto": 1, "registro": str(tmp_path / "registro.log")}
    datos.update(valores)
    (config / "despliegue.json").write_text(json.dumps({k: v for k, v in datos.items() if v is not None}),
                                            encoding="utf-8")
    return config


def _powershell(script, config, *argumentos):
    comun = (RAIZ / "tools" / "despliegue_comun.ps1").read_text(encoding="utf-8-sig")
    assert "LINKEDOUT_CONFIG_DIR" in comun, "el script no lee LINKEDOUT_CONFIG_DIR: correrlo usaría la config real"
    hogar = config.parent / "hogar"
    hogar.mkdir(exist_ok=True)
    entorno = dict(os.environ, LINKEDOUT_CONFIG_DIR=str(config), USERPROFILE=str(hogar), HOME=str(hogar))
    return powershell_utf8.correr(RAIZ / "tools" / script, argumentos, entorno)


@windows
@pytest.mark.parametrize("script", ["deploy.ps1", "rollback.ps1"])
def test_sin_produccion_explicito_no_toca_nada(tmp_path, script):
    config = _config_simulada(tmp_path)
    codigo, salida = _powershell(script, config)
    assert codigo != 0 and "-Produccion" in salida
    assert not (tmp_path / "registro.log").exists()


@windows
@pytest.mark.parametrize("falta", ["produccion", "datos", "tarea", "puerto"])
def test_la_config_no_tiene_valores_reales_por_defecto(tmp_path, falta):
    config = _config_simulada(tmp_path, **{falta: None})
    codigo, salida = _powershell("deploy.ps1", config, "-Produccion")
    assert codigo != 0 and falta in salida and "despliegue.json" in salida
    assert not (tmp_path / "registro.log").exists()


# ---------------------------------------------------------------- verificar.py: workers fijos

def test_verificar_usa_8_workers_y_se_puede_cambiar(monkeypatch, tmp_path):
    sys.path.insert(0, str(RAIZ / "tools"))
    import verificar
    monkeypatch.setenv("LINKEDOUT_CANDADO_COMPUERTA", str(tmp_path / "compuerta.lock"))
    monkeypatch.setattr(verificar, "bajar_prioridad", lambda: None)
    llamadas = []
    monkeypatch.setattr(verificar, "_correr", lambda nombre, args: llamadas.append(args))
    monkeypatch.setenv("CI", "true")  # la suite completa corre solo en el CI
    monkeypatch.setattr(sys, "argv", ["verificar.py", "--rapido"])
    monkeypatch.delenv("LINKEDOUT_WORKERS", raising=False)
    verificar.main()
    assert llamadas[0][llamadas[0].index("-n") + 1] == "8"
    monkeypatch.setenv("LINKEDOUT_WORKERS", "4")
    llamadas.clear()
    verificar.main()
    assert llamadas[0][llamadas[0].index("-n") + 1] == "4"
