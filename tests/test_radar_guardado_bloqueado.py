"""Un «database is locked» al guardar el análisis nunca deja el post en ANALIZANDO con el análisis perdido
(banco del 12/11: el Radar perdió un análisis ya devuelto por AGY y el post quedó en ANALIZANDO hasta un
stop o un reinicio). El guardado se reintenta hasta `ESPERA_MAX_ESCRITURA_S`; si igual no se puede, el post
vuelve a OFERTA con el error a la vista. Sin red ni AGY reales."""
import sqlite3
from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from core import radar, radar_store
from core.radar import RadarLoop

ANALISIS = {"es_oferta": True, "puesto": "Analista Funcional", "empresa": "Acme", "match_score": 80,
            "requisitos": []}


@pytest.fixture
def motor(monkeypatch):
    monkeypatch.setattr(radar, "aviso_util", lambda post: True)
    monkeypatch.setattr(radar, "ESPERA_MAX_ESCRITURA_S", 0.5)  # el tope real es 60 s
    radar_store.update_radar_state(generacion_pausada=True)
    loop = RadarLoop(harvester_factory=Mock(), triage_fn=AsyncMock(),
                     analyze_fn=AsyncMock(return_value=deepcopy(ANALISIS)), pipeline_fn=AsyncMock(),
                     compose_fn=AsyncMock(), send_fn=AsyncMock(), broadcast=AsyncMock(),
                     image_reader_fn=AsyncMock(return_value=""), now=lambda: datetime(2026, 11, 12, 10, 0))
    monkeypatch.setattr(loop, "generar_automaticos", AsyncMock())
    return loop


def _oferta() -> dict:
    pid = radar_store.insert_post_if_new({"urn": "urn:li:activity:9700", "url": "https://example.org/9700",
                                          "autor": "Acme", "texto": "Buscamos Analista Funcional en Acme.",
                                          "emails": ["rrhh@acme.test"]}, "consulta de prueba")
    radar_store.update_post(pid, estado="OFERTA", triage={"oferta": 1, "puesto": "Analista Funcional"})
    return radar_store.get_post(pid)


def _bloquear_guardado_del_analisis(monkeypatch, veces: float):
    """`update_post` con el análisis da «database is locked» las primeras `veces` (inf = siempre)."""
    original = radar_store.update_post
    fallas = {"n": 0}

    def update_post(post_id, **campos):
        if "analisis" in campos and fallas["n"] < veces:
            fallas["n"] += 1
            raise sqlite3.OperationalError("database is locked")
        return original(post_id, **campos)

    monkeypatch.setattr(radar_store, "update_post", update_post)
    return fallas


@pytest.mark.asyncio
async def test_un_locked_pasajero_se_reintenta_y_el_analisis_se_guarda(monkeypatch, motor):
    post = _oferta()
    fallas = _bloquear_guardado_del_analisis(monkeypatch, veces=2)
    await motor._analizar(post)
    guardado = radar_store.get_post(post["id"])
    assert fallas["n"] == 2
    assert guardado["estado"] == "ANALIZADA"
    assert guardado["analisis"]["puesto"] == "Analista Funcional"


@pytest.mark.asyncio
async def test_un_locked_que_no_se_va_devuelve_el_post_a_oferta(monkeypatch, motor):
    post = _oferta()
    _bloquear_guardado_del_analisis(monkeypatch, veces=float("inf"))
    await motor._analizar(post)
    actual = radar_store.get_post(post["id"])
    assert actual["estado"] == "OFERTA", actual["estado"]
    assert "No se pudo guardar el análisis" in actual["error"] and "locked" in actual["error"]


def test_el_tope_de_espera_es_una_constante_con_nombre():
    assert radar.ESPERA_MAX_ESCRITURA_S == 60
