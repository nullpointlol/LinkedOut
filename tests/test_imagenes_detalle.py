"""Detalle real en Chromium a 375 px, sin servidor, CDN ni red."""
from pathlib import Path
import re

import pytest
from playwright.sync_api import sync_playwright


RAIZ = Path(__file__).resolve().parents[1]
UTILIDADES = """
.text-xs{font-size:.75rem;line-height:1rem}
.text-sm{font-size:.875rem}
.whitespace-pre-wrap{white-space:pre-wrap}
.break-words{overflow-wrap:break-word}
.break-all{word-break:break-all}
.min-w-0{min-width:0}
.max-w-full{max-width:100%}
.w-full{width:100%}
[class~="[overflow-wrap:anywhere]"]{overflow-wrap:anywhere}
"""


@pytest.fixture
def pagina():
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>",
                              (RAIZ / "static/index.html").read_text(encoding="utf-8"), re.S))
    # Rediseño E2: el detalle usa los CSS propios (radar.css: .texto-oferta en pre-wrap)
    css += "\n" + "\n".join((RAIZ / "static/css" / n).read_text(encoding="utf-8")
                            for n in ("tokens.css", "componentes.css", "marco.css", "radar.css"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 375, "height": 812})
            page.route("**/*", lambda route: route.abort())
            page.set_content('<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">'
                             f'<style>{UTILIDADES}\n{css}</style>'
                             '<div id="radarDetallePost" class="text-xs"></div>')
            page.add_script_tag(content="""
                window.escapeHtml = value => {
                    const node = document.createElement('span');
                    node.textContent = String(value == null ? '' : value);
                    return node.innerHTML;
                };
            """)
            page.add_script_tag(content=(RAIZ / "static/radar.js").read_text(encoding="utf-8"))
            yield page
        finally:
            browser.close()


def render(page, post):
    page.evaluate("""post => {
        window.Radar._test.S.detalle = post;
        window.Radar._test.renderDetalle();
    }""", post)


def post_base():
    return {"id": 1, "estado": "ANALIZADA", "autor": "Acme", "texto": "Mirá la oferta",
            "fuente": "feed", "canal": "email", "emails": [], "triage": {}, "imagenes": []}


def test_detalle_plegado_seguro_375(pagina):
    texto = ('  Analista QA & requisitos\n'
             '<img id="inyectado" src="https://no-red.test/x" onerror="window.ataque=1">\n'
             '<script>window.ataque=2</script>\n'
             + "REQUISITO" * 120 + '\nFin literal  ')
    post = {**post_base(), "texto_imagen": texto}
    # Observar el setter real, además del DOM: escapar e interpolar en innerHTML
    # tampoco cumple el contrato de construir la transcripción con textContent.
    pagina.evaluate("""() => {
        window.ataque = 0;
        window.escriturasHTML = [];
        const original = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
        Object.defineProperty(Element.prototype, 'innerHTML', {
            ...original,
            set(value) { window.escriturasHTML.push(String(value)); original.set.call(this, value); }
        });
    }""")
    render(pagina, post)
    detalle = pagina.locator("details")
    assert detalle.count() == 1, "Falta el desplegable con el texto leído"
    assert detalle.get_attribute("open") is None
    summary = detalle.locator("summary")
    assert summary.inner_text() == "Leímos el texto de la imagen"
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    summary.click()
    assert detalle.get_attribute("open") is not None
    # Buscar por textContent exacto evita fijar si el bloque es pre, p o div.
    bloque = detalle.locator(":scope *").filter(has_text="Fin literal")
    literal = bloque.evaluate_all("(nodes, texto) => nodes.filter(n => n.textContent === texto).length", texto)
    assert literal >= 1, "La transcripción debe conservar espacios, saltos y HTML literales"
    assert "Analista QA & requisitos" in detalle.inner_text()
    assert detalle.evaluate("""(el, texto) => {
        const nodos = [el.querySelector('summary'), ...el.querySelectorAll('*')]
            .filter(n => n && (n.tagName === 'SUMMARY' || n.textContent === texto));
        return nodos.length >= 2 && nodos.every(n => parseFloat(getComputedStyle(n).fontSize) >= 12);
    }""", texto)
    assert pagina.locator("#inyectado").count() == 0
    assert detalle.locator("img, script").count() == 0
    assert pagina.evaluate("() => window.ataque") == 0
    assert pagina.evaluate("""() => window.escriturasHTML.every(
        valor => !valor.includes('REQUISITOREQUISITO') && !valor.includes('Fin literal'))""")
    assert pagina.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    assert detalle.evaluate("el => el.scrollWidth <= el.clientWidth")
    summary.click()
    assert detalle.get_attribute("open") is None


@pytest.mark.parametrize("texto_vacio", ["", "  \n\t"])
def test_detalle_sin_texto_igual(pagina, texto_vacio):
    post = post_base()
    render(pagina, post)
    antes = pagina.locator("#radarDetallePost").evaluate("el => el.outerHTML")
    visible_antes = pagina.locator("#radarDetallePost").inner_text()
    render(pagina, {**post, "texto_imagen": texto_vacio})
    assert pagina.locator("#radarDetallePost").evaluate("el => el.outerHTML") == antes
    assert pagina.locator("#radarDetallePost").inner_text() == visible_antes
    assert pagina.locator("details").count() == 0
    assert "Leímos el texto de la imagen" not in visible_antes
