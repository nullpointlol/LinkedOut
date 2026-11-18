"""Rediseño E1, el marco (docs/rediseno/plan.md §7 y §10): barra superior, barra de vistas abajo en el celular,
HUD de progreso, aviso de lote y botón de reportar error.

Con el DOM real de index.html y solo los CSS propios (sin Tailwind, Font Awesome ni red): el marco no depende de ellos.
Medidas: los 5 botones de vista con sus ids, rótulos e íconos locales; abajo y táctiles (≥ 44 px) por debajo de
1024 px, arriba desde 1024; sin scroll horizontal a 360/375/1200; la vista activa marcada con aria-current; el HUD
sin HTML inyectado.
"""
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parents[1]
INDEX = RAIZ / "static" / "index.html"
CSS = [RAIZ / "static" / "css" / n for n in ("tokens.css", "componentes.css", "marco.css")]
VISTAS = [("btnVistaManual", "manual", "Manual"), ("btnVistaRadar", "radar", "LinkedIn"),
          ("btnVistaPortales", "portales", "Portales"), ("btnVistaTickets", "tickets", "Tickets"),
          ("btnVistaPerfil", "perfil", "Mi perfil")]


def documento():
    return BeautifulSoup(INDEX.read_text(encoding="utf-8"), "html.parser")


def iconos_del_sprite():
    return set(re.findall(r'<symbol id="([a-z0-9-]+)"', (RAIZ / "static" / "iconos.svg").read_text(encoding="utf-8")))


# ---------------------------------------------------------------- marcado

def test_marco_css_se_carga_despues_de_componentes():
    html = INDEX.read_text(encoding="utf-8")
    assert 0 < html.find('href="/static/css/componentes.css') < html.find('href="/static/css/marco.css')


def test_selector_de_vistas_es_un_nav_con_los_cinco_botones():
    soup = documento()
    nav = soup.select_one("header.topbar nav#selectorVista")
    assert nav is not None and nav.get("aria-label")
    botones = nav.find_all("button", recursive=False)
    assert [b["id"] for b in botones] == [i for i, _, _ in VISTAS]
    for boton, (_, vista, rotulo) in zip(botones, VISTAS):
        assert boton["onclick"].replace('"', "'") == f"cambiarVista('{vista}')"
        assert boton["aria-label"] == rotulo and boton.get_text(" ", strip=True) == rotulo
        assert boton.select_one("svg.ico use")["href"].startswith("/static/iconos.svg#")


def test_marco_sin_font_awesome():
    soup = documento()
    for sel in ("header", "#agenticProgressHUD", "#batchAlert", "#btnReportarError"):
        assert not soup.select_one(sel).select("[class*=fa-]"), sel


def test_cada_icono_que_usa_el_marco_esta_en_el_sprite():
    fuentes = INDEX.read_text(encoding="utf-8") + "".join(
        (RAIZ / "static" / n).read_text(encoding="utf-8") for n in ("app.js", "tickets.js"))
    usados = set(re.findall(r"iconos\.svg#([a-z0-9-]+)", fuentes))
    usados |= set(re.findall(r"\bponerIcono\([^,]+,\s*'([a-z0-9-]+)'", fuentes))
    assert usados and usados <= iconos_del_sprite(), usados - iconos_del_sprite()


def test_css_propio_solo_con_tokens():
    tokens = set(re.findall(r"--([a-z0-9-]+)\s*:", CSS[0].read_text(encoding="utf-8")))
    for archivo in CSS[1:]:
        css = re.sub(r"/\*.*?\*/", "", archivo.read_text(encoding="utf-8"), flags=re.S)
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), f"{archivo.name}: color fuera de tokens.css"
        usados = set(re.findall(r"var\(--([a-z0-9-]+)", css))
        assert usados <= tokens, usados - tokens


def test_hud_y_lote_arrancan_ocultos_con_el_atributo_hidden():
    soup = documento()
    for sel in ("#agenticProgressHUD", "#batchAlert"):
        assert soup.select_one(sel).has_attr("hidden"), sel


# ---------------------------------------------------------------- navegador (sin servidor)

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
    page = navegador.new_page(viewport={"width": 375, "height": 812})
    # Sin red: la página y el sprite salen del disco (con un origen propio, para que <use> resuelva /static/iconos.svg)
    sprite = (RAIZ / "static" / "iconos.svg").read_text(encoding="utf-8")

    def ruta(r):
        url = r.request.url
        if url.endswith("/static/iconos.svg"):
            r.fulfill(body=sprite, content_type="image/svg+xml")
        elif url == "http://marco.test/":
            r.fulfill(body=str(soup), content_type="text/html; charset=utf-8")
        else:
            r.abort()

    page.route("**/*", ruta)
    page.goto("http://marco.test/")
    for archivo in CSS:
        page.add_style_tag(content=archivo.read_text(encoding="utf-8"))
    page.add_script_tag(content=(RAIZ / "static" / "app.js").read_text(encoding="utf-8"))
    page.evaluate("() => { window.Radar = {mostrar() {}}; window.Perfil = {mostrar() {}}; }")
    yield page
    page.close()


