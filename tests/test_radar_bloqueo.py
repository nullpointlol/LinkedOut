"""Tests para la pausa de 6 h cuando LinkedIn bloquea las búsquedas."""
from datetime import datetime, timedelta, timezone

import pytest

from core import radar_store
from core.linkedin_harvester import HarvestResult
from tests.reglas_prueba import REALES
HORAS_PAUSA_BLOQUEO = REALES.bloqueo_primera_h
from tests.test_radar_loop import FakeHarvester, FakeSleep, make_radar


@pytest.mark.asyncio
async def test_bloqueo_corta_ciclo_y_registra_lectura(tmp_path):
    # 1. Con queries=("q1","q2","q3") y q1 devolviendo bloqueo:
    # tras run_cycle, harvester.calls == ["feed", "q1"];
    # radar_store.listar_lecturas() tiene la de q1 con motivo_fin == "bloqueo";
    # ciclos_vacios_consecutivos sigue en 0.
    harvester = FakeHarvester({
        "q1": HarvestResult(posts=[], lectura={"motivo_fin": "bloqueo", "pantallas": [], "scrolls": 0}),
    })
    radar = make_radar(
        tmp_path,
        harvester=harvester,
        queries=("q1", "q2", "q3"),
        now=lambda: datetime.now(),
    )
    await radar.run_cycle()

    assert harvester.calls == ["feed", "q1"]
    lecturas = radar_store.listar_lecturas()
    lectura_q1 = next((l for l in lecturas if l["query"] == "q1"), None)
    assert lectura_q1 is not None
    assert lectura_q1["motivo_fin"] == "bloqueo"
    assert radar_store.get_radar_state()["ciclos_vacios_consecutivos"] == 0


@pytest.mark.asyncio
async def test_segundo_ciclo_inmediato_pausado_solo_feed(tmp_path):
    # 2. Un segundo run_cycle inmediato: solo "feed" se agrega a calls,
    # y el detalle de estado contiene «Búsquedas pausadas por LinkedIn».
    harvester = FakeHarvester({
        "q1": HarvestResult(posts=[], lectura={"motivo_fin": "bloqueo", "pantallas": [], "scrolls": 0}),
    })
    radar = make_radar(
        tmp_path,
        harvester=harvester,
        queries=("q1", "q2", "q3"),
        now=lambda: datetime.now(),
    )
    await radar.run_cycle()
    assert harvester.calls == ["feed", "q1"]

    await radar.run_cycle()
    assert harvester.calls == ["feed", "q1", "feed"]
    estado = radar_store.get_radar_state()
    assert "Búsquedas pausadas por LinkedIn" in estado["estado_detalle"]


@pytest.mark.asyncio
async def test_lectura_bloqueo_hace_7h_reanuda_busquedas(tmp_path):
    # 3. Con una lectura bloqueo registrada hace 7 h:
    # run_cycle vuelve a leer las búsquedas.
    inicio_7h = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    radar_store.registrar_lectura(
        query="q1",
        fuente="busqueda",
        inicio=inicio_7h,
        fin=inicio_7h,
        scrolls=0,
        motivo_fin="bloqueo",
        pantallas=[],
        parseados=0,
        guardados=0,
        destinos={},
    )
    harvester = FakeHarvester()
    radar = make_radar(
        tmp_path,
        harvester=harvester,
        queries=("q1", "q2", "q3"),
        now=lambda: datetime.now(),
    )
    await radar.run_cycle()
    assert harvester.calls == ["feed", "q1", "q2", "q3"]


@pytest.mark.asyncio
async def test_busqueda_mas_reciente_sin_bloqueo_no_pausa(tmp_path):
    # 4. Si después del bloqueo hay una búsqueda más reciente con max_posts: no hay pausa.
    inicio_bloqueo = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    radar_store.registrar_lectura(
        query="q1",
        fuente="busqueda",
        inicio=inicio_bloqueo,
        fin=inicio_bloqueo,
        scrolls=0,
        motivo_fin="bloqueo",
        pantallas=[],
        parseados=0,
        guardados=0,
        destinos={},
    )
    inicio_ok = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    radar_store.registrar_lectura(
        query="q2",
        fuente="busqueda",
        inicio=inicio_ok,
        fin=inicio_ok,
        scrolls=2,
        motivo_fin="max_posts",
        pantallas=[],
        parseados=5,
        guardados=1,
        destinos={},
    )
    harvester = FakeHarvester()
    radar = make_radar(
        tmp_path,
        harvester=harvester,
        queries=("q1", "q2"),
        now=lambda: datetime.now(),
    )
    assert radar._busquedas_pausadas_hasta() is None
    await radar.run_cycle()
    assert harvester.calls == ["feed", "q1", "q2"]
