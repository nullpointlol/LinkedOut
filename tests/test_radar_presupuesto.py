"""Tests de presupuesto de búsquedas, pausas escalonadas y captura (Feature: radar-presupuesto).

Verifica Z1 (consultas unificadas) y Z2 (presupuesto, scroll y bloqueo escalonado) de spec.md.
"""
from datetime import date, datetime, time as dtime, timedelta, timezone

import pytest

from core import radar_store
from core.linkedin_harvester import HarvestResult
from tests.test_radar_loop import FakeHarvester, FakeSleep, make_radar, raw

pytestmark = pytest.mark.presupuesto_real


def _registrar_lectura(query="q1", fuente="busqueda", motivo="max_posts", inicio_dt=None):
    if inicio_dt is None:
        inicio_dt = datetime.now(timezone.utc)
    inicio_iso = inicio_dt.isoformat()
    return radar_store.registrar_lectura(
        query=query,
        fuente=fuente,
        inicio=inicio_iso,
        fin=inicio_iso,
        scrolls=1,
        motivo_fin=motivo,
        pantallas=[],
        parseados=1,
        guardados=1,
        destinos={},
    )


def _limpiar_lecturas():
    conn = radar_store._connect()
    with conn:
        conn.execute("DELETE FROM radar_lecturas")
    conn.close()


@pytest.mark.presupuesto_real
def test_las_consultas_salen_del_archivo_del_candidato():
    """Búsquedas 100 % del usuario (11/11): las activas de busquedas.json (fixture), en el orden del archivo."""
    from core.radar import DEFAULT_QUERIES
    from core.radar_consultas import consultas_activas
    assert DEFAULT_QUERIES == consultas_activas()
    assert '"cv a" pausada' not in DEFAULT_QUERIES


@pytest.mark.presupuesto_real
def test_el_ritmo_sale_del_perfil():
    """Creador, 12/11: presupuesto, pausas, largo de lectura y pausa por bloqueo están en reglas_radar.json."""
    import core.radar as radar_mod
    from core.reglas_radar import leer_reglas
    r = leer_reglas()
    assert (r.busquedas_por_hora, r.busquedas_por_dia, r.porcentaje_dos_pausadas) == (6, 60, 67)
    assert r.pausa_entre_lecturas_s == (180.0, 360.0)
    assert (r.busqueda_posts, r.busqueda_scrolls, r.busqueda_pantallas_sin_nuevos) == (300, 15, 2)
    assert (r.bloqueo_primera_h, r.bloqueo_maxima_h) == (6, 24)
    for nombre in ("BUSQUEDAS_POR_HORA", "BUSQUEDAS_POR_DIA", "HORARIO_BUSQUEDAS", "PAUSA_ENTRE_CONSULTAS",
                   "MAX_SCROLLS_ES", "PACIENCIA_ES", "TOPE_PAUSA_BLOQUEO_H"):
        assert not hasattr(radar_mod, nombre)


RELOJ_FIJO = datetime.combine(date.today(), dtime(12, 0, 0))


def _hace(minutos: float) -> datetime:
    """Instante en UTC `minutos` antes del reloj congelado (las lecturas se guardan en UTC)."""
    return (RELOJ_FIJO - timedelta(minutes=minutos)).astimezone(timezone.utc)


@pytest.mark.presupuesto_real
def test_presupuesto_por_hora(tmp_path):
    radar = make_radar(tmp_path, now=lambda: RELOJ_FIJO)
    for i in range(4):
        _registrar_lectura(query=f"q{i}", inicio_dt=_hace(10 * (i + 1)))
    assert radar._presupuesto_busquedas() == 2

    for i in range(4, 6):
        _registrar_lectura(query=f"q{i}", inicio_dt=_hace(5 * (i + 1)))
    assert radar._presupuesto_busquedas() == 0


@pytest.mark.presupuesto_real
def test_presupuesto_por_dia(tmp_path):
    radar = make_radar(tmp_path, now=lambda: RELOJ_FIJO)
    for i in range(60):
        _registrar_lectura(query=f"qd{i}", inicio_dt=_hace(70 + i))
    assert radar._presupuesto_busquedas() == 0