@pytest.mark.parametrize("ancho", [360, 375, 1200])
def test_sin_scroll_horizontal_y_marco_dentro_del_ancho(pagina, ancho):
    pagina.set_viewport_size({"width": ancho, "height": 812})
    assert pagina.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for sel in ("header h1", "#btnVerTelemetria", "#btnRegenerarGlobal", "#btnNuevaOfertaEncabezado"):
        r = pagina.locator(sel).bounding_box()
        assert r and r["x"] >= 0 and r["x"] + r["width"] <= ancho, sel
    assert pagina.locator("header h1").inner_text().strip() == "LinkedOut"
    assert pagina.locator("header").evaluate("el => el.scrollWidth <= el.clientWidth")


@pytest.mark.parametrize("ancho", [360, 375])
def test_en_el_celular_las_vistas_van_abajo_y_son_tactiles(pagina, ancho):
    pagina.set_viewport_size({"width": ancho, "height": 812})
    nav = pagina.locator("#selectorVista").bounding_box()
    assert nav["y"] + nav["height"] == pytest.approx(812, abs=1) and nav["width"] == pytest.approx(ancho, abs=1)
    for id_, _, rotulo in VISTAS:
        boton = pagina.locator(f"#{id_}")
        r = boton.bounding_box()
        assert r["height"] >= 44 and r["width"] >= 60, id_
        assert boton.locator("span").is_visible() and boton.inner_text().strip() == rotulo
        assert boton.evaluate("el => parseFloat(getComputedStyle(el).fontSize) >= 12")
    # El contenido no queda tapado por la barra
    alto_barra = nav["height"]
    assert pagina.evaluate("parseFloat(getComputedStyle(document.body).paddingBottom)") >= alto_barra - 1
    # El botón de reportar error queda arriba de la barra
    reporte = pagina.locator("#btnReportarError")
    if reporte.is_visible():
        assert reporte.bounding_box()["y"] + reporte.bounding_box()["height"] <= nav["y"]


def test_en_escritorio_las_vistas_van_en_la_barra_superior(pagina):
    pagina.set_viewport_size({"width": 1200, "height": 812})
    barra = pagina.locator("header").bounding_box()
    previo = 0
    for id_, _, _ in VISTAS:
        r = pagina.locator(f"#{id_}").bounding_box()
        assert r["y"] >= barra["y"] and r["y"] + r["height"] <= barra["y"] + barra["height"] + 1, id_
        assert r["x"] >= previo
        previo = r["x"] + r["width"]


def test_la_vista_activa_lleva_aria_current(pagina):
    for _, vista, _ in VISTAS + [("btnVistaManual", "editor", "")]:
        pagina.evaluate("v => cambiarVista(v)", vista)
        activos = pagina.evaluate(
            "() => [...document.querySelectorAll('#selectorVista > button')]"
            ".filter(b => b.getAttribute('aria-current') === 'page').map(b => b.id)")
        esperado = "btnVistaManual" if vista == "editor" else next(i for i, v, _ in VISTAS if v == vista)
        assert activos == [esperado], vista


def test_hud_por_fases_sin_html_inyectado(pagina):
    pagina.evaluate("() => actualizarHUDProgreso({phase: 2, percent: 50, agent: 'REDACTOR', step: 'Redactando'})")
    estado = pagina.evaluate("""() => ({
        visible: !document.getElementById('agenticProgressHUD').hidden,
        pasos: [1, 2, 3].map(i => document.getElementById('stepPill-' + i).dataset.phaseState),
        hecho: !!document.querySelector('#stepIcon-1 svg use'),
        icono: document.querySelector('#hudAgentIcon use').getAttribute('href'),
        pct: document.getElementById('hudPercent').textContent,
        barra: document.getElementById('hudProgressBar').style.width})""")
    assert estado == {"visible": True, "pasos": ["done", "active", "pending"], "hecho": True,
                      "icono": "/static/iconos.svg#pencil", "pct": "50%", "barra": "50%"}
    pagina.evaluate("() => completarHUDProgreso({message: '<img src=x onerror=alert(1)>Listo'})")
    titulo = pagina.locator("#hudStepTitle")
    assert "<img src=x onerror=alert(1)>Listo" in titulo.text_content()
    assert titulo.locator("img").count() == 0
    pagina.evaluate("() => ocultarHUDProgreso()")
    assert pagina.evaluate("() => document.getElementById('agenticProgressHUD').hidden")


def test_aviso_de_lote_se_muestra_con_su_avance(pagina):
    pagina.evaluate("() => mostrarAlertaLote({completed: 2, total: 5, current: 'Acme', percent: 40})")
    assert pagina.locator("#batchAlert").is_visible()
    assert pagina.locator("#batchMessage").inner_text() == "Lote: 2/5 (Acme)"
    assert pagina.locator("#batchProgressBar").evaluate("el => el.style.width") == "40%"
