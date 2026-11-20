"""Resultados por búsqueda: `con_mail` por lectura y el promedio por lectura de cada consulta.

Lectura productiva: una búsqueda (no el feed) del Radar real, de cualquier cuenta, que terminó de leerse (no
cortada por la sesión, un bloqueo o un error). El historial se asocia por consulta exacta.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core import radar_store
from core.linkedin_harvester import HarvestResult
from core.radar_resultados import resultados_por_consulta
from tests.test_radar_loop import make_radar, raw, FakeHarvester


def _lectura(query="q", motivo="max_scrolls", fuente="busqueda", parseados=10, destinos=None, con_mail=None,
             cuenta=radar_store.CUENTA_PRINCIPAL, inicio=None):
    inicio = (inicio or datetime.now(timezone.utc)).isoformat()
    destinos = destinos or {}
    return radar_store.registrar_lectura(
        query=query, fuente=fuente, inicio=inicio, fin=inicio, scrolls=1, motivo_fin=motivo, pantallas=[],
        parseados=parseados, guardados=sum(d in radar_store.DESTINOS_NUEVOS for d in destinos.values()),
        destinos=destinos, con_mail=con_mail, cuenta=cuenta)


def _post(urn, estado, score=None, triage=True):
    pid = radar_store.insert_post_if_new(dict(raw(int(urn.rsplit(":", 1)[1])), urn=urn), "q")
    campos = {"estado": estado}
    if triage:
        campos["triage"] = {"es_busqueda_laboral": True}
    if score is not None:
        campos["analisis"] = {"match_score": score}
    radar_store.update_post(pid, **campos)
    return pid


# ------------------------------------------------------------------ con_mail por lectura

def test_registrar_lectura_guarda_con_mail():
    _lectura(con_mail=2)
    _lectura(query="otra")
    filas = {f["query"]: f for f in radar_store.listar_lecturas()}
    assert filas["q"]["con_mail"] == 2 and filas["otra"]["con_mail"] is None


def test_base_vieja_de_lecturas_suma_con_mail(tmp_path):
    db = tmp_path / "viejo.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE radar_lecturas (id INTEGER PRIMARY KEY, query TEXT, fuente TEXT, inicio TEXT,
                    fin TEXT, scrolls INT, motivo_fin TEXT, pantallas TEXT, parseados INT, guardados INT,
                    destinos TEXT)""")
    conn.execute("INSERT INTO radar_lecturas (query, fuente, inicio, pantallas, destinos) "
                 "VALUES ('q', 'busqueda', '2026-11-12T10:00:00+00:00', '[]', '{}')")
    conn.commit()
    conn.close()
    fila = radar_store.listar_lecturas(db_path=db)[0]
    assert fila["con_mail"] is None
    assert len(radar_store.listar_lecturas(db_path=db)) == 1  # idempotente


@pytest.mark.asyncio
async def test_la_lectura_registra_los_nuevos_con_mail(tmp_path):
    posts = [raw(1), raw(2, emails=()), raw(3, emails=("a@b.test", "c@d.test"))]
    h = FakeHarvester({"q1": HarvestResult(posts=posts)})
    radar = make_radar(tmp_path, h, queries=("q1",))
    radar._wake_processing = lambda: None
    try:
        await radar.run_cycle()
        h.responses["q1"] = HarvestResult(posts=posts)  # releídos: ya no son nuevos
        await radar.run_cycle()
        lecturas = [f for f in radar_store.listar_lecturas() if f["query"] == "q1"]
        assert [f["con_mail"] for f in lecturas] == [0, 2]  # la más reciente primero
        assert lecturas[1]["guardados"] == 3
    finally:
        await radar.stop()


# ------------------------------------------------------------------ promedio por lectura

def test_sin_lecturas_no_hay_resultados():
    assert resultados_por_consulta(["q"]) == {}
    assert resultados_por_consulta([]) == {}


def test_promedios_por_lectura():
    radar_store.update_radar_state(umbral_match=70)
    _post("urn:li:activity:1", "ANALIZADA", score=80)   # oferta y match
    _post("urn:li:activity:5", "DESCARTADO")             # descartada por el clasificador
    _post("urn:li:activity:7", "OFERTA", score=None)     # oferta todavía sin analizar
    # urn 2: el clasificador lo borró (no era oferta); urn 3: descartado sin IA (exterior)
    _lectura(parseados=10, con_mail=2, destinos={
        "urn:li:activity:1": "guardado", "urn:li:activity:2": "guardado", "urn:li:activity:3": "exterior",
        "urn:li:activity:4": "ya_visto"})
    _lectura(parseados=4, con_mail=1, destinos={
        "urn:li:activity:5": "guardado", "urn:li:activity:6": "sin_texto", "urn:li:activity:7": "guardado"})
    r = resultados_por_consulta(["q", "sin_leer"])
    assert set(r) == {"q"}
    assert r["q"]["lecturas"] == 2 and r["q"]["dias"] == radar_store.DIAS_LECTURAS == 14
    assert r["q"]["promedio"] == {
        "leidos": 7.0, "nuevos": 2.5, "con_mail": 1.5, "ofertas": 1.0, "matches": 0.5,
        "descartados": 1.0, "no_oferta": 0.5,
    }


