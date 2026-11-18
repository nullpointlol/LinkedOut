"""Feed sin tope diario y «cero resultados» como error (creador, 11/11)."""
from pathlib import Path

import pytest

import core.radar as radar_mod
from core import radar_store
from core.linkedin_harvester import HarvestResult
from core.radar_store import get_radar_state, update_radar_state
from tests.test_radar_loop import FakeHarvester, make_radar, raw


def _feed(*posts):
    return HarvestResult(posts=list(posts), lectura={"motivo_fin": "max_posts", "urns_parseados": [p["urn"] for p in posts]})


class FeedHarvester(FakeHarvester):
    def __init__(self, feed, **kw):
        super().__init__(**kw)
        self.feed = feed

    async def harvest_feed(self, max_posts=40, max_scrolls=10, conocidos=None):
        self.calls.append("feed")
        self.params.append(("feed", max_posts, max_scrolls, None))
        return self.feed


# ------------------------------------------------------------------ feed

@pytest.mark.asyncio
async def test_el_feed_no_tiene_tope_diario(tmp_path):
    h = FeedHarvester(_feed(*(raw(i) for i in range(1, 6))))
    radar = make_radar(tmp_path, h, queries=())
    try:
        await radar.run_cycle()
        c = get_radar_state()["contadores_dia"]
        assert c["leidos_feed"] == 5
        assert c["leidos_busquedas"] == 0  # el feed tiene su propio contador
        assert len(radar_store.list_posts()) == 5
    finally:
        await radar.stop()


@pytest.mark.presupuesto_real
def test_las_reglas_reales_estan_en_el_perfil():
    """Los valores de hoy (12/11) están en reglas_radar.json, no en el código."""
    from core.reglas_radar import leer_reglas
    r = leer_reglas()
    assert (r.feed_posts, r.feed_scrolls) == (80, 20)
    assert r.pausa_entre_vueltas_s == (1500.0, 2100.0) and r.pausa_entre_lecturas_s == (180.0, 360.0)
    assert (r.busquedas_por_hora, r.busquedas_por_dia) == (6, 60)


def test_la_pausa_entre_vueltas_es_la_del_perfil(tmp_path):
    radar = make_radar(tmp_path, FakeHarvester(), queries=(), queries_en=("en1",))
    for _ in range(20):
        assert 1500.0 <= radar._segundos_pausa() <= 2100.0


# ------------------------------------------------------------------ cero resultados

def _sin_resultados():
    return HarvestResult(posts=[], html="<html>sin</html>", lectura={"motivo_fin": "sin_resultados", "urns_parseados": []})


@pytest.fixture
def reportes(monkeypatch):
    llamados = []

    def _ticket(tipo, mensaje, **kw):
        llamados.append(dict(kw, tipo=tipo, mensaje=mensaje))
        return 1
    monkeypatch.setattr(radar_mod, "_ticket", _ticket)
    return llamados


@pytest.mark.asyncio
async def test_cero_resultados_es_un_error_con_captura(tmp_path, reportes):
    h = FakeHarvester({"q1": _sin_resultados()})
    radar = make_radar(tmp_path, h, queries=("q1",))
    try:
        await radar.run_cycle()
        assert len(reportes) == 1
        r = reportes[0]
        assert r["tipo"] == "sin_resultados" and "q1" in r["mensaje"]
        assert r["datos"]["consulta"] == "q1" and r["datos"]["cuenta"] == radar_store.CUENTA_PRINCIPAL
        carpeta = Path(r["datos"]["carpeta"])
        assert carpeta.parent == tmp_path / "radar_debug" / "sin_resultados"
        assert carpeta in h.dumps and r["html"] == carpeta / "page.html" and r["html"].is_file()
        estado = get_radar_state()
        assert estado["estado"] == "ERROR" and "sin resultados" in estado["estado_detalle"].lower()
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_cero_posts_sin_aviso_tambien_es_error(tmp_path, reportes):
    vacio = HarvestResult(posts=[], lectura={"motivo_fin": "no_crece", "urns_parseados": []})
    radar = make_radar(tmp_path, FakeHarvester({"q1": vacio}), queries=("q1",))
    try:
        await radar.run_cycle()
        assert [r["tipo"] for r in reportes] == ["sin_resultados"]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_con_resultados_o_feed_vacio_no_se_reporta(tmp_path, reportes):
    h = FeedHarvester(HarvestResult(posts=[], lectura={"motivo_fin": "no_crece"}),
                      responses={"q1": HarvestResult(posts=[raw(1)], lectura={"motivo_fin": "max_posts"})})
    radar = make_radar(tmp_path, h, queries=("q1",))
    try:
        await radar.run_cycle()
        assert reportes == []
    finally:
        await radar.stop()
