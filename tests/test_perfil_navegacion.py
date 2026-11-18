"""Navegación con DOM real, sin servidor ni red.

UTILIDADES modela las utilidades de Tailwind que quedan en la página; el marco (barra superior y barra de vistas)
usa los CSS propios del rediseño (E1), que se cargan tal cual. No sustituye la QA visual en el 1199.
"""
import os
from pathlib import Path

from bs4 import BeautifulSoup
import pytest
from playwright.sync_api import sync_playwright


RAIZ = Path(__file__).resolve().parents[1]
CSS_PROPIOS = [RAIZ / "static" / "css" / n for n in ("tokens.css", "componentes.css", "marco.css")]
UTILIDADES = r"""
*,::before,::after{box-sizing:border-box;border-width:0;border-style:solid}
body,h1{margin:0} body{font-family:ui-sans-serif,system-ui,sans-serif}
button{font:inherit;background:transparent;color:inherit;padding:0}
.flex{display:flex}.flex-col{flex-direction:column}.hidden,[hidden]{display:none}
.items-center{align-items:center}.justify-between{justify-content:space-between}
.justify-center{justify-content:center}.min-w-0{min-width:0}.flex-shrink-0{flex-shrink:0}
.text-xs{font-size:12px;line-height:16px}.font-medium{font-weight:500}
.font-semibold{font-weight:600}.tracking-tight{letter-spacing:-.025em}
.whitespace-nowrap{white-space:nowrap}.border{border-width:1px}.border-b{border-bottom-width:1px}
.px-3{padding-left:12px;padding-right:12px}.px-2{padding-left:8px;padding-right:8px}
.py-1{padding-top:4px;padding-bottom:4px}
[class~="py-2.5"]{padding-top:10px;padding-bottom:10px}
[class~="px-2.5"]{padding-left:10px;padding-right:10px}
[class~="py-1.5"]{padding-top:6px;padding-bottom:6px}
[class~="p-0.5"]{padding:2px}.gap-1{gap:4px}.gap-2{gap:8px}
[class~="gap-0.5"]{gap:2px}[class~="gap-1.5"]{gap:6px}.mr-1{margin-right:4px}
.mx-0{margin-left:0;margin-right:0}
[class~="space-x-2"]>:not(:first-child){margin-left:8px}
[class~="space-x-2.5"]>:not(:first-child){margin-left:10px}
[class~="w-1.5"]{width:6px}[class~="h-1.5"]{height:6px}
.w-7{width:28px}.h-7{height:28px}.relative{position:relative}
/* Reserva conservadora para el glifo: no simula el dibujo de Font Awesome. */
:where(.fa-solid){display:inline-block;width:12px;height:12px}
@media(min-width:640px){
 [class~="sm:inline"]{display:inline}[class~="sm:flex"]{display:flex}
 [class~="sm:text-sm"]{font-size:14px;line-height:20px}
 [class~="sm:px-4"]{padding-left:16px;padding-right:16px}
 [class~="sm:mx-2"]{margin-left:8px;margin-right:8px}
}
"""


def documento():
    return BeautifulSoup((RAIZ / "static/index.html").read_text(encoding="utf-8"), "html.parser")


@pytest.fixture
def pagina():
    soup = documento()
    for node in soup.select("script, link[rel=stylesheet]"):
        node.decompose()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 360, "height": 812})
            page.route("**/*", lambda r: r.abort())
            page.set_content(str(soup))
            page.add_style_tag(content=UTILIDADES)
            for archivo in CSS_PROPIOS:
                page.add_style_tag(content=archivo.read_text(encoding="utf-8"))
            # DOMContentLoaded ya ocurrió: no inicializar proveedores, SSE ni Radar.
            page.add_script_tag(content=(RAIZ / "static/app.js").read_text(encoding="utf-8"))
            page.evaluate("""() => {
                window.llamadasRadar = []; window.llamadasPerfil = 0;
                window.Radar = {mostrar: modo => llamadasRadar.push(modo)};
                window.Perfil = {mostrar: () => { llamadasPerfil++; }};
            }""")
            yield page
        finally:
            browser.close()


