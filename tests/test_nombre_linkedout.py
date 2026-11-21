"""Nombre LinkedOut: las variables `LINKEDOUT_*` reemplazan a las del nombre viejo.

Producción arranca con su lanzador actual, que fija las variables viejas: el código lee la nueva y, si falta, la
vieja (`core.paths.variable_entorno`). Lo mismo con la marca de sesión de la cuenta 2 y el candado de la compuerta.
"""
import json
import os
import subprocess
import threading
import sys
import time
from pathlib import Path

import pytest

import core.paths
from core.paths import variable_entorno

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import candado_compuerta  # noqa: E402

GUARDA = 60
# compatibilidad: se borra tras el deploy (el prefijo que usa hoy el lanzador de producción)
PREFIJO_VIEJO = core.paths.PREFIJO_VARIABLES_VIEJO
NOMBRES = ("DATA_DIR", "ENV_FILE", "SIN_RADAR", "MAIL_PRUEBA", "AGY_MAX_CONCURRENTES", "CANDADO_COMPUERTA", "PUERTO")


def _sin_variables_del_portal() -> dict:
    prefijos = ("LINKEDOUT_", PREFIJO_VIEJO)
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(prefijos)}


# ---------------------------------------------------------------- la función común

def test_la_variable_nueva_gana_sobre_la_vieja():
    entorno = {"LINKEDOUT_DATA_DIR": "nueva", PREFIJO_VIEJO + "DATA_DIR": "vieja"}
    assert variable_entorno("DATA_DIR", entorno=entorno) == "nueva"


def test_sin_la_nueva_se_lee_la_vieja():
    assert variable_entorno("DATA_DIR", entorno={PREFIJO_VIEJO + "DATA_DIR": "vieja"}) == "vieja"


def test_sin_ninguna_devuelve_el_valor_por_defecto():
    assert variable_entorno("DATA_DIR", entorno={}) is None
    assert variable_entorno("DATA_DIR", "otro", entorno={}) == "otro"


def test_la_nueva_definida_aunque_vacia_no_cae_en_la_vieja():
    # «MAIL_PRUEBA=» vacío apaga el modo de prueba: nunca se reactiva por la variable vieja
    entorno = {"LINKEDOUT_MAIL_PRUEBA": "", PREFIJO_VIEJO + "MAIL_PRUEBA": "x@example.com"}
    assert variable_entorno("MAIL_PRUEBA", entorno=entorno) == ""


def test_por_defecto_lee_el_entorno_del_proceso(monkeypatch):
    monkeypatch.delenv("LINKEDOUT_SIN_RADAR", raising=False)
    monkeypatch.setenv(PREFIJO_VIEJO + "SIN_RADAR", "1")
    assert variable_entorno("SIN_RADAR") == "1"
    monkeypatch.setenv("LINKEDOUT_SIN_RADAR", "0")
    assert variable_entorno("SIN_RADAR") == "0"


# ---------------------------------------------------------------- la app arranca con las viejas y con las nuevas

ARRANQUE = r"""
import json, sys
from fastapi.testclient import TestClient
import core.paths, core.mailer, server
reanudado = []

async def reanudar():
    reanudado.append(True)

server.radar_loop.resume_if_needed = reanudar
with TestClient(server.app) as cliente:
    r = cliente.get("/")
print("ARRANQUE " + json.dumps({"datos": str(core.paths.DATA_DIR), "env": str(core.mailer.ruta_env()),
                                 "status": r.status_code, "titulo": server.app.title,
                                 "radar_reanuda": bool(reanudado)}), flush=True)
"""


def _arrancar(tmp_path: Path, prefijo: str) -> dict:
    datos, env = tmp_path / "datos", tmp_path / "portal.env"
    env.write_text("", encoding="utf-8")
    entorno = dict(_sin_variables_del_portal(), PYTHONUTF8="1", **{
        prefijo + "DATA_DIR": str(datos), prefijo + "ENV_FILE": str(env), prefijo + "SIN_RADAR": "1"})
    r = subprocess.run([sys.executable, "-c", ARRANQUE], cwd=RAIZ, capture_output=True, text=True,
                       encoding="utf-8", env=entorno, timeout=GUARDA)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    linea = next(l for l in r.stdout.splitlines() if l.startswith("ARRANQUE "))
    resultado = json.loads(linea[len("ARRANQUE "):])
    assert Path(resultado["datos"]) == datos and Path(resultado["env"]) == env
    assert resultado["status"] == 200 and resultado["titulo"] == "LinkedOut"
    assert resultado["radar_reanuda"] is False, "el portal de prueba reanudaría el Radar"
    assert (datos / "postulaciones.db").is_file(), "la base no se creó en la carpeta pedida"
    return resultado


def test_la_app_arranca_solo_con_las_variables_viejas_de_produccion(tmp_path):
    # compatibilidad: se borra tras el deploy (el portal.cmd de producción fija las viejas)
    _arrancar(tmp_path, PREFIJO_VIEJO)


