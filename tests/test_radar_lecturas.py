"""Registro de lecturas: navegador y bases sintéticos, sin servicios reales."""
from collections import Counter
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import database, radar_store
from core.linkedin_harvester import HarvestResult
from core.radar_api import ESTADO_HOSTS_DE_TEST, build_radar_router
from tests.test_linkedin_harvester import FakePage, FIXTURE_HTML, _harvester
from tests.test_radar_loop import FakeHarvester, make_radar, raw


class Pantalla(FakePage):
    def evaluate(self, script):
        assert script == "document.scrollingElement.scrollHeight"
        return 1000


@pytest.mark.parametrize("html,motivo", [
    (FIXTURE_HTML, "no_crece"),
    ("<p>NO SE HAN ENCONTRADO RESULTADOS</p>", "sin_resultados"),
    ("<p>TRÁFICO DE BÚSQUEDA INUSUAL</p>", "bloqueo"),
    ("<p>No results found</p>", "sin_resultados"),
    ("<p>Unusual search traffic</p>", "bloqueo"),
])
def test_harvester_registra_pantallas(html, motivo):
    from bs4 import BeautifulSoup
    page = Pantalla(html=html)
    resultado = _harvester(page)._scroll_and_parse("https://www.linkedin.com/feed/", 100, 8)
    lectura = resultado.lectura
    assert lectura["motivo_fin"] == motivo
    assert lectura["scrolls"] == (2 if motivo == "no_crece" else 0)
    assert lectura["url"] == "https://www.linkedin.com/feed/"
    pantalla = lectura["pantallas"][0]
    soup = BeautifulSoup(html, "html.parser")
    assert pantalla["tarjetas_dom"] == len(soup.select('[role="listitem"][componentkey^="update-card"]')) + len(soup.select('[data-urn^="urn:li:activity:"]'))
    assert pantalla["parseados"] == len(resultado.posts)
    assert pantalla["nuevos"] == len(resultado.posts)
    assert pantalla["alto_antes"] == pantalla["alto_despues"] == 1000


def test_content_reintenta_solo_navegacion():
    page = Pantalla()
    original = page.content
    calls = []
    def content():
        if not calls:
            calls.append(True)
            raise RuntimeError("Unable to retrieve content because page is navigating")
        return original()
    page.content = content
    page.wait_for_load_state = lambda state: calls.append(state)
    resultado = _harvester(page)._scroll_and_parse("https://www.linkedin.com/feed/", 1, 0)
    assert resultado.lectura["motivo_fin"] == "max_posts"
    assert calls == [True, "domcontentloaded"]


@pytest.mark.asyncio
async def test_registra_recorte_del_harvester_sin_guardar_mas(tmp_path, monkeypatch):
    from tests import reglas_prueba
    reglas_prueba.usar(monkeypatch, reglas_prueba.reglas(busqueda_posts=1))
    radar = make_radar(tmp_path, queries=("es",))
    harvester = _harvester(Pantalla())
    try:
        assert await radar._leer_consulta(harvester, "es") == 1
    finally:
        await harvester.close()
    lectura = radar_store.listar_lecturas()[0]
    assert lectura["parseados"] == 4
    assert Counter(lectura["destinos"].values()) == {"guardado": 1, "fuera_de_cupo": 3}
    assert len(radar_store.list_posts()) == 1


def registrar(**extra):
    return radar_store.registrar_lectura(query="consulta", fuente="busqueda",
        inicio="2026-11-10 01:00:00", fin="2026-11-10 01:01:00", scrolls=2,
        motivo_fin="no_crece", pantallas=[{"tarjetas_dom": 5, "nuevos": 3}],
        parseados=3, guardados=1, destinos={"urn:1": "guardado", "urn:2": "sin_texto"}, **extra)