def comprobar_vista(page, vista, activar=True):
    if activar:
        page.evaluate("vista => cambiarVista(vista)", vista)
    estado = page.evaluate("""() => ({
        main: document.querySelector('main').style.display,
        nav: document.getElementById('navMobilePostulaciones').style.display,
        radarHidden: document.getElementById('vista-radar').hidden,
        radarFlex: document.getElementById('vista-radar').classList.contains('flex'),
        radarCol: document.getElementById('vista-radar').classList.contains('flex-col')
    })""")
    radar = vista in ("manual", "radar")
    assert estado == {"main": "" if vista == "editor" else "none",
                      "nav": "" if vista == "editor" else "none",
                      "radarHidden": not radar, "radarFlex": radar, "radarCol": radar}
    # La vista activa lleva aria-current (rediseño E1); el editor es parte de Manual
    marcada = "manual" if vista == "editor" else vista
    for modo, selector in (("manual", "#btnVistaManual"), ("radar", "#btnVistaRadar")):
        assert (page.locator(selector).get_attribute("aria-current") == "page") == (marcada == modo)


def test_invariantes_vistas_actuales(pagina):
    # invariante: Manual/Radar llaman mostrar(modo); Editor deja main/nav visibles.
    for vista in ("manual", "radar", "editor", "manual"):
        comprobar_vista(pagina, vista)
    assert pagina.evaluate("() => llamadasRadar") == ["manual", "radar", "manual"]
    assert pagina.evaluate("() => llamadasPerfil") == 0


def comprobar_header(page, ancho, perfil=False):
    page.set_viewport_size({"width": ancho, "height": 812})
    # Desde 1024 px las vistas van entre el título y Ajustes; antes, en la barra de abajo (rediseño E1)
    selectors = ["header h1", "#selectorVista", "#btnVerTelemetria"] if ancho >= 1024 else ["header h1", "#btnVerTelemetria"]
    rects = [page.locator(s).bounding_box() for s in selectors]
    assert all(rects)
    for rect in rects:
        assert rect["x"] >= 0 and rect["x"] + rect["width"] <= ancho
    for izquierda, derecha in zip(rects, rects[1:]):
        assert izquierda["x"] + izquierda["width"] <= derecha["x"]
    if ancho < 1024:
        barra = page.locator("#selectorVista").bounding_box()
        assert barra["x"] >= 0 and barra["x"] + barra["width"] <= ancho and barra["y"] + barra["height"] <= 812 + 1
    # Volver a analizar, Nueva oferta y Ajustes: a la derecha del título, dentro del ancho y sin pisarse
    controles = page.locator("header button").all()
    visibles = [c.bounding_box() for c in controles if c.is_visible()]
    acciones = [c.bounding_box() for c in page.locator("header .topbar-acciones > button, #btnVerTelemetria").all()
                if c.is_visible()]
    for rect in acciones:
        assert rect["x"] >= rects[0]["x"] + rects[0]["width"]
    for indice, rect in enumerate(visibles):
        assert rect["x"] >= 0 and rect["x"] + rect["width"] <= ancho
        for otro in visibles[indice + 1:]:
            assert (rect["x"] + rect["width"] <= otro["x"] or otro["x"] + otro["width"] <= rect["x"] or
                    rect["y"] + rect["height"] <= otro["y"] or otro["y"] + otro["height"] <= rect["y"])
    assert page.locator("header").evaluate("el => el.scrollWidth <= el.clientWidth")
    titulo = page.locator("header h1")
    assert titulo.inner_text().split("\n")[0].strip() == "LinkedOut"
    assert titulo.evaluate("el => el.scrollWidth <= el.clientWidth")
    botones = ["#btnVistaManual", "#btnVistaRadar", "#btnVerTelemetria"]
    if perfil:
        botones.append("#btnVistaPerfil")
    for selector in ["header h1", *botones]:
        assert page.locator(selector).evaluate("el => parseFloat(getComputedStyle(el).fontSize) >= 12")
    for selector in botones:
        rect = page.locator(selector).bounding_box()
        assert rect and rect["width"] > 0 and rect["x"] >= 0
        assert rect["x"] + rect["width"] <= ancho
        assert page.locator(selector).get_attribute("aria-label")
    if perfil:
        assert page.locator("#btnVistaPerfil span").is_visible()
    return rects


