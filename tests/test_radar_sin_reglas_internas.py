"""«todas las reglas DEBEN ESTAR EN EL PERFIL». Ninguna constante de las reglas 1 a 10 en
core/radar.py, ninguna rama por idioma en la selección, la lectura ni el orden de procesamiento, y las búsquedas se
leen por rondas en el orden del archivo."""
import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest

import core.paths
from core import radar_store
from core.radar import RadarLoop
from tests import reglas_prueba
from tests.test_radar_loop import FIXED_NOW, FakeHarvester, FakeSleep, Recorder, make_radar

FUENTE = Path(__file__).resolve().parent.parent / "core" / "radar.py"

# Reglas 1 a 10 (y la franja horaria): nombres que no pueden volver a core/radar.py
PROHIBIDOS = {
    "ES_POR_CICLO", "DIAS_SALTEO_SIN_RESULTADOS", "CONSULTAS_ES", "CONSULTAS_EN", "CONSULTAS_EN_POR_RONDA",
    "CICLOS_POR_RONDA_EN", "CUOTA_BUSQUEDAS_EN", "LECTURAS_POR_CONSULTA", "MIN_POR_LECTURA", "DIAS_RENDIMIENTO",
    "PISO_CUOTA_CONSULTA", "MAX_POSTS_ES", "MAX_SCROLLS_ES", "PACIENCIA_ES", "MAX_POSTS_POR_CONSULTA",
    "MAX_SCROLLS_POR_CONSULTA", "MAX_POSTS_FEED", "MAX_SCROLLS_FEED", "PAUSA_ENTRE_CONSULTAS", "PAUSA_ENTRE_CICLOS",
    "BUSQUEDAS_POR_HORA", "BUSQUEDAS_POR_DIA", "HORARIO_BUSQUEDAS", "HORAS_PAUSA_BLOQUEO", "TOPE_PAUSA_BLOQUEO_H",
    "_es_espanol", "_cupo", "_pesos_consultas", "_restante", "_restante_del_dia", "_restante_lectura",
    "_espanol_del_ciclo", "_ingles_del_ciclo", "_tope_agotado", "_queries_es", "_queries_en",
    "tope_posts_dia", "leidos_es", "sin_email_en",
}
# Cualquier identificador o clave que hable de idioma en el Radar (consultas_en, _queries_es, "en", "idioma"...)
_IDIOMA_DENTRO = ("idioma", "espanol", "español", "ingles", "inglés")
_IDIOMA_FIN = ("_en", "_es")
_IDIOMA_EXACTO = ("en", "es", "EN", "ES")
# «sigue en» (un estado), no un idioma
_PERMITIDOS = {"_sigue_en"}


def _nombres(arbol):
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Name):
            yield nodo.id
        elif isinstance(nodo, ast.Attribute):
            yield nodo.attr
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield nodo.name
        elif isinstance(nodo, ast.arg):
            yield nodo.arg
        elif isinstance(nodo, ast.keyword) and nodo.arg:
            yield nodo.arg
        elif isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and len(nodo.value) < 40:
            yield nodo.value  # claves como "leidos_es" o "tope_posts_dia"


def test_core_radar_no_tiene_reglas_internas_ni_ramas_por_idioma():
    arbol = ast.parse(FUENTE.read_text(encoding="utf-8"))
    nombres = set(_nombres(arbol))
    assert not (nombres & PROHIBIDOS), sorted(nombres & PROHIBIDOS)
    por_idioma = sorted(n for n in nombres if (any(i in n.lower() for i in _IDIOMA_DENTRO)
                        or n.endswith(_IDIOMA_FIN) or n in _IDIOMA_EXACTO) and n not in _PERMITIDOS)
    assert por_idioma == [], por_idioma


def test_core_radar_no_lee_los_valores_viejos_de_radar_state():
    """Los topes del día eran columnas de radar_state; ahora son del perfil (el de posts ya no existe)."""
    texto = FUENTE.read_text(encoding="utf-8")
    assert "tope_envios_dia\"]" not in texto and "tope_posts_dia" not in texto


# ------------------------------------------------------------------ orden por rondas

@pytest.fixture
def sin_reportes(monkeypatch):
    async def _nada(self, query):
        return None
    monkeypatch.setattr(RadarLoop, "_reportar_sin_resultados", _nada)


def _leidas(h):
    return [c for c in h.calls if c != "feed"]


@pytest.mark.asyncio
async def test_intercaladas_es_en_con_presupuesto_corto_siguen_el_archivo(tmp_path, monkeypatch, sin_reportes):
    """búsquedas es/en intercaladas y presupuesto de 2 por hora; cada vuelta sigue donde
    cortó la anterior, en el orden del archivo, y ninguna queda sin leer dentro de una ronda."""
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(busquedas_por_hora=2, busquedas_por_dia=10**6))
    archivo = ["cv a", "send your cv", "mesa de ayuda", "night shift", "monitoreo"]
    ahora = [FIXED_NOW]
    h = FakeHarvester()
    radar = make_radar(tmp_path, h, queries=archivo, now=lambda: ahora[0])
    try:
        for vuelta in range(5):
            ahora[0] = FIXED_NOW + timedelta(minutes=61 * vuelta)
            await radar.run_cycle()
        assert _leidas(h) == ["cv a", "send your cv", "mesa de ayuda", "night shift", "monitoreo",
                              "cv a", "send your cv", "mesa de ayuda", "night shift", "monitoreo"]
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_presupuesto_amplio_lee_todas_en_una_vuelta(tmp_path, sin_reportes):
    archivo = ["cv a", "send your cv", "mesa de ayuda", "night shift"]
    h = FakeHarvester()
    radar = make_radar(tmp_path, h, queries=archivo)
    try:
        await radar.run_cycle()
        assert h.calls == ["feed"] + archivo
        assert radar_store.get_radar_state()["ronda_leidas"] == []  # ronda completa
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_la_ronda_sigue_donde_corto_aunque_se_reinicie(tmp_path, monkeypatch, sin_reportes):
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(busquedas_por_hora=2, busquedas_por_dia=10**6))
    h = FakeHarvester()
    radar = make_radar(tmp_path, h, queries=("a", "b", "c"))
    try:
        await radar.run_cycle()
        assert _leidas(h) == ["a", "b"]
    finally:
        await radar.stop()
    h2 = FakeHarvester()
    otro = make_radar(tmp_path, h2, queries=("a", "b", "c"), now=lambda: FIXED_NOW + timedelta(hours=2))
    try:
        await otro.run_cycle()
        assert _leidas(h2) == ["c", "a"]  # termina la ronda y empieza la siguiente desde la primera
    finally:
        await otro.stop()