def test_store_endpoint_retencion_y_filtros():
    ident = registrar()
    conn = radar_store._connect()
    with conn:
        conn.execute("UPDATE radar_lecturas SET inicio = datetime('now', '-15 days') WHERE id = ?", (ident,))
    conn.close()
    nuevo = registrar()
    rows = radar_store.listar_lecturas(query="consulta", limite=1)
    assert [r["id"] for r in rows] == [nuevo]
    assert rows[0]["destinos"]["urn:1"] == "guardado"
    assert rows[0]["pantallas"] == [{"tarjetas_dom": 5, "nuevos": 3}]
    assert radar_store.listar_lecturas(query="otra") == []
    app = FastAPI()
    # Como la conftest con server.app: TestClient cuenta como la PC (las lecturas tienen guarda desde S1)
    setattr(app.state, ESTADO_HOSTS_DE_TEST, {"testclient", "testserver"})
    app.include_router(build_radar_router(lambda: None))
    with TestClient(app) as client:
        assert client.get("/api/radar/lecturas?query=consulta&limite=1").json() == rows
        assert client.get("/api/radar/lecturas?query=otra").json() == []


@pytest.mark.asyncio
async def test_destinos_sin_cambiar_retorno(tmp_path, monkeypatch):
    radar = make_radar(tmp_path, queries=("es",), queries_en=("en",))
    vistos = raw(1)
    radar_store.registrar_vistos([vistos["urn"]], "es")
    repetido = raw(2)
    radar_store.insert_post_if_new(repetido, "es")
    vieja = dict(raw(3), fecha_rel="2 meses")
    exterior = raw(4)
    bueno = raw(5)
    error = raw(6)
    sin_texto = raw(7, texto="")
    insertar = radar_store.insert_post_if_new
    def insert(post, *args, **kwargs):
        if post["urn"] == error["urn"]:
            raise RuntimeError("fallo sintético")
        return insertar(post, *args, **kwargs)
    monkeypatch.setattr(radar_store, "insert_post_if_new", insert)
    radar._descartar_exterior = AsyncMock(side_effect=lambda p: p["urn"] == exterior["urn"])
    posts = [vistos, repetido, vieja, exterior, bueno, error, sin_texto]
    destinos = {}
    assert await radar._guardar_posts(posts, "es", destinos=destinos) == len(posts)
    assert destinos == {p["urn"]: d for p, d in zip(posts, ["ya_visto", "texto_repetido", "ignorada_vieja", "exterior", "guardado", "error_guardado", "sin_texto"])}
    # En inglés se guarda igual que en castellano (12/11): sin «solo con email» ni recorte por cuota
    destinos = {}
    assert await radar._guardar_posts([raw(8, emails=()), raw(9)], "en", destinos=destinos) == 2
    assert destinos == {raw(8)["urn"]: "guardado", raw(9)["urn"]: "guardado"}


@pytest.mark.asyncio
async def test_lectura_unica_con_login_y_fallo_registro(tmp_path, monkeypatch):
    radar = make_radar(tmp_path, queries=("es",))
    radar._esperar_login = AsyncMock(return_value=True)
    detalle = {"pantallas": [{"tarjetas_dom": 4, "nuevos": 1}], "scrolls": 2, "motivo_fin": "login"}
    h = FakeHarvester({"es": [HarvestResult(posts=[raw(20)], needs_login=True, lectura=detalle),
        HarvestResult(posts=[raw(20), raw(21)], lectura=dict(detalle, motivo_fin="no_crece"))]})
    assert await radar._leer_consulta(h, "es") == 3
    rows = radar_store.listar_lecturas()
    assert len(rows) == 1
    assert rows[0]["scrolls"] == 4 and len(rows[0]["pantallas"]) == 2
    assert rows[0]["parseados"] == 3
    assert rows[0]["guardados"] == 2
    assert Counter(rows[0]["destinos"].values()) == {"guardado": 2}
    monkeypatch.setattr(radar_store, "registrar_lectura", lambda **kw: (_ for _ in ()).throw(RuntimeError("sin disco")))
    assert await radar._leer_consulta(FakeHarvester(), "es") == 0
