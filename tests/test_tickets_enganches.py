"""Tickets automáticos desde el Radar, la IA y la generación (sin servicios reales)."""
import asyncio

import pytest

import server
from core import database, tickets
from core.bridge import AntigravityBridge
from core.linkedin_harvester import HarvestResult
from tests.test_radar_loop import FakeHarvester, make_radar, raw


def _de_tipo(tipo):
    return [tickets.obtener(t["id"]) for t in tickets.listar(tipo=tipo)]


@pytest.mark.asyncio
async def test_busqueda_sin_resultados_genera_ticket_con_consulta_y_html(tmp_path):
    h = FakeHarvester({"cv a analista": HarvestResult(posts=[], html="<p>No se encontraron resultados</p>",
                                                      lectura={"motivo_fin": "sin_resultados", "scrolls": 1})})
    radar = make_radar(tmp_path, harvester=h, queries=("cv a analista",))
    # El ticket se abre al cerrar la lectura en la vuelta, con la captura de radar_debug/sin_resultados/
    try:
        await radar.run_cycle()
    finally:
        await radar.stop()
    [t] = _de_tipo("sin_resultados")
    assert t["componente"] == "radar" and "cv a analista" in t["mensaje"]
    assert t["datos"]["consulta"] == "cv a analista" and t["datos"]["cuenta"]
    assert t["tiene_html"]
    assert "buscar «cv a analista»" in t["pasos"]


@pytest.mark.asyncio
async def test_una_lectura_con_resultados_no_genera_ticket(tmp_path):
    h = FakeHarvester({"q": HarvestResult(posts=[raw(1)], lectura={"motivo_fin": "no_crece"})})
    radar = make_radar(tmp_path, harvester=h, queries=("q",))
    await radar._leer_consulta(h, "q")
    assert not _de_tipo("sin_resultados")


@pytest.mark.asyncio
async def test_bloqueo_genera_ticket_con_captura_de_la_carpeta_de_debug(tmp_path):
    h = FakeHarvester()

    async def dump(destino):
        destino.mkdir(parents=True, exist_ok=True)
        (destino / "page.html").write_text("<p>Tráfico inusual</p>", encoding="utf-8")
        (destino / "screenshot.png").write_bytes(b"\x89PNG falso")
    h.dump_debug = dump
    radar = make_radar(tmp_path, harvester=h)
    await radar._capturar_bloqueo("cv a soporte")
    [t] = _de_tipo("bloqueo")
    assert t["tiene_captura"] and t["tiene_html"] and t["datos"]["consulta"] == "cv a soporte"


@pytest.mark.asyncio
async def test_lectura_fallida_genera_un_solo_ticket(tmp_path):
    h = FakeHarvester({"q1": RuntimeError("selector .feed-shared no encontrado")})
    radar = make_radar(tmp_path, harvester=h, queries=("q1",))
    async def leer(*a, **k):
        raise RuntimeError("selector .feed-shared no encontrado")
    radar._leer_con_cuenta = leer
    await asyncio.wait_for(radar.run_cycle(), timeout=10)
    fallidas = {t["datos"]["consulta"]: t for t in _de_tipo("lectura_fallida")}
    assert set(fallidas) == {"feed", "q1"}  # un ticket por lectura, no dos por el logger.exception
    assert "selector" in fallidas["q1"]["mensaje"] and "RuntimeError" in fallidas["q1"]["traza"]
    assert not [t for t in tickets.listar(componente="radar") if t["tipo"] == "error_registrado"
                and "Error leyendo" in t["mensaje"]]


def test_falla_de_la_ia_genera_ticket():
    from core import bridge
    bridge._ticket_ia("falla_agy", "Error: 503 Service Unavailable", {"modelo": "m", "esfuerzo": "low"})
    [t] = _de_tipo("falla_agy")
    assert t["componente"] == "ia" and t["datos"]["modelo"] == "m"


@pytest.mark.asyncio
async def test_falla_de_la_generacion_manual_genera_ticket(monkeypatch):
    async def ia_rota(self, prompt, json_mode=True, **kwargs):
        raise RuntimeError("la IA devolvió basura")
    monkeypatch.setattr(AntigravityBridge, "run_prompt", ia_rota)
    pid = database.create_postulacion(titulo="Analista", empresa="Acme", contenido="Oferta de prueba " * 20,
                                      url="", origen="manual")
    await server.run_pipeline_task(pid)
    gen = [t for t in tickets.listar(componente="generacion")]
    assert gen and tickets.obtener(gen[0]["id"])["datos"]["oferta_id"] == pid