@pytest.mark.asyncio
async def test_bloqueo_deja_la_busqueda_pendiente_y_nueva_entra_en_la_ronda(tmp_path, monkeypatch, sin_reportes):
    from core.linkedin_harvester import HarvestResult
    bloqueo = HarvestResult(posts=[], lectura={"motivo_fin": "bloqueo", "urns_parseados": []})
    h = FakeHarvester({"a": [bloqueo]})
    radar = make_radar(tmp_path, h, queries=("a", "b"))

    async def _nada(*a, **k):
        return None
    radar._capturar_bloqueo = _nada
    try:
        await radar.run_cycle()
        assert _leidas(h) == ["a"] and radar_store.get_radar_state()["ronda_leidas"] == []
    finally:
        await radar.stop()
    radar_store.update_radar_state(ronda_leidas=["a"])
    h2 = FakeHarvester()
    otro = make_radar(tmp_path, h2, queries=("n", "a", "b"), now=lambda: FIXED_NOW + timedelta(days=2))
    try:
        await otro.run_cycle()
        assert _leidas(h2) == ["n", "b", "a"]  # «n» (nueva) y «b» completan la ronda; vuelve a empezar
    finally:
        await otro.stop()


# ------------------------------------------------------------------ sin reglas no se lee nada

@pytest.mark.asyncio
async def test_sin_reglas_no_se_lee_nada_y_hay_ticket(tmp_path, monkeypatch, sin_reportes):
    from core import radar as radar_mod
    from core.reglas_radar import ReglasInvalidas
    tickets = []
    monkeypatch.setattr(radar_mod, "_ticket", lambda tipo, mensaje, **kw: tickets.append((tipo, mensaje)) or 1)
    reglas_prueba.usar(monkeypatch, ReglasInvalidas("Falta «busquedas_por_hora»."))
    h = FakeHarvester()
    radar = make_radar(tmp_path, h, queries=("a",))
    try:
        await radar.run_cycle()
        assert h.calls == []  # ni feed ni búsquedas: la cuenta primero
        estado = radar_store.get_radar_state()
        assert estado["estado"] == "ERROR" and "Falta «busquedas_por_hora»" in estado["estado_detalle"]
        assert tickets and tickets[0][0] == "reglas_invalidas"
        assert radar._segundos_pausa() == radar_mod.INTERVALO_REGLAS_S
        assert radar._presupuesto_busquedas() == 0
        assert radar_mod.pausa_busquedas() is None
    finally:
        await radar.stop()


@pytest.mark.asyncio
async def test_sin_reglas_no_se_envia(tmp_path, monkeypatch):
    from core.reglas_radar import ReglasInvalidas
    from tests.test_radar_workers import crear_aprobado, FakeSend, make_radar as make_radar_envios
    reglas_prueba.usar(monkeypatch, ReglasInvalidas("No existe reglas_radar.json"))
    send = FakeSend()
    radar = make_radar_envios(tmp_path, send=send)
    pid = crear_aprobado(1)
    try:
        assert await radar._procesar_envio(pid) is False
        assert send.calls == []
    finally:
        await radar.stop()


@pytest.mark.presupuesto_real
def test_sintaxis_usa_el_presupuesto_del_perfil(tmp_path, monkeypatch):
    from datetime import datetime
    from tools import sintaxis_linkedin as sl
    assert sl.presupuesto_restante([], datetime(2026, 11, 12, 12, 0)) == 6
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path)
    with pytest.raises(sl.NoSePuede, match="No existe"):
        sl.presupuesto_restante([], datetime(2026, 11, 12, 12, 0))


@pytest.mark.asyncio
@pytest.mark.parametrize("falta, esperado", [("cuenta_busquedas", ["feed"]), ("cuenta_feed", ["a"])])
async def test_si_falta_la_cuenta_en_el_perfil_esa_lectura_no_se_hace(tmp_path, monkeypatch, sin_reportes, falta,
                                                                     esperado):
    """la cuenta de cada lectura es del perfil; si falta, esa lectura no se hace y hay ticket."""
    from core import radar as radar_mod
    tickets = []
    monkeypatch.setattr(radar_mod, "_ticket", lambda tipo, mensaje, **kw: tickets.append((tipo, mensaje)) or 1)
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(busquedas_por_hora=10**6, busquedas_por_dia=10**6,
                                                         **{falta: None}))
    h = FakeHarvester()
    radar = make_radar(tmp_path, h, queries=("a",))
    try:
        await radar.run_cycle()
        assert h.calls == esperado
        assert any(t == "reglas_invalidas" and falta in m for t, m in tickets)
    finally:
        await radar.stop()
