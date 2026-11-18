"""Cola del Radar en `/api/radar/status`: cuántos CVs faltan, cuál se hace ahora y cuándo termina."""
import itertools

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

import server
from core import database, radar_store
from core.radar import RadarLoop

_URN = itertools.count(1)


@pytest.fixture
def radar(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


def _post(estado, puesto="", **extra):
    n = next(_URN)
    pid = radar_store.insert_post_if_new(
        {"urn": f"urn:li:activity:cola{n}", "url": f"https://www.linkedin.com/in/x{n}", "autor": f"Autor {n}",
         "cargo_autor": "", "texto": f"Oferta {n}", "fecha_rel": "1 h", "emails": []},
        query="q",
    )
    campos = {"estado": estado, **extra}
    if puesto:
        campos["triage"] = {"puesto": puesto}
    radar_store.update_post(pid, **campos)
    return pid


def _postulacion_con_total(total_s):
    pid = database.create_postulacion("Puesto", "Acme", "oferta")
    database.update_postulacion(pid, telemetry={"etapas": [], "total_s": total_s})
    return pid


def _cola(radar):
    r = TestClient(server.app).get("/api/radar/status")
    assert r.status_code == 200
    return r.json()["cola"]


def test_cola_vacia(radar):
    assert _cola(radar) == {"generando": 0, "analizando": 0, "actual": None, "eta_s": None,
                            "backlog": {"activo": True, "quedan": 0}}


def test_cola_cuenta_estados_y_elige_el_generando_de_menor_id(radar):
    primero = _post("GENERANDO", "Analista Funcional")
    _post("GENERANDO", "Otro puesto")
    for estado in ("OFERTA", "ANALIZANDO", "NUEVO", "NUEVO"):
        _post(estado)
    _post("LISTO")
    radar.etapas_generacion = {primero: "Redactando el mensaje"}
    cola = _cola(radar)
    assert cola["generando"] == 2 and cola["analizando"] == 4
    assert cola["actual"] == {"post_id": primero, "puesto": "Analista Funcional", "etapa": "Redactando el mensaje",
                              "progreso": None}


def test_cola_sin_etapa_conocida_la_deja_en_null(radar):
    _post("GENERANDO", "Analista Funcional")
    assert _cola(radar)["actual"]["etapa"] is None


def test_eta_es_generando_por_la_mediana_de_las_ultimas_20(radar):
    for _ in range(5):
        _post("GENERANDO")
    _postulacion_con_total(9999.0)  # la más vieja de 21: queda afuera
    for _ in range(20):
        _postulacion_con_total(100.0)
    database.create_postulacion("Sin telemetría", "Acme", "oferta")  # no cuenta
    assert _cola(radar)["eta_s"] == 500


def test_eta_null_sin_telemetria(radar):
    _post("GENERANDO")
    database.create_postulacion("Sin telemetría", "Acme", "oferta")
    assert _cola(radar)["eta_s"] is None


@pytest.mark.parametrize("valor", [float("inf"), float("nan")])
def test_eta_ignora_tiempos_no_finitos_y_status_no_falla(radar, valor):
    _post("GENERANDO")
    _postulacion_con_total(valor)
    _postulacion_con_total(100.0)
    assert _cola(radar)["eta_s"] == 100