@pytest.mark.parametrize("ancho", [360, 375, 1200])
def test_header_360_375(pagina, ancho):
    # invariante: título entero >=12px, controles visibles sin intersección ni overflow.
    # Base ANTES de integrar, Chromium + UTILIDADES (x/ancho en px; no QA real):
    # 360: título 22/73.03125, selector 98/76, Ajustes 178/46.
    # 375: título 22/73.03125, selector 103.6875/76, Ajustes 188.34375/46.
    # 1200: título 54/127.203125, selector 305.0625/197.9375, Ajustes 626.875/46.
    # QA aparte con CDN real + Font Awesome cargados (sin servidor):
    # base a 360: título x22/ancho73.03125; Manual x101/ancho35.5;
    # Radar x138.5/ancho34; Ajustes x179.5/ancho46; scrollWidth 360.
    # Integrado: título idéntico; Perfil x151.6875/ancho20.5; scrollWidth 360.
    # QA 360/375/1200: todos los controles sin solaparse ni salir del header,
    # texto >=12. Las capturas se guardaron en el temporal privado de la misión.
    rects = comprobar_header(pagina, ancho)
    print(f"header {ancho}: {rects}")
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert pagina.locator("#btnVistaPerfil").count() == 1
    comprobar_header(pagina, ancho, perfil=True)


def test_estructura_perfil():
    soup = documento()
    # invariante: se conservan los controles y contenedores públicos existentes.
    for selector in ("#selectorVista #btnVistaManual", "#selectorVista #btnVistaRadar",
                     "main", "#navMobilePostulaciones", "section#vista-radar"):
        assert soup.select_one(selector) is not None
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    boton = soup.select_one('#selectorVista #btnVistaPerfil[aria-label="Mi perfil"]')
    assert boton is not None
    assert boton.select_one('svg use[href="/static/iconos.svg#user"]') is not None
    assert boton.select_one("span").get_text(strip=True) == "Mi perfil"
    assert boton["onclick"].replace('"', "'") == "cambiarVista('perfil')"
    seccion = soup.select_one("section#vista-perfil[hidden]")
    assert seccion is not None
    assert "bg-[#0f1115]" in seccion["class"] or "bg-[#16181d]" in seccion["class"]
    assert seccion.select_one("form, input, textarea") is None
    scripts = [s.get("src", "").split("?")[0] for s in soup.select("script[src]")]
    assert "/static/perfil.js" in scripts
    assert scripts.index("/static/perfil.js") > max(scripts.index("/static/app.js"), scripts.index("/static/radar.js"))


def test_transiciones_perfil_manual_radar_editor(pagina):
    # invariante: ejecutar las vistas anteriores antes del escenario nuevo.
    for vista in ("manual", "radar", "editor"):
        comprobar_vista(pagina, vista)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    assert pagina.locator("#vista-perfil").count() == 1
    assert pagina.locator("#vista-perfil").evaluate("el => el.hidden")
    pagina.evaluate("() => { llamadasRadar.length = 0; }")
    for destino in ("manual", "radar", "editor"):
        pagina.locator("#btnVistaPerfil").click()
        comprobar_vista(pagina, "perfil", activar=False)
        assert not pagina.locator("#vista-perfil").evaluate("el => el.hidden")
        assert pagina.locator("#btnVistaPerfil").get_attribute("aria-current") == "page"
        comprobar_vista(pagina, destino)
        assert pagina.locator("#vista-perfil").evaluate("el => el.hidden")
        assert pagina.locator("#btnVistaPerfil").get_attribute("aria-current") is None
    assert pagina.evaluate("() => llamadasPerfil") == 3
    assert pagina.evaluate("() => llamadasRadar") == ["manual", "radar"]
    # La siguiente misión aporta el módulo; ausencia o API incompleta no rompe navegación.
    for reemplazo in ("delete window.Perfil", "window.Perfil = {}"):
        pagina.evaluate(f"() => {{ {reemplazo}; }}")
        comprobar_vista(pagina, "perfil")
        assert not pagina.locator("#vista-perfil").evaluate("el => el.hidden")
        comprobar_vista(pagina, "manual")
        assert pagina.locator("#vista-perfil").evaluate("el => el.hidden")