@pytest.mark.presupuesto_real
@pytest.mark.parametrize("hora", [dtime(0, 30), dtime(1, 59), dtime(2, 10), dtime(23, 50)])
def test_presupuesto_por_dia_no_depende_de_la_hora(tmp_path, hora):
    """Cerca de medianoche: las lecturas de ayer no gastan el cupo de hoy; las de la última hora, sí."""
    ahora = datetime.combine(RELOJ_FIJO.date(), hora)
    radar = make_radar(tmp_path, now=lambda: ahora)
    medianoche = datetime.combine(RELOJ_FIJO.date(), dtime(0, 0))
    for i in range(60):  # ayer, a más de una hora de `ahora`: no gastan nada de hoy
        _registrar_lectura(query=f"ayer{i}", inicio_dt=(medianoche - timedelta(minutes=120 + i)).astimezone(timezone.utc))
    assert radar._presupuesto_busquedas() == 6
    _registrar_lectura(query="recien", inicio_dt=(ahora - timedelta(minutes=5)).astimezone(timezone.utc))
    assert radar._presupuesto_busquedas() == 5


@pytest.mark.presupuesto_real
def test_feed_no_cuenta(tmp_path):
    hoy = date.today()
    radar = make_radar(tmp_path, now=lambda: datetime.combine(hoy, dtime(12, 0, 0)))
    ahora_utc = datetime.now(timezone.utc)
    for i in range(10):
        _registrar_lectura(query="feed", fuente="feed", inicio_dt=ahora_utc - timedelta(minutes=2 * (i + 1)))
    assert radar._presupuesto_busquedas() == 6


@pytest.mark.asyncio
@pytest.mark.presupuesto_real
async def test_ciclo_respeta_el_presupuesto(tmp_path):
    queries = tuple(f"q{i}" for i in range(1, 9))
    h = FakeHarvester({q: HarvestResult(posts=[raw(i)]) for i, q in enumerate(queries, 1)})
    now_local = datetime.combine(date.today(), dtime(12, 0, 0))
    radar = make_radar(tmp_path, harvester=h, queries=queries, now=lambda: now_local)
    try:
        await radar.run_cycle()
        assert h.calls == ["feed", "q1", "q2", "q3", "q4", "q5", "q6"]
        await radar.run_cycle()
        assert h.calls == ["feed", "q1", "q2", "q3", "q4", "q5", "q6", "feed"]
    finally:
        await radar.stop()


@pytest.mark.asyncio
@pytest.mark.presupuesto_real
async def test_rotacion_cuando_el_presupuesto_corta(tmp_path):
    """Con el presupuesto corto la ronda sigue en el orden del archivo donde cortó (12/11): lecturas viejas o
    búsquedas nunca leídas no cambian el orden."""
    queries = tuple(f"q{i}" for i in range(1, 9))
    h = FakeHarvester({q: [HarvestResult(posts=[raw(i)]), HarvestResult(posts=[raw(100 + i)])]
                       for i, q in enumerate(queries, 1)})
    for i, q in enumerate(["q1", "q2", "q3", "q4"]):
        _registrar_lectura(query=q, inicio_dt=_hace(140 - 5 * i))

    ahora = [RELOJ_FIJO]
    radar = make_radar(tmp_path, harvester=h, queries=queries, now=lambda: ahora[0])
    try:
        await radar.run_cycle()
        assert h.calls == ["feed", "q1", "q2", "q3", "q4", "q5", "q6"]
        h.calls.clear()
        ahora[0] = RELOJ_FIJO + timedelta(minutes=61)
        await radar.run_cycle()
        assert h.calls == ["feed", "q7", "q8", "q1", "q2", "q3", "q4"]
    finally:
        await radar.stop()


@pytest.mark.asyncio
@pytest.mark.presupuesto_real
async def test_espera_entre_busquedas(tmp_path):
    h = FakeHarvester({q: HarvestResult(posts=[raw(i)]) for i, q in enumerate(["q1", "q2", "q3"], 1)})
    sleep = FakeSleep()
    now_local = datetime.combine(date.today(), dtime(12, 0, 0))
    radar = make_radar(tmp_path, harvester=h, sleep=sleep, queries=("q1", "q2", "q3"), now=lambda: now_local)
    try:
        await radar.run_cycle()
        assert len(sleep.calls) == 3
        assert all(180.0 <= s <= 360.0 for s in sleep.calls)
    finally:
        await radar.stop()