def test_excluye_feed_cortadas_viejas_y_otras_consultas():
    _lectura(parseados=6)
    _lectura(fuente="feed", parseados=80)
    for motivo in ("login", "bloqueo", "bloqueo_levantado"):
        _lectura(motivo=motivo, parseados=0)
    _lectura(query="otra", parseados=50)
    _lectura(parseados=99, inicio=datetime.now(timezone.utc) - timedelta(days=15))
    r = resultados_por_consulta(["q"])
    assert r["q"]["lecturas"] == 1 and r["q"]["promedio"]["leidos"] == 6.0


def test_sin_resultados_cuenta_como_cero_y_la_cuenta_2_cuenta():
    _lectura(motivo="sin_resultados", parseados=0)
    _lectura(parseados=10, cuenta=radar_store.CUENTA_SECUNDARIA)
    r = resultados_por_consulta(["q"])
    assert r["q"]["lecturas"] == 2 and r["q"]["promedio"]["leidos"] == 5.0


def test_matches_usan_el_umbral_configurado():
    _post("urn:li:activity:1", "LISTO", score=80)
    _lectura(destinos={"urn:li:activity:1": "guardado"})
    radar_store.update_radar_state(umbral_match=70)
    assert resultados_por_consulta(["q"])["q"]["promedio"]["matches"] == 1.0
    radar_store.update_radar_state(umbral_match=90)
    assert resultados_por_consulta(["q"])["q"]["promedio"]["matches"] == 0.0


def test_con_mail_desconocido_no_aparece_y_se_promedia_sobre_lo_conocido():
    _lectura(con_mail=None)
    assert "con_mail" not in resultados_por_consulta(["q"])["q"]["promedio"]
    _lectura(con_mail=3)
    assert resultados_por_consulta(["q"])["q"]["promedio"]["con_mail"] == 3.0


def test_pendientes_solo_si_hay():
    _post("urn:li:activity:1", "NUEVO", triage=False)
    _post("urn:li:activity:2", "ERROR", triage=False)     # la clasificación falló: sigue sin procesar
    _post("urn:li:activity:3", "ERROR", score=50)          # falló la generación: es una oferta
    _post("urn:li:activity:4", "IGNORADA", triage=False)
    _lectura(destinos={"urn:li:activity:1": "guardado", "urn:li:activity:2": "guardado",
                       "urn:li:activity:3": "guardado", "urn:li:activity:4": "ignorada_vieja"})
    p = resultados_por_consulta(["q"])["q"]["promedio"]
    assert p["pendientes"] == 2.0 and p["ofertas"] == 1.0 and p["descartados"] == 1.0


def test_redondea_a_un_decimal():
    for n in (1, 1, 2):
        _lectura(parseados=n)
    assert resultados_por_consulta(["q"])["q"]["promedio"]["leidos"] == 1.3


def test_sintaxis_no_registra_lecturas():
    """Las pruebas de sintaxis con la cuenta 2 no son lecturas productivas: no escriben en radar_lecturas."""
    from pathlib import Path
    fuente = (Path(__file__).resolve().parent.parent / "tools" / "sintaxis_linkedin.py").read_text(encoding="utf-8")
    assert "registrar_lectura" not in fuente and "radar_store" not in fuente


def test_la_purga_de_lecturas_usa_el_reloj_del_radar_no_el_del_sistema(tmp_path):
    # la purga usaba datetime('now'): con el Radar en otra fecha borraba las lecturas recién hechas
    db = tmp_path / "radar.db"
    for dia in ("2020-01-01", "2020-01-10", "2020-01-20"):
        radar_store.registrar_lectura("q", "busqueda", f"{dia}T10:00:00+00:00", f"{dia}T10:01:00+00:00", 1, "fin",
                                      [], 1, 1, {}, db_path=db)
    assert [f["inicio"][:10] for f in radar_store.listar_lecturas(db_path=db)] == ["2020-01-20", "2020-01-10"]
