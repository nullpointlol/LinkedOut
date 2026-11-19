"""Pestañas de la vista LinkedIn en el celular (#radarTabs): los 4 pasos entran completos desde 360 px.

Con el DOM real de index.html y los CSS propios, sin servidor. Medidas: ningún texto de pestaña se corta
(scrollWidth <= clientWidth en el botón y en cada texto), las 4 pestañas a la vista, objetivo táctil de 44 px y sin
scroll horizontal a 360 y 390 px; a 1024 px, las pestañas del celular no se ven.
"""
import pytest
from playwright.sync_api import sync_playwright

from tests.test_rediseno_marco import RAIZ, documento

pytestmark = [pytest.mark.ui]
CSS = [RAIZ / "static" / "css" / n for n in ("tokens.css", "base.css", "componentes.css", "marco.css", "radar.css")]


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def pagina(navegador):
    soup = documento()
    for nodo in soup.select("script, link[rel=stylesheet]"):
        nodo.decompose()
    page = navegador.new_page(viewport={"width": 360, "height": 800})
    sprite = (RAIZ / "static" / "iconos.svg").read_text(encoding="utf-8")

    def ruta(r):
        if r.request.url.endswith("/static/iconos.svg"):
            r.fulfill(body=sprite, content_type="image/svg+xml")
        elif r.request.url == "http://pestanas.test/":
            r.fulfill(body=str(soup), content_type="text/html; charset=utf-8")
        else:
            r.abort()

    page.route("**/*", ruta)
    page.goto("http://pestanas.test/")
    for archivo in CSS:
        page.add_style_tag(content=archivo.read_text(encoding="utf-8"))
    page.evaluate("() => { document.getElementById('vista-radar').hidden = false; }")
    yield page
    page.close()


@pytest.mark.parametrize("ancho", [360, 390])
def test_las_cuatro_pestanas_entran_completas(pagina, ancho):
    pagina.set_viewport_size({"width": ancho, "height": 800})
    medidas = pagina.evaluate("""() => [...document.querySelectorAll('#radarTabs > button')].map(b => ({
        tab: b.dataset.radarTab, visible: b.getClientRects().length > 0, alto: b.getBoundingClientRect().height,
        cortado: b.scrollWidth > b.clientWidth ||
            [...b.querySelectorAll('span')].some(s => s.getClientRects().length && s.scrollWidth > s.clientWidth),
        texto: b.innerText.trim(), derecha: b.getBoundingClientRect().right }))""")
    visibles = [m for m in medidas if m["visible"]]
    assert [m["tab"] for m in visibles] == ["lista", "feed", "detalle", "email"]
    for m in visibles:
        assert not m["cortado"], f"a {ancho} px se corta «{m['texto']}»"
        assert m["alto"] >= 44 and m["derecha"] <= ancho, m
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


def test_en_escritorio_no_se_ven(pagina):
    pagina.set_viewport_size({"width": 1024, "height": 800})
    assert not pagina.locator("#radarTabs").is_visible()