@pytest.mark.presupuesto_real
def test_pausa_escalonada():
    from core.radar import pausa_busquedas

    ahora_utc = datetime.now(timezone.utc)

    # 1. 1 bloqueo hace 1 h -> 6 h
    _limpiar_lecturas()
    _registrar_lectura(query="q1", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=1))
    p1 = pausa_busquedas()
    assert p1 is not None
    assert p1["horas"] == 6
    assert p1["bloqueos_24h"] == 1
    assert datetime.fromisoformat(p1["hasta"]) == datetime.fromisoformat(p1["desde"]) + timedelta(hours=6)

    # 2. Bloqueos hace 20 h y hace 1 h -> 12 h
    _limpiar_lecturas()
    _registrar_lectura(query="q1", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=20))
    _registrar_lectura(query="q2", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=1))
    p2 = pausa_busquedas()
    assert p2 is not None
    assert p2["horas"] == 12
    assert p2["bloqueos_24h"] == 2
    assert datetime.fromisoformat(p2["hasta"]) == datetime.fromisoformat(p2["desde"]) + timedelta(hours=12)

    # 3. Bloqueos hace 22 h, 10 h y 1 h -> 24 h
    _limpiar_lecturas()
    _registrar_lectura(query="q1", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=22))
    _registrar_lectura(query="q2", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=10))
    _registrar_lectura(query="q3", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=1))
    p3 = pausa_busquedas()
    assert p3 is not None
    assert p3["horas"] == 24
    assert p3["bloqueos_24h"] == 3
    assert datetime.fromisoformat(p3["hasta"]) == datetime.fromisoformat(p3["desde"]) + timedelta(hours=24)

    # 4. 4 en 24 h -> 24 h (tope)
    _limpiar_lecturas()
    _registrar_lectura(query="q1", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=23))
    _registrar_lectura(query="q2", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=15))
    _registrar_lectura(query="q3", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=8))
    _registrar_lectura(query="q4", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=1))
    p4 = pausa_busquedas()
    assert p4 is not None
    assert p4["horas"] == 24
    assert p4["bloqueos_24h"] == 4
    assert datetime.fromisoformat(p4["hasta"]) == datetime.fromisoformat(p4["desde"]) + timedelta(hours=24)


@pytest.mark.presupuesto_real
def test_bloqueo_viejo_no_cuenta():
    from core.radar import pausa_busquedas

    _limpiar_lecturas()
    ahora_utc = datetime.now(timezone.utc)
    _registrar_lectura(query="q1", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=30))
    _registrar_lectura(query="q2", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=1))
    p = pausa_busquedas()
    assert p is not None
    assert p["horas"] == 6
    assert p["bloqueos_24h"] == 1
    assert datetime.fromisoformat(p["hasta"]) == datetime.fromisoformat(p["desde"]) + timedelta(hours=6)


@pytest.mark.presupuesto_real
def test_busqueda_ok_posterior_levanta_la_pausa():
    from core.radar import pausa_busquedas

    _limpiar_lecturas()
    ahora_utc = datetime.now(timezone.utc)
    _registrar_lectura(query="q1", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=2))
    _registrar_lectura(query="q2", motivo="max_posts", inicio_dt=ahora_utc - timedelta(hours=1))
    assert pausa_busquedas() is None


@pytest.mark.asyncio
@pytest.mark.presupuesto_real
async def test_captura_al_bloquear(tmp_path):
    h = FakeHarvester({
        "q1": HarvestResult(posts=[], lectura={"motivo_fin": "bloqueo", "pantallas": [], "scrolls": 0}),
    })
    now_local = datetime.combine(date.today(), dtime(12, 0, 0))
    radar = make_radar(tmp_path, harvester=h, queries=("q1",), now=lambda: now_local)
    try:
        await radar.run_cycle()
        esperado_dir = tmp_path / "radar_debug" / "bloqueo"
        assert esperado_dir in h.dumps
        assert (esperado_dir / "page.html").exists()
    finally:
        await radar.stop()


@pytest.mark.presupuesto_real
def test_store_contar_e_inicios():
    _limpiar_lecturas()
    ahora_utc = datetime.now(timezone.utc)
    # Feed:
    _registrar_lectura(query="feed", fuente="feed", motivo="max_posts", inicio_dt=ahora_utc - timedelta(minutes=30))
    _registrar_lectura(query="feed", fuente="feed", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(minutes=20))
    # Búsqueda dentro de la hora:
    _registrar_lectura(query="q1", fuente="busqueda", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(minutes=45))
    _registrar_lectura(query="q2", fuente="busqueda", motivo="max_posts", inicio_dt=ahora_utc - timedelta(minutes=35))
    _registrar_lectura(query="q3", fuente="busqueda", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(minutes=15))
    # Búsqueda fuera de la hora:
    _registrar_lectura(query="q4", fuente="busqueda", motivo="bloqueo", inicio_dt=ahora_utc - timedelta(hours=2))

    desde = ahora_utc - timedelta(hours=1)
    conteo = radar_store.contar_busquedas(desde)
    assert conteo == 3

    inicios = radar_store.inicios_bloqueo(desde)
    assert len(inicios) == 2
    assert inicios[0] < inicios[1]
    assert all(i.tzinfo is not None for i in inicios)
    assert all(i.tzinfo == timezone.utc for i in inicios)
