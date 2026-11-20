"""Etapas del editor con el mismo selector que los estados de LinkedIn (consistente y minimalista).

Debajo del buscador, una sola línea: el selector «Todas · N ▾», que abre en el flujo la lista de las 5 etapas con
sus números, y «Filtros», que abre el estado del CV (Todas, Sin CV, Listas) y el orden. De 360 a 1440 px.
Portal de prueba real (fixture de test_ui_navegador). Medidas: selector y «Filtros» visibles, a la misma altura,
dentro de su fila y sin cortes; sin scroll horizontal; cada etapa se elige desde el menú (mouse y teclado) y el
selector muestra la elegida con su número; el panel de Filtros tiene el CV y el orden.
"""
import re

import pytest

from core import database
from tests.test_ui_navegador import _abrir, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(240)]

ETAPAS = [("tabLifecycleTodas", "Todas", 3), ("tabLifecyclePorPostular", "Por postular", 1),
          ("tabLifecyclePostuladas", "Postuladas", 1), ("tabLifecycleEnProceso", "En proceso", 1),
          ("tabLifecycleFinalizadas", "Finalizadas", 0)]

MEDIR = """() => {
  const sel = document.getElementById('btnEtapaEditor'), fil = document.getElementById('btnFiltrosEditor');
  const fila = sel.parentElement, caja = fila.getBoundingClientRect();
  const a = sel.getBoundingClientRect(), b = fil.getBoundingClientRect();
  const dentro = r => r.width > 0 && r.left >= caja.left - 1 && r.right <= caja.right + 1;
  return { misma: Math.abs(a.top - b.top) < 2, dentro: dentro(a) && dentro(b), mismaFila: fil.parentElement === fila,
           alto: caja.height, desborda: fila.scrollWidth > fila.clientWidth + 1,
           cortado: [...sel.querySelectorAll('span')].some(s => s.scrollWidth > s.clientWidth + 1),
           pagina: document.documentElement.scrollWidth > innerWidth + 1, texto: sel.innerText.trim(),
           menuOculto: document.getElementById('tabsLifecycleFiltros').hidden,
           panelOculto: document.getElementById('panelFiltrosEditor').hidden }; }"""


def _sembrar():
    for titulo, etapa in (("Pastelera", "POR_POSTULAR"), ("Panadero", "POSTULADA"), ("Cocinera", "EN_ENTREVISTA")):
        pid = database.create_postulacion(titulo, "Bodegón Ejemplo", "texto", status="READY")
        database.update_postulacion_lifecycle(pid, etapa)


def _al_editor(page):
    page.evaluate("cambiarVista('editor')")
    page.wait_for_function("() => document.querySelectorAll('#listaOfertasCards .oferta-item').length > 0")
    page.wait_for_timeout(300)


@pytest.mark.parametrize("ancho", [360, 390, 1024, 1280, 1440])
def test_selector_de_etapa_y_filtros_en_una_linea(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    _sembrar()
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _al_editor(page)
        m = page.evaluate(MEDIR)
        assert m["misma"] and m["dentro"] and m["mismaFila"], m
        assert m["alto"] <= 44 and not m["desborda"] and not m["cortado"] and not m["pagina"], m
        assert m["menuOculto"] and m["panelOculto"], m
        assert re.match(r"^Todas\s*·\s*3\b", m["texto"]), m["texto"]
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


@pytest.mark.parametrize("ancho", [390, 1280])
def test_cada_etapa_se_elige_desde_el_menu(navegador, portal, ancho):  # noqa: F811
    base, _ = portal
    _sembrar()
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _al_editor(page)
        boton, menu = page.locator("#btnEtapaEditor"), page.locator("#tabsLifecycleFiltros")
        for id_, texto, n in ETAPAS:
            boton.click()
            assert menu.is_visible() and boton.get_attribute("aria-expanded") == "true"
            item = page.locator(f"#{id_}")
            assert re.search(rf"{texto}\s*{n}$", item.inner_text().strip()), (id_, item.inner_text())
            assert page.evaluate("""el => { const r = el.getBoundingClientRect();
                const arriba = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                return !!arriba && (arriba === el || el.contains(arriba)); }""", item.element_handle()), id_
            item.click()
            assert not menu.is_visible() and boton.get_attribute("aria-expanded") == "false"
            assert re.match(rf"^{texto}\s*·\s*{n}\b", boton.inner_text().strip()), (id_, boton.inner_text())
            assert item.get_attribute("aria-pressed") == "true"
            tarjetas = page.locator("#listaOfertasCards .oferta-item").count()
            assert tarjetas == n, (id_, tarjetas)
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_menu_de_etapas_con_teclado(navegador, portal):  # noqa: F811
    base, _ = portal
    _sembrar()
    contexto, page, errores = _abrir(navegador, base, 1280)
    try:
        _al_editor(page)
        menu = page.locator("#tabsLifecycleFiltros")
        enfocado = "() => document.activeElement && document.activeElement.id"
        page.focus("#btnEtapaEditor")
        page.keyboard.press("Enter")
        assert menu.is_visible() and page.evaluate(enfocado) == "tabLifecycleTodas"
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        assert page.evaluate(enfocado) == "tabLifecyclePostuladas"
        page.keyboard.press("End")
        assert page.evaluate(enfocado) == "tabLifecycleFinalizadas"
        page.keyboard.press("ArrowDown")
        assert page.evaluate(enfocado) == "tabLifecycleTodas"
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Home")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert not menu.is_visible() and page.evaluate(enfocado) == "btnEtapaEditor"
        assert page.locator("#btnEtapaEditor").inner_text().strip().startswith("Por postular")
        page.keyboard.press("ArrowDown")
        assert menu.is_visible() and page.evaluate(enfocado) == "tabLifecyclePorPostular"
        page.keyboard.press("Escape")
        assert not menu.is_visible() and page.evaluate(enfocado) == "btnEtapaEditor"
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_filtros_del_editor_tienen_cv_y_orden(navegador, portal):  # noqa: F811
    base, _ = portal
    _sembrar()
    contexto, page, errores = _abrir(navegador, base, 390)
    try:
        _al_editor(page)
        page.click("#btnEtapaEditor")
        page.click("#btnFiltrosEditor")
        panel = page.locator("#panelFiltrosEditor")
        assert panel.is_visible() and not page.locator("#tabsLifecycleFiltros").is_visible()
        for id_ in ("chipFiltroTodas", "chipFiltroSinCV", "chipFiltroListas", "selectOrdenOfertas"):
            assert panel.locator(f"#{id_}").is_visible(), id_
        page.click("#chipFiltroSinCV")
        assert page.locator("#filtrosEditorCuenta").inner_text().strip() == "1"
        page.select_option("#selectOrdenOfertas", "fecha_desc")
        assert page.locator("#filtrosEditorCuenta").inner_text().strip() == "2"
        page.click("#chipFiltroTodas")
        page.select_option("#selectOrdenOfertas", "match_desc")
        assert not page.locator("#filtrosEditorCuenta").is_visible()
        page.click("#btnEtapaEditor")
        assert not panel.is_visible()
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
