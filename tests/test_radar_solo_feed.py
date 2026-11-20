"""Interruptores del Radar, Feed y Búsquedas: estado en `radar_state`, persistente.

Con las búsquedas apagadas no se ejecuta ninguna búsqueda; con el feed apagado no se lee el feed.
Las búsquedas arrancan apagadas: nada busca en LinkedIn hasta que el usuario lo confirme."""
import sqlite3
from datetime import date, datetime, time as dtime

import pytest

from core import radar_store
from core.linkedin_harvester import HarvestResult
from tests.test_radar_loop import FakeHarvester, make_radar, raw

pytestmark = pytest.mark.presupuesto_real

MEDIODIA = datetime.combine(date.today(), dtime(12, 0, 0))


def _radar(tmp_path, queries=("q1", "q2")):
    h = FakeHarvester({q: HarvestResult(posts=[raw(i)]) for i, q in enumerate(queries, 1)})
    return h, make_radar(tmp_path, harvester=h, queries=queries, now=lambda: MEDIODIA)


# ---------------------------------------------------------------- estado

def test_columnas_nuevas_con_defaults_y_migracion(tmp_path):
    """Una base vieja sin las columnas las gana con feed encendido y búsquedas apagadas."""
    vieja = tmp_path / "vieja.db"
    conn = sqlite3.connect(vieja)
    conn.execute("CREATE TABLE radar_state (id INTEGER PRIMARY KEY CHECK (id = 1), desired_running INTEGER DEFAULT 0,"
                 " estado TEXT DEFAULT 'DETENIDO', estado_detalle TEXT DEFAULT '', next_cycle_at TIMESTAMP,"
                 " umbral_match INTEGER DEFAULT 70, tope_posts_dia INTEGER DEFAULT 600, tope_envios_dia INTEGER"
                 " DEFAULT 40, contadores_dia TEXT DEFAULT '{}', fecha_contadores TEXT, ciclos_vacios_consecutivos"
                 " INTEGER DEFAULT 0, envios_pausados INTEGER DEFAULT 0)")
    conn.execute("INSERT INTO radar_state (id) VALUES (1)")
    conn.commit()
    conn.close()
    st = radar_store.get_radar_state(db_path=vieja)
    assert st["feed_activo"] is True
    assert st["busquedas_activas"] is False
    nueva = radar_store.get_radar_state(db_path=tmp_path / "nueva.db")
    assert nueva["feed_activo"] is True and nueva["busquedas_activas"] is False


def test_update_guarda_booleanos(tmp_path):
    db = tmp_path / "x.db"
    radar_store.update_radar_state(db_path=db, feed_activo=False, busquedas_activas=True)
    st = radar_store.get_radar_state(db_path=db)
    assert st["feed_activo"] is False and st["busquedas_activas"] is True


# ---------------------------------------------------------------- motor

@pytest.mark.asyncio
@pytest.mark.busquedas_por_defecto
async def test_por_defecto_solo_lee_el_feed(tmp_path):
    h, radar = _radar(tmp_path)
    try:
        await radar.run_cycle()
        assert h.calls == ["feed"]
        assert radar._presupuesto_busquedas() == 0
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_con_busquedas_apagadas_solo_lee_el_feed(tmp_path):
    radar_store.update_radar_state(busquedas_activas=False)
    h, radar = _radar(tmp_path)
    try:
        await radar.run_cycle()
        assert h.calls == ["feed"]
        assert radar._presupuesto_busquedas() == 0
        assert radar_store.get_radar_state()["estado_detalle"] == "Solo feed: búsquedas desactivadas"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_con_feed_apagado_solo_busca(tmp_path):
    radar_store.update_radar_state(feed_activo=False, busquedas_activas=True)
    h, radar = _radar(tmp_path)
    try:
        await radar.run_cycle()
        assert h.calls == ["q1", "q2"]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_con_los_dos_apagados_no_lee_nada_ni_abre_el_navegador(tmp_path):
    radar_store.update_radar_state(feed_activo=False, busquedas_activas=False)
    h, radar = _radar(tmp_path)
    try:
        await radar.run_cycle()
        assert h.calls == []
        assert radar._test_created == []
        assert radar._presupuesto_busquedas() == 0
        assert radar_store.get_radar_state()["estado_detalle"] == "Sin lecturas: feed y búsquedas desactivados"
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_apagar_busquedas_a_mitad_del_ciclo_corta_las_que_faltan(tmp_path):
    """Seguridad de la cuenta: si el usuario apaga las búsquedas durante un ciclo, no se hace ninguna más."""
    radar_store.update_radar_state(busquedas_activas=True)
    h, radar = _radar(tmp_path, queries=("q1", "q2", "q3"))
    original = h.harvest

    async def harvest_y_apagar(query, **kw):
        r = await original(query, **kw)
        radar_store.update_radar_state(busquedas_activas=False)
        return r
    h.harvest = harvest_y_apagar
    try:
        await radar.run_cycle()
        assert h.calls == ["feed", "q1"]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_con_busquedas_encendidas_busca(tmp_path):
    radar_store.update_radar_state(busquedas_activas=True)
    h, radar = _radar(tmp_path, queries=("q1",))
    try:
        await radar.run_cycle()
        assert h.calls == ["feed", "q1"]
    finally:
        await radar.stop()


# ---------------------------------------------------------------- API

@pytest.fixture
def cliente():
    from fastapi.testclient import TestClient
    import server
    return TestClient(server.app)


@pytest.mark.parametrize("ruta,campo", [("feed", "feed_activo"), ("busquedas", "busquedas_activas")])
def test_endpoint_cambia_el_estado_en_la_base(cliente, ruta, campo):
    for valor in (True, False, True):
        r = cliente.post(f"/api/radar/{ruta}", json={"activo": valor})
        assert r.status_code == 200
        assert r.json()[campo] is valor
        assert radar_store.get_radar_state()[campo] is valor
    assert cliente.get("/api/radar/status").json()[campo] is True


@pytest.mark.parametrize("ruta,campo", [("feed", "feed_activo"), ("busquedas", "busquedas_activas")])
@pytest.mark.parametrize("cuerpo", [{}, {"activo": "quizás"}, {"pausada": True}])
def test_endpoint_rechaza_cuerpos_invalidos_sin_tocar_la_base(cliente, ruta, campo, cuerpo):
    antes = radar_store.get_radar_state()[campo]
    r = cliente.post(f"/api/radar/{ruta}", json=cuerpo)
    assert r.status_code == 422
    assert radar_store.get_radar_state()[campo] is antes