def test_la_app_arranca_solo_con_las_variables_nuevas(tmp_path):
    _arrancar(tmp_path, "LINKEDOUT_")


# ---------------------------------------------------------------- marca de sesión de la cuenta 2

def test_la_marca_de_sesion_se_lee_con_el_nombre_nuevo_y_el_viejo(tmp_path):
    perfil = tmp_path / "linkedin_profile_2"
    perfil.mkdir()
    assert not core.paths.sesion_marcada(perfil)
    (perfil / core.paths.MARCA_SESION_VIEJA).write_text("x", encoding="utf-8")
    assert core.paths.sesion_marcada(perfil)
    (perfil / core.paths.MARCA_SESION_VIEJA).unlink()
    (perfil / core.paths.MARCA_SESION).write_text("x", encoding="utf-8")
    assert core.paths.sesion_marcada(perfil)


def test_marcar_la_sesion_escribe_solo_el_nombre_nuevo(tmp_path):
    perfil = tmp_path / "linkedin_profile_2"
    perfil.mkdir()
    core.paths.marcar_sesion(perfil, "2026-11-18T10:00:00")
    assert (perfil / core.paths.MARCA_SESION).read_text(encoding="utf-8") == "2026-11-18T10:00:00"
    assert not (perfil / core.paths.MARCA_SESION_VIEJA).exists()
    assert core.paths.MARCA_SESION == "linkedout_sesion_ok"


def test_el_radar_ve_la_cuenta_2_con_la_marca_vieja_y_con_la_nueva(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from core.radar import RadarLoop
    from core import radar_store
    for nombre in (core.paths.MARCA_SESION_VIEJA, core.paths.MARCA_SESION):
        perfil = tmp_path / nombre
        perfil.mkdir()
        radar = RadarLoop(
            harvester_factory=MagicMock(), triage_fn=AsyncMock(), analyze_fn=AsyncMock(), pipeline_fn=AsyncMock(),
            compose_fn=AsyncMock(), send_fn=AsyncMock(), broadcast=AsyncMock(), queries=[],
            debug_dir=tmp_path / "debug", perfil_secundario=perfil)
        assert radar.cuentas() == [radar_store.CUENTA_PRINCIPAL]
        (perfil / nombre).write_text("x", encoding="utf-8")
        assert radar.cuentas() == [radar_store.CUENTA_PRINCIPAL, radar_store.CUENTA_SECUNDARIA]


# ---------------------------------------------------------------- candado de la compuerta

@pytest.fixture
def temporal(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.upper().endswith("CANDADO_COMPUERTA"):
            monkeypatch.delenv(k)
    monkeypatch.setattr(candado_compuerta.tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def test_el_candado_usa_el_nombre_nuevo_y_respeta_el_viejo(temporal):
    rutas = candado_compuerta.rutas()
    assert [r.name for r in rutas] == [candado_compuerta.NOMBRE_VIEJO, "linkedout-compuerta.lock"]
    assert all(r.parent == temporal for r in rutas)


def test_una_compuerta_vieja_con_el_candado_viejo_hace_esperar_a_la_nueva(temporal):
    vieja = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    # En Linux un hijo que terminó queda zombi (su PID sigue «vivo») hasta que el padre lo espera: un hilo lo espera
    # apenas termina, como hace el padre real de una compuerta vieja, que no es este proceso.
    threading.Thread(target=vieja.wait, daemon=True).start()
    viejo = temporal / candado_compuerta.NOMBRE_VIEJO
    viejo.write_text(json.dumps({"pid": vieja.pid, "quien": "compuerta vieja", "desde": "18/11 03:00"}),
                     encoding="utf-8")
    avisos = []
    try:
        with candado_compuerta.candado("nueva", sondeo_s=0.2, avisar=avisos.append):
            assert vieja.poll() is not None, "la nueva corrió mientras la vieja tenía el candado"
            assert (temporal / "linkedout-compuerta.lock").exists()
    finally:
        vieja.wait(GUARDA)
    assert any("Esperando" in a for a in avisos), avisos
    assert not (temporal / "linkedout-compuerta.lock").exists() and not viejo.exists()


def test_con_la_variable_el_candado_es_solo_ese_archivo(temporal, monkeypatch):
    monkeypatch.setenv("LINKEDOUT_CANDADO_COMPUERTA", str(temporal / "propio.lock"))
    assert candado_compuerta.rutas() == [temporal / "propio.lock"]
    monkeypatch.delenv("LINKEDOUT_CANDADO_COMPUERTA")
    monkeypatch.setenv(PREFIJO_VIEJO + "CANDADO_COMPUERTA", str(temporal / "viejo.lock"))
    assert candado_compuerta.rutas() == [temporal / "viejo.lock"]


# ---------------------------------------------------------------- el nombre en la app

def test_la_app_se_llama_linkedout():
    import server
    assert server.app.title == "LinkedOut"
    assert "Antigravity" not in (server.app.description or "")
    html = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
    assert "<title>LinkedOut" in html
