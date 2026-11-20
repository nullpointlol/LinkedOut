"""Selector de estados: en lugar de la fila de chips, un selector compacto
«Relevantes · 30 ▾» que abre la lista de todos los estados con sus contadores, y al lado «Filtros». Los dos en una
sola línea chica, siempre a la vista, en LinkedIn, Manual y Portales, de 360 a 1440 px (y en la columna de 320 px).
El menú va en el flujo de la página (no tapa nada), se maneja con el teclado (flechas, Inicio/Fin, Enter, Esc) y se
cierra al elegir.

Portal de prueba real (fixture de test_ui_navegador). Medidas: selector y «Filtros» visibles, a la misma altura,
dentro de su fila y sin cortes; sin scroll horizontal; los 10 estados se eligen desde el menú (con el mouse y con el
teclado) y el selector muestra el elegido con su número.
"""
import re

import pytest

from tests.test_ui_navegador import _abrir, _sembrar_radar, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

ESTADOS = [("RELEVANTES", "Relevantes"), ("PENDIENTES", "Por analizar"), ("ANALIZADA", "Analizadas"),
           ("LISTO", "Listas"), ("GENERANDO", "Generando"), ("ENVIADO", "Enviadas"), ("DESCARTADO", "Descartadas"),
           ("ERROR", "Con error"), ("TODOS", "Todas"), ("IGNORADA", "Ignoradas")]

MEDIR_LINEA = """() => {
  const sel = document.getElementById('radarEstadoBtn'), fil = document.getElementById('radarFiltrosBtn'),
        fila = sel.parentElement;
  const caja = fila.getBoundingClientRect(), a = sel.getBoundingClientRect(), b = fil.getBoundingClientRect();
  const dentro = r => r.width > 0 && r.left >= caja.left - 1 && r.right <= caja.right + 1;
  return { misma: Math.abs(a.top - b.top) < 2 && Math.abs(a.bottom - b.bottom) < 2,
           dentro: dentro(a) && dentro(b), alto: caja.height, desborda: fila.scrollWidth > fila.clientWidth + 1,
           cortado: [...sel.querySelectorAll('span')].some(s => s.scrollWidth > s.clientWidth + 1),
           pagina: document.documentElement.scrollWidth > innerWidth + 1, texto: sel.innerText.trim(),
           menuOculto: document.getElementById('radarFiltros').hidden }; }"""


def _a_la_vista(page, vista):
    page.click(f"#btnVista{vista.capitalize()}")
    page.wait_for_selector("#radarEstadoBtn")
    page.wait_for_timeout(300)


@pytest.mark.parametrize("ancho", [360, 390, 1024, 1280, 1440])
@pytest.mark.parametrize("vista", ["radar", "manual", "portales"])
def test_selector_y_filtros_en_una_linea(navegador, portal, ancho, vista):  # noqa: F811
    base, _ = portal
    _sembrar_radar(2)
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _a_la_vista(page, vista)
        m = page.evaluate(MEDIR_LINEA)
        assert m["misma"] and m["dentro"], m
        assert m["alto"] <= 44, m
        assert not m["desborda"] and not m["cortado"] and not m["pagina"], m
        assert m["menuOculto"], m
        assert re.match(r"^Relevantes\s*·\s*\d+", m["texto"]), m["texto"]
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


@pytest.mark.parametrize("ancho", [390, 1280])
def test_todos_los_estados_se_eligen_desde_el_menu(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    _sembrar_radar(2)
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _a_la_vista(page, "radar")
        boton, menu = page.locator("#radarEstadoBtn"), page.locator("#radarFiltros")
        for estado, texto in ESTADOS:
            boton.click()
            assert menu.is_visible() and boton.get_attribute("aria-expanded") == "true"
            item = menu.locator(f'button[data-estado="{estado}"]')
            # En el flujo de la página: nada lo tapa y no tapa al selector ni a «Filtros»
            assert page.evaluate("""el => { const r = el.getBoundingClientRect();
                const arriba = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                return !!arriba && (arriba === el || el.contains(arriba)); }""", item.element_handle()), estado
            assert re.search(r"\d", item.inner_text()), (estado, item.inner_text())
            item.click()
            assert not menu.is_visible() and boton.get_attribute("aria-expanded") == "false"
            assert re.match(rf"^{texto}\s*·\s*\d+", boton.inner_text().strip()), (estado, boton.inner_text())
            assert item.get_attribute("aria-pressed") == "true"
            assert page.evaluate("() => window.Radar._test.S.filtro") == estado
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_menu_con_teclado(navegador, portal):  # noqa: F811
    base, _ = portal
    _sembrar_radar(2)
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        _a_la_vista(page, "radar")
        boton, menu = page.locator("#radarEstadoBtn"), page.locator("#radarFiltros")
        enfocado = "() => document.activeElement && (document.activeElement.dataset.estado || document.activeElement.id)"
        boton.focus()
        page.keyboard.press("Enter")
        assert menu.is_visible()
        assert page.evaluate(enfocado) == "RELEVANTES"  # abre sobre el estado activo
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        assert page.evaluate(enfocado) == "ANALIZADA"
        page.keyboard.press("ArrowUp")
        assert page.evaluate(enfocado) == "PENDIENTES"
        page.keyboard.press("End")
        assert page.evaluate(enfocado) == "IGNORADA"
        page.keyboard.press("ArrowDown")  # da la vuelta
        assert page.evaluate(enfocado) == "RELEVANTES"
        page.keyboard.press("Home")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert not menu.is_visible()
        assert page.evaluate("() => window.Radar._test.S.filtro") == "PENDIENTES"
        assert page.evaluate(enfocado) == "radarEstadoBtn"  # el foco vuelve al selector
        page.keyboard.press("ArrowDown")  # flecha abajo en el selector también abre
        assert menu.is_visible() and page.evaluate(enfocado) == "PENDIENTES"
        page.keyboard.press("Escape")
        assert not menu.is_visible() and page.evaluate(enfocado) == "radarEstadoBtn"
        assert page.evaluate("() => window.Radar._test.S.filtro") == "PENDIENTES"
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_abrir_uno_cierra_el_otro(navegador, portal):  # noqa: F811
    base, _ = portal
    contexto, page, errores = _abrir(navegador, base, 390)
    try:
        _a_la_vista(page, "radar")
        page.click("#radarEstadoBtn")
        assert page.locator("#radarFiltros").is_visible()
        page.click("#radarFiltrosBtn")
        assert page.locator("#radarFiltrosPanel").is_visible() and not page.locator("#radarFiltros").is_visible()
        page.click("#radarEstadoBtn")
        assert page.locator("#radarFiltros").is_visible() and not page.locator("#radarFiltrosPanel").is_visible()
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
