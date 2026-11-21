"""Mi perfil edita las reglas del Radar (reglas_radar.json) como los demás archivos: versión, validación y backup."""
import json

import pytest
from fastapi.testclient import TestClient

import core.paths
import server
from tests.reglas_prueba import DATOS_REALES

URL = "/api/candidato/perfil/reglas_radar"


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path)
    (tmp_path / "candidato").mkdir()
    return TestClient(server.app, raise_server_exceptions=False), tmp_path


def _guardar(client, datos, version):
    return client.put(URL, json={"contenido": json.dumps(datos), "version": version})


def test_sin_archivo_se_lee_vacio_y_se_guarda_con_backup(cliente):
    client, data = cliente
    r = client.get(URL)
    assert r.status_code == 200 and r.json()["existe"] is False and r.json()["contenido"] == ""
    g = _guardar(client, DATOS_REALES, r.json()["version"])
    assert g.status_code == 200, g.text
    assert g.json()["ok"] is True and g.json()["backup"].startswith("candidato-")
    assert json.loads((data / "candidato" / "reglas_radar.json").read_text(encoding="utf-8")) == DATOS_REALES
    assert (data / "backups" / g.json()["backup"]).is_dir()


def test_invalido_da_422_con_el_campo(cliente):
    client, data = cliente
    version = client.get(URL).json()["version"]
    malo = dict(DATOS_REALES, busquedas_por_hora=0)
    r = _guardar(client, malo, version)
    assert r.status_code == 422
    assert "«busquedas_por_hora» tiene que ser un entero mayor que 0" in r.json()["detail"]
    assert not (data / "candidato" / "reglas_radar.json").exists()
    sin_campo = {k: v for k, v in DATOS_REALES.items() if k != "tope_envios_dia"}
    assert "Falta «tope_envios_dia»" in _guardar(client, sin_campo, version).json()["detail"]
    r = client.put(URL, json={"contenido": "{roto", "version": version})
    assert r.status_code == 422 and "JSON" in r.json()["detail"]


def test_version_vieja_da_409(cliente):
    client, _ = cliente
    version = client.get(URL).json()["version"]
    assert _guardar(client, DATOS_REALES, version).status_code == 200
    assert _guardar(client, dict(DATOS_REALES, busquedas_por_hora=5), version).status_code == 409


def test_lo_guardado_es_lo_que_lee_el_radar(cliente):
    from core.reglas_radar import leer_reglas
    client, _ = cliente
    version = client.get(URL).json()["version"]
    assert _guardar(client, dict(DATOS_REALES, busquedas_por_hora=4), version).status_code == 200
    assert leer_reglas().busquedas_por_hora == 4
